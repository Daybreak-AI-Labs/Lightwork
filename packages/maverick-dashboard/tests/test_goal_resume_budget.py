"""Resuming from the app preserves authorization and deployment spending caps."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient


@pytest.fixture
def resume_env(monkeypatch, tmp_path):
    from maverick import world_model
    from maverick_dashboard import api
    from maverick_dashboard.app import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    world = world_model.WorldModel(world_model.DEFAULT_DB)
    monkeypatch.setattr(api, "_world", lambda: world)
    monkeypatch.setattr(api, "DEFAULT_MAX_DOLLARS", 2.0)
    calls = []

    async def run(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr("maverick.runner.run_goal_in_background_async", run)
    goal_id = world.create_goal("Review a draft", "")
    world.set_goal_status(goal_id, "blocked", result="Budget exhausted")
    yield TestClient(app, headers={"Origin": "http://testserver"}), world, goal_id, calls
    world.close()


@pytest.mark.parametrize("payload", [None, {}, {"max_dollars": None}])
def test_legacy_resume_keeps_runner_defaults(resume_env, payload):
    client, world, goal_id, calls = resume_env
    response = client.post(f"/api/v1/goals/{goal_id}/resume", **({"json": payload} if payload is not None else {}))
    assert response.status_code == 204, response.text
    assert world.get_goal(goal_id).status == "pending"
    assert len(calls) == 1 and "max_dollars" not in calls[0][1]


def test_resume_sends_per_attempt_limit_and_execution_identity(resume_env, monkeypatch):
    from maverick_dashboard import api

    client, _, goal_id, calls = resume_env
    monkeypatch.setattr(api, "execution_user_id_from_request", lambda request: "alice")
    monkeypatch.setattr(api, "caller_suites", lambda request: frozenset({"privacy"}))
    response = client.post(f"/api/v1/goals/{goal_id}/resume", json={"max_dollars": 1.25})
    assert response.status_code == 204, response.text
    assert calls == [((goal_id,), {"max_dollars": 1.25, "user_id": "alice", "channel": "api", "allowed_suites": frozenset({"privacy"})})]


@pytest.mark.parametrize("value, status", [(0, 422), (-1, 422), ("NaN", 422), ("Infinity", 422), (2.01, 400)])
def test_invalid_or_excessive_limit_cannot_change_goal_or_start_work(resume_env, value, status):
    client, world, goal_id, calls = resume_env
    response = client.post(f"/api/v1/goals/{goal_id}/resume", json={"max_dollars": value})
    assert response.status_code == status, response.text
    assert world.get_goal(goal_id).status == "blocked"
    assert world.get_goal(goal_id).result == "Budget exhausted"
    assert not calls


def test_budget_control_does_not_bypass_goal_ownership(resume_env, monkeypatch):
    from maverick_dashboard import api

    client, world, goal_id, calls = resume_env

    def deny(request, goal):
        raise HTTPException(status_code=404, detail="no such goal")

    monkeypatch.setattr(api, "assert_goal_access", deny)
    response = client.post(f"/api/v1/goals/{goal_id}/resume", json={"max_dollars": 1})
    assert response.status_code == 404
    assert world.get_goal(goal_id).status == "blocked"
    assert not calls
