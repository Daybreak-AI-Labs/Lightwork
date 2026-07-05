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

from collections.abc import Callable
from typing import Any

from . import node_outcomes, store
from .ir import Flow, FlowNode
from .runner import (
    PAUSED_STATUSES,
    STATUS_FAILED,
    STATUS_PAUSED_APPROVAL,
    run_flow,
)

_RESUMABLE = PAUSED_STATUSES


class FlowNotResumable(ValueError):
    """A resume was requested for a run that is not paused."""

# agent_runner(brief, data) -> (result, outcome: float | None)
# action_runner(tool, params, data) -> (result, outcome: float | None)
AgentRunner = Callable[[str, dict], "tuple[Any, float | None]"]
ActionRunner = Callable[[str, dict, dict], "tuple[Any, float | None]"]


DEFAULT_AGENT_BUDGET = 5.0   # matches default_agent_runner's per-node cap


def _budget_gated(agent_runner: AgentRunner, cap: float, per_call: float) -> AgentRunner:
    """Wrap an agent runner with a per-RUN aggregate spend ceiling. Each agent
    node is reserved at its worst-case per-node budget (``per_call``) against a
    shared, lock-guarded total; once the next reservation would exceed ``cap`` the
    node is skipped with an error outcome instead of spawning another goal. This
    bounds fan-out spend -- a wide ``parallel`` or a long ``foreach`` can't
    multiply cost without a ceiling. Conservative: it counts each node at its
    cap, so it never under-counts real spend. Thread-safe for concurrent branches."""
    import threading
    lock = threading.Lock()
    spent = [0.0]

    def run(brief: str, data: dict) -> tuple:
        with lock:
            if spent[0] + per_call > cap:
                return (f"skipped: flow budget exhausted (${cap:g} cap)", 0.0)
            spent[0] += per_call
        return agent_runner(brief, data)
    return run


def execute(flow: Flow, *, agent_runner: AgentRunner, action_runner: ActionRunner,
            owner: str = "", data: dict | None = None,
            approve_fn: Callable[[FlowNode, dict], str] | None = None,
            run_id: str | None = None,
            resume_run_id: str | None = None, decision: str | None = None,
            from_failure: bool = False,
            inputs: dict | None = None, record_outcomes: bool = True,
            agent_budget_dollars: float = DEFAULT_AGENT_BUDGET,
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
    # Wall-clock per work node (accumulated across retries), measured at the
    # executor seam so the pure runner's on_node contract stays unchanged.
    durations: dict[str, float] = {}
    # A concurrent foreach runs the SAME body node ids on several threads through
    # this shared sink; guard the trace mutations so the duration read-modify-
    # write can't lose updates.
    import threading as _threading
    trace_lock = _threading.Lock()

    def _timed(node_id: str, fn, *args):
        import time as _time
        t0 = _time.perf_counter()
        try:
            return fn(*args)
        finally:
            dt = _time.perf_counter() - t0
            with trace_lock:
                durations[node_id] = durations.get(node_id, 0.0) + dt

    def _on_node(node: FlowNode, status: str, outcome: float | None) -> None:
        with trace_lock:
            entry: dict = {"status": status, "outcome": outcome}
            if node.id in durations:
                entry["seconds"] = round(durations[node.id], 3)
            node_states[node.id] = entry
        if outcome is not None and record_outcomes:
            node_outcomes.record(flow.id, node.id, node.kind, outcome)

    # Aggregate per-run spend ceiling: cap total agent-goal spend so a wide
    # parallel / long foreach can't fan out unbounded cost (per-node budget alone
    # doesn't bound the COUNT of nodes). Only for a real (grounded) run.
    if flow.max_dollars and flow.max_dollars > 0 and record_outcomes:
        agent_runner = _budget_gated(agent_runner, flow.max_dollars, agent_budget_dollars)

    res = run_flow(
        flow,
        agent_fn=lambda node, brief, d: _timed(node.id, agent_runner, brief, d),
        action_fn=lambda node, params, d: _timed(node.id, action_runner, node.tool, params, d),
        approve_fn=approve_fn, data=data, resume=resume, on_node=_on_node, now=now,
        # A subflow node resolves to another SAVED flow; the runner's flow_stack
        # guards against a cyclic reference (this loader stays a plain lookup).
        resolve_flow=store.load_flow,
    )
    run = store.FlowRun(
        run_id=rid, flow_id=flow.id, status=res.status, data=res.data,
        cursor=res.cursor, resume_at=res.resume_at, prompt=res.prompt,
        error=res.error, owner=owner,
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
    def run(brief: str, data: dict) -> tuple[Any, float | None]:
        gid = world.create_goal(str(brief)[:200], str(brief)[:8000], owner=owner)
        try:
            from ..runner import run_goal_in_thread
            result = run_goal_in_thread(
                gid, budget_dollars=budget_dollars, wall_seconds=budget_wall_seconds)
        except Exception as e:  # pragma: no cover -- runner wiring varies by env
            return (f"agent step failed: {type(e).__name__}: {e}", 0.0)
        g = world.get_goal(gid)
        outcome = 1.0 if (g and g.status == "done") else 0.0
        return (result if result is not None else (g.result if g else ""), outcome)
    return run


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
