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
    NODE_SUBFLOW,
    Flow,
    FlowNode,
)

# Run statuses ----------------------------------------------------------------
STATUS_COMPLETED = "completed"
STATUS_PAUSED_APPROVAL = "paused_approval"
STATUS_PAUSED_DELAY = "paused_delay"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

_VAR = re.compile(r"\{\{\s*(.+?)\s*\}\}")
_COND = re.compile(r"^\s*(\S+)\s*(==|!=|>=|<=|>|<|contains)\s*(.+?)\s*$")
_FN_CALL = re.compile(r"^([a-zA-Z_]\w*)\s*\((.*)\)$")


def render(text: str, data: dict) -> str:
    """Substitute ``{{expr}}`` from ``data``. ``expr`` is a dotted key
    (``{{a.b}}``) or a transform call over keys/literals (``{{upper(name)}}``,
    ``{{default(x, "n/a")}}``, ``{{concat(first, " ", last)}}``). An unfilled bare
    key stays literal (unchanged); a function always resolves (missing args are
    empty)."""
    def _sub(m):
        v = _eval_expr(m.group(1), data)
        return m.group(0) if v is None else _as_text(v)
    return _VAR.sub(_sub, str(text))


def _as_text(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# Safe transform functions usable inside {{ }} -- no eval, pure string/collection
# helpers. Each takes the evaluated arg list.
_FUNCS: dict[str, Callable[[list], Any]] = {
    "upper": lambda a: str(a[0]).upper() if a else "",
    "lower": lambda a: str(a[0]).lower() if a else "",
    "trim": lambda a: str(a[0]).strip() if a else "",
    "title": lambda a: str(a[0]).title() if a else "",
    "length": lambda a: (len(a[0]) if a and hasattr(a[0], "__len__") else 0),
    "concat": lambda a: "".join(_as_text(x) for x in a),
    "replace": lambda a: (str(a[0]).replace(str(a[1]), str(a[2])) if len(a) >= 3 else (str(a[0]) if a else "")),
    "default": lambda a: (a[0] if (a and a[0] not in (None, "", [], {})) else (a[1] if len(a) > 1 else "")),
    "json": lambda a: (__import__("json").dumps(a[0]) if a else ""),
}


def _split_args(s: str) -> list[str]:
    """Comma-split respecting single/double quotes (so a literal comma survives)."""
    out, cur, q = [], "", None
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == ",":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if s.strip():
        out.append(cur)
    return [a.strip() for a in out]


def _arg_value(tok: str, data: dict) -> Any:
    """A function argument: a quoted/number literal, else a data key (missing -> '')."""
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] in "'\"" and tok[-1] == tok[0]:
        return tok[1:-1]
    low = tok.lower()
    if low in ("true", "false"):
        return low == "true"
    for cast in (int, float):
        try:
            return cast(tok)
        except ValueError:
            pass
    return _dig(data, tok, "")


def _eval_expr(expr: str, data: dict) -> Any:
    """Evaluate a ``{{ }}`` expression: a transform call, else a dotted-key dig
    (returning ``None`` for a missing bare key so render keeps it literal)."""
    m = _FN_CALL.match(expr.strip())
    if m and m.group(1) in _FUNCS:
        args = [_arg_value(a, data) for a in _split_args(m.group(2))]
        try:
            return _FUNCS[m.group(1)](args)
        except Exception:  # a bad transform never breaks rendering
            return ""
    return _dig(data, expr.strip())


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
    resolve_flow: Callable[[str], Flow | None] | None = None
    flow_stack: set = field(default_factory=set)   # subflow ids on the call stack

    def nested(self) -> _Ctx:
        # A copy that cannot pause (foreach body / parallel branch / subflow run
        # straight). Shares flow_stack so subflow cycle detection spans nesting.
        return _Ctx(self.agent_fn, self.action_fn, self.approve_fn, self.on_node,
                    self.now, allow_pause=False, resolve_flow=self.resolve_flow,
                    flow_stack=self.flow_stack)


def _render_params(node: FlowNode, data: dict) -> dict:
    return {k: render(v, data) if isinstance(v, str) else v
            for k, v in (node.params or {}).items()}


def _invoke_work(node: FlowNode, data: dict, ctx: _Ctx):
    if node.kind == NODE_AGENT:
        return ctx.agent_fn(node, render(node.brief, data), data)
    return ctx.action_fn(node, _render_params(node, data), data)


