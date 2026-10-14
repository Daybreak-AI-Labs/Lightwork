"""Shared marketplace import authority contention and failure regressions."""
from __future__ import annotations

import copy
import json
import threading
from types import SimpleNamespace

import pytest
from maverick import agent_trust
from maverick.marketplace import federation as mf
from maverick.marketplace.federation import MarketplaceFederationStoreError

LISTING = {
    "name": "summarize-url",
    "version": "1.0.0",
    "kind": "skills",
    "summary": "Summarize a URL safely.",
    "source": "gh:example/catalog:summarize-url/SKILL.md",
    "sha256": "ab" * 32,
    "author": "example",
}


class _SharedAuthority:
    """Atomic AuthorityDocumentStore double shared by replica instances."""

    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.document: dict = {}
        self.lock = threading.Lock()
        self.mutate_calls = 0
        self.callback_calls = 0
        self.retry_callback_once = False
        self.fail_read = False
        self.fail_mutate = False

    def store(self):
        shared = self

        class _Store:
            @property
            def backend_kind(self):
                return shared.backend_kind

            def read(self):
                if shared.fail_read:
                    raise OSError("database unavailable")
                with shared.lock:
                    return copy.deepcopy(shared.document)

            def mutate(
                self,
                callback,
                *,
                action,
                actor="system",
                max_retries=16,
            ):
                del action, actor, max_retries
                if shared.fail_mutate:
                    raise OSError("database unavailable")
                with shared.lock:
                    shared.mutate_calls += 1
                    document = copy.deepcopy(shared.document)
                    if shared.retry_callback_once:
                        shared.retry_callback_once = False
                        shared.callback_calls += 1
                        callback(copy.deepcopy(document))
                    shared.callback_calls += 1
                    result = callback(document)
                    shared.document = copy.deepcopy(document)
                    return result

        return _Store()


def _envelope(
    *,
    origin: str = "peer-a",
    created: float = 1_750_000_000.0,
    listings: list[dict] | None = None,
) -> dict:
    return {
        "schema": mf.SCHEMA,
        "origin": origin,
        "created_at": mf._iso(created),
        "listings": copy.deepcopy(listings if listings is not None else [LISTING]),
        "pubkey": "11" * 32,
        "key_id": "key",
        "sig": "signature",
    }


