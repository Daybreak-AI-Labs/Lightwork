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
import copy
import inspect
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from ..tool_results import tool_result_failed
from . import node_outcomes, store
from .action_episodes import (
    ActionEpisodeCapture,
    action_capture_scope,
    begin_capture,
    finish_capture,
    native_authorized,
    native_completed,
    resume_capture,
)
from .ir import Flow, FlowNode
from .runner import (
    PAUSED_STATUSES,
    STATUS_BUDGET_LIMITED,
    STATUS_CLAIMED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INDETERMINATE,
    STATUS_PAUSED_APPROVAL,
    STATUS_RESUMING,
    STATUS_RUNNING,
    FlowApprovalStale,
    FlowBudgetExceeded,
    run_flow,
)

TRIGGER_NODE = "__trigger__"  # reserved node id under which a trigger's run-outcome is grounded

_RESUMABLE = PAUSED_STATUSES


class FlowNotResumable(ValueError):
    """A resume was requested for a run that is not paused."""


def _safe_failure(exc: BaseException) -> str:
    try:
        from ..safety.secret_detector import redact

        text, _matches = redact(str(exc))
        return text[:500]
    except Exception:
        return "failure details were redacted"


def _approval_actor_matches(actor: str, assignee: str) -> bool:
    expected = str(assignee or "").strip().casefold()
    if not expected:
        return True
    principal = str(actor or "").strip().casefold()
    aliases = {principal}
    if principal.startswith("user:") and principal[5:]:
        subject = principal[5:]
        aliases.update({subject, f"@{subject}"})
    return expected in aliases

# agent_runner(brief, data) -> (result, outcome: float | None)
# action_runner(tool, params, data) -> (result, outcome: float | None)
AgentRunner = Callable[[str, dict], "tuple[Any, float | None]"]
ActionRunner = Callable[[str, dict, dict], "tuple[Any, float | None]"]


DEFAULT_AGENT_BUDGET = 5.0   # legacy injected-runner reservation; production meters actual usage


class _MeteredAgentResult(tuple):
    """Keep the two-value executor contract with response-local spend."""

    def __new__(cls, result, outcome, dollars):
        value = super().__new__(cls, (result, outcome))
        value.dollars = float(dollars)
        return value


def _budget_gated(agent_runner: AgentRunner, cap: float, per_call: float, spent: list) -> AgentRunner:
    """Wrap an agent runner with a per-RUN aggregate spend ceiling. Each agent
    node is reserved at its worst-case per-node budget (``per_call``) against a
    shared, lock-guarded total; once the next reservation would exceed ``cap`` the
    node is held instead of spawning another goal. This bounds fan-out spend --
    a wide ``parallel`` or a long ``foreach`` can't multiply cost without a
    ceiling. Conservative: it counts each node at its cap, so it never
    under-counts real spend. Thread-safe for concurrent branches.

    Production runners meter actual usage and accept the remaining allowance.
    With an explicit aggregate cap they serialize agent admission, so parallel
    children cannot each consume the same remainder. Injected legacy runners
    retain their declared conservative reservation contract. A pre-dispatch
    refusal is a control stop, with no business result or learning outcome."""
    import threading
    lock = threading.Lock()

    def run(brief: str, data: dict, **kw) -> tuple:
        with lock:
            if getattr(agent_runner, "_meters_usage", False):
                remaining = cap - spent[0]
                if remaining <= 0:
                    raise FlowBudgetExceeded(f"flow spending allowance reached (${cap:g}) before dispatch")
                result = agent_runner(brief, data, allowance=remaining, **kw)
                spent[0] += result.dollars
                return result
            if spent[0] + per_call > cap:
                raise FlowBudgetExceeded(f"flow spending allowance reached (${cap:g}) before dispatch")
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
        if getattr(agent_runner, "_meters_usage", False):
            result = agent_runner(brief, data, **kw)
            with lock:
                spent[0] += result.dollars
            return result
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
_active_node: contextvars.ContextVar[tuple[str, str, str, str]] = contextvars.ContextVar(
    "_flow_active_node", default=("", "", "", ""))


def _runner_accepts_wall(runner: AgentRunner) -> bool:
    """Inspect the runner contract without executing a stateful call twice."""
    try:
        parameters = inspect.signature(runner).parameters.values()
    except (TypeError, ValueError):
        # The production contract accepts ``wall``. For opaque callables, make
        # one contract-compliant invocation and surface any error; never retry a
        # possibly side-effecting call based on its exception type.
        return True
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        or (
            parameter.name == "wall"
            and parameter.kind
            in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
        )
        for parameter in parameters
    )


