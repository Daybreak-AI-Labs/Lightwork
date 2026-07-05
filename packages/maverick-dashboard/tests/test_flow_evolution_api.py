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


def test_auto_apply_softens_a_failing_action_when_enabled(monkeypatch, tmp_path):
    # B9: with auto_evolve + auto_apply on, a chronically-failing action node is
    # autonomously softened to an agent -- the forward arrow, gated + soften-only.
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_FLOWS_AUTO", "1")
    monkeypatch.setattr("maverick.config.get_flows",
                        lambda: {"auto_evolve": True, "auto_apply": True})
    from maverick.flow import node_outcomes, store
    from maverick.flow.ir import Flow, FlowNode
    store.save_flow(Flow(id="fa", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="action", tool="t", brief="do it")}))
    for _ in range(12):
        node_outcomes.record("fa", "a", "action", 0.0)      # it keeps failing
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    aq._handle_flow_evolve()
    assert store.load_flow("fa").nodes["a"].kind == "agent"  # softened autonomously
    from maverick.flow import evolution_log
    assert evolution_log.last_apply("fa", "a")["source"] == "auto-apply"


def test_auto_apply_off_by_default_leaves_the_flow_alone(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_FLOWS_AUTO", "1")           # auto_evolve on, auto_apply OFF
    monkeypatch.setattr("maverick.config.get_flows", lambda: {"auto_evolve": True})
    from maverick.flow import node_outcomes, store
    from maverick.flow.ir import Flow, FlowNode
    store.save_flow(Flow(id="fb", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="action", tool="t", brief="do it")}))
    for _ in range(12):
        node_outcomes.record("fb", "a", "action", 0.0)
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    aq._handle_flow_evolve()
    assert store.load_flow("fb").nodes["a"].kind == "action"  # untouched


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


