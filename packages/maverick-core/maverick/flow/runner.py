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
    NODE_SCOPE,
    NODE_SUBFLOW,
    NODE_SWITCH,
    NODE_WAIT,
    NODE_WHILE,
    Flow,
    FlowNode,
)

# Run statuses ----------------------------------------------------------------
STATUS_COMPLETED = "completed"
STATUS_PAUSED_APPROVAL = "paused_approval"
STATUS_PAUSED_DELAY = "paused_delay"
STATUS_PAUSED_EVENT = "paused_event"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"

# The single source of truth for "which statuses mean a run is parked and
# resumable" and "which statuses mean a run is still in flight". Every gate that
# used to hand-list these literals (the resume/retry endpoints, the sweep, the
# JS overlay) should consume these instead -- a new pause status is then added
# in one place, not six.
PAUSED_STATUSES = frozenset({
    STATUS_PAUSED_APPROVAL, STATUS_PAUSED_DELAY, STATUS_PAUSED_EVENT,
})
ACTIVE_STATUSES = frozenset({"queued", "running"}) | PAUSED_STATUSES

_WHILE_DEFAULT_CAP = 100   # while-loop iterations when the node sets no limit

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
    if isinstance(v, float) and v.is_integer():
        return str(int(v))          # render add(2,3) as "5", not "5.0"
    return str(v)


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


# Safe transform functions usable inside {{ }} -- no eval, pure string/collection/
# arithmetic helpers. Each takes the evaluated arg list.
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
    "first": lambda a: (a[0][0] if a and isinstance(a[0], (list, tuple)) and a[0] else ""),
    "last": lambda a: (a[0][-1] if a and isinstance(a[0], (list, tuple)) and a[0] else ""),
    # arithmetic
    "add": lambda a: sum(_num(x) for x in a),
    "sub": lambda a: (_num(a[0]) - _num(a[1])) if len(a) >= 2 else _num(a[0] if a else 0),
    "mul": lambda a: (_num(a[0]) * _num(a[1])) if len(a) >= 2 else _num(a[0] if a else 0),
    "div": lambda a: (_num(a[0]) / _num(a[1])) if (len(a) >= 2 and _num(a[1])) else 0,
    "round": lambda a: round(_num(a[0]), int(_num(a[1])) if len(a) > 1 else 0) if a else 0,
}


def _split_args(s: str) -> list[str]:
    """Comma-split respecting quotes AND nested parens, so a literal comma or a
    nested call's arguments (``default(x, "y")``) survive as one argument."""
    out, cur, q, depth = [], "", None, 0
    for ch in s:
        if q:
            cur += ch
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth = max(0, depth - 1)
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if s.strip():
        out.append(cur)
    return [a.strip() for a in out]


def _arg_value(tok: str, data: dict) -> Any:
    """A function argument: a quoted/number/bool literal, a NESTED transform call
    (evaluated recursively), else a data key (missing -> '')."""
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
    m = _FN_CALL.match(tok)
    if m and m.group(1) in _FUNCS:          # nested call: {{upper(default(x,"y"))}}
        return _eval_expr(tok, data)
    return _dig(data, tok, "")


def _eval_expr(expr: str, data: dict) -> Any:
    """Evaluate a ``{{ }}`` expression: a transform call (args may themselves be
    transform calls), else a dotted-key dig (``None`` for a missing bare key so
    render keeps it literal)."""
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


def _eval_predicate(expr: str, data: dict) -> bool:
    """One ``<key> <op> <value>`` comparison (or a bare truthiness test)."""
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


_AND_OR = re.compile(r"\s+(?:and|or)\s+", re.IGNORECASE)


def eval_condition(expr: str, data: dict) -> bool:
    """A safe boolean predicate over flow data (no eval).

    A predicate is ``<key> <op> <value>`` (``<value>`` a number / 'quoted' /
    bareword / true|false), or a bare key (truthiness). Predicates compose with
    ``and`` / ``or`` (``or`` of ``and`` groups, standard precedence), e.g.
    ``amount > 100 and region == 'EU'``. Unparsable predicates are falsey."""
    s = str(expr or "").strip()
    if not _AND_OR.search(s):
        return _eval_predicate(s, data)
    for or_part in re.split(r"\s+or\s+", s, flags=re.IGNORECASE):
        parts = [p for p in re.split(r"\s+and\s+", or_part, flags=re.IGNORECASE) if p.strip()]
        if parts and all(_eval_predicate(p, data) for p in parts):
            return True
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


