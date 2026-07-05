"""Import fidelity: lowering an n8n workflow to a flow preserves IF/Filter
branches as real branch nodes instead of flattening the graph; a Power
Automate (WDL) definition preserves If/Foreach; every path emits a per-step
fidelity report."""
from __future__ import annotations

from maverick.automation_import import n8n, power_automate
from maverick.automation_import.to_flow import to_flow, to_flow_with_report
from maverick.flow.ir import NODE_ACTION, NODE_AGENT, NODE_BRANCH, NODE_FOREACH

_IF_WF = {
    "id": "7", "name": "Route by amount", "active": True,
    "nodes": [
        {"name": "Hook", "type": "n8n-nodes-base.webhook", "parameters": {}},
        {"name": "Check", "type": "n8n-nodes-base.if", "parameters": {"conditions": {
            "conditions": [{"leftValue": "{{amount}}", "operator": {"operation": "gt"},
                            "rightValue": "100"}]}}},
        {"name": "Escalate", "type": "n8n-nodes-base.slack", "parameters": {"operation": "post"}},
        {"name": "AutoApprove", "type": "n8n-nodes-base.noOp", "parameters": {}},
    ],
    "connections": {
        "Hook": {"main": [[{"node": "Check", "type": "main", "index": 0}]]},
        "Check": {"main": [[{"node": "Escalate", "type": "main", "index": 0}],
                           [{"node": "AutoApprove", "type": "main", "index": 0}]]},
    },
}


def test_if_node_becomes_a_branch_with_wired_arms():
    f = to_flow(n8n.translate(_IF_WF))
    assert f.validate() == []
    branch = next(n for n in f.nodes.values() if n.kind == NODE_BRANCH)
    assert branch.condition == "{{amount}} gt 100".replace("gt", ">")   # operator mapped
    # the true arm goes to the slack action, the false arm to the no-op agent
    assert f.nodes[branch.if_true].kind == NODE_ACTION
    assert f.nodes[branch.if_false].kind == NODE_AGENT
    assert f.start == branch.id                          # trigger -> the IF first


def test_linear_workflow_still_lowers_linearly():
    wf = {
        "id": "1", "name": "Linear", "active": True,
        "nodes": [
            {"name": "Hook", "type": "n8n-nodes-base.webhook", "parameters": {}},
            {"name": "A", "type": "n8n-nodes-base.slack", "parameters": {"operation": "post"}},
            {"name": "B", "type": "n8n-nodes-base.hubspot", "parameters": {"operation": "create"}},
        ],
        "connections": {
            "Hook": {"main": [[{"node": "A", "type": "main", "index": 0}]]},
            "A": {"main": [[{"node": "B", "type": "main", "index": 0}]]},
        },
    }
    f = to_flow(n8n.translate(wf))
    assert f.validate() == []
    assert not any(n.kind == NODE_BRANCH for n in f.nodes.values())
    assert f.nodes[f.start].next is not None            # chained


def test_graphless_import_falls_back_to_linear():
    # a Make/manual automation (no n8n raw graph) still lowers via the linear path
    from maverick.automation_import import ir as iir
    a = iir.ImportedAutomation("make", "9", "x", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
                               steps=[iir.ImportedStep(name="s1"), iir.ImportedStep(name="s2")])
    f = to_flow(a)
    assert f.validate() == [] and len(f.nodes) == 2


# ---- Power Automate (WDL) structural capture ---------------------------------

