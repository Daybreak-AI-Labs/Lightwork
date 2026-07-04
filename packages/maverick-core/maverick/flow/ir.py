"""Flow IR -- a deterministic skeleton with agentic muscle.

A :class:`Flow` is a graph of typed nodes. The control-flow nodes (branch /
foreach / parallel / approval / delay) run deterministically and are auditable
and replayable; the *work* nodes are either a deterministic ``action`` (a typed
connector/tool call -- subject to that tool's own confirm gate) or an ``agent``
(a brief run as a governed goal -- what today's templates already are). This is
the "do both" architecture: the graph decides *when/whether/in what order* and
waits for human approval at an explicit ``approval`` node; the agent decides
*what happens inside a step*. A single ``agent`` node with no routing IS
today's template, so a Flow is a superset, not a parallel product.

The IR is a plain data structure -- no execution here (see :mod:`.runner`), no
persistence here (see :mod:`.store`). It is JSON-serialisable so it can be
imported from another platform's graph, stored, versioned, and diffed. foreach
and parallel bodies are *nested* Flows, which keeps the graph a clean tree of
sub-flows rather than a soup of cross-referencing ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Node kinds ------------------------------------------------------------------
NODE_ACTION = "action"      # a deterministic tool/connector call
NODE_AGENT = "agent"        # an agentic goal (a brief); the "figure it out" node
NODE_BRANCH = "branch"      # conditional: evaluate, go if_true / if_false
NODE_FOREACH = "foreach"    # loop: run a body sub-flow per item in a list
NODE_PARALLEL = "parallel"  # fan out branch sub-flows, join their data
NODE_APPROVAL = "approval"  # human-in-the-loop gate: pause until approved
NODE_DELAY = "delay"        # wait N seconds, then continue
NODE_SUBFLOW = "subflow"    # run another saved flow as a nested step (reuse)

NODE_KINDS = frozenset({
    NODE_ACTION, NODE_AGENT, NODE_BRANCH, NODE_FOREACH,
    NODE_PARALLEL, NODE_APPROVAL, NODE_DELAY, NODE_SUBFLOW,
})
_WORK_KINDS = frozenset({NODE_ACTION, NODE_AGENT})


@dataclass
class FlowNode:
    """One node in a flow. Kind-specific fields are unused for other kinds.

    Routing: ``next`` is the default successor id (``None`` ends the flow);
    a ``branch`` routes to ``if_true``/``if_false`` instead.
    """

    id: str
    kind: str
    next: str | None = None          # default successor node id

    # action ------------------------------------------------------------------
    tool: str = ""                   # tool/connector name
    params: dict[str, Any] = field(default_factory=dict)   # rendered from flow data
    # agent -------------------------------------------------------------------
    brief: str = ""                  # goal brief (rendered from flow data)
    # branch ------------------------------------------------------------------
    condition: str = ""              # a safe "<key> <op> <value>" expression
    if_true: str | None = None
    if_false: str | None = None
    # foreach -----------------------------------------------------------------
    items: str = ""                  # flow-data key holding the list to iterate
    var: str = "item"                # loop variable exposed to the body
    body: Flow | None = None         # sub-flow run once per item
    # parallel ----------------------------------------------------------------
    branches: list[Flow] = field(default_factory=list)   # sub-flows run + joined
    # approval ----------------------------------------------------------------
    prompt: str = ""                 # what a human is being asked to approve
    # delay -------------------------------------------------------------------
    seconds: float = 0.0
    # subflow -----------------------------------------------------------------
    flow_ref: str = ""               # a saved flow id to run as a nested step
    # error handling ----------------------------------------------------------
    retries: int = 0                 # re-run a failed work node up to N more times
    on_error: str | None = None      # route here when the node fails (else `next`)
    # common ------------------------------------------------------------------
    output: str = ""                 # flow-data key to store this node's result under
    label: str = ""                  # human label (provenance / UI)
    # designer layout (opaque to execution) -----------------------------------
    x: float = 0.0                   # canvas position for the visual designer
    y: float = 0.0

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind}
        for key in ("next", "tool", "brief", "condition", "if_true", "if_false",
                    "items", "var", "prompt", "flow_ref", "on_error", "output", "label"):
            v = getattr(self, key)
            if v not in ("", None):
                d[key] = v
        if self.params:
            d["params"] = self.params
        if self.seconds:
            d["seconds"] = self.seconds
        if self.retries:
            d["retries"] = self.retries
        if self.x or self.y:
            d["x"], d["y"] = self.x, self.y
        if self.body is not None:
            d["body"] = self.body.to_dict()
        if self.branches:
            d["branches"] = [b.to_dict() for b in self.branches]
        return d

    @staticmethod
    def from_dict(d: dict) -> FlowNode:
        body = d.get("body")
        return FlowNode(
            id=str(d["id"]), kind=str(d["kind"]),
            next=d.get("next"), tool=str(d.get("tool", "")),
            params=dict(d.get("params") or {}), brief=str(d.get("brief", "")),
            condition=str(d.get("condition", "")),
            if_true=d.get("if_true"), if_false=d.get("if_false"),
            items=str(d.get("items", "")), var=str(d.get("var", "item")),
            body=Flow.from_dict(body) if body else None,
            branches=[Flow.from_dict(b) for b in (d.get("branches") or [])],
            prompt=str(d.get("prompt", "")), seconds=float(d.get("seconds", 0.0)),
            flow_ref=str(d.get("flow_ref", "")),
            retries=int(d.get("retries", 0) or 0), on_error=d.get("on_error"),
            output=str(d.get("output", "")), label=str(d.get("label", "")),
            x=float(d.get("x", 0.0)), y=float(d.get("y", 0.0)),
        )


@dataclass
class Flow:
    """A named graph of nodes with a single entry point (``start``).

    ``version`` is a monotonic definition version stamped by the store on each
    save (see :mod:`.store`), so an applied self-rewrite or a designer edit is a
    new version the prior one can be rolled back to.
    """

    id: str
    name: str
    start: str
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    version: int = 1

    def node(self, node_id: str | None) -> FlowNode | None:
        return self.nodes.get(node_id) if node_id else None

    def copy(self) -> Flow:
        """A deep, independent copy (round-trips through the dict form)."""
        return Flow.from_dict(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "start": self.start,
            "version": self.version,
            "nodes": [n.to_dict() for n in self.nodes.values()],
        }

    @staticmethod
    def from_dict(d: dict) -> Flow:
        nodes = [FlowNode.from_dict(n) for n in (d.get("nodes") or [])]
        return Flow(
            id=str(d.get("id", "")), name=str(d.get("name", "")),
            start=str(d.get("start", "")),
            nodes={n.id: n for n in nodes},
            version=int(d.get("version", 1) or 1),
        )

    def validate(self) -> list[str]:
        """Structural problems (empty list = valid). Not an execution check."""
        errs: list[str] = []
        if not self.nodes:
            errs.append("flow has no nodes")
            return errs
        if self.start not in self.nodes:
            errs.append(f"start node {self.start!r} is not in the flow")
        seen_ids = set()
        for nid, n in self.nodes.items():
            if n.id != nid:
                errs.append(f"node id mismatch: keyed {nid!r} but node.id={n.id!r}")
            if n.id in seen_ids:
                errs.append(f"duplicate node id {n.id!r}")
            seen_ids.add(n.id)
            if n.kind not in NODE_KINDS:
                errs.append(f"node {n.id!r}: unknown kind {n.kind!r}")
            errs.extend(self._validate_routing(n))
            errs.extend(self._validate_kind(n))
        return errs

    def _validate_routing(self, n: FlowNode) -> list[str]:
        errs = []
        for ref, label in ((n.next, "next"), (n.if_true, "if_true"),
                           (n.if_false, "if_false"), (n.on_error, "on_error")):
            if ref is not None and ref not in self.nodes:
                errs.append(f"node {n.id!r}: {label} -> unknown node {ref!r}")
        return errs

    def _validate_kind(self, n: FlowNode) -> list[str]:
        errs = []
        if n.kind == NODE_ACTION and not n.tool:
            errs.append(f"action node {n.id!r} has no tool")
        if n.kind == NODE_AGENT and not n.brief:
            errs.append(f"agent node {n.id!r} has no brief")
        if n.kind == NODE_BRANCH and not n.condition:
            errs.append(f"branch node {n.id!r} has no condition")
        if n.kind == NODE_FOREACH:
            if not n.items:
                errs.append(f"foreach node {n.id!r} has no items key")
            if n.body is None:
                errs.append(f"foreach node {n.id!r} has no body")
            else:
                errs.extend(f"foreach {n.id!r} body: {e}" for e in n.body.validate())
        if n.kind == NODE_PARALLEL:
            if not n.branches:
                errs.append(f"parallel node {n.id!r} has no branches")
            for i, b in enumerate(n.branches):
                errs.extend(f"parallel {n.id!r} branch {i}: {e}" for e in b.validate())
        if n.kind == NODE_APPROVAL and not n.prompt:
            errs.append(f"approval node {n.id!r} has no prompt")
        if n.kind == NODE_SUBFLOW and not n.flow_ref:
            errs.append(f"subflow node {n.id!r} has no flow_ref")
        return errs

    def is_single_agent(self) -> bool:
        """Whether this flow is just one agent node -- i.e. today's template, the
        degenerate case a Flow generalises."""
        if len(self.nodes) != 1:
            return False
        only = next(iter(self.nodes.values()))
        return only.kind == NODE_AGENT and not only.next


def single_agent_flow(flow_id: str, name: str, brief: str) -> Flow:
    """A one-node flow that runs ``brief`` as an agentic goal -- the bridge from a
    plain template to the flow model (every template is a trivial flow)."""
    n = FlowNode(id="n0", kind=NODE_AGENT, brief=brief, label=name)
    return Flow(id=flow_id, name=name, start="n0", nodes={"n0": n})


__all__ = [
    "Flow", "FlowNode", "single_agent_flow",
    "NODE_ACTION", "NODE_AGENT", "NODE_BRANCH", "NODE_FOREACH",
    "NODE_PARALLEL", "NODE_APPROVAL", "NODE_DELAY", "NODE_SUBFLOW", "NODE_KINDS",
]
