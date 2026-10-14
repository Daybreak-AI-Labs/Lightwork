"""Cross-replica authority coverage for SAML assertion consumption."""
from __future__ import annotations

import copy
import threading

import pytest
from maverick_dashboard import saml_replay


class _AtomicDocument:
    def __init__(self) -> None:
        self.document: dict = {}
        self.lock = threading.Lock()

    def mutate(self, callback, **_kwargs):
        with self.lock:
            working = copy.deepcopy(self.document)
            result = callback(working)
            self.document = working
            return result


def _shared_stores():
    document = _AtomicDocument()
    first = saml_replay.GovernedSamlReplayStore()
    second = saml_replay.GovernedSamlReplayStore()
    first._document = document  # noqa: SLF001 - shared authority fixture
    second._document = document  # noqa: SLF001 - shared authority fixture
    return first, second


def test_governed_claim_converges_across_replicas():
    first, second = _shared_stores()

    assert first.consume("assertion-1", 20.0, now=10.0) is True
    assert second.consume("assertion-1", 20.0, now=11.0) is False


def test_governed_claim_is_atomic_under_contention():
    first, second = _shared_stores()
    stores = [first, second] * 8
    barrier = threading.Barrier(len(stores))
    outcomes: list[bool] = []
    result_lock = threading.Lock()

    def consume(store):
        barrier.wait()
        result = store.consume("same-assertion", 20.0, now=10.0)
        with result_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=consume, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == len(stores) - 1


def test_postgres_consume_uses_shared_store(monkeypatch):
    class _Shared:
        def consume(self, assertion_id, expires_at, *, now):
            assert assertion_id == "assertion-2"
            assert expires_at > now
            return True

    monkeypatch.setattr(saml_replay, "_governed_store", lambda **_kwargs: _Shared())
    monkeypatch.setattr(
        saml_replay,
        "_locked",
        lambda: (_ for _ in ()).throw(AssertionError("local fallback used")),
    )

    assert saml_replay.consume("assertion-2", None) is True


def test_shared_authority_failure_is_wrapped_and_denied(monkeypatch):
    class _Unavailable:
        def consume(self, *_args, **_kwargs):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        saml_replay,
        "_governed_store",
        lambda **_kwargs: _Unavailable(),
    )

    with pytest.raises(saml_replay.ReplayStoreError, match="shared replay authority"):
        saml_replay.consume("assertion-3", None)


def test_live_legacy_claim_blocks_postgres_cutover(monkeypatch, tmp_path):
    legacy = tmp_path / "legacy.json"
    legacy.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        saml_replay,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(saml_replay, "_path", lambda: legacy)
    monkeypatch.setattr(saml_replay, "_load", lambda: {"live": 20.0})

    with pytest.raises(saml_replay.ReplayStoreError, match="cutover"):
        saml_replay._governed_store(now=10.0)
