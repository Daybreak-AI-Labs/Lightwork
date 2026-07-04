"""Per-node error handling (retry + on_error routing) and sub-flow composition
(call a saved flow by id, with cycle protection)."""
from __future__ import annotations

from maverick.flow import Flow, FlowNode
from maverick.flow.runner import STATUS_COMPLETED, run_flow


def _flow(nodes, start="a"):
    return Flow(id="f", name="f", start=start, nodes={n.id: n for n in nodes})


class TestRetryAndOnError:
    def test_retry_recovers_a_transient_failure(self):
        calls = {"n": 0}

        def action(node, params, data):
            calls["n"] += 1
            return ("ok", 1.0) if calls["n"] >= 3 else ("ERROR", 0.0)
        f = _flow([FlowNode(id="a", kind="action", tool="t", retries=3, output="r")])
        res = run_flow(f, agent_fn=lambda *a: ("", None), action_fn=action)
        assert res.status == STATUS_COMPLETED and calls["n"] == 3 and res.data["r"] == "ok"

    def test_exhausted_retries_route_to_on_error(self):
        f = _flow([
            FlowNode(id="a", kind="action", tool="t", retries=1, on_error="b", next="c"),
            FlowNode(id="b", kind="agent", brief="handle", output="handled"),
            FlowNode(id="c", kind="agent", brief="normal", output="normal")])
        seen = []

        def agent(node, brief, data):
            seen.append(node.id)
            return ("x", 1.0)
        res = run_flow(f, agent_fn=agent, action_fn=lambda *a: ("ERROR", 0.0))
        assert "handled" in res.data and "normal" not in res.data     # took the error arm
        assert seen == ["b"]

    def test_no_on_error_continues_to_next(self):
        f = _flow([
            FlowNode(id="a", kind="action", tool="t", next="b"),
            FlowNode(id="b", kind="agent", brief="x", output="done")])
        res = run_flow(f, agent_fn=lambda *a: ("x", 1.0), action_fn=lambda *a: ("ERROR", 0.0))
        assert res.data.get("done") == "x"                            # legacy march-on kept


class TestSubflow:
    def _child(self):
        return Flow(id="child", name="child", start="s",
                    nodes={"s": FlowNode(id="s", kind="agent", brief="do", output="child_out")})

    def test_subflow_runs_and_threads_data_both_ways(self):
        child = self._child()
        parent = _flow([
            FlowNode(id="a", kind="subflow", flow_ref="child", next="b"),
            FlowNode(id="b", kind="agent", brief="after {{child_out}}", output="after")])
        got = {}

        def agent(node, brief, data):
            got[node.id] = brief
            return ("ran", 1.0)
        res = run_flow(parent, agent_fn=agent, action_fn=lambda *a: ("", None),
                       resolve_flow=lambda ref: child if ref == "child" else None)
        assert res.status == STATUS_COMPLETED
        assert res.data["child_out"] == "ran"                         # sub output flowed back
        assert got["b"] == "after ran"                                # parent read it

    def test_missing_subflow_routes_on_error(self):
        parent = _flow([
            FlowNode(id="a", kind="subflow", flow_ref="ghost", on_error="e", next="b"),
            FlowNode(id="e", kind="agent", brief="recover", output="recovered"),
            FlowNode(id="b", kind="agent", brief="x", output="normal")])
        res = run_flow(parent, agent_fn=lambda n, b, d: ("y", 1.0),
                       action_fn=lambda *a: ("", None), resolve_flow=lambda ref: None)
        assert "recovered" in res.data and "normal" not in res.data

    def test_cyclic_subflow_is_blocked_not_infinite(self):
        loop = Flow(id="loop", name="loop", start="a", nodes={
            "a": FlowNode(id="a", kind="subflow", flow_ref="loop", next="b"),
            "b": FlowNode(id="b", kind="agent", brief="end", output="end")})
        res = run_flow(loop, agent_fn=lambda n, bf, d: ("z", 1.0),
                       action_fn=lambda *a: ("", None),
                       resolve_flow=lambda ref: loop if ref == "loop" else None)
        assert res.status == STATUS_COMPLETED and res.data.get("end") == "z"   # cycle skipped


class TestValidation:
    def test_subflow_needs_flow_ref(self):
        assert any("flow_ref" in e for e in _flow([FlowNode(id="a", kind="subflow")]).validate())

    def test_on_error_must_point_to_a_real_node(self):
        f = _flow([FlowNode(id="a", kind="agent", brief="x", on_error="ghost")])
        assert any("on_error" in e for e in f.validate())
