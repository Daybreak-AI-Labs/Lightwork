"""Dashboard flow surface: CRUD + validation, the durable run/resume path through
the automation queue, owner-scoped runs, and self-rewrite proposals. Flow engine
gated (off by default)."""
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


def _reset_aq(monkeypatch, aq):
    monkeypatch.setattr(aq, "_queue", None)
    monkeypatch.setattr(aq, "_worker", None)
    monkeypatch.setattr(aq, "_worker_threads", [])


def _drain(aq):
    # A worker with the flow handlers registered (as automation_queue.start would).
    from maverick.worker import Worker
    w = Worker(queue=aq.queue())
    w.register(aq.FLOW_RUN_KIND, aq._handle_flow_run)
    w.register(aq.FLOW_RESUME_KIND, aq._handle_flow_resume)
    w.drain()


_FLOW = {
    "id": "demo", "name": "Demo", "start": "a",
    "nodes": [
        {"id": "a", "kind": "agent", "brief": "assess {{order}}", "next": "b", "output": "r"},
        {"id": "b", "kind": "approval", "prompt": "ship it?", "next": "c"},
        {"id": "c", "kind": "action", "tool": "notify", "params": {"m": "{{r}}"}},
    ],
}


def test_flows_gated_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_FLOWS", raising=False)
    monkeypatch.setattr("maverick.config.get_flows", lambda: {"enable": False})
    assert _client().get("/api/v1/flows").status_code == 403


def test_save_validate_get_delete(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    assert c.get("/api/v1/flows").json()["flows"][0]["id"] == "demo"
    assert len(c.get("/api/v1/flows/demo").json()["nodes"]) == 3
    assert c.delete("/api/v1/flows/demo").status_code == 204
    assert c.get("/api/v1/flows/demo").status_code == 404


def test_invalid_flow_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    bad = {"id": "b", "name": "x", "start": "z", "nodes": [{"id": "a", "kind": "agent"}]}
    r = _client().post("/api/v1/flows", json={"flow": bad})
    assert r.status_code == 400 and "invalid flow" in r.json()["detail"]


def test_run_drains_through_queue_and_pauses_on_approval(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    # fake live runners so no LLM/tool is needed
    def _fake_agent(world, **k):
        return lambda brief, d: (f"ran:{brief}", 1.0)

    def _fake_action(world, sandbox=None):
        return lambda t, p, d: ("sent", 1.0)

    monkeypatch.setattr(execution, "default_agent_runner", _fake_agent)
    monkeypatch.setattr(execution, "default_action_runner", _fake_action)
    c = _client()
    c.post("/api/v1/flows", json={"flow": _FLOW})
    started = c.post("/api/v1/flows/demo/run", json={"data": {"order": "X-9"}}).json()
    run_id = started["run_id"]
    assert started["status"] == "queued"
    # drain the queued flow_run job
    _drain(aq)
    run = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert run["status"] == "paused_approval" and run["cursor"] == "b"
    assert run["data"]["r"] == "ran:assess X-9"     # agent output threaded through
    # resume approved -> drains to completion, action fires
    c.post(f"/api/v1/flows/runs/{run_id}/resume", json={"decision": "approved"})
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed"


def test_runs_are_owner_scoped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import api
    store.save_run(store.FlowRun(run_id="r1", flow_id="demo", status="completed", owner="user:alice"))
    store.save_run(store.FlowRun(run_id="r2", flow_id="demo", status="completed", owner="user:bob"))
    monkeypatch.setattr(api, "goal_owner_filter", lambda request: "user:alice")
    ids = {r["run_id"] for r in _client().get("/api/v1/flows/runs").json()["runs"]}
    assert ids == {"r1"}


def test_resume_of_non_paused_run_is_409(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.save_run(store.FlowRun(run_id="rc", flow_id="demo", status="completed"))
    r = _client().post("/api/v1/flows/runs/rc/resume", json={"decision": "approved"})
    assert r.status_code == 409


def test_delay_sweep_reenqueues_only_due_runs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    store.save_run(store.FlowRun(run_id="d1", flow_id="demo",
                                 status="paused_delay", resume_at=1.0))          # due
    store.save_run(store.FlowRun(run_id="d2", flow_id="demo",
                                 status="paused_delay", resume_at=9e18))         # not yet
    aq._handle_flow_sweep(None)
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RESUME_KIND]
    assert {j.payload["run_id"] for j in jobs} == {"d1"}


def test_draft_endpoint_returns_a_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # bypass the provider-key guard + the real LLM
    import maverick.flow.draft as draft_mod
    from maverick.flow.ir import single_agent_flow
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    monkeypatch.setattr(draft_mod, "draft_flow",
                        lambda desc, **k: (single_agent_flow(k.get("flow_id", ""), "Drafted", desc), ["note"]))
    r = _client().post("/api/v1/flows/draft", json={"description": "email me on new leads"})
    assert r.status_code == 200
    body = r.json()
    assert body["flow"]["nodes"][0]["kind"] == "agent"
    assert body["notes"] == ["note"]


def test_draft_requires_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("maverick.config.any_provider_configured", lambda: False)
    r = _client().post("/api/v1/flows/draft", json={"description": "x"})
    assert r.status_code == 400


def test_proposals_endpoint(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import node_outcomes, store
    from maverick.flow.ir import Flow, FlowNode
    f = Flow(id="fp", name="f", start="a",
             nodes={"a": FlowNode(id="a", kind="agent", brief="b")})
    store.save_flow(f)
    for _ in range(12):
        node_outcomes.record("fp", "a", "agent", 1.0)   # reliable agent
    props = _client().get("/api/v1/flows/fp/proposals").json()["proposals"]
    assert props and props[0]["to_kind"] == "action"