class _NodeError(Exception):
    """An executor raised inside a specific node -- carries the node id so a
    failed run records WHERE it failed (the retry-from-failure entry point)."""

    def __init__(self, node_id: str, cause: Exception):
        super().__init__(f"node {node_id!r}: {cause}")
        self.node_id = node_id


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
    deadline: float | None = None                  # whole-flow wall-clock deadline

    def nested(self) -> _Ctx:
        # A copy that cannot pause (foreach body / subflow run straight). Shares
        # flow_stack + deadline so cycle detection and the whole-flow deadline
        # span the linear nesting.
        return _Ctx(self.agent_fn, self.action_fn, self.approve_fn, self.on_node,
                    self.now, allow_pause=False, resolve_flow=self.resolve_flow,
                    flow_stack=self.flow_stack, deadline=self.deadline)

    def branched(self) -> _Ctx:
        # For a CONCURRENT parallel branch: its own flow_stack copy (independent
        # cycle detection -- a sibling branch's subflow isn't a cycle here), but a
        # shared clock/deadline and the shared on_node sink.
        return _Ctx(self.agent_fn, self.action_fn, self.approve_fn, self.on_node,
                    self.now, allow_pause=False, resolve_flow=self.resolve_flow,
                    flow_stack=set(self.flow_stack), deadline=self.deadline)


def _render_params(node: FlowNode, data: dict) -> dict:
    return {k: render(v, data) if isinstance(v, str) else v
            for k, v in (node.params or {}).items()}


def _invoke_work(node: FlowNode, data: dict, ctx: _Ctx):
    def _call():
        if node.kind == NODE_AGENT:
            return ctx.agent_fn(node, render(node.brief, data), data)
        return ctx.action_fn(node, _render_params(node, data), data)
    if not (node.timeout and node.timeout > 0):
        return _call()
    # Soft per-node timeout: run the executor on a worker thread and give up
    # waiting after `timeout`s (Python can't kill the thread, so the abandoned
    # call finishes on its own -- but the flow stops blocking on it).
    import concurrent.futures as _cf
    ex = _cf.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(_call)
    try:
        res = fut.result(timeout=node.timeout)
        ex.shutdown(wait=False)
        return res
    except _cf.TimeoutError:
        ex.shutdown(wait=False)          # don't block on the runaway call
        return (f"ERROR: node timed out after {node.timeout}s", 0.0)


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


