from __future__ import annotations

import sqlite3

import pytest
from maverick.api_idempotency import (
    ApiIdempotencyConflict,
    ApiIdempotencyExpired,
    ApiIdempotencyIndeterminate,
    decode_stored_goal_result,
    idempotency_scope_sha256,
    identity_sha256,
    request_sha256,
    validate_idempotency_key,
    validate_principal_id,
)
from maverick.world_model import WorldModel


def _request(title: str = "one") -> dict:
    return {
        "description": "bound brief",
        "domain": "",
        "max_depth": 3,
        "max_dollars": 5.0,
        "max_wall_seconds": 3600.0,
        "title": title,
    }


def _create(world: WorldModel, *, title: str = "one", ttl_seconds: int = 60):
    normalized = _request(title)
    dispatch = {
        "allowed_suites": ["finance"],
        "channel": "api",
        "max_depth": 3,
        "max_dollars": 5.0,
        "max_wall_seconds": 3600.0,
        "schema": "lightwork.dashboard-goal-dispatch.v1",
        "user_id": "alice",
    }
    return world.create_goal_idempotent(
        title,
        "bound brief",
        owner="user:alice",
        operation="POST /api/v1/goals",
        principal_id="user:alice",
        idempotency_key="goal-request-0001",
        request_sha256=request_sha256(normalized),
        dispatch_payload=dispatch,
        ttl_seconds=ttl_seconds,
    )


def test_identity_bearing_values_are_exact_and_never_string_coerced():
    assert validate_principal_id(" user:alice ") == " user:alice "
    assert validate_idempotency_key(" request-1 ") == " request-1 "
    assert identity_sha256("user:alice") != identity_sha256("user:Alice")
    assert identity_sha256("user:alice") != identity_sha256("user:alice ")
    assert idempotency_scope_sha256("POST /goals", "user:alice", "k") != (
        idempotency_scope_sha256("POST /goals", "user:alice ", "k")
    )
    assert idempotency_scope_sha256("POST /goals", "ab", "c") != (
        idempotency_scope_sha256("POST /goals", "a", "bc")
    )

    for value in (None, 7, "", "   ", "user:\nalice"):
        with pytest.raises(ValueError, match="idempotency principal is invalid"):
            validate_principal_id(value)


def test_sealed_result_failures_are_content_free_and_typed():
    def missing_historical_key(_value):
        raise RuntimeError("secret historical key material")

    with pytest.raises(
        ApiIdempotencyIndeterminate,
        match="idempotency result is unavailable",
    ) as exc:
        decode_stored_goal_result("ciphertext", missing_historical_key)

    assert "historical key" not in str(exc.value)


def test_exact_replay_returns_original_durable_result(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    first = _create(world)
    world.set_goal_status(first.goal_id, "done", result="later result")

    replay = _create(world)

    assert replay.replayed is True
    assert replay.goal_id == first.goal_id
    assert replay.response == first.response
    assert replay.dispatch == first.dispatch
    assert world.get_goal_idempotent_dispatch(first.goal_id) == first.dispatch
    assert replay.response["status"] == "pending"
    assert len(world.list_goals()) == 1


def test_same_key_with_different_request_is_rejected(tmp_path):
    world = WorldModel(tmp_path / "world.db")
    _create(world, title="one")

    with pytest.raises(ApiIdempotencyConflict, match="different request"):
        _create(world, title="two")

    assert [goal.title for goal in world.list_goals()] == ["one"]


def test_expired_key_is_a_tombstone_not_a_new_operation(monkeypatch, tmp_path):
    from maverick import world_model

    monkeypatch.setattr(world_model.time, "time", lambda: 100.0)
    world = WorldModel(tmp_path / "world.db")
    first = _create(world, ttl_seconds=1)
    monkeypatch.setattr(world_model.time, "time", lambda: 102.0)

    with pytest.raises(ApiIdempotencyExpired, match="expired"):
        _create(world, ttl_seconds=1)

    assert [goal.id for goal in world.list_goals()] == [first.goal_id]


def test_goal_and_result_claim_roll_back_together(monkeypatch, tmp_path):
    from maverick import world_model

    world = WorldModel(tmp_path / "world.db")
    real_encrypt = world_model._enc_field

    def fail_result(value):
        if isinstance(value, str) and "api-idempotent-goal-result" in value:
            raise RuntimeError("simulated result sealing failure")
        return real_encrypt(value)

    monkeypatch.setattr(world_model, "_enc_field", fail_result)
    with pytest.raises(RuntimeError, match="result sealing failure"):
        _create(world)

    assert world.list_goals() == []
    with sqlite3.connect(world.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM api_idempotency").fetchone()[0] == 0
