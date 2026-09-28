from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick import governance, governed_actions
from maverick_dashboard import agent_action_trace_api as api

BASE = "/api/v1/security/governance/agent-actions"


class _Goal:
    def __init__(self, owner: str) -> None:
        self.owner = owner


class _World:
    goals = {7: _Goal("user:alice"), 8: _Goal("user:alice"), 9: _Goal("user:bob")}

    def get_goal(self, goal_id):
        return self.goals.get(goal_id)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from maverick_dashboard import api as dashboard_api

    monkeypatch.setattr(dashboard_api, "_world", _World)
    permissions: list[str] = []
    monkeypatch.setattr(api, "require_permission", lambda _r, p: permissions.append(p))
    monkeypatch.setattr(governed_actions, "_lineage_dir", lambda store_dir=None: tmp_path)
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    return TestClient(app), permissions, tmp_path


def test_policy_check_reports_the_verdict_read_only(client, monkeypatch) -> None:
    test_client, permissions, _ = client
    seen = {}

    def evaluate(action, *, risk=None):
        seen.update(action=action, risk=risk)
        return governance.Verdict(
            decision=governance.Decision.REQUIRE_HUMAN, reason="A person must approve.", rule="require_human_actions"
        )

    monkeypatch.setattr(governance, "evaluate", evaluate)
    body = test_client.get(f"{BASE}/check", params={"action": "send_email", "risk": "high"}).json()
    assert body == {"action": "send_email", "risk": "high", "decision": "require_human",
                    "reason": "A person must approve.", "rule": "require_human_actions"}
    assert seen == {"action": "send_email", "risk": "high"}
    assert permissions == ["view"]
    assert test_client.get(f"{BASE}/check", params={"action": "<script>"}).status_code == 422
    assert test_client.get(f"{BASE}/check", params={"action": "x", "risk": "extreme"}).status_code == 422


def test_lineage_and_impact_come_from_the_workspace_store(client) -> None:
    test_client, permissions, _ = client
    assert governed_actions.record_tool_lineage(
        7, "send_email", {"to": "secret@example.com"}, skills=("triage",), sources=("kb:policy",), force=True
    )
    body = test_client.get(f"{BASE}/lineage/7").json()
    assert body["chain"] == "valid"
    assert body["items"][0]["action"] == "send_email"
    assert body["items"][0]["skills"] == ["triage"]
    assert "secret@example.com" not in str(body)
    assert test_client.get(f"{BASE}/lineage/8").json()["chain"] == "missing"
    hits = test_client.get(f"{BASE}/impact", params={"name": "triage", "kind": "skill"}).json()
    assert [(hit["goal_id"], hit["via"]) for hit in hits["items"]] == [(7, "skill")]
    assert "hash" not in hits["items"][0]
    assert set(permissions) == {"view"}


def test_a_broken_lineage_chain_is_reported(client) -> None:
    test_client, _, store = client
    governed_actions.record_tool_lineage(9, "send_email", {}, force=True)
    path = store / "9.ndjson"
    path.write_text(path.read_text().replace("send_email", "delete_all"))
    assert test_client.get(f"{BASE}/lineage/9").json()["chain"] == "broken"


def test_lineage_and_impact_follow_the_task_owner_check(client, monkeypatch) -> None:
    from maverick_dashboard import auth

    test_client, _, _ = client
    for goal_id in (7, 9):
        assert governed_actions.record_tool_lineage(
            goal_id, "send_email", {"to": "x"}, sources=("hr/salaries.xlsx",), force=True
        )
    monkeypatch.setattr(auth, "_authorization_principal", lambda request: "user:bob")
    monkeypatch.setattr(auth, "is_dashboard_admin", lambda principal: False)
    # Someone else's task looks exactly like a task that does not exist.
    assert test_client.get(f"{BASE}/lineage/7").status_code == 404
    assert test_client.get(f"{BASE}/lineage/404").status_code == 404
    assert test_client.get(f"{BASE}/lineage/9").json()["count"] == 1
    hits = test_client.get(f"{BASE}/impact", params={"name": "hr/salaries.xlsx"}).json()
    assert [hit["goal_id"] for hit in hits["items"]] == [9]
