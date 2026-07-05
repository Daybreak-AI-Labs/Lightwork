"""G2 feature-parity node kinds: setvar, subflow output, foreach runaway guard.
(switch / while / scope / foreach-concurrent are covered in test_flow_kinds.py.)"""
from __future__ import annotations

from maverick.flow.ir import Flow, FlowNode
from maverick.flow.runner import run_flow


def _flow(nodes, start="a", **kw):
    return Flow(id="f", name="f", start=start, nodes={n.id: n for n in nodes}, **kw)


def _ag(n, br, d):
    return (br, 1.0)


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


# ---- subflow output capture -------------------------------------------------

def test_subflow_output_captures_added_keys():
    sub = Flow(id="sub", name="sub", start="s",
               nodes={"s": FlowNode(id="s", kind="agent", brief="x", output="sub_result")})
    f = _flow([FlowNode(id="a", kind="subflow", flow_ref="sub", output="ret")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None),
                 resolve_flow=lambda fid: sub if fid == "sub" else None, data={})
    assert r.data["ret"] == {"sub_result": "x"}


# ---- foreach runaway guard --------------------------------------------------

def test_foreach_without_limit_caps_and_surfaces_truncation(monkeypatch):
    from maverick.flow import runner
    monkeypatch.setattr(runner, "_FOREACH_DEFAULT_CAP", 5)
    body = _flow([FlowNode(id="x", kind="agent", brief="{{item}}", output="r")], start="x")
    f = _flow([FlowNode(id="a", kind="foreach", items="rows", var="item", body=body, output="out")])
    r = run_flow(f, agent_fn=_ag, action_fn=lambda *a: ("", None),
                 data={"rows": list(range(20))})       # no explicit limit
    assert len(r.data["out"]) == 5                     # bounded to the default cap
    assert r.data["_foreach_truncated"] is True        # and surfaced, not silent

