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
from .runner import STATUS_PAUSED_APPROVAL, run_flow

# agent_runner(brief, data) -> (result, outcome: float | None)
# action_runner(tool, params, data) -> (result, outcome: float | None)
AgentRunner = Callable[[str, dict], "tuple[Any, float | None]"]
ActionRunner = Callable[[str, dict, dict], "tuple[Any, float | None]"]


def execute(flow: Flow, *, agent_runner: AgentRunner, action_runner: ActionRunner,
            owner: str = "", data: dict | None = None,
            approve_fn: Callable[[FlowNode, dict], str] | None = None,
            run_id: str | None = None,
            resume_run_id: str | None = None, decision: str | None = None,
            now: Callable[[], float] | None = None) -> store.FlowRun:
    """Run ``flow`` (or resume a paused run) with live executors, persisting the
    resulting :class:`~.store.FlowRun`. ``run_id`` pre-assigns a fresh run's id
    (so a caller can hand it back for polling); ``decision`` is the human verdict
    when resuming a run paused on an approval (``approved`` / ``rejected``)."""
    resume = None
    prior = None
    if resume_run_id:
        prior = store.load_run(resume_run_id)
        if prior is None:
            raise ValueError(f"no such flow run {resume_run_id!r}")
        run_id = prior.run_id
        owner = owner or prior.owner
        resume = {"node_id": prior.cursor, "data": prior.data}
        if prior.status == STATUS_PAUSED_APPROVAL:
            resume["decision"] = decision or "approved"
    rid = run_id or store.new_run_id()

    def _on_node(node: FlowNode, status: str, outcome: float | None) -> None:
        if outcome is not None:
            node_outcomes.record(flow.id, node.id, node.kind, outcome)

    res = run_flow(
        flow,
        agent_fn=lambda node, brief, d: agent_runner(brief, d),
        action_fn=lambda node, params, d: action_runner(node.tool, params, d),
        approve_fn=approve_fn, data=data, resume=resume, on_node=_on_node, now=now,
    )
    run = store.FlowRun(
        run_id=rid, flow_id=flow.id, status=res.status, data=res.data,
        cursor=res.cursor, resume_at=res.resume_at, prompt=res.prompt,
        error=res.error, owner=owner,
        created=(prior.created if resume_run_id and prior else 0.0),
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


__all__ = ["execute", "default_agent_runner", "default_action_runner"]
