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
this is a superset, not a replacement.

Source-graph branch/loop capture: n8n ``IF``/``Filter`` nodes and Power Automate
(WDL) ``If``/``Foreach`` actions are preserved as real ``branch``/``foreach``
nodes with their arms/bodies wired; other importers (and other WDL action types
-- ``Switch``/``Until``/``Scope``) still flatten, with each such step
approximated as an ``agent`` node that carries the original step's intent.
:func:`to_flow_with_report` says exactly which happened per step (the migration
fidelity report), so an import never silently pretends to be lossless.
"""
from __future__ import annotations

import re

from ..flow.ir import NODE_ACTION, NODE_AGENT, NODE_BRANCH, NODE_FOREACH, Flow, FlowNode
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


# ---- Power Automate (WDL) graph capture --------------------------------------

# WDL comparison functions -> the flow condition-evaluator's operators.
_WDL_OPS = {"equals": "==", "notEquals": "!=", "greater": ">", "greaterOrEquals": ">=",
            "less": "<", "lessOrEquals": "<=", "contains": "contains"}


def _pa_operand(v) -> str:
    """A WDL operand (`@triggerBody()?['status']`, `@outputs('X')?['body/id']`,
    a literal) reduced to a flow-data key / literal the condition grammar takes."""
    s = str(v).strip().strip("'\"")
    fields = re.findall(r"\['([^']+)'\]", s)
    if fields:
        return _safe_line(fields[-1].rsplit("/", 1)[-1], max_chars=40)
    s = s.removeprefix("@")
    return _safe_line(s, max_chars=40)


def _pa_condition(expr) -> str:
    """Best-effort ``<key> <op> <value>`` from a WDL If expression (dict or
    ``@fn(a, b)`` string form). Structure is what we preserve; the operator text
    refines the exact test in the designer."""
    if isinstance(expr, dict):
        if "not" in expr and isinstance(expr["not"], dict):
            inner = expr["not"]
            for k, args in inner.items():
                if k in _WDL_OPS and isinstance(args, list) and len(args) >= 2:
                    op = "!=" if _WDL_OPS[k] == "==" else _WDL_OPS[k]
                    return f"{_pa_operand(args[0])} {op} {_pa_operand(args[1])}"
        for k in ("and", "or"):
            arms = expr.get(k)
            if isinstance(arms, list) and arms and isinstance(arms[0], dict):
                return _pa_condition(arms[0])   # first clause carries the shape
        for k, args in expr.items():
            if k in _WDL_OPS and isinstance(args, list) and len(args) >= 2:
                return f"{_pa_operand(args[0])} {_WDL_OPS[k]} {_pa_operand(args[1])}"
    if isinstance(expr, str):
        m = re.match(r"@?(\w+)\((.+)\)\s*$", expr.strip())
        if m and m.group(1) in _WDL_OPS and "," in m.group(2):
            left, right = m.group(2).split(",", 1)
            return f"{_pa_operand(left)} {_WDL_OPS[m.group(1)]} {_pa_operand(right)}"
    return "result == true"


def _pa_wire_tail(nodes: dict[str, FlowNode], nxt: str | None) -> None:
    """Point an arm's loose ends at the If's successor, so both arms rejoin the
    chain after the branch (WDL's implicit join)."""
    if not nxt:
        return
    for n in nodes.values():
        if n.next is None:
            n.next = nxt


def _pa_lower(actions: dict, prefix: str,
              report: list[dict]) -> tuple[dict[str, FlowNode], str | None]:
    """Lower one WDL action dict (one nesting level) into chained flow nodes.
    Returns ``(nodes, start_id)``. ``If`` arms live in the SAME node dict (wired
    to rejoin after the branch); a ``Foreach`` body becomes a nested Flow."""
    from .power_automate import _step, _toposort_actions
    order = [n for n in _toposort_actions(actions) if isinstance(actions.get(n), dict)]
    ids = {name: f"{prefix}{i}" for i, name in enumerate(order)}
    nodes: dict[str, FlowNode] = {}
    for i, name in enumerate(order):
        spec = actions[name]
        nid = ids[name]
        nxt = ids[order[i + 1]] if i + 1 < len(order) else None
        atype = str(spec.get("type") or "")
        label = name.replace("_", " ").strip()
        if atype == "If":
            then_d = spec.get("actions") if isinstance(spec.get("actions"), dict) else {}
            else_env = spec.get("else") if isinstance(spec.get("else"), dict) else {}
            else_d = else_env.get("actions") if isinstance(else_env.get("actions"), dict) else {}
            t_nodes, t_start = _pa_lower(then_d, nid + "t", report)
            f_nodes, f_start = _pa_lower(else_d, nid + "f", report)
            _pa_wire_tail(t_nodes, nxt)
            _pa_wire_tail(f_nodes, nxt)
            nodes.update(t_nodes)
            nodes.update(f_nodes)
            nodes[nid] = FlowNode(id=nid, kind=NODE_BRANCH, condition=_pa_condition(spec.get("expression")),
                                  if_true=t_start or nxt, if_false=f_start or nxt,
                                  next=nxt, label=label)
            report.append({"step": name, "node": nid, "kind": NODE_BRANCH,
                           "fidelity": "preserved", "note": "WDL If -> branch (both arms wired)"})
        elif atype == "Foreach":
            body_d = spec.get("actions") if isinstance(spec.get("actions"), dict) else {}
            b_nodes, b_start = _pa_lower(body_d, "b", report)
            body = Flow(id="", name=f"{label} body", start=b_start or "",
                        nodes=b_nodes) if b_nodes else None
            items = _pa_operand(spec.get("foreach") or "") or "items"
            if body is not None:
                nodes[nid] = FlowNode(id=nid, kind=NODE_FOREACH, items=items,
                                      body=body, next=nxt, output=f"{nid}_out", label=label)
                report.append({"step": name, "node": nid, "kind": NODE_FOREACH,
                               "fidelity": "preserved", "note": "WDL Foreach -> foreach with a nested body"})
            else:   # a loop with no readable body -> an agent step that does the loop's intent
                nodes[nid] = _node_for_step(_step(name, spec), nid, nxt)
                report.append({"step": name, "node": nid, "kind": nodes[nid].kind,
                               "fidelity": "approximated", "note": "Foreach body unreadable -> agent step"})
        else:
            node = _node_for_step(_step(name, spec), nid, nxt)
            nodes[nid] = node
            if atype in ("Switch", "Until", "Scope"):
                fid, note = "approximated", f"WDL {atype} is not yet captured structurally -> agent step"
            elif node.kind == NODE_ACTION:
                fid, note = "preserved", "connector call -> action node"
            else:
                fid, note = "approximated", "no single-tool mapping -> agent step"
            report.append({"step": name, "node": nid, "kind": node.kind,
                           "fidelity": fid, "note": note})
    return nodes, (ids[order[0]] if order else None)


def _pa_to_flow(automation: ImportedAutomation,
                report: list[dict]) -> Flow | None:
    """Lower a Power Automate flow's WDL action graph, preserving If/Foreach.
    Returns ``None`` when the raw definition isn't usable (caller falls back)."""
    from .power_automate import _definition
    raw = automation.raw if isinstance(automation.raw, dict) else {}
    d = _definition(raw) if raw else {}
    actions = d.get("actions") if isinstance(d.get("actions"), dict) else {}
    if not actions:
        return None
    nodes, start = _pa_lower(actions, "n", report)
    if not nodes or not start:
        return None
    flow = Flow(id=automation.template_name(), name=automation.name,
                start=start, nodes=nodes)
    if flow.validate():
        report.clear()
        return None
    return flow


def _linear_report(flow: Flow, report: list[dict]) -> None:
    """Fidelity entries derived from an already-lowered graph (the n8n path and
    the flat fallback), so every import surfaces a report."""
    for n in flow.nodes.values():
        if n.kind == NODE_BRANCH:
            entry = ("preserved", "source IF -> branch (arms wired)")
        elif n.kind == NODE_ACTION:
            entry = ("preserved", "connector call -> action node")
        else:
            entry = ("approximated", "no structural/tool mapping -> agent step")
        report.append({"step": n.label or n.id, "node": n.id, "kind": n.kind,
                       "fidelity": entry[0], "note": entry[1]})


def to_flow_with_report(automation: ImportedAutomation) -> tuple[Flow, list[dict]]:
    """Like :func:`to_flow`, plus a per-step migration fidelity report:
    ``{step, node, kind, fidelity: preserved|approximated, note}`` -- so the
    importer can say exactly what carried over structurally and what became an
    agent step (which still runs; it is an approximation, not a drop)."""
    report: list[dict] = []
    if automation.source == "n8n":
        graph = _n8n_to_flow(automation)
        if graph is not None:
            _linear_report(graph, report)
            return graph, report
    if automation.source == "power_automate":
        graph = _pa_to_flow(automation, report)
        if graph is not None:
            return graph, report
        report.clear()
    steps = automation.steps
    if not steps:
        # No extractable actions -> a single agent node that runs the automation's
        # intent, so an empty import still yields a runnable (template-equivalent)
        # flow rather than nothing.
        brief = automation.description or automation.name or "Run the imported automation"
        n = FlowNode(id="n0", kind=NODE_AGENT, brief=brief, label=automation.name)
        flow = Flow(id=automation.template_name(), name=automation.name,
                    start="n0", nodes={"n0": n})
        report.append({"step": automation.name or "automation", "node": "n0",
                       "kind": NODE_AGENT, "fidelity": "approximated",
                       "note": "no extractable steps -> one agent step with the automation's intent"})
        return flow, report
    nodes: dict[str, FlowNode] = {}
    for i, step in enumerate(steps):
        node_id = f"n{i}"
        nxt = f"n{i + 1}" if i + 1 < len(steps) else None
        nodes[node_id] = _node_for_step(step, node_id, nxt)
    flow = Flow(id=automation.template_name(), name=automation.name,
                start="n0", nodes=nodes)
    _linear_report(flow, report)
    return flow, report


def to_flow(automation: ImportedAutomation) -> Flow:
    """Build a :class:`Flow` from an imported automation. n8n IF/Filter and
    Power Automate If/Foreach are captured structurally; everything else lowers
    to an ordered chain (see :func:`to_flow_with_report` for the fidelity log)."""
    return to_flow_with_report(automation)[0]


__all__ = ["to_flow", "to_flow_with_report"]