def _call_agent(runner: AgentRunner, brief: str, data: dict, wall: float,
                flow_id: str = "", node_id: str = "", revision: str = "",
                cohort: str = "") -> tuple:
    """Invoke an agent runner, bounding its wall-clock to a node ``timeout`` when
    set so a timed-out node's underlying goal self-terminates near the point the
    flow gives up waiting -- instead of running on to its default cap, still
    spending budget after the flow moved on. Injected 2-arg test runners that
    don't accept ``wall`` are called plainly. Publishes the current node id on a
    ContextVar so the production runner can attribute its goal's tools to it."""
    token = _active_node.set((flow_id, node_id, revision, cohort))
    try:
        if wall and wall > 0 and _runner_accepts_wall(runner):
            return runner(brief, data, wall=wall)
        return runner(brief, data)
    finally:
        _active_node.reset(token)


def _serialize_runs(fn):
    """Hold one durable run lock across state check, execution, and save.

    Queue delivery is intentionally at-least-once.  Without this boundary two
    workers can both load the same paused approval and both execute the approved
    side effect before either publishes ``completed``.  The second worker now
    observes the first worker's terminal state and receives FlowNotResumable.
    """
    @wraps(fn)
    def guarded(flow: Flow, **kwargs):
        resume_id = kwargs.get("resume_run_id")
        run_id = resume_id or kwargs.get("run_id")
        if not run_id:
            return fn(flow, **kwargs)
        with store.run_lock(str(run_id)):
            if not resume_id:
                current = store.load_run(str(run_id))
                if current is not None and current.status == STATUS_RUNNING:
                    # A previous worker crossed the durable execution boundary
                    # and vanished.  Its last tool may have committed, so an
                    # at-least-once delivery must quarantine rather than replay.
                    current.status = STATUS_INDETERMINATE
                    current.error = (
                        current.error
                        or "fresh run interrupted after execution began; external effects may have committed"
                    )
                    store.save_run(current)
                    return current
                if current is not None and current.status not in {"queued", STATUS_CLAIMED}:
                    # At-least-once queue redelivery after the first attempt
                    # published a terminal/pause state is a no-op.
                    return current
            try:
                return fn(flow, **kwargs)
            except BaseException as exc:
                # If execution escaped after the durable resume claim, the
                # external-effect boundary is unknowable.  Preserve that honest
                # state and never let queue redelivery replay it automatically.
                current = store.load_run(str(run_id))
                if current is not None and current.status in {STATUS_RESUMING, STATUS_RUNNING}:
                    current.status = STATUS_INDETERMINATE
                    phase = "resume" if resume_id else "execution"
                    current.error = (
                        f"{phase} interrupted: {type(exc).__name__}: "
                        f"{_safe_failure(exc)}"
                    )
                    store.save_run(current)
                raise
    return guarded


