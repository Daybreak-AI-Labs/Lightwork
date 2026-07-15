"""JobQueue-backed automation for the dashboard.

Replaces the old bespoke asyncio scheduler with maverick-core's durable
:class:`~maverick.job_queue.JobQueue` + :class:`~maverick.worker.Worker`:

* **One recurring ``automation_tick`` cron job** drives polling. Each run reads
  the event-trigger store and polls only the triggers whose per-trigger interval
  is due, so every trigger has its own cadence (``interval_seconds``) without a
  cron job per trigger -- and create/delete need no queue bookkeeping, the next
  tick just sees the new store.
* **Fired goals are enqueued as durable ``run_goal`` jobs**, so a transient run
  failure is retried with backoff and a poison one lands in the ``failed``
  dead-letter state (``maverick queue failed``) -- instead of a bare daemon
  thread whose failure was lost. A small pool of worker threads drains them so a
  slow goal doesn't stall polling.
* **Dreaming is its own ``dream_cycle`` cron job**, re-armed by the worker.
* JobQueue's exactly-once ``claim`` dedupes the tick/dream *jobs*; a non-blocking
  ``flock`` around the poll body dedupes the *poll/cursor* critical section, so
  two overlapping ticks (N dashboard workers, a slow source) can't both advance
  the same cursor and double-create goals.

It runs INSIDE the dashboard process (the only one that can read the dashboard-
owned trigger store) on its OWN ``automation-jobs.db``, so its dashboard-specific
job kinds never reach -- and get dead-lettered by -- a core ``maverick worker``
draining the main queue. TestClient never starts it (the lifespan doesn't run in
tests); the handlers/arming are unit-tested directly.
"""
from __future__ import annotations

import contextlib
import logging
import threading
import time

from maverick import config

log = logging.getLogger("maverick_dashboard.automation_queue")

TICK_KIND = "automation_tick"
DREAM_KIND = "dream_cycle"
FLYWHEEL_KIND = "flywheel_cycle"
FLOW_RUN_KIND = "flow_run"       # one-shot: run a flow to its next pause/finish
FLOW_RESUME_KIND = "flow_resume"  # one-shot: resume a paused flow run
FLOW_SWEEP_KIND = "flow_delay_sweep"  # recurring: re-arm flow runs whose delay elapsed
FLOW_EVOLVE_KIND = "flow_evolve"  # recurring: autonomously revert a regressed rewrite
FLOW_CRON_KIND = "flow_cron"     # recurring: fire a scheduled flow on its own cron cadence
ASSESS_SWEEP_KIND = "assessment_sweep"  # recurring: re-assess subjects + flag due reviews
_ASSESS_SWEEP_CRON = "0 3 * * *"  # re-check governance assessments daily at 03:00
_FLOW_SWEEP_CRON = "* * * * *"   # check for due delays every minute
_FLOW_EVOLVE_CRON = "*/30 * * * *"   # re-check applied rewrites every 30 min
_MIN_INTERVAL = 60               # floor for a per-trigger cadence (matches the UI/schema)
_DEFAULT_INTERVAL = 300          # per-trigger poll cadence when unset (5 min)
_TICK_CRON = "* * * * *"         # evaluate due triggers every minute
_DREAM_CRON_DEFAULT = "0 */6 * * *"  # consolidate every 6h
_FLYWHEEL_CRON_DEFAULT = "30 */6 * * *"  # turn the flywheel every 6h (offset from dream)
_DEFAULT_WORKERS = 3             # goal-runner threads (so a slow goal can't stall the tick)

_LOCK = threading.RLock()   # reentrant: start()/stop() call queue(), which also locks
_queue = None
_worker = None
_worker_threads: list = []


def _db_path():
    return config.dashboard_overrides_path().parent / "automation-jobs.db"


def queue():
    """The dashboard automation JobQueue (its own db). Singleton."""
    global _queue
    with _LOCK:
        if _queue is None:
            from maverick.job_queue import JobQueue
            _queue = JobQueue(db_path=_db_path())
        return _queue


def _cfg_int(section: str, key: str) -> int | None:
    try:
        v = (config.load_config().get(section) or {}).get(key)
        return int(v) if v is not None else None
    except Exception:  # pragma: no cover -- config never blocks the scheduler
        return None


# ---- per-trigger due tracking (a marker mtime, so an idle poll never rewrites
# the trigger store) --------------------------------------------------------

def _poll_marker(name: str):
    return config.dashboard_overrides_path().parent / "automation-poll" / name


def _trigger_poll_key(trigger: dict) -> str:
    """Namespace a cadence marker without placing a tenant id in its path."""
    import hashlib

    tenant = str(trigger.get("tenant") or "")
    if not tenant:
        return str(trigger["name"])
    digest = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:32]
    return f"{trigger['name']}--{digest}"


def _default_interval() -> int:
    v = _cfg_int("automation", "default_interval_seconds")
    return max(_MIN_INTERVAL, v) if v else _DEFAULT_INTERVAL


def _interval(trig: dict) -> int:
    """Per-trigger cadence: a set value floored at 60s (matching the schema/UI),
    else the configurable default."""
    try:
        v = int(trig.get("interval_seconds") or 0)
    except (TypeError, ValueError):
        return _default_interval()
    return max(_MIN_INTERVAL, v) if v > 0 else _default_interval()


def _due(name: str, interval: int) -> bool:
    p = _poll_marker(name)
    try:
        return (time.time() - p.stat().st_mtime) >= interval
    except FileNotFoundError:
        return True
    except Exception:  # pragma: no cover -- a stat error shouldn't mute a trigger
        return True


