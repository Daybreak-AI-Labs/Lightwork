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


def test_sweep_rejects_expired_approvals_but_never_paused_events(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    store.save_run(store.FlowRun(run_id="ap1", flow_id="demo",
                                 status="paused_approval", resume_at=1.0))     # expired
    store.save_run(store.FlowRun(run_id="ap2", flow_id="demo",
                                 status="paused_approval", resume_at=9e18))    # still open
    store.save_run(store.FlowRun(run_id="ev1", flow_id="demo",
                                 status="paused_event", resume_at=1.0))        # event-only
    aq._handle_flow_sweep(None)
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RESUME_KIND]
    assert {j.payload["run_id"] for j in jobs} == {"ap1"}
    # expiry sends the EXPIRED marker -> the runner routes on_expire or rejects
    from maverick.flow.runner import EXPIRED
    assert jobs[0].payload["decision"] == EXPIRED


def test_retry_of_paused_event_run_is_409_not_a_duplicate(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    c = _client()
    assert c.post("/api/v1/flows", json={"flow": _FLOW}).status_code == 201
    store.save_run(store.FlowRun(run_id="pe", flow_id="demo", status="paused_event",
                                 cursor="w"))
    r = c.post("/api/v1/flows/runs/pe/retry")
    assert r.status_code == 409 and "still active" in r.json()["detail"]
    # no duplicate flow_run job was enqueued
    jobs = [j for j in aq.queue().list(status="pending") if j.kind == aq.FLOW_RUN_KIND]
    assert jobs == []


def test_paused_event_run_resumes_via_api_with_event_data(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: lambda brief, d: (f"ran:{brief}", 1.0))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None: lambda t, p, d: ("", 1.0))
    c = _client()
    flow = {"id": "fev", "name": "f", "start": "w", "nodes": [
        {"id": "w", "kind": "wait_event", "prompt": "cb", "next": "a"},
        {"id": "a", "kind": "agent", "brief": "handle {{payload}}", "output": "r"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    run_id = c.post("/api/v1/flows/fev/run", json={"data": {}}).json()["run_id"]
    _drain(aq)
    assert c.get(f"/api/v1/flows/runs/{run_id}").json()["status"] == "paused_event"
    r = c.post(f"/api/v1/flows/runs/{run_id}/resume",
               json={"inputs": {"payload": "evt-7"}})
    assert r.status_code == 200
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed" and done["data"]["r"] == "ran:handle evt-7"


def test_choice_approval_verdict_flows_through_resume(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    monkeypatch.setattr(execution, "default_agent_runner",
                        lambda world, **k: lambda brief, d: (f"ran:{brief}", 1.0))
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None: lambda t, p, d: ("", 1.0))
    c = _client()
    flow = {"id": "fch", "name": "f", "start": "a", "nodes": [
        {"id": "a", "kind": "approval", "prompt": "ship or hold?",
         "choices": ["ship", "hold"], "output": "verdict", "next": "s"},
        {"id": "s", "kind": "switch", "condition": "verdict",
         "cases": [{"value": "ship", "to": "go"}], "next": "no"},
        {"id": "go", "kind": "agent", "brief": "shipping", "output": "r"},
        {"id": "no", "kind": "agent", "brief": "holding", "output": "r"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    run_id = c.post("/api/v1/flows/fch/run", json={"data": {}}).json()["run_id"]
    _drain(aq)
    r = c.post(f"/api/v1/flows/runs/{run_id}/resume", json={"decision": "ship"})
    assert r.status_code == 200, r.text
    _drain(aq)
    done = c.get(f"/api/v1/flows/runs/{run_id}").json()
    assert done["status"] == "completed"
    assert done["data"]["verdict"] == "ship" and done["data"]["r"] == "ran:shipping"


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


def test_chat_endpoint_returns_reply_and_patched_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.chat as chat_mod
    from maverick.flow.chat import FlowChatResult
    from maverick.flow.ir import Flow
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    seen = {}

    def fake_chat(message, *, flow=None, history=(), run=None, **k):
        seen.update(message=message, flow=flow, history=list(history), run=run)
        return FlowChatResult(reply="Added it.", flow=flow,
                              applied=["add delay node d1 after a"])
    monkeypatch.setattr(chat_mod, "chat_flow", fake_chat)
    r = _client().post("/api/v1/flows/chat", json={
        "message": "add a 5 minute delay",
        "flow": _FLOW,
        "history": [{"role": "user", "content": "hi"}],
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "Added it."
    assert body["flow"]["id"] == "demo" and body["applied"]
    assert isinstance(seen["flow"], Flow) and seen["history"][0]["content"] == "hi"


def test_chat_grounds_in_an_owned_run(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.flow.chat as chat_mod
    from maverick.flow import store
    from maverick.flow.chat import FlowChatResult
    monkeypatch.setattr("maverick_dashboard._shared.require_provider_or_400", lambda: None)
    store.save_run(store.FlowRun(run_id="rf", flow_id="demo", status="failed",
                                 cursor="b", error="node 'b': boom"))
    seen = {}

    def fake_chat(message, *, run=None, **k):
        seen["run"] = run
        return FlowChatResult(reply="b failed: boom")
    monkeypatch.setattr(chat_mod, "chat_flow", fake_chat)
    r = _client().post("/api/v1/flows/chat", json={
        "message": "why did it fail?", "flow": _FLOW, "run_id": "rf"})
    assert r.status_code == 200
    assert seen["run"]["cursor"] == "b" and "boom" in seen["run"]["error"]
    # an unknown run id is a 404, not a silent no-context turn
    assert _client().post("/api/v1/flows/chat", json={
        "message": "x", "flow": _FLOW, "run_id": "nope"}).status_code == 404


def test_chat_requires_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr("maverick.config.any_provider_configured", lambda: False)
    r = _client().post("/api/v1/flows/chat", json={"message": "x", "flow": _FLOW})
    assert r.status_code == 400


def test_retry_from_failure_resumes_at_failed_node(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import execution, store
    from maverick_dashboard import automation_queue as aq
    _reset_aq(monkeypatch, aq)
    calls = []

    def _fake_agent(world, **k):
        def run(brief, d):
            calls.append(brief)
            return (f"ran:{brief}", 1.0)
        return run
    monkeypatch.setattr(execution, "default_agent_runner", _fake_agent)
    monkeypatch.setattr(execution, "default_action_runner",
                        lambda world, sandbox=None: lambda t, p, d: ("sent", 1.0))
    c = _client()
    flow = {"id": "fret", "name": "f", "start": "a", "nodes": [
        {"id": "a", "kind": "agent", "brief": "prep", "next": "b", "output": "r1"},
        {"id": "b", "kind": "action", "tool": "notify", "params": {}, "output": "r2"},
    ]}
    assert c.post("/api/v1/flows", json={"flow": flow}).status_code == 201
    # a run that failed AT node b, with node a's work already done
    store.save_run(store.FlowRun(run_id="rx", flow_id="fret", status="failed",
                                 cursor="b", data={"r1": "ok"},
                                 nodes={"a": {"status": "done", "outcome": 1.0}}))
    r = c.post("/api/v1/flows/runs/rx/retry?from_failure=1")
    assert r.status_code == 200, r.text
    assert r.json() == {"run_id": "rx", "status": "resuming", "from_node": "b"}
    _drain(aq)
    done = c.get("/api/v1/flows/runs/rx").json()
    assert done["status"] == "completed" and done["data"]["r2"] == "sent"
    assert calls == []                        # node a was NOT re-run
    # a completed run can't be resumed from failure
    assert c.post("/api/v1/flows/runs/rx/retry?from_failure=1").status_code == 409
    # a failed run with no recorded failure node can't either
    store.save_run(store.FlowRun(run_id="ry", flow_id="fret", status="failed"))
    assert c.post("/api/v1/flows/runs/ry/retry?from_failure=1").status_code == 409


def test_run_events_stream_ends_with_terminal_snapshot(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.save_run(store.FlowRun(run_id="sse1", flow_id="demo", status="completed",
                                 data={"r": "ok"}, nodes={"a": {"status": "done", "outcome": 1.0}}))
    c = _client()
    with c.stream("GET", "/api/v1/flows/runs/sse1/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = ""
        for chunk in r.iter_text():
            body += chunk
            if "event: terminal" in body:
                break
    assert "event: terminal" in body and '"status": "completed"' in body
    # unknown run 404s before a stream slot is held
    assert c.get("/api/v1/flows/runs/nope/events").status_code == 404


def test_run_viewer_page_renders(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/demo/runs/abc123")
    assert r.status_code == 200
    assert 'id="fr-steps"' in r.text and "open in designer" in r.text


def test_designer_page_has_copilot_and_toolbar(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/designer")
    assert r.status_code == 200
    for needle in ('id="fd-chat"', 'id="fd-undo"', 'id="fd-redo"', 'id="fd-fit"',
                   'id="fd-triggers"', 'id="fd-export"', 'data-add="switch"',
                   'data-add="while"', 'data-add="wait_event"', 'data-add="scope"',
                   "/api/v1/flows/chat", "upstreamOutputs", "validateFlow"):
        assert needle in r.text, needle


def test_flow_analytics_aggregates_runs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    for i in range(3):
        store.save_run(store.FlowRun(run_id=f"c{i}", flow_id="demo", status="completed",
                                     nodes={"a": {"status": "done", "outcome": 1.0, "seconds": 2.0 + i}}))
    store.save_run(store.FlowRun(run_id="f1", flow_id="demo", status="failed",
                                 error="node 'b': connector down"))
    store.save_run(store.FlowRun(run_id="p1", flow_id="demo", status="paused_approval"))
    flows = _client().get("/api/v1/flows/analytics").json()["flows"]
    demo = next(f for f in flows if f["flow_id"] == "demo")
    assert demo["runs"] == 5 and demo["completed"] == 3 and demo["failed"] == 1 and demo["paused"] == 1
    assert demo["success_rate"] == 0.75          # 3 completed / 4 finished
    assert demo["p50_seconds"] >= 2.0 and demo["p95_seconds"] >= demo["p50_seconds"]
    assert demo["top_errors"][0]["error"].startswith("node 'b'")


def test_flow_analytics_page_renders(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/flows/analytics")
    assert r.status_code == 200 and 'id="fa-table"' in r.text


def test_flow_schema_endpoint_returns_learned_shapes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    store.record_flow_schema("demo", {"order": {"type": "object", "keys": ["total"]}})
    r = _client().get("/api/v1/flows/demo/schema")
    assert r.status_code == 200
    assert r.json()["schema"]["order"] == {"type": "object", "keys": ["total"]}
    # unknown flow -> empty (not an error)
    assert _client().get("/api/v1/flows/nope/schema").json()["schema"] == {}


def test_gallery_returns_valid_starter_flows(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow
    flows = _client().get("/api/v1/flows/gallery").json()["flows"]
    assert len(flows) >= 3
    for g in flows:
        f = Flow.from_dict(g)
        f.id = "gallery-check"
        assert f.validate() == [], (g["name"], f.validate())


def test_tools_search_reaches_the_full_registry(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    curated = c.get("/api/v1/flows/tools").json()["tools"]
    assert curated and any(t["name"] == "slack_bot" for t in curated)
    hits = c.get("/api/v1/flows/tools?q=http").json()["tools"]
    assert hits and all("http" in (t["name"] + t["description"]).lower() for t in hits)
    # name-prefix matches rank first
    assert hits[0]["name"].startswith("http")


def test_designer_opens_a_template_as_a_flow(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    import maverick.templates as tpl
    tdir = tmp_path / ".maverick" / "templates"
    monkeypatch.setattr(tpl, "USER_TEMPLATES", tdir)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "weekly-digest.md").write_text(
        "---\ntitle: Weekly digest\nparams: []\n---\nWrite the weekly digest.\n",
        encoding="utf-8")
    r = _client().get("/flows/designer?from_template=weekly-digest")
    assert r.status_code == 200
    assert "Write the weekly digest." in r.text          # seeded as an agent brief
    # an unknown template degrades to a blank canvas, not an error
    assert _client().get("/flows/designer?from_template=nope").status_code == 200


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