@_serialize_runs
def execute(flow: Flow, *, agent_runner: AgentRunner, action_runner: ActionRunner,  # noqa: C901
            owner: str = "", data: dict | None = None,
            approve_fn: Callable[[FlowNode, dict], str] | None = None,
            run_id: str | None = None,
            resume_run_id: str | None = None, decision: str | None = None,
            decided_by: str = "",
             expired: bool = False,
             approval_binding_sha256: str = "",
             approval_expiry_binding_sha256: str = "",
             approval_authority_binding_sha256: str = "",
             approval_decision_attestation: object | None = None,
             from_failure: bool = False,
            budget_limit_dollars: float | None = None,
            inputs: dict | None = None, record_outcomes: bool = True,
            agent_budget_dollars: float = DEFAULT_AGENT_BUDGET, origin: str = "manual",
            now: Callable[[], float] | None = None,
            episode_capture: ActionEpisodeCapture | None = None) -> store.FlowRun:
    """Run ``flow`` (or resume a paused run) with live executors, persisting the
    resulting :class:`~.store.FlowRun`. ``run_id`` pre-assigns a fresh run's id
    (so a caller can hand it back for polling); ``decision`` is the human verdict
    when resuming a run paused on an approval (``approved`` / ``rejected``).
    ``record_outcomes=False`` (a dry run) still traces per-node status for the
    overlay but does NOT ground outcomes -- a mock run's fake successes must not
    pollute the self-rewrite signal."""
    # The persisted definition and approved data must describe what executes,
    # even if the original caller edits its objects while a review is pending.
    flow = copy.deepcopy(flow)
    data = copy.deepcopy(data)
    inputs = copy.deepcopy(inputs)
    resume = None
    prior = None
    definition_digest = ""
    release_digest = ""
    release_id = ""
    definition_version = 0
    definition_revision = ""
    subflow_digests: dict[str, str | None] = {}
    execution_channel = ""
    execution_user_id = ""
    allowed_suites: list[str] | None = None
    has_pinned_plan = False
    approval_resume = False
    approval_actor = ""
    approval_submitted_inputs: dict = {}
    approval_check_binding = ""
    approval_check_expired = False
    approval_snapshot = None
    if budget_limit_dollars is not None and not resume_run_id:
        raise FlowNotResumable("a run allowance change requires a saved budget-held run")
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
        retryable_failure = (from_failure and prior.status in {STATUS_FAILED, STATUS_BUDGET_LIMITED}
                             and bool(prior.cursor))
        approval_resume = prior.status == STATUS_PAUSED_APPROVAL
        if prior.status not in _RESUMABLE and not retryable_failure:
            raise FlowNotResumable(
                f"run {resume_run_id!r} is not paused (status {prior.status!r})"
                + ("; it has no failure cursor to retry from"
                   if from_failure and prior.status == STATUS_FAILED else ""))
        if budget_limit_dollars is not None:
            import math
            if (
                prior.status != STATUS_BUDGET_LIMITED or not from_failure
                or isinstance(budget_limit_dollars, bool)
                or not math.isfinite(budget_limit_dollars)
                or budget_limit_dollars <= max(
                    prior.cost_dollars, prior.budget_limit_dollars or 0.0,
                )
            ):
                raise FlowNotResumable("a budget-held run requires a larger finite total allowance")
        # Resume the immutable graph captured at run start.  This makes an
        # approval meaningful: an edit made while the run is paused cannot swap
        # the reviewed downstream action (or a referenced child flow) for a new
        # one.  Runs created before snapshots existed retain legacy behaviour.
        if prior.definition_digest:
            flow = store.load_flow_snapshot(prior.definition_digest)
            definition_digest = prior.definition_digest
            release_digest = prior.release_digest
            release_id = prior.release_id
            definition_version = prior.definition_version
            definition_revision = prior.definition_revision or flow.revision
            subflow_digests = dict(prior.subflow_digests)
            has_pinned_plan = True
        execution_channel = prior.execution_channel
        execution_user_id = prior.execution_user_id
        allowed_suites = (
            list(prior.allowed_suites) if prior.allowed_suites is not None else None
        )
        run_id = prior.run_id
        owner = owner or prior.owner
        # Free-form human input merges into the run data on resume, so an approval
        # can also COLLECT data (a corrected value, a note) -- not just yes/no.
        # The merge is append-only for existing run data: a resume caller must not
        # be able to change values that earlier nodes produced and that a human
        # may have reviewed at the pause. When the paused node declares a `form`,
        # only its declared field names are accepted as the server-side allowlist;
        # form-less nodes keep free-form *new* inputs for compatibility.
        submitted_inputs = dict(inputs or {})
        resume_data = dict(prior.data)
        if inputs:
            paused = flow.node(prior.cursor) if prior.cursor else None
            declared = [str(f.get("name")) for f in getattr(paused, "form", None) or []
                        if isinstance(f, dict) and f.get("name")]
            allowed = set(declared) if declared else set(inputs)
            inputs = {k: v for k, v in inputs.items()
                      if k in allowed and k not in resume_data}
            resume_data.update(inputs)
        resume = {"node_id": prior.cursor, "data": resume_data}
        if retryable_failure:
            resume["rerun"] = True
            if prior.continuation:
                resume["continuation"] = prior.continuation
        elif prior.status == STATUS_PAUSED_APPROVAL:
            # `expired` rides outside the human-verdict string. The dashboard
            # worker validates its bound scheduler reservation and due time
            # immediately before calling this execution boundary.
            if expired:
                if decision or decided_by or submitted_inputs:
                    raise ValueError("approval expiry payload is invalid")
                approval_submitted_inputs = submitted_inputs
                approval_check_binding = approval_expiry_binding_sha256
                approval_check_expired = True
                resume["expired"] = True
            else:
                verdict = str(decision or "").strip()
                paused = flow.node(prior.cursor) if prior.cursor else None
                allowed_verdicts = {"approved", "rejected"}
                if paused is not None:
                    allowed_verdicts.update(str(v) for v in paused.choices)
                if flow.recipe_teaching is not None and prior.cursor == "teaching_review":
                    # Selecting a continuation is distinct from approving it.
                    # Refuse generic approval while retaining the current pause.
                    allowed_verdicts = {"route:0", "route:1", "rejected"}
                if not verdict or verdict not in allowed_verdicts:
                    raise ValueError("an explicit valid approval decision is required")
                approval_actor = str(decided_by or "").strip()
                if not approval_actor:
                    raise ValueError("an attributable approver identity is required")
                assignee = str(
                    (prior.human or {}).get("assignee")
                    or (paused.assignee if paused is not None else "")
                    or ""
                )
                if not _approval_actor_matches(approval_actor, assignee):
                    raise ValueError("the approver is not the declared assignee")
                approval_submitted_inputs = submitted_inputs
                approval_check_binding = approval_binding_sha256
                resume["decision"] = verdict
    rid = run_id or store.new_run_id()
    # A fresh run may have a queued placeholder (saved at enqueue with the
    # idempotency key + original inputs) -- preserve those instead of resetting.
    placeholder = store.load_run(rid) if (run_id and not resume_run_id) else None
    if placeholder is not None and placeholder.definition_digest:
        flow = store.load_flow_snapshot(placeholder.definition_digest)
        definition_digest = placeholder.definition_digest
        release_digest = placeholder.release_digest
        release_id = placeholder.release_id
        definition_version = placeholder.definition_version
        definition_revision = placeholder.definition_revision or flow.revision
        subflow_digests = dict(placeholder.subflow_digests)
        has_pinned_plan = True
    if placeholder is not None:
        execution_channel = placeholder.execution_channel
        execution_user_id = placeholder.execution_user_id
        allowed_suites = (
            list(placeholder.allowed_suites)
            if placeholder.allowed_suites is not None else None
        )
    if has_pinned_plan:
        # Durable runs created before owner-bound snapshots were introduced may
        # already contain a crafted cross-user child manifest.  Revalidate the
        # immutable objects before any node executor is reached.
        expected_flow_id = (
            prior.flow_id
            if prior is not None
            else placeholder.flow_id
            if placeholder is not None
            else flow.id
        )
        if flow.id != expected_flow_id:
            raise store.FlowSnapshotError(
                "flow snapshot identity does not match the durable run"
            )
        store.validate_snapshot_bundle_owners(flow, subflow_digests)
    plan_errors = flow.validate()
    if plan_errors:
        raise ValueError(
            "invalid flow execution plan: " + "; ".join(plan_errors))
    if approval_resume and prior is not None:
        from .approvals import require_current_paused_operation

        # Preserve the exact durable pause that the human reviewed.  The live
        # run row moves to ``resuming`` once an effect is admitted, but every
        # later effect must still be checked against this immutable operation.
        approval_snapshot = copy.deepcopy(prior)
        require_current_paused_operation(
            approval_snapshot,
            flow,
            inputs=approval_submitted_inputs,
            expected_binding=approval_check_binding,
            authority_binding_sha256=approval_authority_binding_sha256,
            decision=str(resume.get("decision") or ""),
            decided_by=approval_actor,
            expired=approval_check_expired,
            decision_attestation=approval_decision_attestation,
        )
    if not has_pinned_plan:
        definition_digest, definition_version, subflow_digests = (
            store.snapshot_flow_bundle(flow))
        release_digest = store.release_digest_for(
            definition_digest, subflow_digests,
        )
        release_id = release_digest
        definition_revision = flow.revision
    evidence_cohort = store.definition_cohort(
        definition_revision or flow.revision,
        definition_version or flow.version,
    )
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
            node_outcomes.record(
                flow.id,
                node.id,
                node.kind,
                outcome,
                revision=definition_revision or flow.revision,
                cohort=evidence_cohort,
            )

    # Origin (what fired this run) comes from the queued placeholder when present,
    # else the caller's argument -- carried across a resume from the prior run.
    run_origin = (prior.origin if (resume_run_id and prior)
                  else (placeholder.origin if placeholder else origin))
    # Track reserved agent spend for the run. Seed resumed runs from the durable
    # cost already reserved before the pause so flow.max_dollars remains an
    # aggregate per-run cap across approval/delay resume boundaries. The
    # max_dollars gate updates this list too; without a gate, wrap a plain spend
    # tracker so cost is still recorded.
    spent = [float(prior.cost_dollars) if (resume_run_id and prior) else 0.0]
    run_budget_limit = (
        budget_limit_dollars if budget_limit_dollars is not None
        else prior.budget_limit_dollars if prior is not None and prior.budget_limit_dollars is not None
        else flow.max_dollars
    )
    if record_outcomes:
        if run_budget_limit and run_budget_limit > 0:
            # Aggregate per-run spend ceiling: a wide parallel / long foreach can't
            # fan out unbounded cost (per-node budget alone doesn't bound the COUNT).
            agent_runner = _budget_gated(agent_runner, run_budget_limit, agent_budget_dollars, spent)
        else:
            agent_runner = _spend_tracked(agent_runner, agent_budget_dollars, spent)

    # Write a "running" state before executing so a long run is distinguishable
    # from a stuck "queued" one on the dashboard (a fresh, non-resume run only).
    if not resume_run_id:
        running = store.FlowRun(
            run_id=rid, flow_id=flow.id, status=STATUS_RUNNING, owner=owner,
            data=dict(data or {}), origin=run_origin,
            created=(placeholder.created if placeholder else 0.0),
            input_data=(placeholder.input_data if placeholder else dict(data or {})),
            idem_key=(placeholder.idem_key if placeholder else ""),
            dry_run=(placeholder.dry_run if placeholder else not record_outcomes),
            definition_digest=definition_digest,
            release_digest=release_digest,
            release_id=release_id,
            definition_version=definition_version,
            definition_revision=definition_revision,
            subflow_digests=subflow_digests,
            execution_channel=execution_channel,
            execution_user_id=execution_user_id,
            allowed_suites=allowed_suites,
        )
        store.save_run(running)

    if resume_run_id and prior is not None:
        # Enrollment is loaded by the authoritative tenant/run identity only.
        # Native approval validation above has succeeded; the saved pause is
        # still intact here, before any durable resuming claim or tool effect.
        durable_capture = resume_capture(
            flow, copy.deepcopy(prior), initial_data=copy.deepcopy(prior.input_data),
            submitted_inputs=copy.deepcopy(submitted_inputs), from_failure=from_failure,
            expired=expired, decision=str(decision or ""),
            decided_by=approval_actor,
            approval_binding_sha256=approval_check_binding,
            approval_authority_binding_sha256=approval_authority_binding_sha256,
            decision_attestation=approval_decision_attestation,
        )
        if durable_capture is not None:
            episode_capture = durable_capture
    begin_capture(
        episode_capture, flow, prior if resume_run_id else running,
        initial_data=dict(data or {}), resumed=bool(resume_run_id),
    )

    def _resolve_pinned_flow(flow_ref: str) -> Flow | None:
        if flow_ref in subflow_digests:
            digest = subflow_digests[flow_ref]
            return store.load_flow_snapshot(digest) if digest else None
        # Backward compatibility for a run persisted before subflow snapshots
        # existed.  New runs never reach this path for a resolvable reference.
        return store.load_flow(flow_ref) if (resume_run_id and not has_pinned_plan) else None

    if resume_run_id and prior is not None and not approval_resume:
        # First-writer-wins claim BEFORE any downstream executor is invoked.  A
        # crash after this point leaves an indeterminate run, never the old
        # paused row that another delivery could replay.
        prior.status = STATUS_RESUMING
        store.save_run(prior)

    approval_effect_claimed = False
    approval_effect_claim_lock = _threading.Lock()

    def _dispatch_action(node: FlowNode, params: dict, action_data: dict):
        nonlocal approval_effect_claimed
        if not approval_resume or approval_snapshot is None:
            return action_runner(node.tool, params, action_data)

        from ..approval_cards import ApprovalCardError
        from ..authority_effects import authority_effect_fence
        from .approvals import require_current_paused_operation

        # Authority mutations and the provider boundary share this fence.  A
        # mutation that wins is observed by the final validation; one that loses
        # cannot land between validation and dispatch.  The durable resume claim
        # is also made here, so a rejection before the first effect leaves the
        # run paused for a fresh human review while a later rejection is honestly
        # quarantined as indeterminate by ``_serialize_runs``.
        with authority_effect_fence():
            try:
                require_current_paused_operation(
                    approval_snapshot,
                    flow,
                    inputs=approval_submitted_inputs,
                    expected_binding=approval_check_binding,
                    authority_binding_sha256=approval_authority_binding_sha256,
                    decision=str(resume.get("decision") or ""),
                    decided_by=approval_actor,
                    expired=approval_check_expired,
                    decision_attestation=approval_decision_attestation,
                )
            except ApprovalCardError as exc:
                raise FlowApprovalStale(
                    "Approval authority changed before execution. Review the current operation again."
                ) from exc
            with approval_effect_claim_lock:
                if not approval_effect_claimed:
                    prior.status = STATUS_RESUMING
                    if not expired:
                        prior.decided_by = approval_actor
                    store.save_run(prior)
                    approval_effect_claimed = True
            return action_runner(node.tool, params, action_data)

    def _run_action(node: FlowNode, params: dict, action_data: dict):
        with action_capture_scope(episode_capture, node):
            return _dispatch_action(node, params, action_data)

    res = run_flow(
        flow,
        run_id=rid,
        agent_fn=lambda node, brief, d: _timed(node.id, lambda: _call_agent(
            agent_runner,
            brief,
            d,
            node.timeout,
            flow_id=flow.id,
            node_id=node.id,
            revision=definition_revision or flow.revision,
            cohort=evidence_cohort,
        )),
        action_fn=lambda node, params, d: _timed(
            node.id, lambda: _run_action(node, params, d)
        ),
        approve_fn=approve_fn, data=data, resume=resume, on_node=_on_node, now=now,
        # A subflow node resolves to another SAVED flow; the runner's flow_stack
        # guards against a cyclic reference (this loader stays a plain lookup).
        resolve_flow=_resolve_pinned_flow,
    )
    # Ground the TRIGGER's run-outcome (which triggers produce valuable runs vs.
    # noise) when a triggered run reaches a terminal state -- the top-of-funnel
    # signal, kept under a reserved node id so it's separable from work nodes.
    if record_outcomes and run_origin != "manual" and res.status in (STATUS_COMPLETED, STATUS_FAILED):
        node_outcomes.record(
            flow.id,
            TRIGGER_NODE,
            "trigger",
            1.0 if res.status == STATUS_COMPLETED else 0.0,
            revision=definition_revision or flow.revision,
            cohort=evidence_cohort,
        )

    # Snapshot the paused human task's declared UI (choices/form/assignee) onto
    # the run, like `prompt` -- the viewer must render what the node said AT
    # PAUSE time, not whatever the flow definition says by the time a human looks.
    human: dict = {}
    if res.status == STATUS_PAUSED_APPROVAL and res.cursor:
        paused_node = flow.node(res.cursor)
        if paused_node is not None:
            from ..approval_cards import build_approval_card, digest
            from .approvals import current_flow_runtime_authority

            next_node = flow.node(paused_node.next) if paused_node.next else None
            continuation = next_node.to_dict() if next_node is not None else {
                "kind": "complete", "id": "end",
            }
            target_label = (
                f"{next_node.kind} node {next_node.label or next_node.id}"
                if next_node is not None
                else f"complete flow {flow.id}"
            )
            expiry = (
                float(res.resume_at)
                if res.resume_at is not None
                else time.time() + 7 * 24 * 3600
            )
            from .payloads import SCHEMA as data_format
            from .payloads import approval_data

            persisted_data = approval_data(res.data or {})
            runtime_authority = current_flow_runtime_authority(
                owner=str(owner or ""),
                execution_user_id=str(execution_user_id or ""),
                execution_channel=str(execution_channel or ""),
                allowed_suites=allowed_suites,
            )
            approval_card = build_approval_card(
                action="flow.resume",
                summary=res.prompt or f"Resume {flow.name or flow.id}",
                target_label=target_label,
                target={
                    "flow_id": flow.id,
                    "pause_node": paused_node.id,
                    "continuation": continuation,
                },
                parameters={"data": persisted_data, "continuation": continuation},
                parameter_summary=[
                    f"Flow: {flow.name or flow.id}",
                    f"Continue after approval node: {paused_node.id}",
                    f"Current input digest: {digest(persisted_data)}",
                    f"Next node: {target_label}",
                ],
                actor=owner or "local:operator",
                workspace=runtime_authority["workspace"],
                role=runtime_authority["role"],
                authority_versions={
                    "definition_digest": definition_digest,
                    "release_digest": release_digest,
                    "release_id": release_id,
                    "execution_principal_sha256": digest(
                        execution_user_id or owner or "local:operator"
                    ),
                    "allowed_suites_sha256": digest(allowed_suites),
                    "runtime_authority_sha256": runtime_authority[
                        "authority_sha256"
                    ],
                },
                input_versions={
                    "run_data_format": data_format,
                    "run_data_sha256": digest(persisted_data),
                    "definition_revision": definition_revision or flow.revision,
                },
                credential_versions={
                    "execution_channel": execution_channel or "local",
                    "inventory_sha256": runtime_authority[
                        "credential_inventory_sha256"
                    ],
                },
                budget={
                    "currency": "USD",
                    "run_limit_dollars": run_budget_limit or "configured runtime limit",
                    "reserved_so_far_dollars": round(spent[0], 4),
                    "authority_sha256": runtime_authority[
                        "budget_authority_sha256"
                    ],
                },
                expires_at=expiry,
                known_effects=[
                    "Resumes only the immutable published flow release recorded on this run.",
                    "The remaining flow may invoke its reviewed external action nodes once.",
                ],
                unresolved_assumptions=[
                    "Connector credentials and target records must still pass dispatch-time authorization.",
                    "External systems may apply notifications or automation after a tool action.",
                ],
                before="The run remains paused and no continuation node has executed.",
                after="The pinned continuation may execute once under current worker authority.",
                irreversible=bool(next_node is not None and next_node.kind == "action"),
            )
            human = {"choices": list(paused_node.choices),
                     "assignee": paused_node.assignee,
                     "form": [dict(f) for f in paused_node.form],
                     "approval_card": approval_card}
    run = store.FlowRun(
        run_id=rid, flow_id=flow.id, status=res.status, data=res.data,
        cursor=res.cursor, resume_at=res.resume_at, prompt=res.prompt,
        continuation=res.continuation,
        error=res.error, owner=owner, origin=run_origin, cost_dollars=spent[0],
        budget_limit_dollars=run_budget_limit or None,
        human=human,
        decided_by=(
            approval_actor
            if resume_run_id and prior is not None and approval_resume and not expired
            else (prior.decided_by if prior is not None else "")
        ),
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
        dry_run=(prior.dry_run if (resume_run_id and prior)
                 else (placeholder.dry_run if placeholder else not record_outcomes)),
        definition_digest=definition_digest,
        release_digest=release_digest,
        release_id=release_id,
        definition_version=definition_version,
        definition_revision=definition_revision,
        subflow_digests=subflow_digests,
        execution_channel=execution_channel,
        execution_user_id=execution_user_id,
        allowed_suites=allowed_suites,
    )
    store.save_run(run)
    finish_capture(episode_capture, run)
    # Learn the shape of this flow's output keys from a REAL run's data (never a
    # dry run -- mock executors produce placeholder shapes), so the designer's
    # data pills can offer nested keys. Field names + types only; best-effort.
    if record_outcomes and res.status != STATUS_INDETERMINATE:
        from .schema_infer import infer_output_shapes
        # record_flow_schema owns the best-effort swallow.
        store.record_flow_schema(
            flow.id,
            infer_output_shapes(flow, res.data),
            revision=definition_revision or flow.revision,
        )
    return run


