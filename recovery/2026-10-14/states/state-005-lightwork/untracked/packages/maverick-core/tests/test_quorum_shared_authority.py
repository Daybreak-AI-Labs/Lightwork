"""Shared Postgres authority regressions for configuration proposals."""
from __future__ import annotations

import copy
import threading

import pytest
from maverick import governed_records, quorum


class _AtomicAuthority:
    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.document: dict = {}
        self.lock = threading.Lock()
        self.fail_read = False
        self.fail_mutate = False
        self.retry_once = False
        self.retry_hook = None
        self.callback_calls = 0
        self.clock = None

    def read(self):
        if self.fail_read:
            raise OSError("database unavailable")
        with self.lock:
            return copy.deepcopy(self.document)

    def mutate(self, callback, **_kwargs):
        if self.fail_mutate:
            raise OSError("database unavailable")
        with self.lock:
            working = copy.deepcopy(self.document)
            if self.retry_once:
                self.retry_once = False
                self.callback_calls += 1
                callback(copy.deepcopy(working))
            self.callback_calls += 1
            result = callback(working)
            self.document = copy.deepcopy(working)
            return result

    def authoritative_time(self):
        assert self.clock is not None
        return self.clock()

    def mutate_with_authoritative_time(self, callback, **_kwargs):
        if self.fail_mutate:
            raise OSError("database unavailable")
        with self.lock:
            working = copy.deepcopy(self.document)
            if self.retry_once:
                self.retry_once = False
                self.callback_calls += 1
                callback(copy.deepcopy(working), self.authoritative_time())
                if self.retry_hook is not None:
                    self.retry_hook(working)
            self.callback_calls += 1
            result = callback(working, self.authoritative_time())
            self.document = copy.deepcopy(working)
            return result


class _Clock:
    def __init__(self) -> None:
        self.value = 1_750_000_000.0
        self.calls = 0

    def __call__(self) -> float:
        self.calls += 1
        return self.value


@pytest.fixture
def shared(monkeypatch, tmp_path):
    authority = _AtomicAuthority()
    clock = _Clock()
    authority.clock = clock
    legacy = tmp_path / "config_proposals.json"
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(quorum, "_default_path", lambda: legacy)
    store = quorum.ProposalStore(clock=clock, authority=authority)
    return store, authority, clock, legacy


def _propose(store, *, change_id="change-1"):
    return quorum.propose(
        change_id,
        "budget.max_dollars",
        5.0,
        50.0,
        "alice",
        store=store,
        policy=quorum.QuorumPolicy(),
    )


def test_concurrent_approvals_accumulate_in_one_cas_document(shared):
    store, authority, _clock, legacy = shared
    _propose(store)
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def approve(who: str) -> None:
        try:
            barrier.wait()
            quorum.approve("change-1", who, store=store)
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=approve, args=(who,)) for who in ("bob", "carol")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
    assert {who for who, _at in store.get("change-1").approvals} == {"bob", "carol"}
    assert quorum.status("change-1", store=store) == quorum.APPROVED
    assert authority.document["schema_version"] == 1
    assert not legacy.exists()


def test_duplicate_proposal_id_has_one_winner_under_contention(shared):
    store, _authority, _clock, _legacy = shared
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def create() -> None:
        barrier.wait()
        try:
            _propose(store)
            outcome = "created"
        except quorum.QuorumError:
            outcome = "duplicate"
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["created", "duplicate"]
    assert len(store.all()) == 1


def test_retried_approval_callback_refreshes_authority_time(shared):
    store, authority, clock, _legacy = shared
    _propose(store)
    calls_before = clock.calls
    authority.retry_once = True

    approved = quorum.approve("change-1", "bob", store=store)

    assert clock.calls == calls_before + 2
    assert approved.approvals == (("bob", clock.value),)
    assert authority.callback_calls == 3  # propose + retried approval twice


def test_shared_approval_ignores_slow_replica_clock(shared):
    store, authority, clock, _legacy = shared
    _propose(store)
    store.clock = lambda: 1.0
    clock.value += 8 * 86400.0

    with pytest.raises(quorum.QuorumError, match="expired"):
        quorum.approve("change-1", "bob", store=store)

    assert authority.document["proposals"]["change-1"]["approvals"] == []


def test_shared_status_ignores_slow_replica_clock(shared):
    store, _authority, clock, _legacy = shared
    _propose(store)
    quorum.approve("change-1", "bob", store=store)
    quorum.approve("change-1", "carol", store=store)
    store.clock = lambda: 1.0
    clock.value += 8 * 86400.0

    assert quorum.status("change-1", store=store) == quorum.EXPIRED


def test_shared_prune_revalidates_delete_recreate_inside_retry(shared):
    store, authority, clock, _legacy = shared
    _propose(store)
    clock.value += 8 * 86400.0
    authority.retry_once = True

    def _recreate_fresh(document):
        document["proposals"]["change-1"]["proposed_at"] = clock.value
        document["proposals"]["change-1"]["approvals"] = []

    authority.retry_hook = _recreate_fresh

    assert quorum.prune(store=store) == 0
    proposal = store.get("change-1")
    assert proposal is not None and proposal.proposed_at == clock.value


def test_shared_outage_never_creates_or_reads_local_fallback(shared):
    store, authority, _clock, legacy = shared
    authority.fail_mutate = True
    with pytest.raises(quorum.QuorumStoreError, match="update failed"):
        _propose(store)
    assert not legacy.exists()

    authority.fail_mutate = False
    _propose(store)
    authority.fail_read = True
    with pytest.raises(quorum.QuorumStoreError, match="read failed"):
        store.get("change-1")
    assert not legacy.exists()


def test_live_or_corrupt_legacy_ledger_blocks_postgres_cutover(shared):
    store, _authority, _clock, legacy = shared
    legacy.write_text('{"old":{"change_id":"old"}}', encoding="utf-8")
    with pytest.raises(quorum.QuorumStoreError, match="migrate"):
        store.all()

    legacy.write_text("{not-json", encoding="utf-8")
    with pytest.raises(quorum.QuorumStoreError, match="corrupt"):
        store.all()


def test_local_path_override_is_refused_in_postgres_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    store = quorum.ProposalStore(path=tmp_path / "override.json")
    with pytest.raises(quorum.QuorumStoreError, match="path overrides"):
        store.all()


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "proposals": {}},
        {"schema_version": 1, "proposals": []},
        {
            "schema_version": 1,
            "proposals": {
                "change-1": {
                    "change_id": "change-1",
                    "key": "budget.max_dollars",
                    "old": 5,
                    "new": 50,
                    "proposer": "alice",
                    "proposed_at": 1.0,
                    "required": 2,
                    "approvals": [["alice", 2.0]],
                }
            },
        },
    ],
)
def test_malformed_shared_state_fails_closed(shared, document):
    store, authority, _clock, _legacy = shared
    authority.document = document
    with pytest.raises(quorum.QuorumStoreError):
        store.all()


def test_backend_drift_after_shared_use_fails_closed(shared):
    store, authority, _clock, _legacy = shared
    _propose(store)
    authority.backend_kind = "local"

    with pytest.raises(quorum.QuorumStoreError, match="requires governed Postgres"):
        store.get("change-1")