@pytest.fixture
def shared_mode(monkeypatch, tmp_path):
    shared = _SharedAuthority()
    legacy = tmp_path / "marketplace_federation_imports.json"
    monkeypatch.setattr(mf, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(
        mf,
        "_store_path",
        lambda path=None: path if path is not None else legacy,
    )
    monkeypatch.setattr(mf, "_new_shared_store", shared.store)
    monkeypatch.setattr(mf, "verify_envelope", lambda *_a, **_k: (True, "ok"))
    monkeypatch.setattr(
        agent_trust,
        "decide_inbound",
        lambda _origin: SimpleNamespace(denied=False, reason="ok"),
    )
    monkeypatch.setattr(
        mf,
        "_moderate",
        lambda *_a, **_k: {"decision": "APPROVE", "reasons": []},
    )
    return shared, legacy


def test_concurrent_origins_preserve_complete_state_and_watermarks(shared_mode):
    shared, _legacy = shared_mode
    barrier = threading.Barrier(2)
    reports: list[dict] = []
    report_lock = threading.Lock()

    def apply(envelope: dict) -> None:
        barrier.wait()
        report = mf.import_listings(envelope, peers={})
        with report_lock:
            reports.append(report)

    threads = [
        threading.Thread(target=apply, args=(_envelope(origin="peer-a"),)),
        threading.Thread(
            target=apply,
            args=(_envelope(origin="peer-b", created=1_750_000_500.0),),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(reports) == 2 and all(report["ok"] for report in reports)
    assert {row["name"] for row in mf.imported_listings()} == {
        "peer-a/summarize-url",
        "peer-b/summarize-url",
    }
    assert set(shared.document["watermarks"]) == {"peer-a", "peer-b"}


def test_raced_newer_import_cannot_be_rolled_back(shared_mode):
    shared, _legacy = shared_mode
    old = _envelope(
        created=1_750_000_000.0,
        listings=[LISTING, {**LISTING, "name": "withdrawn-listing"}],
    )
    new = _envelope(created=1_750_000_500.0, listings=[LISTING])
    barrier = threading.Barrier(2)
    reports: dict[str, dict] = {}
    report_lock = threading.Lock()

    def apply(label: str, envelope: dict) -> None:
        barrier.wait()
        report = mf.import_listings(envelope, peers={})
        with report_lock:
            reports[label] = report

    threads = [
        threading.Thread(target=apply, args=("old", old)),
        threading.Thread(target=apply, args=("new", new)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert reports["new"]["ok"]
    assert shared.document["watermarks"]["peer-a"] == 1_750_000_500.0
    assert [row["name"] for row in mf.imported_listings()] == [
        "peer-a/summarize-url"
    ]


def test_moderation_and_donation_run_once_across_cas_retry(
    shared_mode,
    monkeypatch,
):
    shared, _legacy = shared_mode
    shared.retry_callback_once = True
    calls = {"moderation": 0, "donation": 0}

    def moderate(*_args):
        calls["moderation"] += 1
        return {"decision": "APPROVE", "reasons": []}

    def donation(_url):
        calls["donation"] += 1
        return False, "not allowed"

    monkeypatch.setattr(mf, "_moderate", moderate)
    monkeypatch.setattr(mf, "validate_donation_url", donation)
    report = mf.import_listings(
        _envelope(listings=[{**LISTING, "donation_url": "https://bad.test"}]),
        peers={},
    )

    assert report["ok"]
    assert report["stripped_donations"] == ["summarize-url"]
    assert calls == {"moderation": 1, "donation": 1}
    assert shared.callback_calls == 2


def test_shared_write_outage_fails_closed_without_local_fallback(shared_mode):
    shared, legacy = shared_mode
    shared.fail_mutate = True

    report = mf.import_listings(_envelope(), peers={})

    assert not report["ok"]
    assert "update failed" in report["reason"]
    assert not legacy.exists()


def test_shared_read_outage_never_surfaces_local_data(shared_mode):
    shared, legacy = shared_mode
    legacy.write_text("{}", encoding="utf-8")
    shared.fail_read = True

    with pytest.raises(MarketplaceFederationStoreError, match="read failed"):
        mf.imported_listings()
    assert json.loads(legacy.read_text(encoding="utf-8")) == {}


def test_live_legacy_state_blocks_postgres_cutover(shared_mode):
    shared, legacy = shared_mode
    legacy.write_text(
        json.dumps(
            {
                "skills": {
                    "peer-a/stale": {
                        "name": "peer-a/stale",
                        "kind": "skills",
                    }
                },
                "__watermarks__": {"peer-a": 1.0},
            }
        ),
        encoding="utf-8",
    )

    report = mf.import_listings(_envelope(), peers={})

    assert not report["ok"] and "migrate" in report["reason"]
    assert shared.mutate_calls == 0
    with pytest.raises(MarketplaceFederationStoreError, match="migrate"):
        mf.imported_listings()


def test_store_path_override_is_forbidden_in_postgres(shared_mode, tmp_path):
    shared, _legacy = shared_mode
    override = tmp_path / "override.json"

    report = mf.import_listings(_envelope(), peers={}, store_path=override)

    assert not report["ok"] and "store_path override" in report["reason"]
    assert shared.mutate_calls == 0
    with pytest.raises(MarketplaceFederationStoreError, match="store_path override"):
        mf.imported_listings(store_path=override)
    assert not override.exists()


@pytest.mark.parametrize(
    "document",
    [
        {
            "schema_version": 999,
            "listings": {},
            "watermarks": {},
        },
        {
            "schema_version": 1,
            "listings": {},
            "watermarks": {f"peer-{index}": 1.0 for index in range(65)},
        },
        {
            "schema_version": 1,
            "listings": {
                "skills": {
                    "peer-a/wrong": {
                        **LISTING,
                        "name": "peer-a/wrong",
                        "fed_name": "different",
                        "fed_origin": "peer-a",
                        "imported_at": 1.0,
                    }
                }
            },
            "watermarks": {"peer-a": 1.0},
        },
    ],
)
def test_malformed_or_overbound_shared_state_is_never_served(
    shared_mode,
    document,
):
    shared, _legacy = shared_mode
    shared.document = copy.deepcopy(document)
    before = copy.deepcopy(shared.document)

    with pytest.raises(MarketplaceFederationStoreError):
        mf.imported_listings()
    report = mf.import_listings(_envelope(), peers={})
    assert not report["ok"]
    assert shared.document == before


def test_oversized_incoming_listing_is_rejected_before_moderation(
    shared_mode,
    monkeypatch,
):
    _shared, _legacy = shared_mode
    moderation_calls = 0

    def moderate(*_args):
        nonlocal moderation_calls
        moderation_calls += 1
        return {"decision": "APPROVE", "reasons": []}

    monkeypatch.setattr(mf, "_moderate", moderate)
    report = mf.import_listings(
        _envelope(listings=[{**LISTING, "summary": "x" * 70_000}]),
        peers={},
    )

    assert report["ok"]
    assert report["accepted"] == []
    assert report["rejected"][0]["name"] == "summarize-url"
    assert moderation_calls == 0


def test_postgres_backend_drift_is_refused(shared_mode):
    shared, legacy = shared_mode
    shared.backend_kind = "local"

    report = mf.import_listings(_envelope(), peers={})

    assert not report["ok"] and "requires governed Postgres" in report["reason"]
    with pytest.raises(MarketplaceFederationStoreError, match="requires governed"):
        mf.imported_listings()
    assert not legacy.exists()
