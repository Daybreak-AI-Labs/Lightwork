"""Postgres SQL-path checks for the event execution hold without a live service."""
from __future__ import annotations

from contextlib import contextmanager

import pytest
from maverick.event_admission import EventRegistryUnavailable, LegacyEventGoalHeld
from maverick.world_model_backends import postgres


class _Cursor:
    def __init__(self, selected):
        self.selected = selected
        self.queries = []
        self.rowcount = 0

    def execute(self, sql, params):
        self.queries.append((sql, tuple(params)))
        self.rowcount = 1 if sql.lstrip().startswith("UPDATE") else 0

    def fetchone(self):
        return self.selected


def _world(monkeypatch, selected):
    cursor = _Cursor(selected)

    @contextmanager
    def transaction(_self):
        yield cursor

    monkeypatch.setattr(postgres.PostgresWorldModel, "_tx", transaction)
    monkeypatch.setattr(postgres, "_seal", lambda value: value)
    monkeypatch.setattr(postgres, "_tenant_scope", lambda column="tenant_id": (
        f"{column}=%s", ["tenant-a"],
    ))
    monkeypatch.setattr(postgres, "_mutation_tenant_scope", lambda column="tenant_id": (
        f"{column}=%s", ["tenant-a"],
    ))
    return postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel), cursor


def test_postgres_local_start_blocks_pending_legacy_origin(monkeypatch):
    world, cursor = _world(monkeypatch, ("pending", "user:alice", "event", "orders", None))
    with pytest.raises(LegacyEventGoalHeld):
        world.set_goal_status(7, "active")
    assert "o.kind IN ('event','event_v2')" in cursor.queries[0][0]
    assert "g.tenant_id=%s" in cursor.queries[0][0]
    assert "FOR UPDATE OF g" in cursor.queries[0][0]
    assert "status='blocked'" in cursor.queries[1][0]
    assert all(params[0] != "tenant-b" for _, params in cursor.queries)


def test_postgres_remote_claim_blocks_legacy_and_allows_current(monkeypatch):
    legacy, held_sql = _world(monkeypatch, ("user:alice", "event", "orders", None))
    assert not legacy.claim_goal_for_run(7, expected_owner="user:alice")
    assert "g.owner=%s" in held_sql.queries[0][0]
    assert "status='blocked'" in held_sql.queries[1][0]

    current, allowed_sql = _world(monkeypatch, None)
    assert current.claim_goal_for_run(8, expected_owner="user:alice")
    assert "status='active'" in allowed_sql.queries[-1][0]


def test_postgres_unreadable_registry_does_not_claim_or_block(monkeypatch):
    def unreadable(*_args):
        raise EventRegistryUnavailable("synthetic transient registry read failure")

    monkeypatch.setattr(postgres, "compatible_event_goal", unreadable)
    local, local_sql = _world(monkeypatch, (
        "pending", "user:alice", "event", "orders", "generation",
    ))
    with pytest.raises(EventRegistryUnavailable):
        local.set_goal_status(7, "active")
    assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in local_sql.queries)

    remote, remote_sql = _world(monkeypatch, (
        "user:alice", "event", "orders", "generation",
    ))
    with pytest.raises(EventRegistryUnavailable):
        remote.claim_goal_for_run(7, expected_owner="user:alice")
    assert not any(sql.lstrip().startswith("UPDATE") for sql, _ in remote_sql.queries)


def test_postgres_exact_legacy_receipt_may_start_after_binding_proof(monkeypatch):
    monkeypatch.setattr(
        postgres, "compatible_event_goal",
        lambda kind, ref, owner, generation: (kind, ref, owner, generation)
        == ("event", "orders", "user:alice", "saved-generation"),
    )
    local, local_sql = _world(monkeypatch, (
        "pending", "user:alice", "event", "orders", "saved-generation",
    ))
    local.set_goal_status(7, "active")
    assert all("status='blocked'" not in sql for sql, _ in local_sql.queries)

    remote, remote_sql = _world(monkeypatch, (
        "user:alice", "event", "orders", "saved-generation",
    ))
    assert remote.claim_goal_for_run(8, expected_owner="user:alice")
    assert "status='active'" in remote_sql.queries[-1][0]


def test_postgres_current_event_without_receipt_is_held_at_both_claims(monkeypatch):
    local, local_sql = _world(monkeypatch, (
        "pending", "user:alice", "event_v2", "orders", None,
    ))
    with pytest.raises(LegacyEventGoalHeld):
        local.set_goal_status(7, "active")
    assert "o.kind IN ('event','event_v2')" in local_sql.queries[0][0]
    assert "status='blocked'" in local_sql.queries[1][0]

    remote, remote_sql = _world(monkeypatch, (
        "user:alice", "event_v2", "orders", None,
    ))
    assert not remote.claim_goal_for_run(8, expected_owner="user:alice")
    assert "o.kind IN ('event','event_v2')" in remote_sql.queries[0][0]
    assert "status='blocked'" in remote_sql.queries[1][0]


def test_postgres_current_email_start_needs_exact_binding_proof(monkeypatch):
    calls = []

    def compatible(kind, ref, owner, generation):
        calls.append((kind, ref, owner, generation))
        return (kind, ref, owner, generation) == (
            "event_v2", "orders", "user:alice", "current-generation",
        )

    monkeypatch.setattr(postgres, "compatible_event_goal", compatible)
    local, local_sql = _world(monkeypatch, (
        "pending", "user:alice", "event_v2", "orders", "current-generation",
    ))
    local.set_goal_status(7, "active")
    assert calls == [("event_v2", "orders", "user:alice", "current-generation")]
    assert all("status='blocked'" not in sql for sql, _ in local_sql.queries)

    remote, remote_sql = _world(monkeypatch, (
        "user:alice", "event_v2", "orders", "old-generation",
    ))
    assert not remote.claim_goal_for_run(8, expected_owner="user:alice")
    assert calls[-1] == ("event_v2", "orders", "user:alice", "old-generation")
    assert "status='blocked'" in remote_sql.queries[1][0]