def test_signals_surfaces_approval_and_trigger_stats(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    c.post("/api/v1/flows", json={"flow": {
        "id": "s", "name": "S", "start": "g",
        "nodes": [{"id": "g", "kind": "approval", "prompt": "ok?", "label": "gate"}]}})
    from maverick.flow import execution, node_outcomes
    node_outcomes.record("s", "g", "approval", 1.0)          # approved
    node_outcomes.record("s", "g", "approval", 0.0)          # rejected
    node_outcomes.record("s", execution.TRIGGER_NODE, "trigger", 1.0)
    node_outcomes.record("s", execution.TRIGGER_NODE, "trigger", 1.0)
    body = c.get("/api/v1/flows/s/signals").json()
    assert body["nodes"]["g"]["mean"] == 0.5 and body["nodes"]["g"]["kind"] == "approval"
    assert body["nodes"]["g"]["label"] == "gate"
    assert body["trigger"]["n"] == 2 and body["trigger"]["mean"] == 1.0
    # the reserved trigger node is not listed among the flow's own nodes
    assert execution.TRIGGER_NODE not in body["nodes"]


def test_signals_unknown_flow_is_404(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert _client().get("/api/v1/flows/ghost/signals").status_code == 404


def test_impact_unchanged_node(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    r = c.get("/api/v1/flows/f/nodes/a/impact")
    assert r.status_code == 200 and r.json()["changed"] is False


def test_insights_surfaces_applied_changes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    c = _client()
    _save(c)
    assert c.post("/api/v1/flows/f/apply",
                  json={"node_id": "a", "to_kind": "action", "tool": "t"}).status_code == 200
    body = c.get("/api/v1/flows/insights").json()
    flow = next(fl for fl in body["flows"] if fl["flow_id"] == "f")
    assert flow["changes"] and flow["changes"][0]["to_kind"] == "action"


def _build_regressed_scenario():
    # A hardened (agent->action) node whose grounded outcomes then regressed.
    from maverick.flow import Flow, FlowNode, evolution_log, evolve, node_outcomes, store
    store.save_flow(Flow(id="f", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="agent", brief="summarize")}))
    for _ in range(4):
        node_outcomes.record("f", "a", "agent", 0.9)        # worked well as an agent
    saved = store.save_flow(evolve.apply_proposal(store.load_flow("f"), "a", "action", tool="t"))
    evolution_log.record_apply("f", "a", "agent", "action", saved.version, source="manual")
    for _ in range(6):
        node_outcomes.record("f", "a", "action", 0.2)        # regressed as an action


def test_evolve_pass_reverts_a_regression_when_enabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("MAVERICK_FLOWS_AUTO", "1")
    from maverick.flow import evolution_log, store
    from maverick_dashboard import automation_queue as aq
    _build_regressed_scenario()
    aq._handle_flow_evolve()
    assert store.load_flow("f").nodes["a"].kind == "agent"          # undone
    assert evolution_log.last_apply("f", "a")["source"] == "auto-revert"


def test_evolve_pass_is_a_noop_when_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.delenv("MAVERICK_FLOWS_AUTO", raising=False)
    from maverick.flow import store
    from maverick_dashboard import automation_queue as aq
    _build_regressed_scenario()
    aq._handle_flow_evolve()
    assert store.load_flow("f").nodes["a"].kind == "action"         # left alone


def test_notify_flow_pushes_when_enabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow
    from maverick.flow.store import FlowRun
    from maverick_dashboard import automation_queue as aq
    sent = []
    monkeypatch.setattr("maverick.notifications.notify",
                        lambda body, **k: (sent.append((body, k.get("priority"))), 1)[1])
    flow = Flow(id="f", name="Payout", start="a", nodes={}, notify=True)
    aq._notify_flow(flow, FlowRun(run_id="r", flow_id="f", status="paused_approval", prompt="ok?"))
    assert sent and "Payout" in sent[0][0] and sent[0][1] == "high"


def test_notify_flow_silent_when_disabled(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow
    from maverick.flow.store import FlowRun
    from maverick_dashboard import automation_queue as aq
    sent = []
    monkeypatch.setattr("maverick.notifications.notify", lambda body, **k: sent.append(body) or 1)
    aq._notify_flow(Flow(id="f", name="X", start="a", nodes={}, notify=False),
                    FlowRun(run_id="r", flow_id="f", status="completed"))
    assert sent == []                                       # opt-in: no notify by default


def test_dry_run_handler_completes_without_grounding(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow, FlowNode, node_outcomes, store
    from maverick_dashboard import automation_queue as aq
    store.save_flow(Flow(id="f", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="agent", brief="x")}))
    rid = store.new_run_id()
    store.save_run(store.FlowRun(run_id=rid, flow_id="f", status="queued"))

    class _Job:
        payload = {"flow_id": "f", "run_id": rid, "data": {}, "dry_run": True}
    aq._handle_flow_run(_Job())
    assert store.load_run(rid).status == "completed"
    assert node_outcomes.stats("f") == {}              # dry run grounded nothing


def test_run_api_redacts_secrets_in_data(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    rid = store.new_run_id()
    store.save_run(store.FlowRun(run_id=rid, flow_id="f", status="completed",
                                 data={"api_key": "sk-secret", "user": "ada"}))  # pragma: allowlist secret
    got = _client().get("/api/v1/flows/runs/" + rid).json()
    assert got["data"]["api_key"] == "***redacted***" and got["data"]["user"] == "ada"


def test_enqueue_flow_run_dedups_on_idem_key(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow, FlowNode, store
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    store.save_flow(Flow(id="f", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="agent", brief="x")}))
    r1 = aq.enqueue_flow_run("f", {"k": 1}, "", "evt:1")
    r2 = aq.enqueue_flow_run("f", {"k": 1}, "", "evt:1")     # same event -> dedup to r1
    r3 = aq.enqueue_flow_run("f", {"k": 1}, "", "evt:2")     # different event -> new run
    assert r1 == r2 and r3 != r1


def test_retry_reruns_from_original_inputs(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import Flow, FlowNode, store
    from maverick_dashboard import automation_queue as aq
    monkeypatch.setattr(aq, "_queue", None)
    store.save_flow(Flow(id="f", name="F", start="a",
                         nodes={"a": FlowNode(id="a", kind="agent", brief="x")}))
    rid = store.new_run_id()
    store.save_run(store.FlowRun(run_id=rid, flow_id="f", status="failed",
                                 input_data={"order": "A1"}, data={"order": "A1", "junk": "z"}))
    r = _client().post("/api/v1/flows/runs/" + rid + "/retry")
    assert r.status_code == 200 and r.json()["retried_from"] == rid
    new = store.load_run(r.json()["run_id"])
    assert new.run_id != rid and new.input_data == {"order": "A1"}   # original inputs, not mutated data


def test_retry_of_active_run_is_409(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    rid = store.new_run_id()
    store.save_run(store.FlowRun(run_id=rid, flow_id="f", status="paused_approval"))
    assert _client().post("/api/v1/flows/runs/" + rid + "/retry").status_code == 409


def test_tools_catalog_lists_connectors_with_metadata(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    r = _client().get("/api/v1/flows/tools")
    assert r.status_code == 200
    tools = r.json()["tools"]
    names = [t["name"] for t in tools]
    assert "slack_bot" in names and "github_issues" in names
    for t in tools:                       # every entry carries the picker's fields
        assert "description" in t and isinstance(t.get("params"), list)