def default_agent_runner(
    world,
    *,
    owner: str = "",
    budget_dollars: float | None = None,
    budget_wall_seconds: float | None = None,
    channel: str | None = None,
    user_id: str | None = None,
    allowed_suites: frozenset[str] | None = None,
    concurrency_principal: str | None = None,
) -> AgentRunner:
    """Production ``agent_runner``: an agent node becomes a governed goal.

    Creates a goal from the (rendered) brief, runs it through the durable runner,
    and grounds a terminal outcome (done=1.0 / not=0.0) as the node's signal.
    Best-effort: a runner failure yields a failure result + a 0.0 outcome rather
    than raising into the flow."""
    def run(brief: str, data: dict, wall: float | None = None,
            *, allowance: float | None = None) -> tuple[Any, float | None]:
        from ..runner import clamp_task_limit, configured_max_dollars, configured_max_wall_seconds

        gid = world.create_goal(str(brief)[:200], str(brief)[:8000], owner=owner)
        # A per-node timeout bounds this goal's wall so a timed-out node's goal
        # self-terminates instead of running to the default cap after the flow
        # gave up on it (kernel rule 3 still holds -- this only ever lowers it).
        ws = clamp_task_limit(budget_wall_seconds, configured_max_wall_seconds())
        ws = clamp_task_limit(wall if wall and wall > 0 else None, ws)
        dollars = clamp_task_limit(budget_dollars, configured_max_dollars())
        dollars = clamp_task_limit(allowance, dollars)
        usage = []
        try:
            from ..runner import run_goal_in_thread
            result = run_goal_in_thread(
                gid,
                max_dollars=dollars,
                max_wall_seconds=ws,
                channel=channel,
                user_id=user_id,
                allowed_suites=allowed_suites,
                concurrency_principal=(concurrency_principal or owner or user_id),
                usage_observer=usage.append,
            )
        except Exception as e:  # pragma: no cover -- runner wiring varies by env
            return _MeteredAgentResult(
                f"agent step failed: {type(e).__name__}: {_safe_failure(e)}", 0.0,
                usage[-1] if usage else 0.0,
            )
        from ..run_outcome import GoalAdmissionRefused

        if (
            isinstance(result, GoalAdmissionRefused)
            and result.goal_id == gid
            and result.stop_kind in {"budget", "organization_budget"}
        ):
            # This newly-created goal never entered orchestration. A string
            # status, a zero bill, or a budget-looking error after execution
            # began cannot establish the same replay-safe fact.
            raise FlowBudgetExceeded(result.detail)
        g = world.get_goal(gid)
        outcome = 1.0 if (g and g.status == "done") else 0.0
        # Attribute this goal's tool usage back to the node (from the ContextVar
        # _call_agent published), so the self-rewrite pass can infer which tool a
        # reliably-one-tool agent node should harden into. Only on SUCCESS: a node
        # with retries invokes this runner once PER ATTEMPT, so recording every
        # attempt would let one retried run masquerade as many "runs" and inflate
        # the inference support -- and a failed attempt's tools are noise anyway.
        # A reliable node (the only harden candidate) succeeds ~once per run, so
        # this yields ~one tool record per logical run, aligned with node_outcomes.
        fid, nid, revision, cohort = _active_node.get()
        if fid and nid and outcome == 1.0:
            _capture_node_tools(
                fid, nid, gid, revision=revision, cohort=cohort,
            )
        return _MeteredAgentResult(
            g.result if g is not None else (result or ""), outcome,
            usage[-1] if usage else 0.0,
        )
    run._meters_usage = True
    return run


