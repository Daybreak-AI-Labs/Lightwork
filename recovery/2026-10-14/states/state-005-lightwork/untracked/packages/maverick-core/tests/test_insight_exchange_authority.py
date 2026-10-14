"""Shared authority tests for federated insight replay claims."""
from __future__ import annotations

import copy
import threading
import uuid

import pytest
from maverick import governed_records, insight_exchange
from maverick.privacy_ops import RecordConflict


class _MemoryBackend:
    kind = "postgres"
    shared = True

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.lock = threading.Lock()

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def save(self, record, *, expected_revision=None):
        with self.lock:
            record_id = record["id"]
            current = self.records.get(record_id)
            revision = int((current or {}).get("revision", 0))
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(record)
            saved["revision"] = revision + 1
            saved.setdefault("created_at", 1.0)
            saved.setdefault("updated_at", 1.0)
            self.records[record_id] = saved
            return copy.deepcopy(saved)

    def load(self, record_id):
        with self.lock:
            current = self.records.get(record_id)
            return copy.deepcopy(current) if current is not None else None

    def list(self):
        with self.lock:
            return copy.deepcopy(list(self.records.values()))

    def iter_record_ids(self, *, start_after="", limit=100):
        with self.lock:
            record_ids = sorted(self.records)
        for record_id in record_ids[:limit]:
            yield record_id, record_id

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        with self.lock:
            current = self.records.get(record_id)
            if current is None:
                return None
            revision = int(current["revision"])
            if expected_revision is not None and expected_revision != revision:
                raise RecordConflict("changed")
            saved = copy.deepcopy(current)
            mutate(saved)
            saved["revision"] = revision if metadata_only else revision + 1
            self.records[record_id] = saved
            return copy.deepcopy(saved)


@pytest.fixture(autouse=True)
def _select_postgres_authority(monkeypatch):
    dsn = "postgresql://test:secret@db.internal/lightwork"  # pragma: allowlist secret
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: governed_records._BackendSelection("postgres", dsn, False),
    )
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")


def _ledger(backend):
    return insight_exchange._GovernedInsightReplayLedger(backend=backend)


def test_shared_claim_converges_across_instances(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    peer = "11" * 32
    signature = "22" * 64

    assert _ledger(backend).claim(peer, signature, now=10.0, expires_at=20.0) is False
    assert _ledger(backend).claim(peer, signature, now=11.0, expires_at=20.0) is True


def test_shared_claim_is_atomic_under_contention(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    ledgers = [_ledger(backend) for _ in range(16)]
    barrier = threading.Barrier(len(ledgers))
    outcomes: list[bool] = []
    result_lock = threading.Lock()

    def claim(ledger):
        barrier.wait()
        result = ledger.claim("33" * 32, "44" * 64, now=10.0, expires_at=20.0)
        with result_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=claim, args=(ledger,)) for ledger in ledgers]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(False) == 1
    assert outcomes.count(True) == len(ledgers) - 1


def test_postgres_selection_never_uses_requested_local_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        insight_exchange,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(insight_exchange, "_GOVERNED_REPLAY_LEDGER", None)
    requested = tmp_path / "local-bypass.sqlite3"

    selected = insight_exchange._insight_replay_ledger(requested)

    assert isinstance(selected, insight_exchange._GovernedInsightReplayLedger)
    assert not requested.exists()


def test_malformed_shared_document_fails_closed(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    ledger = _ledger(backend)
    ledger._document.mutate(  # noqa: SLF001 - adversarial authority fixture
        lambda document: document.update({"claims": []}),
        action="corrupt",
    )

    try:
        ledger.claim("55" * 32, "66" * 64, now=10.0, expires_at=20.0)
    except insight_exchange._BundleError as exc:
        assert "claims" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("malformed shared replay document was accepted")
