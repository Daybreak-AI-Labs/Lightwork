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

from ..flow.ir import NODE_ACTION, NODE_AGENT, Flow, FlowNode
from .ir import ImportedAutomation, ImportedStep, safe_params


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


def to_flow(automation: ImportedAutomation) -> Flow:
    """Build a linear :class:`Flow` from an imported automation (one node/step)."""
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
