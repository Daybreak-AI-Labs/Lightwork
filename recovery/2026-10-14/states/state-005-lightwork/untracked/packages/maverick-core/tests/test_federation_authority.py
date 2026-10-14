"""Shared replay, rate, and correlation authority for federation ingress."""
from __future__ import annotations

import copy
import threading
import uuid

import pytest
from maverick import federation, governed_records
from maverick.federation_authority import GovernedFederationState
from maverick.privacy_ops import RecordConflict


class _MemoryBackend:
    # A concrete in-memory implementation of the Postgres contract. Mandatory
    # authority tests must not bypass the deployment selector with an invented
    # backend kind.
    kind = "postgres"
    shared = True

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.clock = 100.0

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def authoritative_time(self) -> float:
        return self.clock

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


class _CommitThenRaiseBackend(_MemoryBackend):
    """Persist the first create, then lose only its acknowledgement."""

    def __init__(self) -> None:
        super().__init__()
        self.raise_after_create = True

    def save(self, record, *, expected_revision=None):
        saved = super().save(record, expected_revision=expected_revision)
        if self.raise_after_create:
            self.raise_after_create = False
            raise ConnectionError("commit acknowledgement was lost")
        return saved


class _UpdateCommitThenRaiseBackend(_MemoryBackend):
    """Persist the first update, then lose only its acknowledgement."""

    def __init__(self) -> None:
        super().__init__()
        self.raise_after_update = True
        self.update_calls = 0

    def update(
        self,
        record_id,
        mutate,
        *,
        expected_revision=None,
        metadata_only=False,
    ):
        self.update_calls += 1
        saved = super().update(
            record_id,
            mutate,
            expected_revision=expected_revision,
            metadata_only=metadata_only,
        )
        if self.raise_after_update:
            self.raise_after_update = False
            raise ConnectionError("commit acknowledgement was lost")
        return saved


@pytest.fixture(autouse=True)
def _select_postgres_authority(monkeypatch):
    dsn = "postgresql://test:secret@db.internal/lightwork"  # pragma: allowlist secret
    monkeypatch.setattr(
        governed_records,
        "_selection",
        lambda: governed_records._BackendSelection("postgres", dsn, False),
    )
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")


def _state(backend):
    return GovernedFederationState(
        correlation_ttl_s=60.0,
        correlation_max=16,
        correlation_per_peer_max=8,
        replay_max_per_scope=16,
        rate_max_keys=16,
        backend=backend,
    )


