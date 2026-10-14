"""Adversarial API-idempotency retirement and delivery-state contracts."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager

import pytest
from maverick.api_idempotency import (
    ApiIdempotencyConflict,
    ApiIdempotencyError,
    ApiIdempotencyIndeterminate,
    request_sha256,
)
from maverick.world_model import WorldModel

_OPERATION = "POST /api/v1/goals"
_PRINCIPAL = " user:alice "
_KEY = " goal-request-retirement-0001 "
_DISPATCH = {
    "allowed_suites": ["finance"],
    "channel": "api",
    "max_depth": 3,
    "max_dollars": 5.0,
    "max_wall_seconds": 3600.0,
    "schema": "lightwork.dashboard-goal-dispatch.v1",
    "user_id": "alice",
}


def _request(title: str = "one") -> dict:
    return {
        "description": "private bound brief",
        "domain": "",
        "max_depth": 3,
        "max_dollars": 5.0,
        "max_wall_seconds": 3600.0,
        "title": title,
    }


def _create(
    world,
    *,
    title: str = "one",
    principal: object = _PRINCIPAL,
    key: object = _KEY,
    dispatch: dict | None = _DISPATCH,
):
    normalized = _request(title)
    return world.create_goal_idempotent(
        title,
        normalized["description"],
        owner=_PRINCIPAL,
        operation=_OPERATION,
        principal_id=principal,
        idempotency_key=key,
        request_sha256=request_sha256(normalized),
        dispatch_payload=dispatch,
    )


def _lookup(world, *, title: str = "one", principal: object = _PRINCIPAL):
    return world.get_goal_idempotent(
        operation=_OPERATION,
        principal_id=principal,
        idempotency_key=_KEY,
        request_sha256=request_sha256(_request(title)),
    )


def test_sqlite_storage_preserves_exact_principal_scope_and_never_coerces(
    tmp_path,
):
    world = WorldModel(tmp_path / "world.db")
    edged = _create(world)
    plain = _create(world, principal="user:alice")

    assert edged.goal_id != plain.goal_id
    principals = {
        str(row[0])
        for row in world.conn.execute(
            "SELECT principal_id FROM api_idempotency ORDER BY id"
        ).fetchall()
    }
    assert principals == {_PRINCIPAL, "user:alice"}

    before = len(world.list_goals(limit=100))
    for invalid in (None, 7, object()):
        with pytest.raises(ValueError, match="idempotency principal is invalid"):
            _create(world, principal=invalid, key=f"invalid-{id(invalid)}")
    assert len(world.list_goals(limit=100)) == before


def test_sqlite_read_only_exact_lookup_has_no_write_side_effect(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    created = _create(world)
    before_changes = world.conn.total_changes

    replay = _lookup(world)

    assert world.conn.total_changes == before_changes
    assert replay is not None
    assert replay.goal_id == created.goal_id
    assert replay.response == created.response
    assert replay.replayed is True
    assert replay.delivery_pending is True
    assert replay.dispatch == _DISPATCH

    missing_before = world.conn.total_changes
    missing = world.get_goal_idempotent(
        operation=_OPERATION,
        principal_id=_PRINCIPAL,
        idempotency_key="a genuinely unused request key",
        request_sha256=request_sha256(_request()),
    )
    assert missing is None
    assert world.conn.total_changes == missing_before

    with pytest.raises(ApiIdempotencyConflict, match="different request"):
        _lookup(world, title="a changed request")
    assert world.conn.total_changes == missing_before


def test_sqlite_delivery_marker_retires_dispatch_from_every_exact_replay(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    created = _create(world)

    assert created.delivery_pending is True
    assert created.dispatch == _DISPATCH
    assert world.get_goal_idempotent_dispatch(created.goal_id) == _DISPATCH

    assert world.mark_goal_idempotent_dispatch_delivered(created.goal_id) is True
    assert world.mark_goal_idempotent_dispatch_delivered(created.goal_id) is True
    assert world.get_goal_idempotent_dispatch(created.goal_id) is None

    replay = _create(world)
    looked_up = _lookup(world)
    for result in (replay, looked_up):
        assert result is not None
        assert result.replayed is True
        assert result.goal_id == created.goal_id
        assert result.response == created.response
        assert result.delivery_pending is False
        assert result.dispatch is None
    assert len(world.list_goals(limit=100)) == 1


def test_sqlite_missing_historical_result_key_is_typed_and_content_free(
    monkeypatch,
    tmp_path,
):
    from maverick import world_model

    world = WorldModel(tmp_path / "world.db")
    _create(world)

    def missing_historical_key(_value):
        raise RuntimeError("kms://private/customer/key-version-7")

    monkeypatch.setattr(world_model, "_dec_field", missing_historical_key)
    with pytest.raises(ApiIdempotencyIndeterminate) as caught:
        _lookup(world)

    rendered = str(caught.value)
    assert "customer" not in rendered
    assert "key-version-7" not in rendered
    assert "kms://" not in rendered


def test_erased_goal_retires_key_without_plaintext_or_result_retention(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    conversation = world.get_or_create_conversation("telegram", "private-user")
    created = _create(world)
    world.append_turn(
        conversation.id,
        "user",
        "private conversation content",
        goal_id=created.goal_id,
    )
    plan = world.plan_conversation_erasure([conversation.id])

    removed, _paths, removed_turns = world.erase_conversations(
        [conversation.id],
        expected_conversation_ids=plan["conversation_ids"],
        expected_goal_ids=plan["goal_ids"],
        expected_episode_ids=plan["episode_ids"],
    )

    assert removed == {created.goal_id}
    assert removed_turns == 1
    assert world.get_goal(created.goal_id) is None
    assert world.conn.execute(
        "SELECT COUNT(*) FROM api_idempotency"
    ).fetchone()[0] == 0

    columns = [
        str(row[1])
        for row in world.conn.execute(
            "PRAGMA table_info(api_idempotency_tombstones)"
        ).fetchall()
    ]
    assert columns
    assert {
        "operation",
        "principal_id",
        "idempotency_key",
        "goal_id",
        "result_json",
    }.isdisjoint(columns)
    assert world.conn.execute(
        "PRAGMA foreign_key_list(api_idempotency_tombstones)"
    ).fetchall() == []

    rows = world.conn.execute(
        "SELECT * FROM api_idempotency_tombstones"
    ).fetchall()
    assert len(rows) == 1
    stored = json.dumps([list(row) for row in rows], ensure_ascii=False)
    for plaintext in (
        _OPERATION,
        _PRINCIPAL,
        _KEY,
        "private bound brief",
        "private conversation content",
        "goal-request-retirement-0001",
    ):
        assert plaintext not in stored
    assert len(re.findall(r"[0-9a-f]{64}", stored)) >= 2

    for title in ("one", "a changed request"):
        with pytest.raises(ApiIdempotencyError):
            _create(world, title=title)
        assert world.list_goals(limit=100) == []

    deliberate_new_operation = _create(
        world,
        title="deliberately new operation",
        key="fresh-key-after-erasure",
    )
    assert world.get_goal(deliberate_new_operation.goal_id) is not None


class _PostgresCreateCursor:
    """Minimal SQL-aware cursor that tolerates additive lookup steps."""

    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []
        self._row = None
        self.rowcount = 0

    def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        lowered = compact.lower()
        self.calls.append((compact, params))
        self.rowcount = 0
        self._row = None
        if "from api_idempotency_tombstones" in lowered:
            self._row = None
        elif re.match(r"insert into api_idempotency\s*\(", lowered):
            self._row = (71,)
            self.rowcount = 1
        elif lowered.startswith("insert into goals("):
            self._row = (101,)
            self.rowcount = 1
        elif lowered.startswith("update api_idempotency set"):
            self.rowcount = 1

    def fetchone(self):
        return self._row


def test_postgres_create_binds_exact_principal_text_without_coercion(monkeypatch):
    from maverick.world_model_backends import postgres

    cursor = _PostgresCreateCursor()
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)

    @contextmanager
    def tx():
        yield cursor

    monkeypatch.setattr(world, "_tx", tx)
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "tenant-a")
    monkeypatch.setattr(postgres, "_seal", lambda value: value)

    result = _create(world)

    assert result.goal_id == 101
    insert_params = next(
        params
        for sql, params in cursor.calls
        if re.match(r"insert into api_idempotency\s*\(", sql.lower())
    )
    assert _PRINCIPAL in insert_params
    assert _KEY in insert_params
    assert "user:alice" not in insert_params

    calls_before = len(cursor.calls)
    with pytest.raises(ValueError, match="idempotency principal is invalid"):
        _create(world, principal=7, key="postgres-invalid-principal")
    assert len(cursor.calls) == calls_before
