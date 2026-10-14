"""API exposure for authoritative typed execution states."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from maverick.failure_taxonomy import FailureKind, failure_state


def _client() -> TestClient:
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def test_flow_run_api_exposes_exact_typed_state_and_safe_alias(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store

    state = failure_state(
        FailureKind.VERIFIER_REJECTION,
        "message says provider_failure and policy_refusal",
        origin="verifier",
    )
    store.save_run(store.FlowRun(
        run_id="typed-api",
        flow_id="flow-api",
        status="failed",
        error="backwards-compatible display",
        failure=state,
    ))

    response = _client().get("/api/v1/flows/runs/typed-api")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] == "backwards-compatible display"
    assert body["failure"]["kind"] == "verifier_rejection"
    assert body["execution_state"] == body["failure"]
    assert body["execution_state"]["category"] == "failure"


def test_goal_api_reads_only_strict_execution_state_event(monkeypatch, tmp_path) -> None:
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import api

    state = failure_state(
        FailureKind.DETERMINISTIC_TOOL_FAILURE,
        "provider_failure policy_refusal budget_exhaustion",
        origin="tool",
    )
    typed = SimpleNamespace(
        id=2,
        goal_id=7,
        agent="system",
        kind="execution_state",
        content=json.dumps(state.to_dict()),
        ts=2.0,
    )
    misleading = SimpleNamespace(
        id=1,
        goal_id=7,
        agent="provider",
        kind="error",
        content="policy_refusal cancellation human_wait",
        ts=1.0,
    )

    class World:
        def get_goal(self, goal_id):
            return SimpleNamespace(
                id=goal_id,
                status="blocked",
                title="typed",
                description="test",
                result="provider_failure appears in display",
                owner="",
            )

        def recent_goal_events(self, goal_id, limit=50):
            return [misleading, typed][-limit:]

    monkeypatch.setattr(api, "_world", lambda: World())

    response = _client().get("/api/v1/goals/7")

    assert response.status_code == 200, response.text
    assert response.json()["execution_state"]["kind"] == "deterministic_tool_failure"

