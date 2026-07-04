"""Import fidelity: lowering an n8n workflow to a flow preserves IF/Filter
branches as real branch nodes instead of flattening the graph."""
from __future__ import annotations

from maverick.automation_import import n8n
from maverick.automation_import.to_flow import to_flow
from maverick.flow.ir import NODE_ACTION, NODE_AGENT, NODE_BRANCH

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
