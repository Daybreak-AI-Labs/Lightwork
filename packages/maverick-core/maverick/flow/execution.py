"""Run a stored flow with live executors + durable, resumable run state.

This binds the pure interpreter (:mod:`.runner`) to the real system: an ``agent``
node becomes a governed goal, an ``action`` node a tool call, and every work
node's outcome is recorded per-node (:mod:`.node_outcomes`) for the self-rewrite
pass. The run's state is persisted after each pause/finish (:mod:`.store`) so a
flow paused on an approval survives a restart and resumes days later.

The executors are injected (``agent_runner`` / ``action_runner``), so the whole
driver is testable without an LLM or a live connector; :func:`default_agent_runner`
and :func:`default_action_runner` supply the production bindings.
"""
from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

from . import node_outcomes, store
from .ir import Flow, FlowNode
from .runner import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PAUSED_APPROVAL,
    STATUS_PAUSED_DELAY,
    run_flow,
)

STATUS_RUNNING = "running"   # written while a run is executing (was: stuck on "queued")
TRIGGER_NODE = "__trigger__"  # reserved node id under which a trigger's run-outcome is grounded

_RESUMABLE = (STATUS_PAUSED_APPROVAL, STATUS_PAUSED_DELAY)


class FlowNotResumable(ValueError):
    """A resume was requested for a run that is not paused."""

# agent_runner(brief, data) -> (result, outcome: float | None)
# action_runner(tool, params, data) -> (result, outcome: float | None)
AgentRunner = Callable[[str, dict], "tuple[Any, float | None]"]
ActionRunner = Callable[[str, dict, dict], "tuple[Any, float | None]"]


DEFAULT_AGENT_BUDGET = 5.0   # matches default_agent_runner's per-node cap


def _budget_gated(agent_runner: AgentRunner, cap: float, per_call: float, spent: list) -> AgentRunner:
    """Wrap an agent runner with a per-RUN aggregate spend ceiling. Each agent
    node is reserved at its worst-case per-node budget (``per_call``) against a
    shared, lock-guarded total; once the next reservation would exceed ``cap`` the
    node is skipped instead of spawning another goal. This bounds fan-out spend --
    a wide ``parallel`` or a long ``foreach`` can't multiply cost without a
    ceiling. Conservative: it counts each node at its cap, so it never
    under-counts real spend. Thread-safe for concurrent branches.

    A skipped node grounds a ``None`` outcome, NOT ``0.0``: it never ran, so it is
    not a failure -- grounding 0.0 would make a healthy node look flaky (skewing
    self-rewrite proposals), burn its retries against the still-exhausted gate,
    and fire its on_error branch. ``None`` means "no signal," which is correct."""
    import threading
    lock = threading.Lock()

    def run(brief: str, data: dict, **kw) -> tuple:
        with lock:
            if spent[0] + per_call > cap:
                return (f"skipped: flow budget exhausted (${cap:g} cap)", None)
            spent[0] += per_call
        return agent_runner(brief, data, **kw)
    return run


def _spend_tracked(agent_runner: AgentRunner, per_call: float, spent: list) -> AgentRunner:
    """Wrap a runner to accumulate reserved spend (``per_call`` per invocation)
    into ``spent`` so the run's total cost can be persisted -- used when there is
    NO ``max_dollars`` gate (the gate already tracks spend itself)."""
    import threading
    lock = threading.Lock()

    def run(brief: str, data: dict, **kw) -> tuple:
        with lock:
            spent[0] += per_call
        return agent_runner(brief, data, **kw)
    return run


# The node an agent runner is currently serving, set by _call_agent around the
# call and read by default_agent_runner to attribute the goal's tool usage back
# to the node (for harden-tool inference). A ContextVar, not a kwarg, so the
# runner contract stays (brief, data[, wall]) -- no new signature to break, and
# no double-invocation of the stateful budget wrappers on a fallback path. Set +
# read happen synchronously on one thread (even under the foreach pool), so the
# value is always the right node.
_active_node: contextvars.ContextVar[tuple[str, str]] = contextvars.ContextVar(
    "_flow_active_node", default=("", ""))