def _run_work(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    result, outcome = _invoke_work(node, data, ctx)
    # Retry a failed work node (outcome 0.0) up to `retries` more times -- a
    # transient tool/agent failure gets another attempt before we give up.
    tries = 0
    while outcome == 0.0 and tries < max(0, node.retries):
        tries += 1
        result, outcome = _invoke_work(node, data, ctx)
    if node.output:
        data[node.output] = result
    ctx.on_node(node, "done", outcome)
    # A node that still failed routes to its on_error handler if it has one, so a
    # flow can catch and recover instead of poisoning `data` and marching on.
    if outcome == 0.0 and node.on_error:
        return node.on_error
    return node.next


def _run_subflow(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    sub = ctx.resolve_flow(node.flow_ref) if ctx.resolve_flow else None
    if sub is None or sub.id in ctx.flow_stack:   # missing OR would recurse -> failure
        ctx.on_node(node, "error", 0.0)
        return node.on_error or node.next
    # Runs on the SAME data dict (threaded), so the sub-flow reads parent data and
    # its outputs flow back. Nested: it cannot pause (v1 -- HITL lives up top). The
    # flow id is on the stack for its duration so a cyclic reference is caught.
    ctx.flow_stack.add(sub.id)
    try:
        _run_chain(sub, data, ctx.nested(), sub.start)
    finally:
        ctx.flow_stack.discard(sub.id)
    ctx.on_node(node, "done", None)
    return node.next


def _run_branch(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    took = eval_condition(node.condition, data)
    ctx.on_node(node, "true" if took else "false", None)
    nxt = node.if_true if took else node.if_false
    return nxt if nxt is not None else node.next


def _run_foreach(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    items = list(_dig(data, node.items) or [])
    if node.limit and node.limit > 0:
        items = items[:node.limit]          # cap iterations (a runaway-list guard)
    results = []
    for item in items:
        sub = dict(data)
        sub[node.var] = item
        _run_chain(node.body, sub, ctx.nested(), node.body.start)
        results.append(sub)          # per-iteration data snapshot (body outputs included)
        if sub.get("_break"):        # the body asked to stop early
            break
    if node.output:
        data[node.output] = results
    ctx.on_node(node, "done", None)
    return node.next


def _run_parallel(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    base = dict(data)                     # frozen pre-parallel snapshot
    branch_outputs = []
    for branch in node.branches:
        sub = dict(base)                  # each branch is isolated (sees only base)
        _run_chain(branch, sub, ctx.nested(), branch.start)
        branch_outputs.append(sub)
    for sub in branch_outputs:            # merge new keys after all branches finish
        for k, v in sub.items():          # (a later branch wins a key conflict)
            if k not in base:
                data[k] = v
    if node.output:
        data[node.output] = branch_outputs
    ctx.on_node(node, "done", None)
    return node.next


def _run_approval(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    decision = ctx.approve_fn(node, data) if ctx.approve_fn else "pending"
    ctx.on_node(node, decision, None)
    if decision == "approved":
        return node.next
    if decision == "rejected":
        raise _Reject(node, data)
    if ctx.allow_pause:                    # pending + top level -> wait for a human
        raise _Pause("approval", node, data)
    raise _Reject(node, data)              # nested can't pause -> treat as not approved


def _run_delay(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    if ctx.allow_pause and node.seconds > 0:
        raise _Pause("delay", node, data, resume_at=ctx.now() + node.seconds)
    return node.next


_HANDLERS = {
    NODE_AGENT: _run_work, NODE_ACTION: _run_work, NODE_BRANCH: _run_branch,
    NODE_FOREACH: _run_foreach, NODE_PARALLEL: _run_parallel,
    NODE_APPROVAL: _run_approval, NODE_DELAY: _run_delay,
    NODE_SUBFLOW: _run_subflow,
}


def _run_node(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    handler = _HANDLERS.get(node.kind)
    if handler is None:
        raise ValueError(f"unknown node kind {node.kind!r}")
    return handler(node, data, ctx)


def _run_chain(flow: Flow, data: dict, ctx: _Ctx, start: str | None) -> None:
    cursor = start
    seen = 0
    limit = max(1000, len(flow.nodes) * 100)   # cycle backstop
    while cursor is not None:
        node = flow.node(cursor)
        if node is None:
            return
        cursor = _run_node(node, data, ctx)
        seen += 1
        if seen > limit:
            raise RuntimeError(f"flow {flow.id!r} exceeded {limit} steps (cycle?)")


def run_flow(flow: Flow, *, agent_fn: AgentFn, action_fn: ActionFn,
             data: dict | None = None,
             approve_fn: Callable[[FlowNode, dict], str] | None = None,
             on_node: Callable[[FlowNode, str, float | None], None] | None = None,
             resume: dict | None = None,
             now: Callable[[], float] | None = None,
             resolve_flow: Callable[[str], Flow | None] | None = None) -> FlowRunResult:
    """Run ``flow`` to completion or the next pause.

    ``resume`` continues a paused run: ``{"node_id", "data", "decision"?}`` --
    ``decision`` is the human's verdict on the approval node that paused it
    (``approved`` continues, ``rejected`` ends); a delay resume omits it.
    ``resolve_flow(flow_ref) -> Flow | None`` resolves a ``subflow`` node's target
    (with the caller's cycle/depth guard); ``None`` makes a subflow a no-op error.
    """
    ctx = _Ctx(agent_fn, action_fn, approve_fn, on_node or (lambda *_: None),
               now or time.time, allow_pause=True, resolve_flow=resolve_flow,
               flow_stack={flow.id})   # so the top flow can't recurse into itself
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
