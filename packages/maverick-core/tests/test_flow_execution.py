"""Flow lowering (import -> Flow), the live execution driver + resumable state,
per-node outcomes, and the self-rewrite proposal pass."""
from __future__ import annotations

from maverick.automation_import import ir as iir
from maverick.automation_import.to_flow import to_flow
from maverick.flow import evolve, execution, node_outcomes
from maverick.flow.ir import NODE_ACTION, NODE_AGENT, Flow, FlowNode


def _patch_home(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.paths.data_dir", lambda *p, **k: tmp_path.joinpath(*p))


# ---- lowering ---------------------------------------------------------------

class TestLowering:
    def test_single_step_lowers_to_single_agent(self):
        a = iir.ImportedAutomation(
            "n8n", "1", "One thing", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
            steps=[iir.ImportedStep(name="do it", description="carry out the thing")])
        f = to_flow(a)
        assert len(f.nodes) == 1
        assert f.nodes["n0"].kind == NODE_AGENT
        assert f.validate() == []

    def test_clean_tool_step_becomes_action(self):
        a = iir.ImportedAutomation(
            "n8n", "2", "notify", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
            steps=[iir.ImportedStep(name="Post", app="slack", operation="post",
                                    params={"text": "hi"}, tools_hint=["slack"])])
        f = to_flow(a)
        assert f.nodes["n0"].kind == NODE_ACTION and f.nodes["n0"].tool == "slack"

    def test_ambiguous_step_becomes_agent(self):
        a = iir.ImportedAutomation(
            "n8n", "3", "x", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
            steps=[iir.ImportedStep(name="Decide", tools_hint=["a", "b"])])  # >1 hint
        f = to_flow(a)
        assert f.nodes["n0"].kind == NODE_AGENT

    def test_empty_import_still_yields_runnable_flow(self):
        a = iir.ImportedAutomation("n8n", "4", "Empty", iir.ImportedTrigger(), steps=[])
        f = to_flow(a)
        assert f.validate() == [] and f.nodes["n0"].kind == NODE_AGENT

    def test_multi_step_chains_in_order(self):
        a = iir.ImportedAutomation(
            "n8n", "5", "chain", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
            steps=[iir.ImportedStep(name="s1"), iir.ImportedStep(name="s2"),
                   iir.ImportedStep(name="s3")])
        f = to_flow(a)
        assert f.start == "n0"
        assert f.nodes["n0"].next == "n1" and f.nodes["n1"].next == "n2"
        assert f.nodes["n2"].next is None


# ---- live execution driver + resumable state --------------------------------

class TestExecution:
    def test_execute_persists_completed_run(self, tmp_path, monkeypatch):
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="f1", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind=NODE_AGENT, brief="do {{x}}", output="r")})
        run = execution.execute(
            f, agent_runner=lambda brief, d: (f"ran:{brief}", 0.8),
            action_runner=lambda t, p, d: ("", None), data={"x": "1"}, owner="user:a")
        assert run.status == "completed" and run.data["r"] == "ran:do 1"
        from maverick.flow import store
        assert store.load_run(run.run_id).owner == "user:a"

    def test_execute_records_node_outcomes(self, tmp_path, monkeypatch):
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="fX", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind=NODE_AGENT, brief="b")})
        execution.execute(f, agent_runner=lambda brief, d: ("ok", 0.9),
                          action_runner=lambda t, p, d: ("", None))
        assert node_outcomes.stats("fX")["a"] == {"n": 1, "mean": 0.9, "kind": NODE_AGENT}

    def test_execute_records_per_node_run_trace(self, tmp_path, monkeypatch):
        # The run persists a per-node {status, outcome} trace for the designer's
        # live-run overlay.
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="fT", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind=NODE_AGENT, brief="ok step", next="b"),
            "b": FlowNode(id="b", kind=NODE_ACTION, tool="t", next=None)})
        run = execution.execute(
            f, agent_runner=lambda brief, d: ("ok", 1.0),
            action_runner=lambda t, p, d: ("ERROR: boom", 0.0))
        assert run.nodes["a"] == {"status": "done", "outcome": 1.0}
        assert run.nodes["b"] == {"status": "done", "outcome": 0.0}
        from maverick.flow import store
        assert store.load_run(run.run_id).nodes["b"]["outcome"] == 0.0   # persisted

    def test_run_trace_survives_pause_and_resume(self, tmp_path, monkeypatch):
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="fTR", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind=NODE_AGENT, brief="first", output="r", next="b"),
            "b": FlowNode(id="b", kind="approval", prompt="ok?", next="c"),
            "c": FlowNode(id="c", kind=NODE_AGENT, brief="last")})
        paused = execution.execute(f, agent_runner=lambda b, d: ("x", 1.0),
                                   action_runner=lambda *a: ("", None))
        assert paused.status == "paused_approval" and paused.nodes["a"]["outcome"] == 1.0
        resumed = execution.execute(f, agent_runner=lambda b, d: ("x", 1.0),
                                    action_runner=lambda *a: ("", None),
                                    resume_run_id=paused.run_id, decision="approved")
        # the pre-pause node's trace is preserved alongside the post-resume nodes
        assert resumed.nodes["a"]["outcome"] == 1.0 and resumed.nodes["c"]["outcome"] == 1.0

    def test_pause_resume_across_persisted_state(self, tmp_path, monkeypatch):
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="f2", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind="approval", prompt="ok?", next="b"),
            "b": FlowNode(id="b", kind=NODE_AGENT, brief="go", output="done")})
        def ag(brief, d):
            return ("ran", 1.0)
        paused = execution.execute(f, agent_runner=ag, action_runner=lambda *a: ("", None),
                                   data={"n": 1})
        assert paused.status == "paused_approval" and paused.cursor == "a"
        resumed = execution.execute(f, agent_runner=ag, action_runner=lambda *a: ("", None),
                                    resume_run_id=paused.run_id, decision="approved")
        assert resumed.status == "completed" and resumed.data["done"] == "ran"
        assert resumed.run_id == paused.run_id          # same run, resumed in place

    def test_resume_merges_free_form_human_inputs(self, tmp_path, monkeypatch):
        # An approval can also COLLECT data: inputs given at resume merge into the
        # run data, so a downstream node sees the human's correction.
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="fi", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind="approval", prompt="fix?", next="b"),
            "b": FlowNode(id="b", kind=NODE_AGENT, brief="use {{corrected}}", output="r")})
        got = {}

        def ag(brief, d):
            got["brief"] = brief
            return ("done", 1.0)
        paused = execution.execute(f, agent_runner=ag, action_runner=lambda *a: ("", None), data={})
        assert paused.status == "paused_approval"
        resumed = execution.execute(f, agent_runner=ag, action_runner=lambda *a: ("", None),
                                    resume_run_id=paused.run_id, decision="approved",
                                    inputs={"corrected": "the fixed value"})
        assert got["brief"] == "use the fixed value"        # human input threaded in
        assert resumed.data["corrected"] == "the fixed value"

    def test_resume_of_completed_run_is_rejected(self, tmp_path, monkeypatch):
        # Guard against re-running a finished flow (duplicate side effects) or
        # running a node a human rejected.
        import pytest
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="fc", name="f", start="a",
                 nodes={"a": FlowNode(id="a", kind=NODE_AGENT, brief="b")})
        run = execution.execute(f, agent_runner=lambda b, d: ("ok", 1.0),
                                action_runner=lambda *a: ("", None))
        assert run.status == "completed"
        with pytest.raises(execution.FlowNotResumable):
            execution.execute(f, agent_runner=lambda b, d: ("ok", 1.0),
                              action_runner=lambda *a: ("", None), resume_run_id=run.run_id)

    def test_resume_rejected_ends_run(self, tmp_path, monkeypatch):
        _patch_home(tmp_path, monkeypatch)
        f = Flow(id="f3", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind="approval", prompt="ok?", next="b"),
            "b": FlowNode(id="b", kind=NODE_AGENT, brief="go", output="done")})
        paused = execution.execute(f, agent_runner=lambda *a: ("", None),
                                   action_runner=lambda *a: ("", None))
        rej = execution.execute(f, agent_runner=lambda *a: ("", None),
                                action_runner=lambda *a: ("", None),
                                resume_run_id=paused.run_id, decision="rejected")
        assert rej.status == "rejected" and "done" not in rej.data


