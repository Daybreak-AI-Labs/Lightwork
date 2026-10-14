"""Native process provenance without external providers or connector effects."""
from __future__ import annotations

import pytest
from maverick import audit, world_model
from maverick.flow import execution, store
from maverick.flow.ir import Flow, FlowNode


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr("maverick.config.get_intent_value", lambda: {"enable": True})
    events = []

    def receipt(kind, **payload):
        events.append({"kind": kind, **payload})
        return True

    monkeypatch.setattr(audit, "audit_event", receipt)
    return world_model.WorldModel(tmp_path / "world.db"), events


def _flow(kind="agent", **node_kwargs):
    return Flow(id="rfp", name="RFP work", revision="revision-one", start="a", nodes={
        "a": FlowNode(id="a", kind=kind, brief="Draft an RFP", tool="web_search", **node_kwargs),
    })


def _actions(monkeypatch, result="done"):
    from maverick.tools import Tool, ToolRegistry

    registry = ToolRegistry()
    registry.register(Tool(name="web_search", description="local fixture", input_schema={"type": "object"},
                           fn=lambda _args: result))
    monkeypatch.setattr("maverick.tools.base_registry", lambda *a, **k: registry)
    monkeypatch.setattr("maverick.tool_authz.authorize", lambda *a, **k: None)
    return execution.default_action_runner(None, user_id="alice")


def test_native_goals_have_exact_durable_run_origin_before_dispatch(local, monkeypatch):
    world, events = local
    origins_at_dispatch = []

    def complete(goal_id, **kwargs):
        origins_at_dispatch.append([g.id for g in world.goals_for_origin("flow", "run-one")])
        world.set_goal_status(goal_id, "done", result="done")
        kwargs["usage_observer"](0.04)
        return "done"

    monkeypatch.setattr("maverick.runner.run_goal_in_thread", complete)
    run = execution.execute(_flow(), run_id="run-one", owner="user:alice",
                            agent_runner=execution.default_agent_runner(world),
                            action_runner=lambda *a: ("", None))
    refs = run.nodes["a"]["native_goal_ids"]
    assert origins_at_dispatch == [refs]
    assert run.cost_dollars == pytest.approx(0.04)
    terminal = [e for e in events if e["kind"] == "flow_execution"]
    assert len(terminal) == 1
    assert terminal[0]["flow_run_id"] == "run-one"
    assert terminal[0]["definition_revision"] == "revision-one"
    assert terminal[0]["native_goal_ids"] == refs
    assert terminal[0]["status"] == "completed"
    assert execution._active_run.get() == ""
    assert execution._active_node.get() == ("", "", "", "")


def test_actions_have_content_free_receipts_grouped_once_and_redelivery_is_noop(local, monkeypatch):
    _world, events = local
    flow = _flow("action", params={"private": "do not log this"}, next="b")
    flow.nodes["b"] = FlowNode(id="b", kind="action", tool="web_search")
    runner = _actions(monkeypatch, "private connector output")
    run = execution.execute(flow, run_id="run-two", agent_runner=lambda *a: ("", None), action_runner=runner)
    terminal = [e for e in events if e["kind"] == "flow_execution"]
    tools = [e for e in events if e["kind"] == "tool_result"]
    assert len(terminal) == 1 and len(tools) == 2
    assert {e["flow_node_id"] for e in tools} == {"a", "b"}
    assert {e["flow_run_id"] for e in tools} == {"run-two"}
    assert set(terminal[0]["native_action_call_ids"]) == {e["call_id"] for e in tools}
    assert "do not log this" not in str(events) and "private connector output" not in str(events)
    assert run.status == "completed"
    execution.execute(flow, run_id="run-two", agent_runner=lambda *a: ("", None), action_runner=runner)
    assert len([e for e in events if e["kind"] == "flow_execution"]) == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_mock_or_dry_execution_never_creates_native_evidence(local, dry_run):
    _world, events = local
    agent, action = execution.sandbox_runners()
    run = execution.execute(_flow(), run_id="mock-run", agent_runner=agent, action_runner=action,
                            record_outcomes=not dry_run)
    assert run.status == "completed"
    assert not any(e["kind"] == "flow_execution" for e in events)


def test_missing_terminal_action_receipt_never_claims_completed_work(local, monkeypatch):
    _world, events = local
    monkeypatch.setattr(audit, "audit_event", lambda *a, **k: False)
    run = execution.execute(_flow("action"), run_id="missing-receipt", agent_runner=lambda *a: ("", None),
                            action_runner=_actions(monkeypatch))
    assert run.status == "indeterminate"
    assert not run.nodes["a"].get("native_action_call_ids")
    assert not events


def test_failed_lifecycle_receipt_does_not_replay_or_fail_completed_action(local, monkeypatch):
    _world, events = local
    original = audit.audit_event

    def fail_terminal(kind, **payload):
        if kind == "flow_execution":
            raise OSError("unavailable")
        return original(kind, **payload)

    monkeypatch.setattr(audit, "audit_event", fail_terminal)
    run = execution.execute(_flow("action"), run_id="terminal-unavailable", agent_runner=lambda *a: ("", None),
                            action_runner=_actions(monkeypatch))
    assert run.status == "completed"
    assert store.load_run(run.run_id).status == "completed"
    assert len([e for e in events if e["kind"] == "tool_result"]) == 1
    assert not any(e["kind"] == "flow_execution" for e in events)


def test_native_references_survive_a_delay_and_resume(local, monkeypatch):
    _world, events = local
    flow = _flow("action", next="wait")
    flow.nodes["wait"] = FlowNode(id="wait", kind="delay", seconds=1, next="b")
    flow.nodes["b"] = FlowNode(id="b", kind="action", tool="web_search")
    runner = _actions(monkeypatch)
    paused = execution.execute(flow, run_id="resumed-run", agent_runner=lambda *a: ("", None), action_runner=runner)
    first_refs = list(paused.nodes["a"]["native_action_call_ids"])
    resumed = execution.execute(flow, resume_run_id=paused.run_id,
                                agent_runner=lambda *a: ("", None), action_runner=runner)
    terminal = [e for e in events if e["kind"] == "flow_execution"]
    assert resumed.status == "completed" and len(terminal) == 2
    assert first_refs[0] in terminal[-1]["native_action_call_ids"]
    assert len(terminal[-1]["native_action_call_ids"]) == 2


def test_run_context_restored_when_agent_raises():
    def refused(*_args):
        assert execution._active_run.get() == "inner"
        raise ValueError("stop")

    token = execution._active_run.set("outer")
    try:
        with pytest.raises(ValueError, match="stop"):
            execution._call_agent(refused, "work", {}, 0, flow_run_id="inner")
        assert execution._active_run.get() == "outer"
    finally:
        execution._active_run.reset(token)