def _run_switch(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    """n-way route: the value under the ``condition`` data key is compared (as
    text) to each case's ``value``; the first match routes to its ``to``. No
    match falls through to ``next`` (the default arm)."""
    subject = _as_text(_dig(data, node.condition))
    for case in node.cases:
        if _as_text(case.get("value")) == subject:
            ctx.on_node(node, f"case:{_as_text(case.get('value'))[:40]}", None)
            to = case.get("to")
            return to if to is not None else node.next
    ctx.on_node(node, "default", None)
    return node.next


def _run_while(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    """Run the body sub-flow while the condition holds, on the SAME data dict
    (so the body can change what the condition sees). Iterations are capped by
    ``limit`` (default 100) as the runaway guard; the body may set ``_break``."""
    cap = node.limit if node.limit and node.limit > 0 else _WHILE_DEFAULT_CAP
    passes = 0
    while passes < cap and eval_condition(node.condition, data):
        _run_chain(node.body, data, ctx.nested(), node.body.start)
        passes += 1
        if data.get("_break"):
            data.pop("_break", None)
            break
    if node.output:
        data[node.output] = passes
    ctx.on_node(node, "done", None)
    return node.next


def _run_wait(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    """Pause the run until an external event resumes it (a webhook delivery,
    an API resume). Nested contexts can't pause, so there it is a no-op step
    (the outer graph is where waiting lives, same as approvals)."""
    if ctx.allow_pause:
        ctx.on_node(node, "waiting", None)
        raise _Pause("event", node, data)
    # Nested (foreach/while/scope body, parallel branch): can't pause. Fail
    # CLOSED like a nested approval rather than silently continuing -- otherwise
    # downstream nodes run before the awaited event's data has arrived.
    ctx.on_node(node, "skipped", None)
    raise _Reject(node, data)


def _run_scope(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    """try/catch: run the body on the shared data; if a body node raises, route
    to ``on_error`` (the catch arm) instead of failing the whole run. Without an
    ``on_error`` the failure propagates -- a try with no catch still fails."""
    try:
        _run_chain(node.body, data, ctx.nested(), node.body.start)
    except _NodeError as e:
        if node.on_error is None:
            raise
        data["_error"] = str(e)          # the catch arm can read what went wrong
        ctx.on_node(node, "caught", 0.0)
        return node.on_error
    ctx.on_node(node, "done", None)
    return node.next


def _run_foreach(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    items = list(_dig(data, node.items) or [])
    if node.limit and node.limit > 0:
        items = items[:node.limit]          # cap iterations (a runaway-list guard)
    results = []
    if node.concurrent and len(items) > 1:
        # Concurrent iteration: each item gets an isolated data copy and its own
        # branch context (independent cycle detection), on the same bounded pool
        # the parallel node uses. Results keep ITEM ORDER; ``_break`` doesn't
        # apply (iterations overlap, there is no "early" to stop at).
        import concurrent.futures as _cf
        subs = []
        for item in items:
            sub = dict(data)
            sub[node.var] = item
            subs.append(sub)
        workers = min(len(subs), _PARALLEL_MAX_WORKERS)
        with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_run_chain, node.body, sub, ctx.branched(), node.body.start)
                    for sub in subs]
            for f in futs:
                f.result()
        results = subs
    else:
        for item in items:
            sub = dict(data)
            sub[node.var] = item
            _run_chain(node.body, sub, ctx.nested(), node.body.start)
            results.append(sub)      # per-iteration data snapshot (body outputs included)
            if sub.get("_break"):    # the body asked to stop early
                break
    if node.output:
        data[node.output] = results
    ctx.on_node(node, "done", None)
    return node.next


_PARALLEL_MAX_WORKERS = 8   # cap fan-out threads per parallel node


def _run_parallel(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    base = dict(data)                     # frozen pre-parallel snapshot
    branch_outputs = [dict(base) for _ in node.branches]   # each isolated (sees only base)
    if len(node.branches) > 1:
        # Run the branches CONCURRENTLY. Each has an isolated data copy and its
        # own flow_stack (ctx.branched()); the shared world DB serialises writes
        # (WAL + busy_timeout). A branch that raises propagates after the pool
        # joins the rest, so there are no leaked threads.
        import concurrent.futures as _cf
        workers = min(len(node.branches), _PARALLEL_MAX_WORKERS)
        with _cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_run_chain, br, sub, ctx.branched(), br.start)
                    for br, sub in zip(node.branches, branch_outputs, strict=True)]
            for f in futs:
                f.result()                # wait for all; re-raise the first error
    else:
        for br, sub in zip(node.branches, branch_outputs, strict=True):
            _run_chain(br, sub, ctx.nested(), br.start)
    for sub in branch_outputs:            # merge new keys deterministically, in order
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
    # "approved" -- or any verdict the node explicitly offers (a choice approval:
    # "ship"/"hold"/"escalate") -- continues; the verdict is recorded to `output`
    # so downstream nodes (a branch/switch) can route on it.
    if decision == "approved" or (node.choices and decision in node.choices):
        if node.output:
            data[node.output] = decision
        return node.next
    if decision == "rejected":
        raise _Reject(node, data)
    if ctx.allow_pause:                    # pending + top level -> wait for a human
        # node.timeout on an approval is its EXPIRY: the delay sweep resolves a
        # still-pending approval as rejected once resume_at passes (opt-in).
        resume_at = ctx.now() + node.timeout if node.timeout and node.timeout > 0 else None
        raise _Pause("approval", node, data, resume_at=resume_at)
    raise _Reject(node, data)              # nested can't pause -> treat as not approved


def _run_delay(node: FlowNode, data: dict, ctx: _Ctx) -> str | None:
    if ctx.allow_pause and node.seconds > 0:
        raise _Pause("delay", node, data, resume_at=ctx.now() + node.seconds)
    return node.next


_HANDLERS = {
    NODE_AGENT: _run_work, NODE_ACTION: _run_work, NODE_BRANCH: _run_branch,
    NODE_SWITCH: _run_switch, NODE_FOREACH: _run_foreach, NODE_WHILE: _run_while,
    NODE_PARALLEL: _run_parallel, NODE_APPROVAL: _run_approval,
    NODE_DELAY: _run_delay, NODE_WAIT: _run_wait, NODE_SCOPE: _run_scope,
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
        if ctx.deadline is not None and ctx.now() > ctx.deadline:
            raise RuntimeError(f"flow {flow.id!r} exceeded its wall-clock deadline")
        node = flow.node(cursor)
        if node is None:
            return
        try:
            cursor = _run_node(node, data, ctx)
        except (_Pause, _Reject, _NodeError):
            raise
        except Exception as e:
            raise _NodeError(node.id, e) from e
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
    _now = now or time.time
    ctx = _Ctx(agent_fn, action_fn, approve_fn, on_node or (lambda *_: None),
               _now, allow_pause=True, resolve_flow=resolve_flow,
               flow_stack={flow.id},   # so the top flow can't recurse into itself
               deadline=(_now() + flow.max_seconds if flow.max_seconds and flow.max_seconds > 0 else None))
    if resume:
        data = dict(resume.get("data") or data or {})
        node = flow.node(resume.get("node_id"))
        decision = resume.get("decision")
        # The paused/failed node must still exist in this (current) flow version.
        # If an edit or rollback removed or replaced it between pause and resume,
        # fail CLOSED -- silently falling back to flow.start would re-execute the
        # whole flow and re-drive every pre-pause side effect.
        if node is None:
            return FlowRunResult(
                status=STATUS_FAILED, data=data, cursor=resume.get("node_id"),
                error=(f"cannot resume: node {resume.get('node_id')!r} no longer "
                       "exists in this flow version"))
        if decision == "rejected":
            return FlowRunResult(status=STATUS_REJECTED, data=data, cursor=node.id)
        if node.kind == NODE_APPROVAL and not resume.get("rerun"):
            # Resuming an approval gate: accept ONLY the literal "approved" or one
            # of the node's explicitly declared choices. Anything else fails
            # CLOSED (rejected), so a loosened decision string can never wave a
            # run past a human gate. The accepted verdict is recorded to the
            # node's output for downstream routing (matches the inline path).
            if decision == "approved" or (decision and decision in node.choices):
                if node.output:
                    data[node.output] = decision
            else:
                return FlowRunResult(status=STATUS_REJECTED, data=data, cursor=node.id)
        # A pause resumes AFTER its node (the approval/delay is done); a
        # failure retry re-enters AT the node that failed ("rerun").
        start = node.id if resume.get("rerun") else node.next
    else:
        data = dict(data or {})
        start = flow.start
    try:
        _run_chain(flow, data, ctx, start)
        return FlowRunResult(status=STATUS_COMPLETED, data=data)
    except _Pause as p:
        status = {"approval": STATUS_PAUSED_APPROVAL,
                  "event": STATUS_PAUSED_EVENT}.get(p.kind, STATUS_PAUSED_DELAY)
        return FlowRunResult(status=status, data=p.data, cursor=p.node.id,
                             resume_at=p.resume_at, prompt=p.node.prompt)
    except _Reject as r:
        return FlowRunResult(status=STATUS_REJECTED, data=r.data, cursor=r.node.id)
    except _NodeError as e:  # an executor blew up inside a known node -- keep the
        # node id as the cursor so the run can be retried from right there.
        return FlowRunResult(status=STATUS_FAILED, data=data, error=str(e),
                             cursor=e.node_id)
    except Exception as e:  # a node executor blew up -> surface, don't crash the caller
        return FlowRunResult(status=STATUS_FAILED, data=data, error=str(e))


__all__ = [
    "run_flow", "render", "eval_condition", "FlowRunResult",
    "STATUS_COMPLETED", "STATUS_PAUSED_APPROVAL", "STATUS_PAUSED_DELAY",
    "STATUS_PAUSED_EVENT", "STATUS_REJECTED", "STATUS_FAILED",
    "PAUSED_STATUSES", "ACTIVE_STATUSES",
]
