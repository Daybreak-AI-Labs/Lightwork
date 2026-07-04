"""Lower an imported automation to a :class:`~maverick.flow.ir.Flow`.

The prose ``render()`` collapses a workflow into one agentic goal; this instead
preserves each step as its own typed node, chained in order:

* a step that maps cleanly to a single tool becomes a deterministic ``action``
  node (auditable, replayable, subject to that tool's own confirm gate) with its
  params carried through;
* every other step becomes an ``agent`` node whose brief is the step's
  instruction (the "figure it out" node).

That is the "do both" migration: the graph keeps the workflow's shape while any
step can still be a full agentic goal. A one-step automation lowers to a single
``agent`` node -- i.e. exactly what ``render()`` + a template already produce, so
this is a superset, not a replacement. Source-graph branch/loop capture (turning
an ``IF``/``Switch`` node into a ``branch``) builds on this and is the next
increment; today's importers flatten to an ordered step list, so the lowered flow
is a faithful linear chain of that list.
"""
from __future__ import annotations

from ..flow.ir import NODE_ACTION, NODE_AGENT, NODE_BRANCH, Flow, FlowNode
from .ir import ImportedAutomation, ImportedStep, _safe_line, safe_params


def _node_for_step(step: ImportedStep, node_id: str, nxt: str | None) -> FlowNode:
    tool = step.tools_hint[0] if len(step.tools_hint) == 1 else ""
    label = step.name or step.operation or tool or "step"
    if tool and step.operation:
        # A clean single-tool mapping -> a deterministic action node. Params are
        # the (secret-redacted) step inputs, so they aren't dumped raw.
        return FlowNode(id=node_id, kind=NODE_ACTION, tool=tool,
                        params=safe_params(step.params), next=nxt,
                        output=f"{node_id}_out", label=label)
    brief_bits = [step.name or step.operation or "Carry out this step"]
    if step.description and step.description != step.name:
        brief_bits.append(step.description)
    if step.app:
        brief_bits.append(f"(using {step.app})")
    return FlowNode(id=node_id, kind=NODE_AGENT, brief=" ".join(brief_bits).strip(),
                    next=nxt, output=f"{node_id}_out", label=label)


# n8n operator names -> the flow condition-evaluator's operators.
_N8N_OPS = {"gt": ">", "larger": ">", "gte": ">=", "largerEqual": ">=",
            "lt": "<", "smaller": "<", "lte": "<=", "smallerEqual": "<=",
            "equal": "==", "equals": "==", "notEqual": "!=", "notEquals": "!=",
            "contains": "contains"}


def _n8n_condition(node: dict) -> str:
    """Best-effort condition text from an n8n IF node (v1 or v2 param shapes),
    mapped to the flow evaluator's ``<key> <op> <value>`` syntax. Structure is
    what we preserve; the operator refines the exact test in the designer."""
    params = node.get("parameters") if isinstance(node.get("parameters"), dict) else {}
    conds = params.get("conditions")
    if isinstance(conds, dict):
        # v2: {"conditions": {"conditions": [{"leftValue","operator","rightValue"}]}}
        inner = conds.get("conditions")
        if isinstance(inner, list) and inner and isinstance(inner[0], dict):
            c = inner[0]
            op = c.get("operator")
            raw_op = op.get("operation") if isinstance(op, dict) else op
            left = _safe_line(c.get("leftValue", ""), max_chars=40)
            right = _safe_line(c.get("rightValue", ""), max_chars=40)
            return f"{left} {_N8N_OPS.get(str(raw_op), '==')} {right}".strip()
        # v1: {"conditions": {"number":[{value1,operation,value2}], "string":[...]}}
        for arr in conds.values():
            if isinstance(arr, list) and arr and isinstance(arr[0], dict):
                c = arr[0]
                left = _safe_line(c.get("value1", ""), max_chars=40)
                right = _safe_line(c.get("value2", ""), max_chars=40)
                return f"{left} {_N8N_OPS.get(str(c.get('operation')), '==')} {right}".strip()
    return "result == true"


def _n8n_targets(connections: dict, node_name: str, idmap: dict):
    """(true-target, false-target) flow ids for an n8n node's first two output
    groups (IF: group 0 = true, group 1 = false)."""
    groups = (connections.get(node_name, {}) or {}).get("main", []) or []

    def first(gi):
        if gi < len(groups):
            for edge in groups[gi] or []:
                tgt = edge.get("node") if isinstance(edge, dict) else None
                if tgt in idmap:
                    return idmap[tgt]
        return None
    return first(0), first(1)


def _n8n_to_flow(automation: ImportedAutomation) -> Flow | None:
    """Lower an n8n workflow's raw graph to a Flow, capturing IF/Filter nodes as
    real ``branch`` nodes (true/false wired to their downstream nodes) instead of
    flattening them. Returns ``None`` if the raw graph isn't usable (caller falls
    back to the linear lowering)."""
    from .n8n import _is_trigger_node, _short_type, _step_from_node
    raw = automation.raw if isinstance(automation.raw, dict) else {}
    nodes = [n for n in (raw.get("nodes") or []) if isinstance(n, dict) and n.get("name")]
    connections = raw.get("connections") if isinstance(raw.get("connections"), dict) else {}
    if not nodes or not connections:
        return None
    by_name = {n["name"]: n for n in nodes}
    action_names = [nm for nm, n in by_name.items() if not _is_trigger_node(n)]
    if not action_names:
        return None
    idmap = {nm: f"n{i}" for i, nm in enumerate(action_names)}
    fnodes: dict[str, FlowNode] = {}
    for nm in action_names:
        node, nid = by_name[nm], idmap[nm]
        t_tgt, f_tgt = _n8n_targets(connections, nm, idmap)
        if _short_type(node.get("type", "")) in ("if", "filter"):
            fnodes[nid] = FlowNode(id=nid, kind=NODE_BRANCH, condition=_n8n_condition(node),
                                   if_true=t_tgt, if_false=f_tgt, label=nm)
        else:
            fnodes[nid] = _node_for_step(_step_from_node(node), nid, t_tgt)
    trigger_node = next((n for n in nodes if _is_trigger_node(n)), None)
    start = None
    if trigger_node:
        start = _n8n_targets(connections, trigger_node["name"], idmap)[0]
    start = start or idmap[action_names[0]]
    flow = Flow(id=automation.template_name(), name=automation.name, start=start, nodes=fnodes)
    return flow if not flow.validate() else None


def to_flow(automation: ImportedAutomation) -> Flow:
    """Build a :class:`Flow` from an imported automation. For n8n, capture the
    source graph's IF/Filter branches; otherwise a linear chain (one node/step)."""
    if automation.source == "n8n":
        graph = _n8n_to_flow(automation)
        if graph is not None:
            return graph
    steps = automation.steps
    if not steps:
        # No extractable actions -> a single agent node that runs the automation's
        # intent, so an empty import still yields a runnable (template-equivalent)
        # flow rather than nothing.
        brief = automation.description or automation.name or "Run the imported automation"
        n = FlowNode(id="n0", kind=NODE_AGENT, brief=brief, label=automation.name)
        return Flow(id=automation.template_name(), name=automation.name,
                    start="n0", nodes={"n0": n})
    nodes: dict[str, FlowNode] = {}
    for i, step in enumerate(steps):
        node_id = f"n{i}"
        nxt = f"n{i + 1}" if i + 1 < len(steps) else None
        nodes[node_id] = _node_for_step(step, node_id, nxt)
    return Flow(id=automation.template_name(), name=automation.name,
                start="n0", nodes=nodes)


__all__ = ["to_flow"]
