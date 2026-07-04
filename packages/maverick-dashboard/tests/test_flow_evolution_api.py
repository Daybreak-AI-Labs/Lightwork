"""Dashboard endpoints for the flow self-learning loop: apply a rewrite proposal,
walk version history + roll back, and read the before/after impact of an applied
change. Flow engine gated (enabled in the fixture)."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _save(c, fid="f"):
    return c.post("/api/v1/flows", json={"flow": {
        "id": fid, "name": "F", "start": "a",
        "nodes": [{"id": "a", "kind": "agent", "brief": "summarize {{x}}"}]}})


def test_apply_hardens_agent_to_action_and_bumps_version(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    assert _save(c).status_code == 201
    r = c.post("/api/v1/flows/f/apply",
               json={"node_id": "a", "to_kind": "action", "tool": "summarize_tool"})
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == 2 and body["from_kind"] == "agent" and body["to_kind"] == "action"
    node_a = next(n for n in c.get("/api/v1/flows/f").json()["nodes"] if n["id"] == "a")
    assert node_a["kind"] == "action" and node_a["tool"] == "summarize_tool"


def test_apply_without_tool_is_400(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    assert c.post("/api/v1/flows/f/apply",
                  json={"node_id": "a", "to_kind": "action"}).status_code == 400


def test_apply_noop_same_kind_is_400(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    assert c.post("/api/v1/flows/f/apply",
                  json={"node_id": "a", "to_kind": "agent"}).status_code == 400


def test_apply_unknown_node_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    assert c.post("/api/v1/flows/f/apply",
                  json={"node_id": "zzz", "to_kind": "action", "tool": "t"}).status_code == 404


def test_apply_unknown_flow_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert _client().post("/api/v1/flows/ghost/apply",
                          json={"node_id": "a", "to_kind": "action", "tool": "t"}).status_code == 404


def test_versions_and_rollback(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)                                                          # v1 (agent)
    c.post("/api/v1/flows/f/apply",
           json={"node_id": "a", "to_kind": "action", "tool": "summarize_tool"})  # v2 (action)
    versions = c.get("/api/v1/flows/f/versions").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1]                 # newest first
    rb = c.post("/api/v1/flows/f/rollback", json={})                  # -> v3, agent again
    assert rb.status_code == 200 and rb.json()["version"] == 3
    node_a = next(n for n in c.get("/api/v1/flows/f").json()["nodes"] if n["id"] == "a")
    assert node_a["kind"] == "agent"


def test_rollback_unknown_version_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    assert c.post("/api/v1/flows/f/rollback", json={"version": 99}).status_code == 404


def test_impact_reports_before_after_once_applied(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    from maverick.flow import node_outcomes
    node_outcomes.record("f", "a", "agent", 0.4)             # before the change
    node_outcomes.record("f", "a", "agent", 0.5)
    assert c.post("/api/v1/flows/f/apply",
                  json={"node_id": "a", "to_kind": "action", "tool": "t"}).status_code == 200
    node_outcomes.record("f", "a", "action", 1.0)            # after the change
    body = c.get("/api/v1/flows/f/nodes/a/impact").json()
    assert body["changed"] is True and body["applied"]["to_kind"] == "action"
    imp = body["impact"]
    assert imp["before"]["n"] + imp["after"]["n"] == 3       # all outcomes accounted for


def test_impact_unchanged_node(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    r = c.get("/api/v1/flows/f/nodes/a/impact")
    assert r.status_code == 200 and r.json()["changed"] is False