# ---- self-rewrite proposals -------------------------------------------------

class TestEvolve:
    def _flow(self):
        return Flow(id="fp", name="f", start="a", nodes={
            "a": FlowNode(id="a", kind=NODE_AGENT, brief="reliable", next="b"),
            "b": FlowNode(id="b", kind=NODE_ACTION, tool="flaky", next="c"),
            "c": FlowNode(id="c", kind=NODE_AGENT, brief="middling")})

    def test_proposes_hardening_reliable_agent_and_softening_flaky_action(self):
        stats = {
            "a": {"n": 20, "mean": 0.95, "kind": NODE_AGENT},   # -> harden to action
            "b": {"n": 15, "mean": 0.3, "kind": NODE_ACTION},   # -> soften to agent
            "c": {"n": 20, "mean": 0.7, "kind": NODE_AGENT},    # middling -> no proposal
        }
        props = {p.node_id: p for p in evolve.propose(self._flow(), stats=stats)}
        assert props["a"].to_kind == NODE_ACTION
        assert props["b"].to_kind == NODE_AGENT
        assert "c" not in props

    def test_min_support_gates_out_small_samples(self):
        stats = {"a": {"n": 3, "mean": 1.0, "kind": NODE_AGENT}}
        assert evolve.propose(self._flow(), stats=stats) == []

    def test_maybe_propose_is_gated_off_by_default(self, monkeypatch):
        monkeypatch.delenv("MAVERICK_FLOWS", raising=False)
        monkeypatch.setattr("maverick.config.get_flows", lambda: {"enable": False})
        assert evolve.maybe_propose(self._flow()) == []
