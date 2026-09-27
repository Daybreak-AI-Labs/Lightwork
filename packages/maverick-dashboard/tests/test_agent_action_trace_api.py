from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick import governance, governed_actions
from maverick_dashboard import agent_action_trace_api as api

BASE = "/api/v1/security/governance/agent-actions"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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