_PA_WF = {
    "name": "flow-1",
    "properties": {
        "displayName": "Escalate blocked orders",
        "definition": {
            "triggers": {"When_a_row_is_added": {
                "type": "OpenApiConnection",
                "inputs": {"host": {"apiId": ".../apis/shared_commondataservice",
                                    "operationId": "OnNewRow"}}}},
            "actions": {
                "Check_status": {
                    "type": "If",
                    "runAfter": {},
                    "expression": {"and": [
                        {"equals": ["@triggerBody()?['status']", "blocked"]}]},
                    "actions": {"Post_alert": {
                        "type": "OpenApiConnection", "runAfter": {},
                        "inputs": {"host": {"apiId": ".../apis/shared_slack",
                                            "operationId": "PostMessage"},
                                   "parameters": {"text": "blocked!"}}}},
                    "else": {"actions": {"Log_ok": {
                        "type": "Compose", "runAfter": {}, "inputs": "ok"}}},
                },
                "Wrap_up": {
                    "type": "OpenApiConnection",
                    "runAfter": {"Check_status": ["Succeeded"]},
                    "inputs": {"host": {"apiId": ".../apis/shared_teams",
                                        "operationId": "PostReply"},
                               "parameters": {"text": "done"}},
                },
            },
        },
    },
}


def test_power_automate_if_becomes_branch_with_rejoining_arms():
    f, report = to_flow_with_report(power_automate.translate(_PA_WF))
    assert f.validate() == []
    branch = next(n for n in f.nodes.values() if n.kind == NODE_BRANCH)
    assert branch.condition == "status == blocked"       # WDL equals -> ==
    assert f.start == branch.id
    then_node = f.nodes[branch.if_true]
    else_node = f.nodes[branch.if_false]
    assert then_node.kind == NODE_ACTION and then_node.tool == "slack"
    assert else_node.kind == NODE_AGENT                  # Compose -> agent approximation
    # both arms REJOIN at the action after the If (WDL's implicit join)
    wrap = next(n for n in f.nodes.values() if n.tool == "teams")
    assert then_node.next == wrap.id and else_node.next == wrap.id
    fidelity = {r["step"]: r["fidelity"] for r in report}
    assert fidelity["Check_status"] == "preserved"
    assert fidelity["Post_alert"] == "preserved"
    assert fidelity["Log_ok"] == "approximated"


def test_power_automate_foreach_becomes_a_loop_with_a_body():
    wf = {
        "properties": {"displayName": "Digest rows", "definition": {
            "triggers": {"Manual": {"type": "Request"}},
            "actions": {"For_each_row": {
                "type": "Foreach", "runAfter": {},
                "foreach": "@triggerBody()?['rows']",
                "actions": {"Summarize": {"type": "Compose", "runAfter": {},
                                          "inputs": "@item()"}},
            }},
        }},
    }
    f, report = to_flow_with_report(power_automate.translate(wf))
    assert f.validate() == []
    loop = next(n for n in f.nodes.values() if n.kind == NODE_FOREACH)
    assert loop.items == "rows"
    assert loop.body is not None and len(loop.body.nodes) == 1
    assert {r["fidelity"] for r in report if r["step"] == "For_each_row"} == {"preserved"}


def test_power_automate_switch_approximates_with_report():
    wf = {
        "properties": {"displayName": "Route", "definition": {
            "triggers": {"Manual": {"type": "Request"}},
            "actions": {"Route_by_type": {
                "type": "Switch", "runAfter": {}, "expression": "@triggerBody()?['t']",
                "cases": {}, "default": {}},
            },
        }},
    }
    f, report = to_flow_with_report(power_automate.translate(wf))
    assert f.validate() == []
    only = next(iter(f.nodes.values()))
    assert only.kind == NODE_AGENT                       # runs, but approximated
    r = next(r for r in report if r["step"] == "Route_by_type")
    assert r["fidelity"] == "approximated" and "Switch" in r["note"]


def test_every_lowering_path_emits_a_report():
    from maverick.automation_import import ir as iir
    a = iir.ImportedAutomation("make", "9", "x", iir.ImportedTrigger(kind=iir.TRIGGER_WEBHOOK),
                               steps=[iir.ImportedStep(name="s1")])
    _, report = to_flow_with_report(a)
    assert report and report[0]["fidelity"] in ("preserved", "approximated")
    _, n8n_report = to_flow_with_report(n8n.translate(_IF_WF))
    assert any(r["kind"] == NODE_BRANCH and r["fidelity"] == "preserved" for r in n8n_report)