def _mark_polled(name: str) -> None:
    p = _poll_marker(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()   # sets mtime to now
    except Exception:  # pragma: no cover
        pass


@contextlib.contextmanager
def _poll_guard():
    """Portable cross-process lock around the poll/cursor transaction.

    A second worker may wait briefly, then re-check due markers under the lock;
    correctness is more important than the old POSIX-only non-blocking shortcut,
    which silently provided no serialization on Windows.
    """
    from maverick.file_lock import cross_process_lock
    p = config.dashboard_overrides_path().parent / "automation-poll.lock"
    with cross_process_lock(p):
        yield True


# ---- handlers ---------------------------------------------------------------

def _world():
    from maverick_dashboard.app import _world as _w
    return _w()


def _job_tenant_payload() -> dict:
    """The active tenant, captured for a queued job's payload -- so a worker
    (which has no request/trigger context of its own) can restore it before
    loading/executing the flow. Empty (omitted) for single-tenant deployments."""
    from maverick.paths import current_tenant_id
    tenant = current_tenant_id()
    return {"tenant": tenant} if tenant else {}


def _run_in_payload_tenant(payload: dict, fn) -> None:
    """Run ``fn`` inside the tenant captured on the job payload (if any), so a
    queued flow_run/flow_resume job loads/executes/persists under the SAME
    tenant namespace its creating trigger ran in -- not whatever tenant the
    worker thread happens to be scoped to."""
    tenant = payload.get("tenant")
    if not tenant:
        fn()
        return
    from maverick.tenant.registry import assert_tenant_active
    assert_tenant_active(str(tenant))
    from maverick.paths import reset_tenant, set_tenant
    token = set_tenant(str(tenant))
    try:
        fn()
    finally:
        reset_tenant(token)


def _configured_tenant_floor() -> str:
    """The deployment's explicit tenant floor, ignoring ambient ContextVars.

    Recurring workers must not inherit whichever request/job tenant happened to
    invoke them.  A deployment-wide ``MAVERICK_TENANT`` or client binding is a
    real namespace floor, however, and remains the base namespace instead of an
    unreachable shared root.
    """
    import os

    tenant = os.environ.get("MAVERICK_TENANT", "").strip()
    if tenant:
        return tenant
    try:
        from maverick.client import client_id

        return str(client_id() or "").strip()
    except Exception:  # pragma: no cover -- a client lookup never blocks maintenance
        return ""


def _run_for_active_tenants(operation: str, fn) -> bool:
    """Run tenant-scoped maintenance in the base + every active namespace.

    The automation queue is process-global while learning/flow stores live under
    :func:`maverick.paths.data_dir`.  One unscoped cron therefore cannot rely on
    a worker thread's ambient ContextVar.  Each namespace is pinned explicitly,
    restored in ``finally``, and isolated so one tenant's failure cannot starve
    the remaining tenants until the next cron.
    """
    from maverick.paths import reset_tenant, set_tenant

    base_tenant = _configured_tenant_floor()

    def _run_one(tenant: str, *, require_active: bool) -> bool:
        token = None
        try:
            if require_active:
                from maverick.tenant.registry import assert_tenant_active

                assert_tenant_active(tenant)
            token = set_tenant(tenant or None)
            fn()
            return True
        except Exception:
            log.exception("%s failed for tenant %s", operation, tenant or "<shared>")
            return False
        finally:
            if token is not None:
                reset_tenant(token)

    try:
        from maverick.tenant.registry import list_tenants

        tenants = list(list_tenants())
    except Exception:
        log.exception("%s could not enumerate active tenants", operation)
        return False
    registered = {
        str(getattr(rec, "id", "") or "").strip(): rec for rec in tenants
    }
    # Explicitly pin even the shared/client-floor pass. This clears a stale
    # request/job ContextVar instead of accidentally learning in that namespace.
    # Provisioned floors must pass the same suspension check as every other
    # tenant; only an unregistered legacy single-tenant floor bypasses it.
    complete = _run_one(base_tenant, require_active=base_tenant in registered)

    seen = {base_tenant} if base_tenant else set()
    for rec in tenants:
        tenant = str(getattr(rec, "id", "") or "").strip()
        if not tenant or not getattr(rec, "active", False) or tenant in seen:
            continue
        seen.add(tenant)
        complete = _run_one(tenant, require_active=True) and complete
    return complete


_SUITES_UNSET = object()


def _initial_flow_identity(
    owner: str,
    *,
    channel: str | None = None,
    user_id: str | None = None,
    allowed_suites=_SUITES_UNSET,
) -> tuple[str, str, list[str] | None]:
    """Capture the authenticated execution identity in the durable run row."""
    uid = str(user_id or _user_id_from_owner(owner) or "").strip()
    run_channel = str(channel or ("api" if uid else "")).strip()
    suites = allowed_suites
    if suites is _SUITES_UNSET:
        suites = None
        if uid and owner.startswith("user:"):
            from maverick.suite_grants import granted_suites

            suites = granted_suites(owner)
    normalized = None if suites is None else sorted({str(s) for s in suites if str(s)})
    return run_channel, uid, normalized


def enqueue_flow_run_once(
    flow_id: str,
    data: dict,
    owner: str = "",
    idem_key: str = "",
    origin: str = "manual",
    *,
    dry_run: bool = False,
    channel: str | None = None,
    user_id: str | None = None,
    allowed_suites=_SUITES_UNSET,
) -> tuple[str, bool]:
    """Start a durable flow run (a pre-assigned, pollable run id + a flow_run job).
    Shared by the background tick and the on-demand poll so a flow-target trigger
    fires the same way on both paths. ``idem_key`` (flow_id + a stable event id)
    deduplicates: a re-delivered event returns the existing run instead of firing
    a second one. ``origin`` records what fired the run (cron/event/form/manual)
    for attribution + trigger-outcome learning. The original inputs are kept so
    the run can be retried."""
    from maverick.flow import store as flow_store
    # The claim lock spans lookup, run reservation, queue insert, and durable
    # claim publication.  Two processes can no longer both observe "missing".
    lock = (
        flow_store.idempotency_lock(flow_id, idem_key)
        if idem_key else contextlib.nullcontext()
    )
    with lock:
        # Pin the current generation before consulting the idempotency index.
        # Delete/recreate intentionally starts a new key space even when the
        # replacement reuses the same flow id and external event id.
        definition_digest, definition_version, subflow_digests = (
            flow_store.snapshot_current_flow_bundle(flow_id)
        )
        snapshotted_flow = flow_store.load_flow_snapshot(definition_digest)
        existing = (
            flow_store.find_run_by_idem(
                flow_id, idem_key, revision=snapshotted_flow.revision)
            if idem_key else None
        )
        if existing is not None:
            if existing.status == "queued":
                _ensure_queued_flow_job(existing, tenant=str(
                    _job_tenant_payload().get("tenant") or ""))
            return existing.run_id, True
        run_id = flow_store.new_run_id()
        run_channel, run_user_id, run_suites = _initial_flow_identity(
            owner,
            channel=channel,
            user_id=user_id,
            allowed_suites=allowed_suites,
        )
        flow_store.save_run(flow_store.FlowRun(
            run_id=run_id, flow_id=flow_id, status="queued", owner=owner,
            data=dict(data), input_data=dict(data), idem_key=idem_key, origin=origin,
            dry_run=bool(dry_run),
            definition_digest=definition_digest,
            definition_version=definition_version,
            definition_revision=snapshotted_flow.revision,
            subflow_digests=subflow_digests,
            execution_channel=run_channel,
            execution_user_id=run_user_id,
            allowed_suites=run_suites))
        # Publish the durable first-writer claim before crossing into the SQLite
        # outbox.  A crash after this point deduplicates back to the queued JSON
        # reservation, whose missing job is repaired below/on startup.  A stale
        # claim left by an ordinary enqueue exception is harmless because lookup
        # verifies the referenced run before accepting it.
        if idem_key:
            flow_store.save_idempotency_claim(flow_id, idem_key, run_id)
        payload = {"flow_id": flow_id, "run_id": run_id, "data": data, "owner": owner,
                   "origin": origin, "dry_run": bool(dry_run)}
        payload.update(_job_tenant_payload())
        try:
            queue().enqueue(FLOW_RUN_KIND, payload)
        except Exception:
            # The exception boundary is ambiguous: SQLite may have committed
            # before a wrapper/driver raised. Keep the JSON outbox reservation
            # and idempotency claim. A caller retry or startup reconciliation
            # detects an existing job or publishes the missing one, but never
            # creates a second run against a possibly committed delivery.
            raise
        return run_id, False


def enqueue_flow_run(
    flow_id: str,
    data: dict,
    owner: str = "",
    idem_key: str = "",
    origin: str = "manual",
    *,
    dry_run: bool = False,
    channel: str | None = None,
    user_id: str | None = None,
    allowed_suites=_SUITES_UNSET,
) -> str:
    return enqueue_flow_run_once(
        flow_id,
        data,
        owner,
        idem_key,
        origin,
        dry_run=dry_run,
        channel=channel,
        user_id=user_id,
        allowed_suites=allowed_suites,
    )[0]


def _flow_job_for_run(run_id: str):
    """Find any durable queue row for ``run_id`` (pending through terminal)."""
    q = queue()
    total = sum(q.counts().values())
    # Queue history is normally purged; cap a corrupt/unbounded database scan.
    for job in q.list(limit=min(max(1, total), 100_000)):
        if (job.kind == FLOW_RUN_KIND
                and str((job.payload or {}).get("run_id") or "") == run_id):
            return job
    return None


def _ensure_queued_flow_job(run, *, tenant: str = "") -> None:
    """Repair the JSON-reservation -> SQLite-job crash gap without replaying.

    The run row is the durable outbox record. If a process died after reserving
    it but before inserting the queue job, startup or an idempotent retry safely
    publishes the missing job. A terminal job is never re-published.
    """
    from maverick.flow import store

    with store.run_lock(run.run_id):
        current = store.load_run(run.run_id)
        if current is None or current.status != "queued":
            return
        if not current.definition_digest:
            current.status = "failed"
            current.error = (
                "immutable execution plan was not captured; create a replacement run"
            )
            store.save_run(current)
            return
        job = _flow_job_for_run(current.run_id)
        if job is not None:
            if job.status in {"done", "failed"}:
                current.status = "failed"
                current.error = "queue delivery ended before execution began"
                store.save_run(current)
            return
        payload = {
            "flow_id": current.flow_id,
            "run_id": current.run_id,
            "data": dict(current.input_data or current.data or {}),
            "owner": current.owner,
            "origin": current.origin,
            "dry_run": bool(current.dry_run),
        }
        if tenant:
            payload["tenant"] = tenant
        queue().enqueue(FLOW_RUN_KIND, payload)


def _repair_queued_flow_outbox() -> None:
    """Recover missing flow jobs across shared and active tenant namespaces."""
    from maverick.flow import store
    from maverick.paths import current_tenant_id

    def _repair() -> None:
        tenant = current_tenant_id() or ""
        runs = store.list_runs(limit=None)  # newest first
        # One bounded startup pass indexes pre-claim legacy runs. Afterwards a
        # random idempotency key never forces an attacker-amplifiable full scan.
        for run in runs:
            if run.idem_key:
                with store.idempotency_lock(run.flow_id, run.idem_key):
                    store.save_idempotency_claim(
                        run.flow_id, run.idem_key, run.run_id, overwrite=False)
        for run in runs:
            if run.status == "queued":
                _ensure_queued_flow_job(run, tenant=tenant)

    # A reconcile invoked inside a tenant-pinned request must not republish that
    # reservation as a tenantless job before the global sweep clears ambient
    # request state. Visit it explicitly, then sweep the configured/active set.
    ambient = current_tenant_id() or ""
    if ambient:
        try:
            _run_in_payload_tenant({"tenant": ambient}, _repair)
        except Exception:
            log.exception("could not repair ambient tenant flow-run outbox")
    _run_for_active_tenants("queued flow-run outbox repair", _repair)


def _user_id_from_owner(owner: str) -> str:
    """Derive a runner ``user_id`` from a trigger owner principal (``user:<id>``),
    mirroring ``execution_user_id_from_request`` for the request path. Empty for
    unowned triggers or non-``user:`` owners (e.g. a future ``tenant:`` scope)."""
    if owner.startswith("user:") and len(owner) > len("user:"):
        return owner[len("user:"):]
    return ""


def _poll_due_triggers() -> None:
    from maverick_dashboard import event_poll, event_triggers_store
    due = [
        t
        for t in event_triggers_store.list_triggers()
        if _due(_trigger_poll_key(t), _interval(t))
    ]
    if not due:
        return
    q = queue()

    # Group by owner and poll each group separately so the durable run_goal job
    # carries the trigger owner's execution identity -- otherwise every fired
    # goal runs under the fallback `user:local` principal, bypassing per-user
    # concurrency/quota attribution. Unowned triggers (owner == "") keep the
    # legacy behavior: no user_id, principal="".
    groups: dict[str, list[dict]] = {}
    for t in due:
        groups.setdefault(t.get("owner") or "", []).append(t)

    errored: set[str] = set()
    for owner, triggers in groups.items():
        def _fire(goal_id: int, owner: str = owner) -> None:
            from maverick_dashboard.auth import stored_automation_identity

            identity_owner, user_id, suites = stored_automation_identity(owner)
            payload: dict = {"goal_id": int(goal_id)}
            payload.update(_job_tenant_payload())
            if user_id:
                payload["user_id"] = user_id
                payload["channel"] = "api"
            if identity_owner:
                payload["concurrency_principal"] = identity_owner
            if suites is not None:
                payload["allowed_suites"] = sorted(suites)
            q.enqueue("run_goal", payload)   # durable: retry + dead-letter

        group_results = event_poll.poll_and_fire(
            _world,
            triggers,
            principal=owner,
            fire=_fire,
            fire_flow=enqueue_flow_run,
        )
        for trigger, result in zip(triggers, group_results, strict=True):
            if result.get("poll_error"):
                errored.add(_trigger_poll_key(trigger))

    # Advance the due-marker only for triggers that actually polled; a trigger
    # whose source errored is left un-marked so it retries on the next tick
    # rather than going dark for a full interval.
    for t in due:
        key = _trigger_poll_key(t)
        if key not in errored:
            _mark_polled(key)


def _handle_tick(_job=None) -> None:
    """Poll every DUE event trigger and fire its goals as durable run_goal jobs.
    Fail-open: a raise here must NOT fail the job (which would retry-storm every
    60s); the next cron tick re-runs. Single-polled across workers via flock."""
    from maverick import automation_events
    if not automation_events.enabled():
        return
    try:
        with _poll_guard() as acquired:
            if acquired:
                _poll_due_triggers()
    except Exception:  # pragma: no cover -- fail-open, never retry-storm
        log.exception("automation tick failed")


def _handle_dream(_job=None) -> None:
    """Fail-open: swallow errors so a partial dream isn't re-applied by the
    worker's retry (dream_cycle is not transactional); the next cron runs it.
    Learning stores are tenant-scoped, so the one process-global cron explicitly
    visits the shared/client floor and every active tenant."""
    from maverick import dreaming

    def _cycle() -> None:
        # Enablement can be overridden per tenant; check it inside the pin.
        if dreaming.enabled():
            dreaming.dream_cycle(_world())

    _run_for_active_tenants("scheduled dream cycle", _cycle)


def _handle_assess_sweep(_job=None) -> None:
    """Re-assess every governance subject on a cadence, so drift flips accepted
    reviews back to needs-review and overdue re-reviews surface without a human
    remembering to click. Assessment registers are tenant-scoped, so the global
    cron visits each active namespace explicitly and fails open per tenant."""
    from maverick import assessments

    _run_for_active_tenants("scheduled assessment sweep", assessments.sweep_due)


def _handle_flywheel(_job=None) -> None:
    """Turn the Cognitive Data Engine flywheel over the accumulated Operating
    Record + grounded outcomes. This is the missing scheduled driver: outcomes
    (failures, sign-offs, feedback, real system-of-record results) land
    continuously, but nothing consolidated them into guardrails/habits without a
    manual `maverick flywheel`. Gated (`maybe_run` is a no-op unless
    ``[data_engine]`` is on), tenant-explicit, and fail-open like the dream
    cycle."""
    from maverick import flywheel

    _run_for_active_tenants("scheduled flywheel cycle", flywheel.maybe_run)


def _flow_execution_identity(run) -> tuple[str | None, str | None, frozenset[str] | None]:
    """Resolve a run's identity and apply the current suite grant as a floor.

    A queued grant may narrow but never widen while waiting: if either the
    enqueue-time or current grant is restricted, dispatch uses their
    intersection.  This preserves revocations and prevents deleting a grant
    from silently broadening an already-authorized queued run.
    """
    user_id = str(run.execution_user_id or _user_id_from_owner(run.owner) or "").strip()
    if not user_id:
        return None, None, None
    channel = str(run.execution_channel or "api").strip() or "api"
    initial = (
        None if run.allowed_suites is None else frozenset(str(s) for s in run.allowed_suites)
    )
    from maverick.suite_grants import granted_suites

    current = granted_suites(run.owner) if run.owner.startswith("user:") else None
    if initial is None:
        allowed = current
    elif current is None:
        allowed = initial
    else:
        allowed = initial & current
    return channel, user_id, allowed


def _flow_execution_authorized(run) -> bool:
    """Apply current lifecycle + RBAC policy as a dispatch-time floor."""
    try:
        from maverick_dashboard.auth import stored_automation_identity

        stored_automation_identity(str(run.owner or ""))
        return True
    except Exception:
        # Lifecycle/RBAC policy is a security boundary. An unreadable policy
        # store is not a reason to run an already-queued external side effect.
        return False


def _flow_runners(run):
    from maverick.flow import execution
    from maverick.sandbox import build_sandbox
    w = _world()
    sandbox = build_sandbox()
    channel, user_id, allowed_suites = _flow_execution_identity(run)
    return (
        execution.default_agent_runner(
            w,
            owner=run.owner,
            channel=channel,
            user_id=user_id,
            allowed_suites=allowed_suites,
            concurrency_principal=run.owner or user_id,
        ),
        execution.default_action_runner(
            w,
            sandbox=sandbox,
            channel=channel,
            user_id=user_id,
        ),
    )


def _public_base_url() -> str:
    """The externally-reachable dashboard base URL for approval links, from
    ``MAVERICK_PUBLIC_URL`` or ``[dashboard] public_url``. Empty when unset --
    then approval links are omitted and the human uses the dashboard."""
    import os
    url = os.environ.get("MAVERICK_PUBLIC_URL", "").strip()
    if not url:
        try:
            cfg = config.load_config()
            url = str((cfg.get("dashboard") or {}).get("public_url")
                      or (cfg.get("flows") or {}).get("public_url") or "").strip()
        except Exception:  # pragma: no cover
            url = ""
    return url


def enqueue_flow_resume(flow_id: str, run_id: str, decision: str, owner: str = "",
                        *, tenant: str = "") -> None:
    """Enqueue a durable resume of a paused flow run with a human decision. Shared
    by the API resume endpoint and the signed-link approval endpoint."""
    payload = {
        "flow_id": flow_id, "run_id": run_id, "decision": decision, "owner": owner}
    payload.update({"tenant": tenant} if tenant else _job_tenant_payload())
    queue().enqueue(FLOW_RESUME_KIND, payload)


def _approval_body(name: str, run) -> str:
    """The approval notification text, with clickable approve/reject links when a
    public URL + webhook secret are configured (else just the prompt)."""
    ask = run.prompt or "review and resume"
    base = _public_base_url()
    if base:
        from maverick.flow.approvals import approval_links
        links = approval_links(base, run.run_id, cursor=run.cursor,
                               prompt=run.prompt, updated=run.updated)
        if links:
            return (f"“{name}” needs your approval: {ask}\n"
                    f"✅ Approve: {links['approve']}\n"
                    f"🚫 Reject: {links['reject']}")
    return f"“{name}” needs your approval: {ask}"


def _notify_flow(flow, run) -> None:
    """Best-effort push when a notify-enabled flow needs a human (paused on an
    approval) or finishes (completed/failed). A no-op unless the flow opted in
    AND notifications are configured; never raises into the worker. An approval
    pause carries actionable approve/reject links when a public URL is set."""
    if flow is None or not getattr(flow, "notify", False):
        return
    from maverick.flow.runner import (
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_INDETERMINATE,
        STATUS_PAUSED_APPROVAL,
    )
    name = flow.name or flow.id
    if run.status == STATUS_PAUSED_APPROVAL:
        body, prio = _approval_body(name, run), "high"
    elif run.status == STATUS_COMPLETED:
        body, prio = f"“{name}” finished.", "default"
    elif run.status == STATUS_FAILED:
        body, prio = f"“{name}” failed: {run.error or 'see the run'}", "high"
    elif run.status == STATUS_INDETERMINATE:
        body = (
            f"“{name}” stopped with an indeterminate external effect. "
            "Reconcile the target system before retrying."
        )
        prio = "high"
    else:
        return
    try:
        from maverick import notifications
        notifications.notify(body, title="Lightwork flow", priority=prio)
    except Exception:  # pragma: no cover -- notification is best-effort
        log.debug("flow notify failed", exc_info=True)


def _schedule_paused_resume(run, source_payload: dict) -> None:
    """Persist the tenant-aware delayed continuation when a run parks.

    This is the primary delay/approval-expiry driver.  The periodic sweep remains
    only as recovery for legacy runs; it cannot safely discover every tenant's
    private run directory from an unscoped worker.
    """
    if not run.resume_at:
        return
    from maverick.flow.runner import STATUS_PAUSED_APPROVAL, STATUS_PAUSED_DELAY
    if run.status not in {STATUS_PAUSED_APPROVAL, STATUS_PAUSED_DELAY}:
        return
    payload = {"flow_id": run.flow_id, "run_id": run.run_id, "owner": run.owner}
    if run.status == STATUS_PAUSED_APPROVAL:
        payload["expired"] = True
    tenant = source_payload.get("tenant")
    if tenant:
        payload["tenant"] = tenant
    queue().enqueue(FLOW_RESUME_KIND, payload, run_at=float(run.resume_at))


_SINGLETON_DEFER_SECONDS = 30.0   # how long to wait before re-checking a busy singleton
_SINGLETON_MAX_DEFERS = 40        # ~20 min of deferral before giving up (avoid infinite requeue)


@contextlib.contextmanager
def _flow_slot_guard(flow_id: str):
    """Portable per-flow lock around the max_concurrent check + claim."""
    from maverick.file_lock import cross_process_lock
    from maverick.flow.store import _safe_id
    from maverick.paths import data_dir

    # Tenant-scoped placement prevents a tenant that reuses a flow id from
    # contending on another tenant's singleton lock.
    p = data_dir("flows", "slots", _safe_id(flow_id))
    with cross_process_lock(p):
        yield True


def _active_run_count(flow_id: str, exclude_run_id: str) -> int:
    """In-flight runs of a flow (running / paused), excluding this run. Queued runs
    don't count -- they're exactly what the slot guard gates -- else two queued
    events would each see the other and defer forever."""
    from maverick.flow import store
    from maverick.flow.runner import ACTIVE_STATUSES
    occupying = ACTIVE_STATUSES - {"queued"}
    return sum(1 for r in store.list_runs(flow_id=flow_id, limit=None)
               if r.run_id != exclude_run_id and r.status in occupying)


def _defer_for_slot(payload: dict) -> bool:
    """Re-enqueue a flow_run job later because the flow is at its concurrency cap.
    Bounded so a permanently-stuck run (e.g. a run paused on approval forever)
    can't requeue endlessly -- past the cap the run is left queued and skipped.
    Returns True if it deferred, False if the defer budget is exhausted."""
    defers = int(payload.get("_slot_defers") or 0)
    if defers >= _SINGLETON_MAX_DEFERS:
        return False
    nxt = dict(payload)
    nxt["_slot_defers"] = defers + 1
    queue().enqueue(FLOW_RUN_KIND, nxt, run_at=time.time() + _SINGLETON_DEFER_SECONDS)
    return True


def _claim_run_slot(run_id: str) -> None:
    """Claim a queued concurrency slot without impersonating execution.

    ``running`` means an executor crossed the possible-effect boundary; a stale
    delivery that finds it must quarantine rather than replay. ``claimed`` is a
    distinct pre-execution state that the first delivery may safely consume.
    """
    from maverick.flow import store
    from maverick.flow.runner import STATUS_CLAIMED

    run = store.load_run(run_id)
    if run is not None and run.status == "queued":
        run.status = STATUS_CLAIMED
        store.save_run(run)


def _skip_run_at_cap(run_id: str) -> None:
    """Mark a run that never got a concurrency slot as skipped (not failed) -- it
    never executed, so it's not a failure to learn from, just a dropped attempt."""
    from maverick.flow import store
    run = store.load_run(run_id)
    if run is not None and run.status == "queued":
        run.status = "skipped_concurrency"
        store.save_run(run)


def _mark_flow_run_failed(run_id: str, message: str) -> None:
    """Publish a bounded, non-secret terminal failure for a known-safe preflight."""
    from maverick.flow import store

    run = store.load_run(run_id)
    if run is None:
        return
    run.status = "failed"
    run.error = str(message)[:500]
    store.save_run(run)


def _handle_flow_run(job=None) -> None:
    """Run a stored flow to its next pause/finish (durable one-shot job). An
    agent node becomes a real goal, an action node a tool call; the run state is
    persisted so a pause survives. Fail-open: a bad flow must not retry-storm."""
    payload = getattr(job, "payload", None) or {}
    flow_id = str(payload.get("flow_id") or "")
    if not flow_id:
        return
    try:
        def _run() -> None:
            from maverick.flow import execution, store
            run_id = str(payload.get("run_id") or "")
            placeholder = store.load_run(run_id) if run_id else None
            if placeholder is None:
                log.warning("flow_run: no durable reservation for %s", run_id)
                return
            if placeholder.flow_id != flow_id:
                _mark_flow_run_failed(run_id, "queue reservation does not match flow")
                return
            if not placeholder.definition_digest:
                _mark_flow_run_failed(
                    run_id,
                    "immutable execution plan was not captured; create a replacement run",
                )
                return
            try:
                flow = store.load_flow_snapshot(placeholder.definition_digest)
            except store.FlowSnapshotError:
                _mark_flow_run_failed(run_id, "immutable execution plan is unavailable")
                return
            if flow is None:
                log.warning("flow_run: no such flow %s", flow_id)
                _mark_flow_run_failed(run_id, "flow definition is unavailable")
                return
            if flow.id != placeholder.flow_id:
                _mark_flow_run_failed(run_id, "immutable execution plan identity mismatch")
                return
            try:
                store.validate_snapshot_bundle_owners(
                    flow, placeholder.subflow_digests)
            except store.FlowSnapshotError:
                _mark_flow_run_failed(run_id, "immutable execution plan is unavailable")
                return
            # The persisted reservation is authoritative. Queue rows are a
            # delivery mechanism and must not be able to upgrade a dry run,
            # swap its owner/inputs, or change learning attribution.
            owner = placeholder.owner
            dry_run = bool(placeholder.dry_run)
            if not dry_run and not _flow_execution_authorized(placeholder):
                _mark_flow_run_failed(run_id, "flow execution authorization was revoked")
                return
            # Per-flow concurrency cap: a singleton/limited flow (e.g. "drain the queue"
            # / "sync record X") must not run overlapping instances -- that double-fires
            # side effects and races the shared world. Under a per-flow lock, count
            # in-flight runs; if at the cap, mark this run "running" only when a slot is
            # free (so a concurrent sibling sees it), else defer the job a bit and let
            # the running one finish. A dry run and a resume are never gated.
            if flow.max_concurrent and not dry_run and run_id and not payload.get("_resume"):
                with _flow_slot_guard(flow_id) as held:
                    if held:
                        if _active_run_count(flow_id, run_id) >= flow.max_concurrent:
                            if _defer_for_slot(payload):
                                return
                            log.warning("flow_run: %s at concurrency cap, defer budget spent"
                                        " -- skipping run %s", flow_id, run_id)
                            _skip_run_at_cap(run_id)
                            return
                        _claim_run_slot(run_id)   # flip queued -> running while holding the lock
                    elif not _defer_for_slot(payload):   # another worker is deciding; retry later
                        _skip_run_at_cap(run_id)
                        return
                    else:
                        return
            # A dry run exercises the graph with mock executors and records no grounded
            # outcome -- so "Test" can't hit production or skew the learning signal.
            agent_runner, action_runner = (execution.sandbox_runners() if dry_run
                                           else _flow_runners(placeholder))
            run = execution.execute(flow, agent_runner=agent_runner, action_runner=action_runner,
                                    owner=owner, data=dict(
                                        placeholder.input_data or placeholder.data or {}),
                                    run_id=payload.get("run_id"), record_outcomes=not dry_run,
                                    origin=placeholder.origin)
            _schedule_paused_resume(run, payload)
            if not dry_run:
                _notify_flow(flow, run)
        _run_in_payload_tenant(payload, _run)
    except Exception:
        log.exception("flow run failed for %s", flow_id)
        raise


def _handle_flow_resume(job=None) -> None:
    """Resume a paused flow run with a human decision (durable one-shot). A stale
    resume (the run already advanced, e.g. a duplicate delay-sweep enqueue) is a
    clean no-op, not an error."""
    payload = getattr(job, "payload", None) or {}
    run_id = str(payload.get("run_id") or "")
    flow_id = str(payload.get("flow_id") or "")
    if not (run_id and flow_id):
        return
    from maverick.flow import execution
    try:
        def _resume() -> None:
            from maverick.flow import store
            prior = store.load_run(run_id)
            if prior is None:
                return
            if prior.flow_id != flow_id:
                _mark_flow_run_failed(run_id, "queue reservation does not match flow")
                return
            if not prior.definition_digest:
                _mark_flow_run_failed(
                    run_id,
                    "immutable execution plan was not captured; create a replacement run",
                )
                return
            try:
                flow = store.load_flow_snapshot(prior.definition_digest)
            except store.FlowSnapshotError:
                _mark_flow_run_failed(run_id, "immutable execution plan is unavailable")
                return
            if flow is None:
                _mark_flow_run_failed(run_id, "flow definition is unavailable")
                return
            if flow.id != prior.flow_id:
                _mark_flow_run_failed(run_id, "immutable execution plan identity mismatch")
                return
            try:
                store.validate_snapshot_bundle_owners(flow, prior.subflow_digests)
            except store.FlowSnapshotError:
                _mark_flow_run_failed(run_id, "immutable execution plan is unavailable")
                return
            owner = prior.owner
            dry_run = bool(prior.dry_run)
            if not dry_run and not _flow_execution_authorized(prior):
                _mark_flow_run_failed(run_id, "flow execution authorization was revoked")
                return
            agent_runner, action_runner = (
                execution.sandbox_runners() if dry_run else _flow_runners(prior)
            )
            run = execution.execute(flow, agent_runner=agent_runner, action_runner=action_runner,
                                    owner=owner, resume_run_id=run_id,
                                    decision=str(payload.get("decision") or "approved"),
                                    expired=bool(payload.get("expired")),
                                    from_failure=bool(payload.get("from_failure")),
                                    inputs=dict(payload.get("inputs") or {}),
                                    record_outcomes=not dry_run)
            _schedule_paused_resume(run, payload)
            if not dry_run:
                _notify_flow(flow, run)
        _run_in_payload_tenant(payload, _resume)
    except execution.FlowNotResumable:
        return  # already resumed/finished -- benign
    except Exception:
        log.exception("flow resume failed for %s", run_id)
        raise


def _handle_flow_sweep(_job=None) -> None:
    """Re-arm flow runs whose delay has elapsed. A delay node persists the run as
    paused_delay with a resume_at; this cron (every minute when flows are on) is
    the driver that continues it -- without it, 'wait N seconds' never resumes.
    Fail-open + idempotent (a stale resume is a no-op)."""
    from maverick import flow
    if not flow.enabled():
        return

    def _sweep_current_tenant() -> None:
        from maverick.flow import runner, store

        now = time.time()
        for run in store.list_runs(owner=None, limit=1000):
            if not (run.resume_at and run.resume_at <= now):
                continue
            if run.status == runner.STATUS_PAUSED_DELAY:
                sweep_payload = {"flow_id": run.flow_id, "run_id": run.run_id,
                                 "owner": run.owner}
                sweep_payload.update(_job_tenant_payload())
                queue().enqueue(FLOW_RESUME_KIND, sweep_payload)
            elif run.status == runner.STATUS_PAUSED_APPROVAL:
                # An approval whose node set an expiry resolves once it lapses.
                # The OUT-OF-BAND `expired` flag (not a decision string, so no
                # resume caller can forge it through the verdict channel) lets
                # the runner route to the node's `on_expire` escalation lane if
                # set; without one it fails closed (rejected) -- never a silent
                # approval.
                sweep_payload = {"flow_id": run.flow_id, "run_id": run.run_id,
                                 "owner": run.owner, "expired": True}
                sweep_payload.update(_job_tenant_payload())
                queue().enqueue(FLOW_RESUME_KIND, sweep_payload)
            # paused_event runs are NEVER swept -- only their event resumes them.

    _run_for_active_tenants("flow delay sweep", _sweep_current_tenant)
    # Also re-sync per-flow cron schedules from the store, so a flow scheduled
    # (or unscheduled) after boot is armed/disarmed within a minute -- no restart.
    try:
        _reconcile_flow_crons()
    except Exception:  # pragma: no cover -- fail-open
        log.exception("flow cron reconcile failed")


def _handle_flow_evolve(_job=None) -> None:
    """Autonomous self-correction: for each rewritten flow node, measure its
    grounded outcomes before vs. after the change and REVERT it if it clearly
    regressed -- the arrow that lets the loop undo its own mistakes. Gated on
    ``[flows] auto_evolve`` (off by default); fail-open and idempotent (once a
    node is reverted its newest apply is the revert, so it's skipped next pass).
    The durable cron is process-global but definitions/evidence are tenant-scoped,
    therefore every pass is explicitly pinned to one namespace at a time."""

    _run_for_active_tenants("flow evolve pass", _evolve_current_tenant)


def _evolve_current_tenant() -> None:
    """Run one evolve/revert/auto-apply pass in the already-pinned namespace."""
    from maverick import flow as flow_mod

    if not flow_mod.auto_evolve_enabled():
        return
    from maverick.flow import evolution_log, evolve, store

    for f in store.list_flows():
        for node_id in list(f.nodes):
            last = evolution_log.last_apply(f.id, node_id)
            if not last or last.get("source") == "auto-revert":
                continue  # never applied, or already auto-reverted -> settled
            impact = evolve.measure(f.id, node_id, float(last.get("ts") or 0.0))
            if not evolve.regressed(impact):
                continue
            apply_version = int(last.get("version") or 0)
            if int(f.version or 0) != apply_version:
                continue  # stale apply boundary; a newer save may be human work
            prior = store.load_version(f.id, apply_version - 1)
            if prior is None or node_id not in prior.nodes:
                continue
            reverted = evolve.revert_node(f, node_id, prior.nodes[node_id])
            if reverted.validate():
                continue  # never save a structurally broken flow
            applied_kind = f.nodes[node_id].kind
            try:
                saved = store.save_flow(
                    reverted,
                    expected_version=f.version,
                    expected_revision=f.revision,
                )
            except store.FlowVersionConflict:
                # A human or another learner won the race. Never overwrite
                # that newer definition with a stale measurement snapshot.
                continue
            evolution_log.record_apply(f.id, node_id, applied_kind,
                                       prior.nodes[node_id].kind, saved.version,
                                       source="auto-revert")
            log.info("auto-reverted regressed rewrite: flow=%s node=%s (delta %.2f)",
                     f.id, node_id, impact.get("delta") or 0.0)
    if flow_mod.auto_apply_enabled():
        _auto_apply_proposals()


def _auto_apply_proposals() -> None:
    """Autonomously APPLY the strongest self-rewrite proposal per flow (the
    forward arrow, gated on ``[flows] auto_apply``). Two directions are applied
    without a human:

    * SOFTEN (action -> agent): a chronically-failing fixed step handed to an
      agent; the brief derives from the node.
    * HARDEN (agent -> action): never auto-applied. Even when telemetry infers
      a likely tool, that inference is model-influenced and must remain a
      surfaced proposal for human review before it can persist a direct action.

    Skips a node that already has a pending apply (so it is measured -- and
    auto-reverted if it regresses -- before another change). Idempotent +
    conservative: one apply per flow per pass."""
    from maverick.flow import evolution_log, evolve, store
    from maverick.flow.ir import NODE_ACTION
    for f in store.list_flows():
        for prop in evolve.maybe_propose(f):
            if evolution_log.last_apply(f.id, prop.node_id):
                continue                            # already changed -> let it be measured first
            node = f.nodes.get(prop.node_id)
            kwargs: dict = {}
            if prop.from_kind == NODE_ACTION:       # soften: derive the brief
                kwargs["brief"] = (node.brief or node.label) if node else ""
            else:
                continue                            # harden stays human-gated, even with an inferred tool
            try:
                changed = evolve.apply_proposal(f, prop.node_id, prop.to_kind, **kwargs)
            except (KeyError, ValueError):
                continue
            if changed.validate():
                continue                            # never save a structurally broken flow
            try:
                saved = store.save_flow(
                    changed,
                    expected_version=f.version,
                    expected_revision=f.revision,
                )
            except store.FlowVersionConflict:
                continue
            evolution_log.record_apply(f.id, prop.node_id, prop.from_kind, prop.to_kind,
                                       saved.version, source="auto-apply")
            log.info("auto-applied rewrite: flow=%s node=%s %s->%s (%.0f%% over %d)%s",
                     f.id, prop.node_id, prop.from_kind, prop.to_kind, prop.mean * 100, prop.n,
                     f" via {prop.inferred_tool}" if prop.inferred_tool else "")
            break                                    # one apply per flow per pass


def _handle_flow_cron(job=None) -> None:
    """Fire a scheduled flow on its cron cadence: enqueue a fresh flow run each
    occurrence. The worker re-arms the next occurrence (payload carries
    ``__cron__``). Re-check the flow still exists and still carries THIS schedule,
    so a schedule that was removed or changed stops firing immediately -- before
    the next reconcile() cancels the now-stale cron job. Fail-open."""
    payload = getattr(job, "payload", None) or {}
    flow_id = str(payload.get("flow_id") or "")
    if not flow_id:
        return
    try:
        def _fire() -> None:
            from maverick import flow as flow_mod
            if not flow_mod.enabled():
                return
            from maverick.flow import store
            flow = store.load_flow(flow_id)
            if (flow is None
                    or flow.schedule.strip() != str(payload.get("__cron__") or "").strip()):
                return  # deleted, or schedule changed out from under this cron
            # enqueue_flow_run captures the tenant restored from this payload.
            enqueue_flow_run(flow_id, {}, owner=flow.owner, origin=f"cron:{flow_id}")

        _run_in_payload_tenant(payload, _fire)
    except Exception:  # pragma: no cover -- fail-open
        log.exception("scheduled flow run failed for %s", flow_id)


# ---- cron seeding -----------------------------------------------------------

def _pending(kind: str) -> list:
    return [j for j in queue().list(status="pending", limit=1000) if j.kind == kind]


def _ensure_cron(kind: str, expr: str) -> None:
    """Ensure exactly one recurring job of ``kind`` is armed. The worker re-arms
    it on each run (payload carries ``__cron__``); this seeds the first one, and
    collapses to one lineage if two dashboard workers happened to seed
    concurrently at boot (both saw no pending job before either enqueued). A
    surviving duplicate is harmless anyway -- the poll flock serializes them."""
    from maverick.scheduler import schedule_cron
    q = queue()
    pending = _pending(kind)
    if pending:
        for extra in pending[1:]:
            q.cancel(extra.id)
        return
    schedule_cron(q, expr, kind, {"__cron__": expr})


def _reconcile_flow_crons() -> None:
    """Seed one ``flow_cron`` job per scheduled flow and cancel the crons of
    flows that lost (or changed) their schedule. Keyed by ``flow_id`` in the
    payload, expression tracked in ``__cron__``: a job whose flow no longer has
    the same schedule is stale and cancelled; a scheduled flow with no matching
    live cron gets one seeded. Idempotent -- safe to run on every reconcile."""
    from maverick.flow import store
    from maverick.scheduler import CronError, next_run, schedule_cron
    want: dict[tuple[str, str], tuple[str, str]] = {}

    def _collect_current_tenant() -> None:
        from maverick.paths import current_tenant_id

        tenant = current_tenant_id() or ""
        for f in store.list_flows():
            expr = f.schedule.strip()
            if not expr:
                continue
            tz = f.timezone.strip()
            try:
                next_run(expr, tz=tz)  # only schedules with a future occurrence
            except CronError:
                continue
            want[(tenant, f.id)] = (expr, tz)

    # Explicitly clear any ambient request/job tenant for the base pass, then
    # enumerate active named tenants. This prevents a reconcile invoked from a
    # tenant-scoped request from publishing that tenant's cron as tenantless.
    if not _run_for_active_tenants(
        "flow cron collection", _collect_current_tenant
    ):
        # An incomplete inventory is not authority to cancel durable schedules
        # that may belong to the tenant/store we could not read.
        return
    q = queue()
    covered: set[tuple[str, str]] = set()
    for j in _pending(FLOW_CRON_KIND):
        p = j.payload or {}
        fid = str(p.get("flow_id") or "")
        tenant = str(p.get("tenant") or "")
        key = (tenant, fid)
        cur = (str(p.get("__cron__") or ""), str(p.get("__tz__") or ""))
        if want.get(key) == cur and key not in covered:
            covered.add(key)          # exactly one correct cron per scoped flow
        else:
            q.cancel(j.id)            # stale (flow unscheduled, schedule/tz changed, or dup)
    for (tenant, fid), (expr, tz) in want.items():
        if (tenant, fid) not in covered:
            try:
                payload = {"flow_id": fid, "__cron__": expr, "__tz__": tz}
                if tenant:
                    payload["tenant"] = tenant
                schedule_cron(q, expr, FLOW_CRON_KIND,
                              payload)
            except CronError:
                continue


def _dream_cron() -> str:
    try:
        expr = str((config.load_config().get("dreaming") or {}).get("cron") or "").strip()
        return expr or _DREAM_CRON_DEFAULT
    except Exception:  # pragma: no cover
        return _DREAM_CRON_DEFAULT


def _flywheel_cron() -> str:
    try:
        expr = str((config.load_config().get("data_engine") or {}).get("cron") or "").strip()
        return expr or _FLYWHEEL_CRON_DEFAULT
    except Exception:  # pragma: no cover
        return _FLYWHEEL_CRON_DEFAULT


def reconcile() -> None:
    """Seed the tick + dream + flywheel cron jobs for whichever subsystems are
    enabled."""
    from maverick import automation_events, data_engine, dreaming, flow
    if automation_events.enabled():
        _ensure_cron(TICK_KIND, _TICK_CRON)
    if dreaming.enabled():
        _ensure_cron(DREAM_KIND, _dream_cron())
    if data_engine.enabled():
        _ensure_cron(FLYWHEEL_KIND, _flywheel_cron())
    if flow.enabled():
        _repair_queued_flow_outbox()
        _ensure_cron(FLOW_SWEEP_KIND, _FLOW_SWEEP_CRON)
        _reconcile_flow_crons()
    if flow.auto_evolve_enabled():
        _ensure_cron(FLOW_EVOLVE_KIND, _FLOW_EVOLVE_CRON)
    # Governance assessments re-check daily whenever the automation worker runs.
    # Cheap and a no-op with an empty register; keeps accepted reviews honest
    # (drift) and surfaces overdue re-reviews without a human remembering to.
    _ensure_cron(ASSESS_SWEEP_KIND, _ASSESS_SWEEP_CRON)


# ---- lifecycle --------------------------------------------------------------

def _worker_count() -> int:
    v = _cfg_int("automation", "workers")
    return max(1, min(8, v)) if v else _DEFAULT_WORKERS


def start() -> bool:
    """Start the automation worker pool + seed cron jobs. No-op when none of
    event triggers, dreaming, the data engine, or flows is enabled, and
    idempotent. Returns whether the pool was started."""
    global _worker, _worker_threads
    from maverick import automation_events, data_engine, dreaming, flow
    if not (automation_events.enabled() or dreaming.enabled()
            or data_engine.enabled() or flow.enabled()):
        return False
    with _LOCK:
        if _worker_threads:
            return True
        from maverick.worker import Worker
        w = Worker(queue=queue())
        w.register(TICK_KIND, _handle_tick)
        w.register(DREAM_KIND, _handle_dream)
        w.register(FLYWHEEL_KIND, _handle_flywheel)
        w.register(FLOW_RUN_KIND, _handle_flow_run)
        w.register(FLOW_RESUME_KIND, _handle_flow_resume)
        w.register(FLOW_SWEEP_KIND, _handle_flow_sweep)
        w.register(FLOW_EVOLVE_KIND, _handle_flow_evolve)
        w.register(FLOW_CRON_KIND, _handle_flow_cron)
        w.register(ASSESS_SWEEP_KIND, _handle_assess_sweep)
        # Seed the cron jobs + actually start the threads BEFORE committing to the
        # module globals, so a failure in reconcile() (e.g. a boot-time sqlite
        # write error) leaves us cleanly un-started and retryable -- not wedged
        # with recorded-but-never-started threads that stop() would join().
        reconcile()
        # One Worker, N threads sharing it: JobQueue.claim is atomic, so the
        # threads safely divide the jobs -- a slow run_goal on one thread can't
        # block the tick/dream on another.
        threads = [
            threading.Thread(target=w.run_forever, name=f"automation-worker-{i}", daemon=True)
            for i in range(_worker_count())
        ]
        for t in threads:
            t.start()
        _worker = w
        _worker_threads = threads
    log.info("automation worker started (%d threads, db=%s)", len(threads), _db_path())
    return True


def stop() -> None:
    global _worker, _worker_threads
    with _LOCK:
        w = _worker
        threads = list(_worker_threads)
        _worker = None
        _worker_threads = []
    if w is not None:
        w.stop()
    for t in threads:
        t.join(timeout=5.0)


__all__ = ["start", "stop", "reconcile", "queue",
           "TICK_KIND", "DREAM_KIND", "FLYWHEEL_KIND",
           "FLOW_RUN_KIND", "FLOW_RESUME_KIND", "FLOW_SWEEP_KIND", "FLOW_EVOLVE_KIND",
           "FLOW_CRON_KIND", "enqueue_flow_run", "enqueue_flow_run_once",
           "enqueue_flow_resume"]