def _capture_node_tools(
    flow_id: str, node_id: str, goal_id: int, *, revision: str = "",
    cohort: str = "",
) -> None:
    """Record the distinct tools an agent node's goal used (for harden inference).
    No-op when trajectory capture is off (nothing to read); never raises."""
    try:
        from .. import trajectory_store
        if not trajectory_store.enabled():
            return
        tools = trajectory_store.tools_for_goal(goal_id)
        from . import node_tools
        node_tools.record(
            flow_id, node_id, tools, revision=revision, cohort=cohort,
        )
    except Exception:  # pragma: no cover -- capture must never break a run
        pass


def default_action_runner(
    world,
    sandbox=None,
    *,
    channel: str | None = None,
    user_id: str | None = None,
) -> ActionRunner:
    """Production ``action_runner``: resolve the named tool from the base registry
    and call it with the rendered params. Outcome is success (1.0) unless the tool
    returns a failed, refused, or indeterminate result (0.0). Ordinary tool
    failures are returned to the flow; intentional control-flow stops propagate
    after one terminal-audit attempt. High-risk nodes turn a 0.0 into the runner's
    indeterminate quarantine rather than an automatic retry."""
    def run(tool: str, params: dict, data: dict) -> tuple[Any, float | None]:
        import asyncio
        import secrets

        from ..audit.errors import AuditRefused

        call_id = secrets.token_hex(12)
        from ..audit import TOOL_CALL_COMMITMENT_ALGORITHM, tool_call_commitment

        call_commitment = tool_call_commitment(
            call_id=call_id,
        )
        authorized = False
        observation = None

        def audit_result(
            status: str,
            result: object,
            *,
            disposition: str,
        ) -> bool:
            from ..audit import EventKind, audit_event
            from ..tool_authz import _audit_summary

            return audit_event(
                EventKind.TOOL_RESULT,
                agent="flow:action",
                name=tool,
                origin="flow",
                principal=user_id,
                call_id=call_id,
                call_commitment=call_commitment,
                call_commitment_algorithm=TOOL_CALL_COMMITMENT_ALGORITHM,
                status=status,
                disposition=disposition,
                output_summary=_audit_summary(result),
            )

        try:
            # One authorization gate for every non-agent dispatch. This used to
            # be action_tool_policy_error alone -- no shield scan, no governance
            # policy, and no audit row -- on a path that is live (bound at
            # automation_queue) and reaches every registered connector including
            # stripe, gmail, s3, salesforce, sap and workday.
            #
            # The missing audit row was the worst of it: the attestation's
            # policy_envelope claims "no RECORDED action violated the envelope",
            # so an unrecorded dispatch satisfied it trivially and made the
            # claim unfalsifiable rather than merely incomplete.
            from ..tool_authz import authorize

            if denial := authorize(
                tool, params, origin="flow", principal=user_id, call_id=call_id,
            ):
                return (denial, 0.0)
            authorized = True
            from ..sandbox import LocalBackend
            from ..tools import base_registry
            reg = base_registry(
                world,
                sandbox or LocalBackend(),
                goal_id=None,
                channel=channel,
                user_id=user_id,
            )
            t = next((x for x in reg.all() if x.name == tool), None)
            if t is None:
                result = f"ERROR: no such tool {tool!r}"
            else:
                observation = native_authorized(
                    tool, params, call_id=call_id, principal=user_id,
                    input_schema=getattr(t, "input_schema", None),
                )
                # Go through the registry's authoritative dispatch path: it owns the
                # ToolFn(args_dict) contract, sync/async/generator normalization,
                # tracing, cache invalidation and retry-safety policy.
                from ..workflow import _drive
                result = _drive(reg.run(tool, params))
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit) as e:
            if authorized:
                if isinstance(e, asyncio.CancelledError):
                    disposition = "cancelled"
                elif isinstance(e, KeyboardInterrupt):
                    disposition = "interrupted"
                else:
                    disposition = "system_exit"
                try:
                    audit_result(
                        "error",
                        f"ERROR: {type(e).__name__}",
                        disposition=disposition,
                    )
                except BaseException:
                    # A terminal writer failure must never replace the caller's
                    # cancellation/process stop.  The audit was attempted once;
                    # the durable flow boundary marks the escaped run
                    # indeterminate and prevents automatic replay.
                    pass
            raise
        except Exception as e:
            from ..budget import BudgetExceeded
            from ..killswitch import Halted
            if isinstance(e, AuditRefused):
                raise
            if isinstance(e, (BudgetExceeded, Halted)):
                if authorized:
                    try:
                        audit_result(
                            "error",
                            f"ERROR: {type(e).__name__}",
                            disposition=(
                                "budget_exceeded"
                                if isinstance(e, BudgetExceeded)
                                else "halted"
                            ),
                        )
                    except AuditRefused:
                        # Preserve the control-flow stop.  The tool boundary has
                        # already raised it, and a terminal evidence refusal must
                        # not replace that stop with a retryable executor error.
                        pass
                raise
            # Connector exceptions can contain response bodies, credentials, or
            # tenant data.  The caller and signed audit row need the failure
            # class, not the exception's attacker-controlled text.
            result = f"ERROR: tool execution raised {type(e).__name__}"
        if isinstance(result, str) and result.startswith("ERROR: "):
            # ToolRegistry converts connector exceptions to
            # ``ERROR: ExceptionType: raw message`` instead of raising them.
            # Strip that raw message at this outer trust boundary too.
            exception_name, separator, _detail = result[7:].partition(": ")
            if (
                separator
                and exception_name.replace("_", "").isalnum()
                and exception_name.endswith(("Error", "Exception"))
            ):
                result = f"ERROR: tool execution raised {exception_name}"
        ok = not tool_result_failed(result)
        if authorized:
            # Keep result auditing outside the tool-dispatch try block: a strict
            # audit refusal must propagate once, not be mistaken for a tool
            # exception and recursively re-audited.
            try:
                result_recorded = audit_result(
                    "ok" if ok else "error",
                    result,
                    disposition="completed" if ok else "tool_error",
                )
            except AuditRefused:
                # Authorization refusals occur inside the dispatch try above
                # and still propagate before execution.  This refusal is for
                # the terminal row after the connector returned, so propagating
                # it would make the flow runner retry a possibly committed
                # operation.
                result_recorded = False
            if not result_recorded:
                return (
                    "INDETERMINATE: the tool ran, but its result audit row could "
                    "not be written; reconcile the external state and do not "
                    "retry automatically",
                    0.0,
                )
            native_completed(observation, result, terminal_recorded=(result_recorded is True and ok))
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


__all__ = [
    "execute", "default_agent_runner", "default_action_runner", "sandbox_runners",
    "STATUS_RUNNING", "STATUS_RESUMING", "STATUS_INDETERMINATE",
]