def _call_agent(runner: AgentRunner, brief: str, data: dict, wall: float,
                flow_id: str = "", node_id: str = "") -> tuple:
    """Invoke an agent runner, bounding its wall-clock to a node ``timeout`` when
    set so a timed-out node's underlying goal self-terminates near the point the
    flow gives up waiting -- instead of running on to its default cap, still
    spending budget after the flow moved on. Injected 2-arg test runners that
    don't accept ``wall`` are called plainly. Publishes the current node id on a
    ContextVar so the production runner can attribute its goal's tools to it."""
    token = _active_node.set((flow_id, node_id))
    try:
        if wall and wall > 0:
            try:
                return runner(brief, data, wall=wall)
            except TypeError:
                pass
        return runner(brief, data)
    finally:
        _active_node.reset(token)


def execute(flow: Flow, *, agent_runner: AgentRunner, action_runner: ActionRunner,
            owner: str = "", data: dict | None = None,
            approve_fn: Callable[[FlowNode, dict], str] | None = None,
            run_id: str | None = None,
            resume_run_id: str | None = None, decision: str | None = None,
            from_failure: bool = False,
            inputs: dict | None = None, record_outcomes: bool = True,
            agent_budget_dollars: float = DEFAULT_AGENT_BUDGET, origin: str = "manual",
            now: Callable[[], float] | None = None) -> store.FlowRun:
    """Run ``flow`` (or resume a paused run) with live executors, persisting the
    resulting :class:`~.store.FlowRun`. ``run_id`` pre-assigns a fresh run's id
    (so a caller can hand it back for polling); ``decision`` is the human verdict
    when resuming a run paused on an approval (``approved`` / ``rejected``).
    ``record_outcomes=False`` (a dry run) still traces per-node status for the
    overlay but does NOT ground outcomes -- a mock run's fake successes must not
    pollute the self-rewrite signal."""
    resume = None
    prior = None
    if resume_run_id:
        prior = store.load_run(resume_run_id)
        if prior is None:
            raise ValueError(f"no such flow run {resume_run_id!r}")
        # Only a paused run may be resumed -- or, with ``from_failure``, a FAILED
        # run whose failing node was recorded (it re-enters AT that node with the
        # data as of the failure, instead of re-running the whole flow). Without
        # this guard, resuming a completed run re-executes the whole flow
        # (duplicate side effects) and resuming a rejected run runs the very node
        # a human rejected -- defeating the approval gate.
        retryable_failure = (from_failure and prior.status == STATUS_FAILED
                             and bool(prior.cursor))
        if prior.status not in _RESUMABLE and not retryable_failure:
            raise FlowNotResumable(
                f"run {resume_run_id!r} is not paused (status {prior.status!r})"
                + ("; it has no failure cursor to retry from"
                   if from_failure and prior.status == STATUS_FAILED else ""))
        run_id = prior.run_id
        owner = owner or prior.owner
        # Free-form human input merges into the run data on resume, so an approval
        # can also COLLECT data (a corrected value, a note) -- not just yes/no.
        resume_data = dict(prior.data)
        if inputs:
            resume_data.update(inputs)
        resume = {"node_id": prior.cursor, "data": resume_data}
        if retryable_failure:
            resume["rerun"] = True
        elif prior.status == STATUS_PAUSED_APPROVAL:
            resume["decision"] = decision or "approved"
    rid = run_id or store.new_run_id()
    # A fresh run may have a queued placeholder (saved at enqueue with the
    # idempotency key + original inputs) -- preserve those instead of resetting.
    placeholder = store.load_run(rid) if (run_id and not resume_run_id) else None
    # Per-node run trace for the designer overlay. Seeded from the prior run on
    # resume so the pre-pause nodes' status survives across the pause.
    node_states: dict = dict(prior.nodes) if (resume_run_id and prior) else {}

    def _on_node(node: FlowNode, status: str, outcome: float | None) -> None:
        node_states[node.id] = {"status": status, "outcome": outcome}
        if outcome is not None and record_outcomes:
            node_outcomes.record(flow.id, node.id, node.kind, outcome)

    # Origin (what fired this run) comes from the queued placeholder when present,
    # else the caller's argument -- carried across a resume from the prior run.
    run_origin = (prior.origin if (resume_run_id and prior)
                  else (placeholder.origin if placeholder else origin))
    # Track reserved agent spend for the run. The max_dollars gate already tracks
    # it; without a gate, wrap a plain spend tracker so cost is still recorded.
    spent = [0.0]
    if record_outcomes:
        if flow.max_dollars and flow.max_dollars > 0:
            # Aggregate per-run spend ceiling: a wide parallel / long foreach can't
            # fan out unbounded cost (per-node budget alone doesn't bound the COUNT).
            agent_runner = _budget_gated(agent_runner, flow.max_dollars, agent_budget_dollars, spent)
        else:
            agent_runner = _spend_tracked(agent_runner, agent_budget_dollars, spent)

    # Write a "running" state before executing so a long run is distinguishable
    # from a stuck "queued" one on the dashboard (a fresh, non-resume run only).
    if not resume_run_id:
        store.save_run(store.FlowRun(
            run_id=rid, flow_id=flow.id, status=STATUS_RUNNING, owner=owner,
            data=dict(data or {}), origin=run_origin,
            created=(placeholder.created if placeholder else 0.0),
            input_data=(placeholder.input_data if placeholder else dict(data or {})),
            idem_key=(placeholder.idem_key if placeholder else "")))

    res = run_flow(
        flow,
        agent_fn=lambda node, brief, d: _call_agent(agent_runner, brief, d, node.timeout,
                                                    flow_id=flow.id, node_id=node.id),
        action_fn=lambda node, params, d: action_runner(node.tool, params, d),
        approve_fn=approve_fn, data=data, resume=resume, on_node=_on_node, now=now,
        # A subflow node resolves to another SAVED flow; the runner's flow_stack
        # guards against a cyclic reference (this loader stays a plain lookup).
        resolve_flow=store.load_flow,
    )
    # Ground the TRIGGER's run-outcome (which triggers produce valuable runs vs.
    # noise) when a triggered run reaches a terminal state -- the top-of-funnel
    # signal, kept under a reserved node id so it's separable from work nodes.
    if record_outcomes and run_origin != "manual" and res.status in (STATUS_COMPLETED, STATUS_FAILED):
        node_outcomes.record(flow.id, TRIGGER_NODE, "trigger",
                             1.0 if res.status == STATUS_COMPLETED else 0.0)

    run = store.FlowRun(
        run_id=rid, flow_id=flow.id, status=res.status, data=res.data,
        cursor=res.cursor, resume_at=res.resume_at, prompt=res.prompt,
        error=res.error, owner=owner, origin=run_origin, cost_dollars=round(spent[0], 4),
        created=(prior.created if resume_run_id and prior
                 else (placeholder.created if placeholder else 0.0)),
        nodes=node_states,
        # Keep the original trigger payload (from the queued placeholder or this
        # fresh data) / carry it across a resume, so a failed run can be retried
        # from scratch with the same inputs; carry the idempotency key too.
        input_data=(prior.input_data if (resume_run_id and prior)
                    else (placeholder.input_data if placeholder else dict(data or {}))),
        idem_key=(prior.idem_key if (resume_run_id and prior)
                  else (placeholder.idem_key if placeholder else "")),
    )
    store.save_run(run)
    return run


