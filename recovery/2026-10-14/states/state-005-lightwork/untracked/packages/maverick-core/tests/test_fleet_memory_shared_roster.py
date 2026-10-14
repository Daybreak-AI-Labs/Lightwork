"""Shared Postgres fleet-roster authority regressions."""
from __future__ import annotations

import copy
import threading

import pytest
from maverick import fleet_memory, governed_records


class _AtomicRoster:
    def __init__(self) -> None:
        self.backend_kind = "postgres"
        self.document: dict = {}
        self.lock = threading.Lock()
        self.fail_read = False
        self.fail_mutate = False
        self.retry_once = False
        self.drift_after_read = False
        self.drift_after_mutate = False

    def read(self):
        if self.fail_read:
            raise OSError("database unavailable")
        with self.lock:
            value = copy.deepcopy(self.document)
            if self.drift_after_read:
                self.backend_kind = "local"
            return value

    def mutate(self, callback, **_kwargs):
        if self.fail_mutate:
            raise OSError("database unavailable")
        with self.lock:
            working = copy.deepcopy(self.document)
            if self.retry_once:
                self.retry_once = False
                callback(copy.deepcopy(working))
            result = callback(working)
            self.document = copy.deepcopy(working)
            if self.drift_after_mutate:
                self.backend_kind = "local"
            return result


@pytest.fixture
def shared_roster(monkeypatch, tmp_path):
    shared = _AtomicRoster()
    legacy = tmp_path / "agents.ndjson"
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(fleet_memory, "registry_path", lambda: legacy)
    monkeypatch.setattr(fleet_memory, "_REGISTRY_AUTHORITY", shared)
    return shared, legacy


def test_concurrent_registrations_preserve_every_agent(shared_roster):
    shared, legacy = shared_roster
    barrier = threading.Barrier(8)
    results: list[bool] = []
    lock = threading.Lock()

    def register(index: int) -> None:
        barrier.wait()
        result = fleet_memory.register_agent(f"agent-{index}", "vendor")
        with lock:
            results.append(result)

    threads = [threading.Thread(target=register, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(results) and len(results) == 8
    assert [row["agent_id"] for row in fleet_memory.roster()] == [
        f"agent-{index}" for index in range(8)
    ]
    assert not legacy.exists()
    assert shared.document["schema_version"] == 1


def test_one_vendor_wins_raced_registration_for_same_principal(shared_roster):
    _shared, _legacy = shared_roster
    barrier = threading.Barrier(2)
    results: dict[str, bool] = {}
    lock = threading.Lock()

    def register(vendor: str) -> None:
        barrier.wait()
        result = fleet_memory.register_agent("same-agent", vendor)
        with lock:
            results[vendor] = result

    threads = [threading.Thread(target=register, args=(vendor,)) for vendor in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results.values()) == [False, True]
    assert len(fleet_memory.roster()) == 1


def test_cas_retry_does_not_duplicate_registration_audit(
    shared_roster,
    monkeypatch,
):
    shared, _legacy = shared_roster
    shared.retry_once = True
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        fleet_memory,
        "_audit",
        lambda event, **payload: events.append((event, payload)),
    )

    assert fleet_memory.register_agent("agent", "vendor")
    assert [event for event, _payload in events] == ["register"]
    assert len(fleet_memory.roster()) == 1


def test_shared_outage_never_falls_back_to_agents_file(shared_roster):
    shared, legacy = shared_roster
    shared.fail_mutate = True
    assert not fleet_memory.register_agent("agent", "vendor")
    assert not legacy.exists()

    shared.fail_mutate = False
    assert fleet_memory.register_agent("agent", "vendor")
    shared.fail_read = True
    with pytest.raises(fleet_memory.FleetRosterAuthorityError, match="read failed"):
        fleet_memory.roster()
    assert (
        fleet_memory._registration_denial("agent", "vendor")
        == "fleet roster authority is unavailable"
    )
    assert not legacy.exists()


def test_live_or_malformed_legacy_roster_blocks_cutover(shared_roster):
    _shared, legacy = shared_roster
    legacy.write_text(
        '{"agent_id":"old","vendor":"v","source":"v:old"}\n',
        encoding="utf-8",
    )
    with pytest.raises(fleet_memory.FleetRosterAuthorityError, match="migrate"):
        fleet_memory.roster()

    legacy.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(fleet_memory.FleetRosterAuthorityError, match="migrate"):
        fleet_memory.roster()


@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2, "agents": {}},
        {"schema_version": 1, "agents": []},
        {
            "schema_version": 1,
            "agents": {
                "agent": {
                    "agent_id": "agent",
                    "vendor": "vendor",
                    "source": "other:agent",
                    "description": "",
                    "ts": 1.0,
                }
            },
        },
    ],
)
def test_malformed_shared_roster_fails_closed(shared_roster, document):
    shared, _legacy = shared_roster
    shared.document = document
    with pytest.raises(fleet_memory.FleetRosterAuthorityError):
        fleet_memory.roster()


@pytest.mark.parametrize("operation", ["read", "mutate"])
def test_backend_drift_is_detected(shared_roster, operation):
    shared, legacy = shared_roster
    if operation == "read":
        shared.document = {"schema_version": 1, "agents": {}}
        shared.drift_after_read = True
        with pytest.raises(
            fleet_memory.FleetRosterAuthorityError,
            match="requires governed Postgres",
        ):
            fleet_memory.roster()
    else:
        shared.drift_after_mutate = True
        assert not fleet_memory.register_agent("agent", "vendor")
    assert not legacy.exists()