def test_replay_claim_is_atomic_across_replicas(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    states = [_state(backend) for _ in range(16)]
    barrier = threading.Barrier(len(states))
    outcomes: list[bool] = []
    result_lock = threading.Lock()

    def claim(state):
        barrier.wait()
        result = state.claim_replay(
                "peer:delegate",
                "signed-envelope",
                expires_at=200.0,
                now=10.0,
        )
        with result_lock:
            outcomes.append(result)

    threads = [threading.Thread(target=claim, args=(state,)) for state in states]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count(False) == 1
    assert outcomes.count(True) == len(states) - 1


def test_fast_replica_cannot_prune_live_replay_claim(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    first = _state(backend)
    fast = _state(backend)

    assert first.claim_replay(
        "peer:delegate", "signed-envelope", expires_at=160.0, now=100.0
    ) is False
    backend.clock = 101.0
    assert fast.claim_replay(
        "peer:delegate", "signed-envelope", expires_at=160.0, now=10_000.0
    ) is True


def test_rate_window_is_one_fleet_wide_budget(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    first = _state(backend)
    second = _state(backend)

    assert first.rate_ok("peer-a", now=100.0, limit=2) is True
    backend.clock = 101.0
    assert second.rate_ok("peer-a", now=101.0, limit=2) is True
    backend.clock = 102.0
    assert first.rate_ok("peer-a", now=102.0, limit=2) is False
    backend.clock = 161.0
    assert second.rate_ok("peer-a", now=161.0, limit=2) is True


def test_fast_replica_cannot_reset_shared_rate_window(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    first = _state(backend)
    fast = _state(backend)

    assert first.rate_ok("peer-a", now=100.0, limit=2) is True
    backend.clock = 101.0
    assert first.rate_ok("peer-a", now=101.0, limit=2) is True
    # Replica claims it is far in the future. The governed database clock is
    # still inside the original minute, so its request remains over capacity.
    backend.clock = 102.0
    assert fast.rate_ok("peer-a", now=10_000.0, limit=2) is False

    record = next(iter(backend.records.values()))
    windows = record["document"]["windows"]
    assert next(iter(windows.values())) == [100.0, 101.0]


def test_correlation_owner_and_result_converge(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    first = _state(backend)
    second = _state(backend)
    digest = "aa" * 32

    claim, cached, owner = first.claim_correlation(
        "peer-a",
        "request-1",
        digest,
        now=100.0,
    )
    assert (claim, cached) == ("owner", None)
    assert second.claim_correlation(
        "peer-a",
        "request-1",
        digest,
        now=101.0,
    )[:2] == ("busy", None)

    first.finish_correlation(
        "peer-a",
        "request-1",
        digest,
        owner,
        {"goal_id": 42, "accepted": True},
    )
    assert second.correlation_result(
        "peer-a",
        "request-1",
        digest,
    ) == ("cached", {"accepted": True, "goal_id": 42})
    assert second.claim_correlation(
        "peer-a",
        "request-1",
        digest,
        now=102.0,
    )[:2] == ("cached", {"accepted": True, "goal_id": 42})


def test_correlation_claim_recovers_same_owner_after_lost_commit_response(
    monkeypatch,
):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _CommitThenRaiseBackend()
    state = _state(backend)
    operation_id = "12" * 16
    digest = "ab" * 32

    claim, cached, owner = state.claim_correlation(
        "peer-a",
        "request-lost-response",
        digest,
        now=100.0,
        operation_id=operation_id,
    )

    assert (claim, cached, owner) == ("owner", None, operation_id)
    assert state.claim_correlation(
        "peer-a",
        "request-lost-response",
        digest,
        now=100.0,
        operation_id="34" * 16,
    )[:2] == ("busy", None)
    entries = next(iter(backend.records.values()))["document"]["entries"]
    assert len(entries) == 1


def test_correlation_finish_recovers_same_result_after_lost_commit_response(
    monkeypatch,
):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _UpdateCommitThenRaiseBackend()
    state = _state(backend)
    digest = "cd" * 32
    claim, _cached, owner = state.claim_correlation(
        "peer-a",
        "request-lost-finish-response",
        digest,
        now=100.0,
        operation_id="56" * 16,
    )
    assert claim == "owner"

    result = {"accepted": True, "goal_id": 88}
    state.finish_correlation(
        "peer-a",
        "request-lost-finish-response",
        digest,
        owner,
        result,
    )

    assert backend.update_calls >= 2
    assert backend.raise_after_update is False
    assert state.correlation_result(
        "peer-a",
        "request-lost-finish-response",
        digest,
    ) == ("cached", result)


def test_expired_in_progress_correlations_stop_consuming_capacity(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    state = GovernedFederationState(
        correlation_ttl_s=60.0,
        correlation_max=2,
        correlation_per_peer_max=2,
        replay_max_per_scope=16,
        rate_max_keys=16,
        backend=backend,
    )
    digest = "cd" * 32
    first = state.claim_correlation(
        "peer-a", "abandoned-1", digest, now=100.0, operation_id="11" * 16
    )
    second = state.claim_correlation(
        "peer-a", "abandoned-2", digest, now=100.0, operation_id="22" * 16
    )
    assert first[0] == second[0] == "owner"
    assert state.claim_correlation(
        "peer-a", "blocked", digest, now=100.0, operation_id="33" * 16
    )[0] == "capacity"

    backend.clock = 161.0
    replacement = state.claim_correlation(
        "peer-a", "replacement", digest, now=100.0, operation_id="44" * 16
    )
    assert replacement[0] == "owner"
    with pytest.raises(ValueError, match="ownership changed"):
        state.finish_correlation(
            "peer-a",
            "abandoned-1",
            digest,
            first[2],
            {"accepted": True},
        )


def test_fast_replica_cannot_expire_completed_correlation(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    first = _state(backend)
    fast = _state(backend)
    digest = "ef" * 32
    claim, _cached, owner = first.claim_correlation(
        "peer-a", "request-fast", digest, now=100.0
    )
    assert claim == "owner"
    first.finish_correlation(
        "peer-a", "request-fast", digest, owner, {"accepted": True}
    )

    backend.clock = 110.0
    assert fast.claim_correlation(
        "peer-a", "request-fast", digest, now=10_000.0
    )[:2] == ("cached", {"accepted": True})


def test_correlation_rejects_conflict_and_stale_owner(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    state = _state(backend)
    digest = "bb" * 32
    claim, _cached, owner = state.claim_correlation(
        "peer-a",
        "request-2",
        digest,
        now=100.0,
    )
    assert claim == "owner"
    assert state.claim_correlation(
        "peer-a",
        "request-2",
        "cc" * 32,
        now=101.0,
    )[0] == "conflict"
    with pytest.raises(ValueError, match="ownership changed"):
        state.finish_correlation(
            "peer-a",
            "request-2",
            digest,
            "0" * 32,
            {"accepted": True},
        )
    state.finish_correlation(
        "peer-a",
        "request-2",
        digest,
        owner,
        {"accepted": True},
    )


def test_release_requires_exact_owner(monkeypatch):
    monkeypatch.setattr("maverick.audit.record", lambda *_a, **_k: True)
    backend = _MemoryBackend()
    state = _state(backend)
    digest = "dd" * 32
    _claim, _cached, owner = state.claim_correlation(
        "peer-a",
        "request-3",
        digest,
        now=100.0,
    )

    state.release_correlation("peer-a", "request-3", digest, "0" * 32)
    assert state.correlation_result("peer-a", "request-3", digest)[0] == "busy"
    state.release_correlation("peer-a", "request-3", digest, owner)
    assert state.correlation_result("peer-a", "request-3", digest)[0] == "missing"


def test_postgres_selection_rejects_local_state_override(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "maverick.governed_records.configured_governed_records_backend",
        lambda: "postgres",
    )

    with pytest.raises(ValueError, match="cannot override Postgres"):
        federation._federation_state(tmp_path / "local.sqlite3")
