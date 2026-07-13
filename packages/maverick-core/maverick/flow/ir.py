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
NODE_SWITCH = "switch"      # n-way conditional: route by a data key's value
NODE_FOREACH = "foreach"    # loop: run a body sub-flow per item in a list
NODE_WHILE = "while"        # loop: run a body sub-flow while a condition holds
NODE_PARALLEL = "parallel"  # fan out branch sub-flows, join their data
NODE_APPROVAL = "approval"  # human-in-the-loop gate: pause until approved
NODE_DELAY = "delay"        # wait N seconds, then continue
NODE_WAIT = "wait_event"    # pause until an external event resumes the run
NODE_SCOPE = "scope"        # try/catch: run a body; a failure routes to on_error
NODE_SUBFLOW = "subflow"    # run another saved flow as a nested step (reuse)
NODE_SETVAR = "setvar"      # set flow-data keys from expressions (compose / loop counter)

MAX_FLOW_NODE_RETRIES = 10
MAX_FLOW_NODE_RETRY_BACKOFF_TOTAL = 120.0
MAX_FLOW_NODE_RETRY_SLEEP = 30.0

NODE_KINDS = frozenset({
    NODE_ACTION, NODE_AGENT, NODE_BRANCH, NODE_SWITCH, NODE_FOREACH, NODE_WHILE,
    NODE_PARALLEL, NODE_APPROVAL, NODE_DELAY, NODE_WAIT, NODE_SCOPE, NODE_SUBFLOW,
    NODE_SETVAR,
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
                                     # (switch: the data key to route on;
                                     #  while: the keep-looping condition)
    if_true: str | None = None
    if_false: str | None = None
    # switch ------------------------------------------------------------------
    cases: list[dict] = field(default_factory=list)   # [{"value": x, "to": node_id}]
    # foreach / while ----------------------------------------------------------
    items: str = ""                  # flow-data key holding the list to iterate
    var: str = "item"                # loop variable exposed to the body
    body: Flow | None = None         # sub-flow run per item / per while pass / scope
    limit: int = 0                   # cap iterations (0 = default cap); body may set _break
    concurrent: bool = False         # foreach: run iterations on a bounded pool
    # setvar ------------------------------------------------------------------
    assignments: dict[str, Any] = field(default_factory=dict)   # {key: expression}; sets flow data
    # parallel ----------------------------------------------------------------
    branches: list[Flow] = field(default_factory=list)   # sub-flows run + joined
    # approval / wait_event ----------------------------------------------------
    prompt: str = ""                 # what a human approves / what the run waits for
    choices: list[str] = field(default_factory=list)  # approval: allowed verdicts
                                     # beyond approve/reject (recorded to output)
    assignee: str = ""               # approval: who to notify / who owns the task
    expires_after: float = 0.0       # approval: seconds until it auto-resolves
                                     # (0 = waits forever); its own field so a kind
                                     # change never confuses it with the execution
                                     # `timeout`. Falls back to `timeout` for
                                     # approvals saved before this field existed.
    on_expire: str | None = None     # approval: route here on expiry (else rejected)
    form: list[dict] = field(default_factory=list)   # approval: fields to collect
                                     # on sign-off [{"name","label"}] (merged into data)
    # delay -------------------------------------------------------------------
    seconds: float = 0.0
    # subflow -----------------------------------------------------------------
    flow_ref: str = ""               # a saved flow id to run as a nested step
    subflow_inputs: dict[str, Any] = field(default_factory=dict)   # {child_key: expr};
                                     # when set, the child runs ISOLATED on only these
                                     # (no shared parent data); outputs return via `output`
    # error handling ----------------------------------------------------------
    retries: int = 0                 # re-run a failed work node up to N more times
    retry_backoff: float = 0.0       # base seconds between retries; exponential (0 = tight loop)
    on_error: str | None = None      # route here when the node fails (else `next`)
    timeout: float = 0.0             # per-node soft wall-clock cap in seconds (0 = none)
    # common ------------------------------------------------------------------
    output: str = ""                 # flow-data key to store this node's result under
    label: str = ""                  # human label (provenance / UI)
    # designer layout (opaque to execution) -----------------------------------
    x: float = 0.0                   # canvas position for the visual designer
    y: float = 0.0

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "kind": self.kind}
        for key in ("next", "tool", "brief", "condition", "if_true", "if_false",
                    "items", "var", "prompt", "flow_ref", "on_error", "output", "label",
                    "assignee", "on_expire"):
            v = getattr(self, key)
            if v not in ("", None):
                d[key] = v
        if self.params:
            d["params"] = self.params
        if self.seconds:
            d["seconds"] = self.seconds
        if self.retries:
            d["retries"] = self.retries
        if self.retry_backoff:
            d["retry_backoff"] = self.retry_backoff
        if self.timeout:
            d["timeout"] = self.timeout
        if self.limit:
            d["limit"] = self.limit
        if self.concurrent:
            d["concurrent"] = True
        if self.cases:
            d["cases"] = self.cases
        if self.choices:
            d["choices"] = self.choices
        if self.expires_after:
            d["expires_after"] = self.expires_after
        if self.form:
            d["form"] = self.form
        if self.assignments:
            d["assignments"] = self.assignments
        if self.subflow_inputs:
            d["subflow_inputs"] = self.subflow_inputs
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
        # Legacy-shape migration, done HERE so every consumer (runner, sweep,
        # designer, drafter) sees one field: old approvals encoded their expiry
        # in `timeout` before `expires_after` existed.
        expires_after = float(d.get("expires_after", 0.0) or 0.0)
        if str(d.get("kind")) == NODE_APPROVAL and not expires_after:
            expires_after = float(d.get("timeout", 0.0) or 0.0)
        return FlowNode(
            id=str(d["id"]), kind=str(d["kind"]),
            next=d.get("next"), tool=str(d.get("tool", "")),
            params=dict(d.get("params") or {}), brief=str(d.get("brief", "")),
            condition=str(d.get("condition", "")),
            if_true=d.get("if_true"), if_false=d.get("if_false"),
            items=str(d.get("items", "")), var=str(d.get("var", "item")),
            limit=int(d.get("limit", 0) or 0),
            concurrent=bool(d.get("concurrent", False)),
            cases=[dict(c) for c in (d.get("cases") or []) if isinstance(c, dict)],
            choices=[str(c) for c in (d.get("choices") or [])],
            assignee=str(d.get("assignee", "")),
            expires_after=expires_after,
            on_expire=d.get("on_expire"),
            form=[c for c in (d.get("form") or []) if isinstance(c, dict)],
            assignments=dict(d.get("assignments") or {}),
            subflow_inputs=dict(d.get("subflow_inputs") or {}),
            body=Flow.from_dict(body) if body else None,
            branches=[Flow.from_dict(b) for b in (d.get("branches") or [])],
            prompt=str(d.get("prompt", "")), seconds=float(d.get("seconds", 0.0)),
            flow_ref=str(d.get("flow_ref", "")),
            retries=int(d.get("retries", 0) or 0),
            retry_backoff=float(d.get("retry_backoff", 0.0) or 0.0),
            on_error=d.get("on_error"),
            timeout=float(d.get("timeout", 0.0) or 0.0),
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
    notify: bool = False             # push a notification on approval-pause / finish
    max_seconds: float = 0.0         # whole-flow wall-clock deadline (0 = no limit)
    max_dollars: float = 0.0         # aggregate agent spend ceiling for one run (0 = no limit)
    schedule: str = ""               # a cron expression to run this flow on (empty = not scheduled)
    timezone: str = ""               # IANA zone the schedule is in (empty = UTC)
    owner: str = ""                  # principal that owns this flow (for RBAC + attributing scheduled runs)
    max_concurrent: int = 0          # cap simultaneous in-flight runs of this flow (0 = unlimited)
    inputs: list[dict] = field(default_factory=list)   # declared manual-run inputs:
                                     # [{key, type: text|number|bool|date, label, required, default}]

    def node(self, node_id: str | None) -> FlowNode | None:
        return self.nodes.get(node_id) if node_id else None

    def copy(self) -> Flow:
        """A deep, independent copy (round-trips through the dict form)."""
        return Flow.from_dict(self.to_dict())

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "start": self.start,
            "version": self.version, "notify": self.notify,
            "max_seconds": self.max_seconds, "max_dollars": self.max_dollars,
            "schedule": self.schedule, "timezone": self.timezone, "owner": self.owner,
            "max_concurrent": self.max_concurrent, "inputs": self.inputs,
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
            notify=bool(d.get("notify", False)),
            max_seconds=float(d.get("max_seconds", 0.0) or 0.0),
            max_dollars=float(d.get("max_dollars", 0.0) or 0.0),
            schedule=str(d.get("schedule", "")), timezone=str(d.get("timezone", "")),
            owner=str(d.get("owner", "")),
            max_concurrent=int(d.get("max_concurrent", 0) or 0),
            inputs=[dict(i) for i in (d.get("inputs") or []) if isinstance(i, dict)],
        )

    def validate(self) -> list[str]:
        """Structural problems (empty list = valid). Not an execution check."""
        errs: list[str] = []
        if not self.nodes:
            errs.append("flow has no nodes")
            return errs
        if self.start not in self.nodes:
            errs.append(f"start node {self.start!r} is not in the flow")
        if self.schedule.strip():
            from ..scheduler import CronError, parse_cron
            try:
                parse_cron(self.schedule)
            except CronError as e:
                errs.append(f"invalid schedule {self.schedule!r}: {e}")
        seen_ids = set()
        for nid, n in self.nodes.items():
            if n.id != nid:
                errs.append(f"node id mismatch: keyed {nid!r} but node.id={n.id!r}")
            if n.id in seen_ids:
                errs.append(f"duplicate node id {n.id!r}")
            seen_ids.add(n.id)
            if n.kind not in NODE_KINDS:
                errs.append(f"node {n.id!r}: unknown kind {n.kind!r}")
            errs.extend(self._validate_retry_policy(n))
            errs.extend(self._validate_routing(n))
            errs.extend(self._validate_kind(n))
        seen_input_keys = set()
        for i, spec in enumerate(self.inputs):
            key = str(spec.get("key") or "").strip()
            if not key:
                errs.append(f"input {i}: no key")
                continue
            if key in seen_input_keys:
                errs.append(f"duplicate input key {key!r}")
            seen_input_keys.add(key)
            typ = str(spec.get("type") or "text")
            if typ not in INPUT_TYPES:
                errs.append(f"input {key!r}: unknown type {typ!r}")
        return errs

    def _validate_retry_policy(self, n: FlowNode) -> list[str]:
        errs = []
        retries = max(0, n.retries)
        backoff = max(0.0, n.retry_backoff)
        if retries > MAX_FLOW_NODE_RETRIES:
            errs.append(f"node {n.id!r}: retries must be <= {MAX_FLOW_NODE_RETRIES}")
        if backoff and _retry_backoff_total(min(retries, MAX_FLOW_NODE_RETRIES), backoff) > MAX_FLOW_NODE_RETRY_BACKOFF_TOTAL:
            errs.append(
                f"node {n.id!r}: retry backoff total must be <= "
                f"{MAX_FLOW_NODE_RETRY_BACKOFF_TOTAL:g}s"
            )
        return errs

    def _validate_routing(self, n: FlowNode) -> list[str]:
        errs = []
        for ref, label in ((n.next, "next"), (n.if_true, "if_true"),
                           (n.if_false, "if_false"), (n.on_error, "on_error"),
                           (n.on_expire, "on_expire")):
            if ref is not None and ref not in self.nodes:
                errs.append(f"node {n.id!r}: {label} -> unknown node {ref!r}")
        return errs

    def _validate_kind(self, n: FlowNode) -> list[str]:
        return self._validate_kind_fields(n) + self._validate_kind_nested(n)

    def _validate_kind_fields(self, n: FlowNode) -> list[str]:
        """Flat required-field checks per kind (no sub-flow recursion)."""
        errs = []
        if n.kind == NODE_ACTION and not n.tool:
            errs.append(f"action node {n.id!r} has no tool")
        if n.kind == NODE_AGENT and not n.brief:
            errs.append(f"agent node {n.id!r} has no brief")
        if n.kind == NODE_BRANCH and not n.condition:
            errs.append(f"branch node {n.id!r} has no condition")
        if n.kind == NODE_SWITCH:
            if not n.condition:
                errs.append(f"switch node {n.id!r} has no key (condition)")
            if not n.cases:
                errs.append(f"switch node {n.id!r} has no cases")
            for i, c in enumerate(n.cases):
                to = c.get("to")
                if to is not None and to not in self.nodes:
                    errs.append(f"switch {n.id!r} case {i}: to -> unknown node {to!r}")
        if n.kind == NODE_WAIT and not n.prompt:
            errs.append(f"wait_event node {n.id!r} has no prompt")
        if n.kind == NODE_APPROVAL and not n.prompt:
            errs.append(f"approval node {n.id!r} has no prompt")
        if n.kind == NODE_SUBFLOW and not n.flow_ref:
            errs.append(f"subflow node {n.id!r} has no flow_ref")
        return errs

    def _validate_kind_nested(self, n: FlowNode) -> list[str]:
        """Body/branch checks for the container kinds (recurses into sub-flows)."""
        errs = []
        if n.kind == NODE_WHILE and not n.condition:
            errs.append(f"while node {n.id!r} has no condition")
        if n.kind == NODE_FOREACH and not n.items:
            errs.append(f"foreach node {n.id!r} has no items key")
        if n.kind in (NODE_WHILE, NODE_SCOPE, NODE_FOREACH):
            if n.body is None:
                errs.append(f"{n.kind} node {n.id!r} has no body")
            else:
                errs.extend(f"{n.kind} {n.id!r} body: {e}" for e in n.body.validate())
                errs.extend(f"{n.kind} {n.id!r} body: {e}" for e in _pause_node_errors(n.body))
        if n.kind == NODE_PARALLEL:
            if not n.branches:
                errs.append(f"parallel node {n.id!r} has no branches")
            for i, b in enumerate(n.branches):
                errs.extend(f"parallel {n.id!r} branch {i}: {e}" for e in b.validate())
                errs.extend(f"parallel {n.id!r} branch {i}: {e}" for e in _pause_node_errors(b))
        if n.kind == NODE_SETVAR and not n.assignments:
            errs.append(f"setvar node {n.id!r} has no assignments")
        return errs

    def is_single_agent(self) -> bool:
        """Whether this flow is just one agent node -- i.e. today's template, the
        degenerate case a Flow generalises."""
        if len(self.nodes) != 1:
            return False
        only = next(iter(self.nodes.values()))
        return only.kind == NODE_AGENT and not only.next


def _pause_node_errors(flow: Flow) -> list[str]:
    """Errors for ``approval``/``delay`` nodes anywhere inside a NESTED sub-flow
    (a foreach body, a parallel branch, and recursively their own nested bodies).
    Human-in-the-loop lives only at the top flow: a nested context can't pause, so
    a nested ``approval`` rejects the WHOLE run and a nested ``delay`` silently
    does nothing. Flagging it at validation turns a runtime footgun into a clear
    save-time error."""
    errs: list[str] = []
    for n in flow.nodes.values():
        if n.kind == NODE_APPROVAL:
            errs.append(f"approval node {n.id!r} can't pause inside a loop/branch "
                        "(nested approvals reject the whole run); move it to the top flow")
        if n.kind == NODE_DELAY:
            errs.append(f"delay node {n.id!r} can't wait inside a loop/branch "
                        "(nested delays are skipped); move it to the top flow")
        if n.kind in (NODE_FOREACH, NODE_WHILE, NODE_SCOPE) and n.body is not None:
            errs.extend(_pause_node_errors(n.body))
        if n.kind == NODE_PARALLEL:
            for b in n.branches:
                errs.extend(_pause_node_errors(b))
    return errs


def single_agent_flow(flow_id: str, name: str, brief: str) -> Flow:
    """A one-node flow that runs ``brief`` as an agentic goal -- the bridge from a
    plain template to the flow model (every template is a trivial flow)."""
    n = FlowNode(id="n0", kind=NODE_AGENT, brief=brief, label=name)
    return Flow(id=flow_id, name=name, start="n0", nodes={"n0": n})


INPUT_TYPES = frozenset({"text", "number", "bool", "date"})


def coerce_inputs(flow: Flow, data: dict) -> tuple[dict, list[str]]:
    """Validate + coerce a manual run's ``data`` against ``flow.inputs`` (the
    declared input schema). Returns ``(coerced, errors)``: numbers become floats,
    bools are parsed from truthy strings, dates are ISO-validated, a missing input
    falls back to its ``default`` (else errors if ``required``). Keys not in the
    schema pass through untouched, so a flow with no declared inputs is a clean
    no-op. Pure -- the run endpoint 400s when ``errors`` is non-empty."""
    out = dict(data or {})
    errors: list[str] = []
    for spec in (flow.inputs or []):
        key = str(spec.get("key") or "").strip()
        if not key:
            continue
        typ = str(spec.get("type") or "text")
        present = key in out and out[key] not in (None, "")
        if not present:
            if "default" in spec:
                out[key] = spec["default"]
            elif spec.get("required"):
                errors.append(f"missing required input {key!r}")
            continue
        val = out[key]
        if typ == "number":
            try:
                out[key] = float(val)
            except (TypeError, ValueError):
                errors.append(f"input {key!r} must be a number")
        elif typ == "bool":
            out[key] = val if isinstance(val, bool) else \
                str(val).strip().lower() in ("true", "1", "yes", "on")
        elif typ == "date":
            import datetime as _dt
            try:
                _dt.date.fromisoformat(str(val)[:10])
            except ValueError:
                errors.append(f"input {key!r} must be an ISO date (YYYY-MM-DD)")
    return out, errors


__all__ = [
    "Flow", "FlowNode", "single_agent_flow", "coerce_inputs", "INPUT_TYPES",
    "NODE_ACTION", "NODE_AGENT", "NODE_BRANCH", "NODE_SWITCH", "NODE_FOREACH",
    "NODE_WHILE", "NODE_PARALLEL", "NODE_APPROVAL", "NODE_DELAY", "NODE_WAIT",
    "NODE_SCOPE", "NODE_SUBFLOW", "NODE_KINDS",
]


def _retry_backoff_total(retries: int, backoff: float) -> float:
    total = 0.0
    for attempt in range(1, max(0, retries) + 1):
        total += min(MAX_FLOW_NODE_RETRY_SLEEP, backoff * (2 ** (attempt - 1)))
    return total