def default_agent_runner(world, *, owner: str = "", budget_dollars: float = 5.0,
                         budget_wall_seconds: float = 3600.0) -> AgentRunner:
    """Production ``agent_runner``: an agent node becomes a governed goal.

    Creates a goal from the (rendered) brief, runs it through the durable runner,
    and grounds a terminal outcome (done=1.0 / not=0.0) as the node's signal.
    Best-effort: a runner failure yields a failure result + a 0.0 outcome rather
    than raising into the flow."""
    def run(brief: str, data: dict, wall: float | None = None) -> tuple[Any, float | None]:
        gid = world.create_goal(str(brief)[:200], str(brief)[:8000], owner=owner)
        # A per-node timeout bounds this goal's wall so a timed-out node's goal
        # self-terminates instead of running to the default cap after the flow
        # gave up on it (kernel rule 3 still holds -- this only ever lowers it).
        ws = min(budget_wall_seconds, wall) if (wall and wall > 0) else budget_wall_seconds
        try:
            from ..runner import run_goal_in_thread
            result = run_goal_in_thread(
                gid, budget_dollars=budget_dollars, wall_seconds=ws)
        except Exception as e:  # pragma: no cover -- runner wiring varies by env
            return (f"agent step failed: {type(e).__name__}: {e}", 0.0)
        g = world.get_goal(gid)
        outcome = 1.0 if (g and g.status == "done") else 0.0
        # Attribute this goal's tool usage back to the node (from the ContextVar
        # _call_agent published), so the self-rewrite pass can infer which tool a
        # reliably-one-tool agent node should harden into. Best-effort + only when
        # trajectory capture recorded anything.
        fid, nid = _active_node.get()
        if fid and nid:
            _capture_node_tools(fid, nid, gid)
        return (result if result is not None else (g.result if g else ""), outcome)
    return run


