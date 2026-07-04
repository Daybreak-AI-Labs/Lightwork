"""Flow interpreter -- runs a :class:`~.ir.Flow` deterministically.

The control-flow nodes (branch / foreach / parallel / approval / delay) are
evaluated here; the *work* nodes call injected executors (``agent_fn`` for an
agentic goal, ``action_fn`` for a deterministic tool call), so the interpreter is
pure and unit-testable without an LLM or a live connector. A ``data`` dict is
threaded through the graph -- each node may read prior values (``{{key}}`` in a
brief/param, ``key`` in a condition) and write its result under ``output``.

Durability / resume: the top-level run PAUSES at an ``approval`` node (when the
``approve_fn`` policy returns ``pending``) or a ``delay`` node, returning enough
state to continue later (:func:`run_flow` with ``resume=``). foreach bodies and
parallel branches run to completion in one pass (v1: no pause inside a loop
body/branch -- an approval there must resolve via ``approve_fn``); the outer graph
is where human-in-the-loop lives.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .ir import (
    NODE_ACTION,
    NODE_AGENT,
    NODE_APPROVAL,
    NODE_BRANCH,
    NODE_DELAY,
    NODE_FOREACH,
    NODE_PARALLEL,
    Flow,
    FlowNode,
)

# Run statuses ----------------------------------------------------------------
STATUS_COMPLETED = "completed"
STATUS_PAUSED_APPROVAL = "paused_approval"
STATUS_PAUSED_DELAY = "paused_delay"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

_VAR = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
_COND = re.compile(r"^\s*(\S+)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*$")


def render(text: str, data: dict) -> str:
    """Substitute ``{{key}}`` from ``data`` (unfilled placeholders stay literal)."""
    return _VAR.sub(lambda m: str(_dig(data, m.group(1), m.group(0))), str(text))


def _dig(data: dict, dotted: str, default: Any = None) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def _coerce(token: str) -> Any:
    token = token.strip()
    if len(token) >= 2 and token[0] in "'\"" and token[-1] == token[0]:
        return token[1:-1]
    low = token.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        return token


def eval_condition(expr: str, data: dict) -> bool:
    """A safe ``<key> <op> <value>`` predicate over flow data (no eval).

    ``<key>`` reads flow data (dotted paths allowed); ``<value>`` is a literal
    (number / 'quoted' / bareword / true|false). A bare ``expr`` tests truthiness
    of that data key. Unknown operators / unparsable exprs are falsey."""
    m = _COND.match(str(expr or ""))
    if not m:
        return bool(_dig(data, str(expr).strip()))
    key, op, rhs = m.group(1), m.group(2), _coerce(m.group(3))
    lhs = _dig(data, key)
    try:
        if op == "==":
            return lhs == rhs
        if op == "!=":
            return lhs != rhs
        if op == "contains":
            return str(rhs) in str(lhs) if lhs is not None else False
        # numeric comparisons; non-numeric operands are never ordered-true
        lf, rf = float(lhs), float(rhs)  # type: ignore[arg-type]
        return {">": lf > rf, "<": lf < rf, ">=": lf >= rf, "<=": lf <= rf}[op]
    except (TypeError, ValueError):
        return False


@dataclass
class FlowRunResult:
    status: str
    data: dict = field(default_factory=dict)
    cursor: str | None = None       # node paused/rejected at (for resume)
    resume_at: float | None = None  # epoch for a delay pause
    prompt: str = ""                # approval prompt for the UI
    error: str = ""


# Executor signatures (injected):
#   agent_fn(node, rendered_brief, data) -> (result: str, outcome: float | None)
#   action_fn(node, rendered_params, data) -> (result, outcome: float | None)
#   approve_fn(node, data) -> "approved" | "rejected" | "pending"
#   on_node(node, status: str, outcome: float | None) -> None   (telemetry/grounding)
AgentFn = Callable[[FlowNode, str, dict], tuple[Any, float | None]]
ActionFn = Callable[[FlowNode, dict, dict], tuple[Any, float | None]]


class _Pause(Exception):
    def __init__(self, kind: str, node: FlowNode, data: dict, resume_at: float | None = None):
        self.kind, self.node, self.data, self.resume_at = kind, node, data, resume_at


class _Reject(Exception):
    def __init__(self, node: FlowNode, data: dict):
        self.node, self.data = node, data


@dataclass
class _Ctx:
    agent_fn: AgentFn
    action_fn: ActionFn
    approve_fn: Callable[[FlowNode, dict], str] | None
    on_node: Callable[[FlowNode, str, float | None], None]
    now: Callable[[], float]
    allow_pause: bool

    def nested(self) -> _Ctx:
        # A copy that cannot pause (foreach body / parallel branch run straight).
        return _Ctx(self.agent_fn, self.action_fn, self.approve_fn, self.on_node,
                    self.now, allow_pause=False)


def _render_params(node: FlowNode, data: dict) -> dict:
    return {k: render(v, data) if isinstance(v, str) else v
            for k, v in (node.params or {}).items()}


def _run_node(flow: Flow, node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    kind = node.kind
    if kind == NODE_AGENT:
        result, outcome = ctx.agent_fn(node, render(node.brief, data), data)
        if node.output:
            data[node.output] = result
        ctx.on_node(node, "done", outcome)
        return node.next
    if kind == NODE_ACTION:
        result, outcome = ctx.action_fn(node, _render_params(node, data), data)
        if node.output:
            data[node.output] = result
        ctx.on_node(node, "done", outcome)
        return node.next
    if kind == NODE_BRANCH:
        took = eval_condition(node.condition, data)
        ctx.on_node(node, "true" if took else "false", None)
        nxt = node.if_true if took else node.if_false
        return nxt if nxt is not None else node.next
    if kind == NODE_FOREACH:
        items = _dig(data, node.items) or []
        results = []
        for item in list(items):
            sub = dict(data)
            sub[node.var] = item
            _run_chain(node.body, sub, ctx.nested(), node.body.start)
            results.append(sub)          # per-iteration data snapshot (body outputs included)
        if node.output:
            data[node.output] = results
        ctx.on_node(node, "done", None)
        return node.next
    if kind == NODE_PARALLEL:
        branch_outputs = []
        for branch in node.branches:
            sub = dict(data)
            _run_chain(branch, sub, ctx.nested(), branch.start)
            branch_outputs.append(sub)
            for k, v in sub.items():          # merge branch-produced keys back up
                if k not in data:
                    data[k] = v
        if node.output:
            data[node.output] = branch_outputs
        ctx.on_node(node, "done", None)
        return node.next
    if kind == NODE_APPROVAL:
        decision = ctx.approve_fn(node, data) if ctx.approve_fn else "pending"
        ctx.on_node(node, decision, None)
        if decision == "approved":
            return node.next
        if decision == "rejected":
            raise _Reject(node, data)
        if ctx.allow_pause:                    # pending + top level -> wait for a human
            raise _Pause("approval", node, data)
        raise _Reject(node, data)              # nested can't pause -> treat as not approved
    if kind == NODE_DELAY:
        if ctx.allow_pause and node.seconds > 0:
            raise _Pause("delay", node, data, resume_at=ctx.now() + node.seconds)
        return node.next
    raise ValueError(f"unknown node kind {kind!r}")


def _run_chain(flow: Flow, data: dict, ctx: _Ctx, start: str | None) -> None:
    cursor = start
    seen = 0
    limit = max(1000, len(flow.nodes) * 100)   # cycle backstop
    while cursor is not None:
        node = flow.node(cursor)
        if node is None:
            return
        cursor = _run_node(flow, node, data, ctx)
        seen += 1
        if seen > limit:
            raise RuntimeError(f"flow {flow.id!r} exceeded {limit} steps (cycle?)")


def run_flow(flow: Flow, *, agent_fn: AgentFn, action_fn: ActionFn,
             data: dict | None = None,
             approve_fn: Callable[[FlowNode, dict], str] | None = None,
             on_node: Callable[[FlowNode, str, float | None], None] | None = None,
             resume: dict | None = None,
             now: Callable[[], float] | None = None) -> FlowRunResult:
    """Run ``flow`` to completion or the next pause.

    ``resume`` continues a paused run: ``{"node_id", "data", "decision"?}`` --
    ``decision`` is the human's verdict on the approval node that paused it
    (``approved`` continues, ``rejected`` ends); a delay resume omits it.
    """
    ctx = _Ctx(agent_fn, action_fn, approve_fn, on_node or (lambda *_: None),
               now or time.time, allow_pause=True)
    if resume:
        data = dict(resume.get("data") or data or {})
        node = flow.node(resume.get("node_id"))
        if resume.get("decision") == "rejected":
            return FlowRunResult(status=STATUS_REJECTED, data=data,
                                 cursor=resume.get("node_id"))
        start = node.next if node else flow.start
    else:
        data = dict(data or {})
        start = flow.start
    try:
        _run_chain(flow, data, ctx, start)
        return FlowRunResult(status=STATUS_COMPLETED, data=data)
    except _Pause as p:
        status = STATUS_PAUSED_APPROVAL if p.kind == "approval" else STATUS_PAUSED_DELAY
        return FlowRunResult(status=status, data=p.data, cursor=p.node.id,
                             resume_at=p.resume_at, prompt=p.node.prompt)
    except _Reject as r:
        return FlowRunResult(status=STATUS_REJECTED, data=r.data, cursor=r.node.id)
    except Exception as e:  # a node executor blew up -> surface, don't crash the caller
        return FlowRunResult(status=STATUS_FAILED, data=data, error=str(e))


__all__ = [
    "run_flow", "render", "eval_condition", "FlowRunResult",
    "STATUS_COMPLETED", "STATUS_PAUSED_APPROVAL", "STATUS_PAUSED_DELAY",
    "STATUS_REJECTED", "STATUS_FAILED",
]
