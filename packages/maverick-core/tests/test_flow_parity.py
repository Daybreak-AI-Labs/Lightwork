"""G2 feature-parity node kinds: switch, setvar, foreach concurrency, subflow output."""
from __future__ import annotations

import threading

from maverick.flow.ir import Flow, FlowNode
from maverick.flow.runner import run_flow


def _flow(nodes, start="a", **kw):
    return Flow(id="f", name="f", start=start, nodes={n.id: n for n in nodes}, **kw)


def _ag(n, br, d):
    return (br, 1.0)


# ---- switch -----------------------------------------------------------------

def test_switch_routes_to_first_matching_case():
    f = _flow([
        FlowNode(id="a", kind="switch", cases=[
            {"condition": "tier == gold", "goto": "g"},
            {"condition": "tier == silver", "goto": "s"}], next="d"),
        FlowNode(id="g", kind="agent", brief="gold", output="r"),
        FlowNode(id="s", kind="agent", brief="silver", output="r"),
        FlowNode(id="d", kind="agent", brief="default", output="r")])
    assert run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None),
                    data={"tier": "silver"}).data["r"] == "silver"


def test_switch_falls_through_to_default_next():
    f = _flow([
        FlowNode(id="a", kind="switch", cases=[{"condition": "x == 1", "goto": "g"}], next="d"),
        FlowNode(id="g", kind="agent", brief="one", output="r"),
        FlowNode(id="d", kind="agent", brief="default", output="r")])
    assert run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None),
                    data={"x": 9}).data["r"] == "default"


def test_switch_validates_cases_and_gotos():
    f = _flow([FlowNode(id="a", kind="switch", cases=[{"condition": "x == 1", "goto": "ghost"}])])
    errs = f.validate()
    assert any("unknown node 'ghost'" in e for e in errs)


# ---- setvar + a while-loop via setvar/branch/cycle --------------------------

def test_setvar_composes_and_counts():
    f = _flow([
        FlowNode(id="a", kind="setvar", assignments={"greeting": "hi {{name}}", "n": "{{add(count,1)}}"},
                 next="b"),
        FlowNode(id="b", kind="agent", brief="{{greeting}}|{{n}}", output="r")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None), data={"name": "sam", "count": 4})
    assert r.data["r"] == "hi sam|5"


def test_while_loop_via_setvar_and_branch():
    # count from 0, increment via setvar, branch back until count == 3
    f = _flow([
        FlowNode(id="a", kind="setvar", assignments={"count": "{{add(count,1)}}"}, next="b"),
        FlowNode(id="b", kind="branch", condition="count < 3", if_true="a", if_false="done"),
        FlowNode(id="done", kind="agent", brief="{{count}}", output="r")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None), data={"count": 0})
    assert r.data["r"] == "3"


# ---- foreach concurrency ----------------------------------------------------

def test_foreach_concurrency_runs_iterations_in_parallel():
    barrier = threading.Barrier(3, timeout=5.0)

    def rendezvous(n, br, d):
        barrier.wait()                 # all 3 iterations must be in flight at once
        return (br, 1.0)
    body = _flow([FlowNode(id="x", kind="agent", brief="{{item}}", output="r")], start="x")
    f = _flow([FlowNode(id="a", kind="foreach", items="rows", var="item", body=body,
                        concurrency=3, output="out")])
    r = run_flow(f, agent_fn=rendezvous, action_fn=lambda *a: ("", None),
                 data={"rows": [1, 2, 3]})
    assert r.status == "completed"
    assert sorted(row["r"] for row in r.data["out"]) == ["1", "2", "3"]


# ---- subflow output capture -------------------------------------------------

def test_subflow_output_captures_added_keys():
    sub = Flow(id="sub", name="sub", start="s",
               nodes={"s": FlowNode(id="s", kind="agent", brief="x", output="sub_result")})
    f = _flow([FlowNode(id="a", kind="subflow", flow_ref="sub", output="ret")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None),
                 resolve_flow=lambda fid: sub if fid == "sub" else None, data={})
    assert r.data["ret"] == {"sub_result": "x"}


# ---- try/catch scope --------------------------------------------------------

def test_try_catches_a_body_failure_and_routes_to_on_error():
    body = _flow([FlowNode(id="x", kind="action", tool="boom", output="r")], start="x")
    f = _flow([
        FlowNode(id="a", kind="try", body=body, on_error="rescue", next="ok"),
        FlowNode(id="rescue", kind="agent", brief="recovered", output="path"),
        FlowNode(id="ok", kind="agent", brief="fine", output="path")])

    def boom(node, params, data):
        raise RuntimeError("kaboom")
    r = run_flow(f, agent_fn=_ag, action_fn=boom, data={})
    assert r.status == "completed"
    assert r.data["path"] == "recovered"          # the catch handler ran
    assert "kaboom" in r.data["_error"]


def test_try_passes_through_on_success():
    body = _flow([FlowNode(id="x", kind="agent", brief="work", output="r")], start="x")
    f = _flow([FlowNode(id="a", kind="try", body=body, on_error="rescue", next="ok"),
               FlowNode(id="rescue", kind="agent", brief="recovered", output="path"),
               FlowNode(id="ok", kind="agent", brief="fine", output="path")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None), data={})
    assert r.data["path"] == "fine"


def test_try_requires_a_body():
    f = _flow([FlowNode(id="a", kind="try", on_error="x"),
               FlowNode(id="x", kind="agent", brief="h")])
    assert any("try node 'a' has no body" in e for e in f.validate())