def _capture_node_tools(flow_id: str, node_id: str, goal_id: int) -> None:
    """Record the distinct tools an agent node's goal used (for harden inference).
    No-op when trajectory capture is off (nothing to read); never raises."""
    try:
        from .. import trajectory_store
        if not trajectory_store.enabled():
            return
        tools = trajectory_store.tools_for_goal(goal_id)
        from . import node_tools
        node_tools.record(flow_id, node_id, tools)
    except Exception:  # pragma: no cover -- capture must never break a run
        pass


def default_action_runner(world, sandbox=None) -> ActionRunner:
    """Production ``action_runner``: resolve the named tool from the base registry
    and call it with the rendered params. Outcome is success (1.0) unless the tool
    returns an ``ERROR:`` string (0.0). Never raises into the flow."""
    def run(tool: str, params: dict, data: dict) -> tuple[Any, float | None]:
        try:
            from ..sandbox import LocalBackend
            from ..tools import base_registry
            reg = base_registry(world, sandbox or LocalBackend(), goal_id=None)
            t = next((x for x in reg.all() if x.name == tool), None)
            if t is None:
                return (f"ERROR: no such tool {tool!r}", 0.0)
            result = t.fn(**params)
        except Exception as e:
            return (f"ERROR: {type(e).__name__}: {e}", 0.0)
        ok = not (isinstance(result, str) and result.startswith("ERROR"))
        return (result, 1.0 if ok else 0.0)
    return run


def sandbox_runners() -> tuple[AgentRunner, ActionRunner]:
    """Mock executors for a DRY RUN: they exercise the graph (routing, data
    threading, {{ }} rendering) without creating a real goal or calling a real
    connector, so a user can safely test a flow with sample input before wiring
    it to a live trigger. Each returns a labeled placeholder + a success outcome."""
    def agent(brief: str, data: dict) -> tuple[Any, float | None]:
        return (f"[dry-run agent] {str(brief)[:120]}", 1.0)

    def action(tool: str, params: dict, data: dict) -> tuple[Any, float | None]:
        return ({"dry_run": True, "tool": tool, "params": params}, 1.0)
    return agent, action


__all__ = ["execute", "default_agent_runner", "default_action_runner", "sandbox_runners"]
