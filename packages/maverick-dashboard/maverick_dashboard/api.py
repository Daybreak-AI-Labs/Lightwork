"""REST API for Lightwork (mounted at /api/v1).

v0.1.6: BackgroundTask runner moved to maverick.runner.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import os
import threading
import time

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from maverick.runner import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DOLLARS,
    DEFAULT_MAX_WALL_SECONDS,
)
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, StreamingResponse

from ._shared import (
    _any_provider_key_set,
    _get_sse_semaphore,
    _world,
    require_provider_or_400,
)
from ._shared import _world_cache as _world_cache  # re-export: tests clear api._world_cache
from .api_schemas import (
    AgentOverrideIn,
    AnswerIn,
    AssessmentEvidenceIn,
    AssessmentRefreshIn,
    AssessmentReviewIn,
    AttachmentOut,
    CachePurgeIn,
    CatalogInstallIn,
    ChildIn,
    ComposeIn,
    ConnectionIn,
    DeliverableEditIn,
    EventTriggerIn,
    EventTriggerOut,
    FactIn,
    FeedbackIn,
    FleetCreateIn,
    FleetRunIn,
    FlowApplyIn,
    FlowAutonomyIn,
    FlowChatIn,
    FlowDraftIn,
    FlowResumeIn,
    FlowRollbackIn,
    FlowRunIn,
    FlowSaveIn,
    GoalEventOut,
    GoalEventsResponse,
    GoalIn,
    GoalOut,
    HaltIn,
    ImportRunIn,
    ImportRunOut,
    LearningToggleIn,
    OAuthAuthorizeIn,
    OAuthExchangeIn,
    OutcomeByKeyIn,
    OutcomeIn,
    OutcomeLinkIn,
    RedactIn,
    ReparentIn,
    RetitleIn,
    RoleOverrideIn,
    ScheduleIn,
    ScheduleOut,
    SignoffIn,
    SkillCreateIn,
    SkillInstallIn,
    SkillOut,
    SpeakIn,
    TenantCreateIn,
    TenantOut,
    TenantPlanIn,
    TenantQuotaIn,
    TenantRoleIn,
    TriggerIn,
    TriggerOut,
    TrustAgentIn,
    TrustRevokeIn,
    UserSuitesIn,
    WorkflowDraftIn,
    WorkflowRefineIn,
    WorkflowSaveIn,
)
from .auth import (
    assert_goal_access,
    caller_principal,
    caller_suites,
    can_access_goal,
    execution_user_id_from_request,
    goal_owner_filter,
    is_dashboard_admin,
    require_global_permission,
    require_permission,
    require_suite,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["v1"])

# Shared-store channel for Idempotency-Key dedup on goal creation (see
# create_goal). Distinct from the channel/webhook idempotency namespaces.
_IDEMPOTENCY_CHANNEL = "idempotency:api:goals"


def _shared_halt_backend() -> bool:
    """Whether the cluster-wide (shared-store) halt is in play. Only on a shared
    backend (Postgres); on single-host SQLite the local HALT file is the whole
    mechanism and the killswitch never consults the shared row, so the dashboard
    leaves it untouched (keeps single-host behavior unchanged)."""
    try:
        from maverick.world_model_backends import is_postgres_configured
        return bool(is_postgres_configured())
    except Exception:
        return False


def _require_halt_permission(request: Request) -> None:
    """Gate dashboard halt toggles at the correct blast radius.

    In local/SQLite mode the halt is process-local and remains an operator action.
    In shared Postgres mode the halt row is fleet-wide and untenanted, so toggling
    it is a global control-plane action reserved for dashboard admins.
    """
    require_permission(request, "admin" if _shared_halt_backend() else "operate")


_PERF_SLA_CACHE_TTL_SECONDS = 60.0
_PERF_SLA_LOCK = asyncio.Lock()
_PERF_SLA_CACHE: tuple[float, list[dict], str | None] | None = None
_PERF_HISTORY_MAX_FILES = 128
_COMPLIANCE_PACKET_CACHE_TTL_SECONDS = 60.0
_COMPLIANCE_PACKET_LOCK = asyncio.Lock()
_COMPLIANCE_PACKET_CACHE: tuple[float, str] | None = None


















def _to_goal_out(g) -> GoalOut:
    return GoalOut(
        id=g.id, status=g.status, title=g.title,
        description=g.description, result=g.result,
    )


# --- Tenant provisioning (admin only) ----------------------------------------
# A control-plane surface so operators can spin tenants up/down without shelling
# into the box for `maverick tenant ...`. All endpoints require the "admin"
# permission; auth-off (single-operator) deployments treat the local caller as
# admin, matching the rest of the API.

def _to_tenant_out(rec) -> TenantOut:
    from maverick.workspace import Workspace
    config_path = str(Workspace(rec.id).root / "config.toml")
    return TenantOut(
        id=rec.id, status=rec.status, plan=rec.plan,
        display_name=rec.display_name, max_daily_dollars=rec.max_daily_dollars,
        created_at=rec.created_at, updated_at=rec.updated_at,
        config_path=config_path,
    )


def _get_tenant_or_404(tenant_id: str):
    from maverick.tenant import registry as tenant_registry
    rec = tenant_registry.get_tenant(tenant_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="no such tenant")
    return rec


@router.get("/admin/tenants", response_model=list[TenantOut])
async def list_tenants(request: Request) -> list[TenantOut]:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    return [_to_tenant_out(r) for r in tenant_registry.list_tenants()]


@router.get("/workforce/posture")
async def workforce_posture(request: Request) -> dict:
    """The workforce autonomy dial: fleet posture + graduation candidates.

    Read-only surface for the dashboard -- whether per-agent autonomy levels are
    on, the distribution of baseline authority rungs across the roster, how many
    hires are still onboarding, and which have earned graduation from a clean
    approval record (advisory; the client lifts onboarding to act on it).
    """
    require_permission(request, "view")
    from maverick.agent_autonomy import graduation_candidates, levels_enabled
    from maverick.domain_audit import audit_roster, summarize
    s = summarize(audit_roster())
    out: dict = {
        "levels_enabled": levels_enabled(),
        "autonomy_posture": s.get("autonomy_posture", {}),
        "packs_onboarding": s.get("packs_onboarding", 0),
        "graduation_candidates": [],
    }
    try:
        from maverick.domain import available_domains
        from maverick.world_model import DEFAULT_DB, WorldModel
        wm = WorldModel(DEFAULT_DB)
        cands = graduation_candidates(wm.list_approvals(limit=5000), sorted(available_domains()))
        out["graduation_candidates"] = [
            {"name": c.name, "sample": c.sample, "approve_rate": c.approve_rate,
             "confidence": c.confidence, "reason": c.reason}
            for c in cands
        ]
    except Exception:  # pragma: no cover -- no DB / empty install -> empty list
        pass
    return out


@router.post("/admin/tenants", response_model=TenantOut, status_code=201)
async def create_tenant(request: Request, body: TenantCreateIn) -> TenantOut:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    try:
        rec = tenant_registry.create_tenant(
            body.id, plan=body.plan, display_name=body.display_name,
            max_daily_dollars=body.max_daily_dollars,
        )
    except ValueError as e:
        # Already exists, or an invalid/over-long id.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _to_tenant_out(rec)


@router.get("/admin/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(request: Request, tenant_id: str) -> TenantOut:
    require_global_permission(request, "admin")
    return _to_tenant_out(_get_tenant_or_404(tenant_id))


@router.post("/admin/tenants/{tenant_id}/suspend", response_model=TenantOut)
async def suspend_tenant(request: Request, tenant_id: str) -> TenantOut:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    _get_tenant_or_404(tenant_id)
    return _to_tenant_out(tenant_registry.suspend_tenant(tenant_id))


@router.post("/admin/tenants/{tenant_id}/resume", response_model=TenantOut)
async def resume_tenant(request: Request, tenant_id: str) -> TenantOut:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    _get_tenant_or_404(tenant_id)
    return _to_tenant_out(tenant_registry.resume_tenant(tenant_id))


@router.post("/admin/tenants/{tenant_id}/plan", response_model=TenantOut)
async def set_tenant_plan(
    request: Request, tenant_id: str, body: TenantPlanIn,
) -> TenantOut:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    _get_tenant_or_404(tenant_id)
    return _to_tenant_out(tenant_registry.set_plan(tenant_id, body.plan))


@router.post("/admin/tenants/{tenant_id}/quota", response_model=TenantOut)
async def set_tenant_quota(
    request: Request, tenant_id: str, body: TenantQuotaIn,
) -> TenantOut:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    _get_tenant_or_404(tenant_id)
    return _to_tenant_out(
        tenant_registry.set_quota(tenant_id, body.max_daily_dollars)
    )


@router.delete("/admin/tenants/{tenant_id}", status_code=204)
async def delete_tenant(
    request: Request, tenant_id: str, purge: bool = False,
) -> Response:
    require_global_permission(request, "admin")
    from maverick.tenant import registry as tenant_registry
    _get_tenant_or_404(tenant_id)
    tenant_registry.delete_tenant(tenant_id, purge=purge)
    return Response(status_code=204)


# Per-tenant RBAC: a principal can hold a different role in each tenant. These
# memberships override the global role for that tenant only (bootstrap admins
# stay globally admin). Managed admin-only.

def _reject_tenant_role_assignment_under_per_user_tenancy() -> None:
    """Per-tenant RBAC keys on the request's active tenant, but per-user tenancy
    (``MAVERICK_TENANT_BY_USER``) force-pins every request to the caller's own
    isolated tenant (``api:<principal>``) -- so a role assigned to any named
    tenant can never be the active tenant and would be stored but DEAD. Reject
    the mutation so the silent no-op becomes an explicit error instead of a
    footgun (an admin thinking they scoped a user when they did not). Use the
    global per-user role assignment (``POST /users/set``) in that mode.

    Deletions remain allowed: stale roles for generated ``api:<principal>``
    tenants can still be active under the read path and must be revocable.
    """
    from maverick.paths import tenant_by_user_enabled
    if tenant_by_user_enabled():
        raise HTTPException(
            status_code=409,
            detail="Tenant-level roles aren't available on this deployment — "
                   "per-user tenancy is enabled, so each user already works in "
                   "their own isolated tenant (MAVERICK_TENANT_BY_USER). Assign "
                   "the user a global role instead (POST /users/set).",
        )


@router.get("/admin/tenants/{tenant_id}/roles", response_model=dict[str, str])
async def list_tenant_roles(request: Request, tenant_id: str) -> dict[str, str]:
    require_global_permission(request, "admin")
    from maverick_dashboard import rbac
    _get_tenant_or_404(tenant_id)
    return rbac.list_tenant_roles(tenant_id)


@router.put("/admin/tenants/{tenant_id}/roles/{principal}", status_code=204)
async def set_tenant_role(
    request: Request, tenant_id: str, principal: str, body: TenantRoleIn,
) -> Response:
    require_global_permission(request, "admin")
    _reject_tenant_role_assignment_under_per_user_tenancy()
    from maverick_dashboard import rbac
    _get_tenant_or_404(tenant_id)
    rbac.set_tenant_role(tenant_id, principal, body.role,
                         actor=caller_principal(request) or "local")
    return Response(status_code=204)


@router.delete("/admin/tenants/{tenant_id}/roles/{principal}", status_code=204)
async def remove_tenant_role(
    request: Request, tenant_id: str, principal: str,
) -> Response:
    require_global_permission(request, "admin")
    from maverick_dashboard import rbac
    _get_tenant_or_404(tenant_id)
    rbac.remove_tenant_role(tenant_id, principal,
                            actor=caller_principal(request) or "local")
    return Response(status_code=204)


# Per-user department access (suite grants): job-function scoping of the
# workforce — a finance analyst uses Finance specialists, never Legal's.
# Global control-plane data like the RBAC roster, so managed under the GLOBAL
# admin role. Enforcement lives in auth.caller_suites/require_suite and the
# departments/fleets routes.

@router.get("/users/suites")
async def list_user_suites(request: Request) -> dict:
    """Every explicit department grant, plus the grantable suite catalog."""
    require_global_permission(request, "admin")
    from maverick_dashboard import suite_grants
    default = suite_grants.default_suites()
    return {
        "grants": suite_grants.list_grants(),
        "suites": sorted(suite_grants.known_suites()),
        "default_suites": sorted(default) if default is not None else None,
    }


@router.put("/users/{principal}/suites", status_code=204)
async def set_user_suites(
    request: Request, principal: str, body: UserSuitesIn,
) -> Response:
    """Scope a user to exactly these departments (empty list = none)."""
    require_global_permission(request, "admin")
    from maverick_dashboard import suite_grants
    try:
        suite_grants.set_suites(principal, body.suites,
                                actor=caller_principal(request) or "local")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)


@router.delete("/users/{principal}/suites", status_code=204)
async def remove_user_suites(request: Request, principal: str) -> Response:
    """Lift a user's department scoping (back to unrestricted/default)."""
    require_global_permission(request, "admin")
    from maverick_dashboard import suite_grants
    suite_grants.remove_grant(principal,
                              actor=caller_principal(request) or "local")
    return Response(status_code=204)


@router.post("/goals", response_model=GoalOut, status_code=201)
async def create_goal(request: Request, payload: GoalIn, bg: BackgroundTasks) -> GoalOut:
    require_permission(request, "operate")
    require_provider_or_400()
    # Shared sliding-window cap across /chat/send + this route, so a
    # runaway loop can't spawn unbounded (paid) goals.
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request)
    title = payload.title
    description = payload.description
    if payload.template:
        from maverick.templates import load_template
        try:
            tpl = load_template(payload.template)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            # Don't echo the raw error: its str() carries the absolute on-disk
            # template path. Reflect the caller's own template name instead; the
            # original (with path) stays chained for server-side logs.
            raise HTTPException(
                status_code=404, detail=f"template not found: {payload.template!r}"
            ) from e
        try:
            title, description = tpl.render(**(payload.params or {}))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    title = (title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    w = _world()
    # Idempotency-Key (optional, RFC-style): a client whose POST times out at the
    # LB and retries must not double-create / double-bill a paid run -- and on a
    # multi-replica deployment the retry can land on a different replica, so the
    # dedup must be in the shared store (reusing the backend-agnostic
    # mark/lookup_processed_message primitive). The key is scoped to the caller so
    # it can't collide across principals. A replay returns the ORIGINAL goal and
    # dispatches no second run.
    idem_key = (request.headers.get("Idempotency-Key") or "").strip()
    idem_ext = ""
    if idem_key:
        if len(idem_key) > 255:
            raise HTTPException(status_code=400, detail="Idempotency-Key too long (max 255)")
        idem_ext = f"{caller_principal(request) or ''}:{idem_key}"
        prior = w.lookup_processed_message(_IDEMPOTENCY_CHANNEL, idem_ext)
        if prior is not None:
            g0 = w.get_goal(prior)
            if g0 is not None:
                return _to_goal_out(g0)  # replay: original goal, no new run
    goal_id = w.create_goal(title[:200], description, owner=caller_principal(request) or "")
    if idem_key and not w.mark_message_processed(
        _IDEMPOTENCY_CHANNEL, idem_ext, goal_id=goal_id
    ):
        # Lost a concurrent race on the same key: return the winner's goal and do
        # NOT dispatch a run for this (now-orphaned, never-run) goal row.
        prior = w.lookup_processed_message(_IDEMPOTENCY_CHANNEL, idem_ext)
        g0 = w.get_goal(prior) if prior is not None else None
        return _to_goal_out(g0 or w.get_goal(goal_id))
    from maverick.runner import run_goal_in_thread
    # Enforce server-side execution caps even when callers request larger values.
    max_dollars = min(payload.max_dollars, DEFAULT_MAX_DOLLARS)
    max_wall_seconds = min(payload.max_wall_seconds, DEFAULT_MAX_WALL_SECONDS)
    max_depth = min(payload.max_depth, DEFAULT_MAX_DEPTH)

    user_id = execution_user_id_from_request(request)
    if user_id:
        bg.add_task(
            run_goal_in_thread, goal_id,
            max_dollars, max_wall_seconds, max_depth,
            channel="api", user_id=user_id,
        )
    else:
        bg.add_task(
            run_goal_in_thread, goal_id,
            max_dollars, max_wall_seconds, max_depth,
        )
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=500, detail="goal vanished after create")
    return _to_goal_out(g)


@router.get("/goals", response_model=list[GoalOut])
async def list_goals(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[GoalOut]:
    """List goals (newest first), paginated.

    Council perf fix: previous version pulled every goal ever into
    Python via ``list_goals()``, then sliced. Now the LIMIT/OFFSET are
    pushed to SQL.

    Owner-scoped: a non-admin authenticated caller sees only their own goals;
    auth-off and admin callers see all (``goal_owner_filter`` returns None).
    """
    w = _world()
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    goals = w.list_goals(
        status=status, owner=goal_owner_filter(request),
        limit=limit, offset=offset, order="desc",
    )
    return [_to_goal_out(g) for g in goals]


@router.get("/goals/search", response_model=list[GoalOut])
async def search_goals(request: Request, q: str, limit: int = 50) -> list[GoalOut]:
    """Search across runs (goals) by text in title / description / result.

    Owner-scoped: a non-admin authenticated caller searches only their own
    goals; auth-off and admin callers search all. Declared before
    ``/goals/{goal_id}`` so the literal ``search`` path wins over the int param.
    """
    query = (q or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or 50), 200))
    goals = _world().search_goals(query, owner=goal_owner_filter(request), limit=limit)
    return [_to_goal_out(g) for g in goals]


@router.get("/goals/{goal_id}", response_model=GoalOut)
async def get_goal(request: Request, goal_id: int) -> GoalOut:
    g = _world().get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    return _to_goal_out(g)


@router.get("/goals/{goal_id}/events", response_model=GoalEventsResponse)
async def goal_events(
    request: Request, goal_id: int, since: int = 0, limit: int = 200,
) -> GoalEventsResponse:
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    events = w.goal_events(goal_id, since_id=since, limit=max(1, min(limit, 500)))
    return GoalEventsResponse(
        status=g.status,
        result=g.result,
        next_id=events[-1].id if events else since,
        events=[
            GoalEventOut(id=e.id, agent=e.agent, kind=e.kind,
                         content=e.content, ts=e.ts)
            for e in events
        ],
    )


def _sse_event(e) -> str:
    """Format one goal event as a Server-Sent Event frame."""
    data = json.dumps({"id": e.id, "agent": e.agent, "kind": e.kind,
                       "content": e.content, "ts": e.ts})
    kind = str(e.kind or "message").replace("\n", " ").replace("\r", " ")
    return f"id: {e.id}\nevent: {kind}\ndata: {data}\n\n"


_TERMINAL_STATUSES = frozenset({"done", "failed", "cancelled", "blocked", "error"})

# ----- v1 SSE stream resource limits -----
# Match the legacy dashboard stream hardening: open SSE streams hold an async
# task and repeatedly poll SQLite, so cap concurrency, enforce a finite stream
# lifetime, and use a server-controlled polling cadence with idle backoff. The
# semaphore lives in _shared so app and api share ONE process-wide cap (was a
# duplicate definition here -> two independent caps). _get_sse_semaphore is
# imported above.


_SSE_POLL_INTERVAL = 0.5
_SSE_MAX_POLL_INTERVAL = 5.0
_SSE_IDLE_HEARTBEAT_EVERY = 30.0
_SSE_MAX_STREAM_SECONDS = 300.0
_SSE_MAX_BATCH = 200

@router.get("/goals/{goal_id}/events/stream")
async def goal_events_stream(
    request: Request, goal_id: int, since: int = 0, limit: int = 0,
    poll: float = 1.0,
) -> StreamingResponse:
    """Real-time **SSE** stream of a goal's events (`text/event-stream`).

    Tails the durable `goal_events` log (so it works across the worker/dashboard
    process split, unlike an in-process bus): emits each new event as it lands,
    ends when the goal reaches a terminal status with no more events, or on
    client disconnect. ``limit`` (>0) closes after N events — used by tests and
    bounded consumers. ``poll`` is accepted for compatibility but ignored; the
    server controls polling cadence and idle backoff.
    """
    del poll
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)

    sem = _get_sse_semaphore()
    if sem.locked():
        raise HTTPException(
            status_code=503,
            detail="too many concurrent event streams; retry shortly",
            headers={"Retry-After": "5"},
        )
    await sem.acquire()

    async def _gen():
        started = asyncio.get_running_loop().time()
        last = since
        sent = 0
        idle_for = 0.0
        poll_interval = _SSE_POLL_INTERVAL
        try:
            yield ": connected\n\n"   # open the stream immediately
            while True:
                if await request.is_disconnected():
                    break
                if (asyncio.get_running_loop().time() - started) >= _SSE_MAX_STREAM_SECONDS:
                    yield "event: timeout\ndata: {\"detail\": \"stream lifetime exceeded\"}\n\n"
                    return
                events = await run_in_threadpool(
                    w.goal_events, goal_id, last, _SSE_MAX_BATCH)
                for e in events:
                    yield _sse_event(e)
                    last = e.id
                    sent += 1
                    if limit and sent >= limit:
                        return
                cur = await run_in_threadpool(w.get_goal, goal_id)
                if events:
                    idle_for = 0.0
                    poll_interval = _SSE_POLL_INTERVAL
                else:
                    idle_for += poll_interval
                    if idle_for >= _SSE_IDLE_HEARTBEAT_EVERY:
                        yield ": heartbeat\n\n"
                        idle_for = 0.0
                    poll_interval = min(_SSE_MAX_POLL_INTERVAL, poll_interval * 1.5)
                if cur is not None and cur.status in _TERMINAL_STATUSES:
                    if len(events) < _SSE_MAX_BATCH:
                        yield f"event: end\ndata: {json.dumps({'status': cur.status})}\n\n"
                        return
                    # A full batch means more backlog may remain: keep draining
                    # (without sleeping) and end only once a read comes up short.
                    continue
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            return
        finally:
            sem.release()

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.post("/goals/{goal_id}/answer", status_code=204)
async def answer_question(request: Request, goal_id: int, payload: AnswerIn) -> None:
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    require_permission(request, "operate")
    answer = (payload.answer or "").strip()
    if not answer:
        raise HTTPException(status_code=400, detail="answer is required")
    qs = w.open_questions(goal_id=goal_id)
    if not any(q.id == payload.question_id for q in qs):
        raise HTTPException(status_code=404, detail="no such open question for this goal")
    w.answer(payload.question_id, answer)




@router.post(
    "/goals/{goal_id}/attachments",
    response_model=AttachmentOut,
    status_code=201,
)
async def upload_attachment(
    request: Request, goal_id: int, bg: BackgroundTasks,
    file: UploadFile = File(...),
) -> AttachmentOut:
    """Upload a file of any supported kind and attach it to a goal.

    Text, images, audio, video, PDFs, and Office/OpenDocument files are
    accepted (executables and generic archives are always denied by
    magic-byte sniffing). Size and mime-type validation are enforced
    server-side; the agent's `list_attachments` tool exposes the uploaded
    set, images are auto-embedded as vision blocks and PDFs as document
    blocks on the first message.
    """
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)

    from maverick.attachments import (
        MAX_FILE_BYTES,
        AttachmentRejected,
        store,
    )

    data = await file.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"file too large: {len(data)} bytes (limit {MAX_FILE_BYTES})"
            ),
        )

    mime = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"

    existing = sum(a.size_bytes for a in w.list_attachments(goal_id))
    try:
        stored = store(
            goal_id,
            filename=filename,
            mime=mime,
            data=data,
            existing_total=existing,
        )
    except AttachmentRejected as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    aid = w.add_attachment(
        goal_id=goal_id,
        filename=stored.filename,
        mime=stored.mime,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        path=str(stored.path),
    )
    # Companion generation (transcripts / extracted text) off the request
    # path; best-effort, never raises.
    from maverick.attachments import generate_companions
    bg.add_task(generate_companions, w, goal_id)
    return AttachmentOut(
        id=aid,
        filename=stored.filename,
        mime=stored.mime,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )


@router.get("/goals/{goal_id}/attachments", response_model=list[AttachmentOut])
async def list_goal_attachments(request: Request, goal_id: int) -> list[AttachmentOut]:
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    return [
        AttachmentOut(
            id=a.id, filename=a.filename, mime=a.mime,
            size_bytes=a.size_bytes, sha256=a.sha256,
        )
        for a in w.list_attachments(goal_id)
    ]


@router.get("/goals/{goal_id}/attachments/{attachment_id}/download")
async def download_goal_attachment(
    request: Request, goal_id: int, attachment_id: int,
) -> Response:
    """Serve one stored attachment back to the browser (goal-page panel).

    Same access rules as the list; the bytes come off the local store (with
    an S3 pull-back for multi-host deployments). Served as a download with
    the ORIGINAL filename — never inline, so a crafted HTML/SVG upload can't
    execute in the dashboard origin.
    """
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    match = next(
        (a for a in w.list_attachments(goal_id) if a.id == attachment_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="no such attachment")
    from pathlib import Path as _Path
    path = _Path(match.path)
    if not path.is_file():
        # Uploaded on another worker host: try the S3 mirror.
        from maverick.attachments import s3_fetch
        fetched = await run_in_threadpool(s3_fetch, goal_id, path.name)
        if fetched is None:
            raise HTTPException(
                status_code=410, detail="attachment bytes not on this host")
        path = fetched
    data = await run_in_threadpool(path.read_bytes)
    safe_name = match.filename.replace('"', "_")
    return Response(
        content=data,
        media_type=match.mime or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/facts", response_model=dict[str, str])
async def list_facts() -> dict[str, str]:
    return _world().get_facts()


@router.post("/facts", status_code=204)
async def set_fact(request: Request, payload: FactIn) -> None:
    require_permission(request, "operate")
    key = (payload.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="fact key is required")
    _world().upsert_fact(key, payload.value)


@router.post("/outcomes", status_code=204)
async def record_outcome(request: Request, payload: OutcomeIn) -> None:
    """Ingest a real downstream outcome for a past episode (the grounded reward).

    The HTTP entrypoint a system-of-record connector (CRM / ERP / ticketing
    webhook) calls once reality reports back -- invoice paid (1.0), ticket
    reopened (0.0), or a graded result. The Cognitive Data Engine flywheel then
    prefers this over the verifier proxy on its next turn, so learning is grounded
    in what actually happened. ``value`` is clamped to [0, 1] by the store.
    """
    require_permission(request, "operate")
    goal_id = int(payload.goal_id)
    episode_id = int(payload.episode_id)
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    if not w.episode_exists(goal_id, episode_id):
        raise HTTPException(status_code=404, detail="no such episode")
    from maverick.consequence import record_outcome as _rec
    _rec(goal_id, episode_id, float(payload.value), kind=(payload.kind or ""))


@router.post("/outcomes/link", status_code=204)
async def link_outcome(request: Request, payload: OutcomeLinkIn) -> None:
    """Link an external business key to the episode that acted on it.

    The registration half of the system-of-record loop: when a run creates an
    invoice / opens a ticket, this records ``invoice:INV-42 -> (goal, episode)``
    so a later outcome reported against that key (see ``/outcomes/by-key``) can be
    grounded without the reporter knowing any episode id. Always harmless to store.
    """
    require_permission(request, "operate")
    goal_id = int(payload.goal_id)
    episode_id = int(payload.episode_id)
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    if not w.episode_exists(goal_id, episode_id):
        raise HTTPException(status_code=404, detail="no such episode")
    from maverick.consequence import link_outcome_key
    link_outcome_key(payload.key, goal_id, episode_id)


@router.post("/outcomes/by-key")
async def record_outcome_by_key(request: Request, payload: OutcomeByKeyIn) -> dict:
    """Ingest a real downstream outcome using only the business key.

    The endpoint a system-of-record webhook (Stripe "invoice.paid", Zendesk
    "ticket.reopened") calls: it knows its own key, not a Lightwork episode. We
    resolve the key to the episode that acted (registered via ``/outcomes/link``)
    and ground the reward there. ``matched`` is ``false`` when no run has linked
    that key -- the connector reported an outcome for work Lightwork didn't do.
    """
    require_permission(request, "operate")
    from maverick import consequence
    w = _world()

    def _authorize(goal_id: int, _episode_id: int) -> bool:
        # Enforce the same access boundary as the goal/episode ingest so an
        # operate caller can't ground outcomes on a goal they can't see (the
        # correlation store is already tenant-scoped by path).
        g = w.get_goal(goal_id)
        return g is not None and can_access_goal(request, g)

    matched = consequence.record_outcome_for_key(
        payload.key, float(payload.value), kind=(payload.kind or ""),
        authorize=_authorize)
    return {"matched": bool(matched)}


# ---- turnkey "connect this account" OAuth flow --------------------------------
#
# Connect a SaaS account by NAME using a provider preset, so an OAuth'd event
# source (oauth_http_json / github_issues with provider=<name>) can poll it.
# Authorizing a token is the same trust as sending it, so all mutating steps are
# ADMIN-gated (matching the provider-trigger admin gate). The client secret is
# read from the operator's env, never posted here or persisted.


def _require_oauth_vault() -> None:
    from maverick import oauth_vault
    if not oauth_vault.enabled():
        raise HTTPException(
            status_code=403,
            detail=("Account connections aren't enabled yet — an administrator "
                    "must turn on the OAuth vault first (enable [oauth] vault "
                    "in the server configuration, or set MAVERICK_OAUTH_VAULT=1)."),
        )


@router.get("/oauth/providers")
async def oauth_providers_endpoint(request: Request) -> dict:
    """The connectable provider presets + which are already connected (view)."""
    require_permission(request, "view")
    from maverick import oauth_providers, oauth_vault
    connected: list[str] = []
    status: dict[str, dict] = {}
    if oauth_vault.enabled():
        try:
            vault = oauth_vault.get_vault()
            connected = vault.providers()
            # Token-free per-provider health so the UI can flag an expired
            # connection without a live network call.
            for name in connected:
                st = vault.status(name)
                if st is not None:
                    status[name] = st
        except Exception:  # pragma: no cover -- a vault read never errors the list
            connected = []
            status = {}
    return {
        "providers": oauth_providers.provider_catalog(),
        "connected": connected,
        "status": status,
        "vault_enabled": oauth_vault.enabled(),
    }


@router.post("/oauth/{provider}/authorize-url")
async def oauth_authorize_url(request: Request, provider: str,
                              payload: OAuthAuthorizeIn) -> dict:
    """Build the consent URL for a preset provider (admin). Returns the URL to
    open and the PKCE verifier to keep -- pass the verifier back to /exchange.
    Stateless: the verifier round-trips through the (trusted) operator."""
    require_permission(request, "admin")
    _require_oauth_vault()
    from maverick import oauth_providers
    built = oauth_providers.build_authorize_url(
        provider, redirect_uri=payload.redirect_uri,
        client_id=payload.client_id or "", scopes=payload.scopes)
    if built is None:
        prov = oauth_providers.get_provider(provider)
        if prov is None:
            raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
        raise HTTPException(
            status_code=400,
            detail=f"set {prov.default_client_id_env()} (the OAuth app client id) first")
    url, verifier = built
    return {"authorize_url": url, "verifier": verifier}


@router.post("/oauth/{provider}/exchange")
async def oauth_exchange(request: Request, provider: str,
                         payload: OAuthExchangeIn) -> dict:
    """Swap the authorization code for tokens and seal them in the per-tenant
    vault under the provider name (admin). The provider preset supplies the token
    endpoint; the client secret is read from the operator's env."""
    require_permission(request, "admin")
    _require_oauth_vault()
    from maverick import oauth_providers
    if oauth_providers.get_provider(provider) is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    try:
        summary = oauth_providers.exchange_code(
            provider, code=payload.code, redirect_uri=payload.redirect_uri,
            verifier=payload.verifier or "", client_id=payload.client_id or "")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # a provider/network failure -> 502, not a 500
        raise HTTPException(status_code=502, detail=f"token exchange failed: {e}") from e
    return {"connected": True, **summary}


@router.post("/oauth/{provider}/test")
async def oauth_test(request: Request, provider: str) -> dict:
    """Validate a connected account (admin): obtain a live access token from the
    sealed vault, refreshing it through the preset's token endpoint if it's
    expired. Reports health only -- never the token itself. ``ok`` is true when
    a usable token could be produced, false (with a redacted ``error``) when the
    refresh failed, so an operator sees a stale connection before a flow does."""
    require_permission(request, "admin")
    _require_oauth_vault()
    from maverick import oauth_providers, oauth_vault
    if oauth_providers.get_provider(provider) is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider!r}")
    vault = oauth_vault.get_vault()
    if vault.status(provider) is None:
        raise HTTPException(status_code=404, detail=f"{provider!r} is not connected")
    refresher = oauth_providers.make_refresher(provider)
    try:
        token = vault.access_token(provider, refresher=refresher)
    except Exception as e:  # a provider/network failure -> reported, not a 500
        return {"ok": False, "error": str(e), "status": vault.status(provider)}
    return {"ok": bool(token), "status": vault.status(provider)}


@router.delete("/oauth/{provider}", status_code=204)
async def oauth_disconnect(request: Request, provider: str) -> None:
    """Remove a connected account's sealed token from the vault (admin)."""
    require_permission(request, "admin")
    _require_oauth_vault()
    from maverick import oauth_vault
    oauth_vault.get_vault().delete(provider)


# ---- connections: named, sealed SaaS credentials -----------------------------


def _require_connections() -> None:
    from maverick import connections
    if not connections.enabled():
        raise HTTPException(
            status_code=403,
            detail=("Connections aren't enabled on this server yet — an "
                    "administrator can switch them on (enable [connections] in "
                    "the server configuration, or set MAVERICK_CONNECTIONS=1)."),
        )


@router.get("/connections")
async def list_connections_endpoint(request: Request) -> dict:
    """Named connections the caller may see (tokens never returned -- only
    ``has_token``). Owner-scoped when auth is on."""
    require_permission(request, "operate")
    _require_connections()
    from maverick import connections
    return {"connections": connections.list_connections(owner=goal_owner_filter(request))}


@router.post("/connections", status_code=201)
async def create_connection_endpoint(request: Request, payload: ConnectionIn) -> dict:
    """Create or replace a named connection (base URL + token, sealed at rest).
    A connector can then be wired without env vars -- the connection is the
    fallback when ``<NAME>_TOKEN`` is unset. Admin-gated: a saved connection
    holds a live credential, so only administrators may create/rotate one."""
    require_permission(request, "admin")
    _require_connections()
    from maverick import connections
    try:
        # Owner-scope like triggers: a non-admin can't overwrite another
        # tenant's connection by reusing its name. Enforced INSIDE the store
        # lock (expected_owner) so the check and the write are atomic.
        rec = connections.set_connection(
            payload.name, connector=payload.connector, base_url=payload.base_url,
            token=payload.token, owner=caller_principal(request) or "",
            expected_owner=goal_owner_filter(request))
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return rec


@router.post("/connections/{name}/test")
async def test_connection_endpoint(request: Request, name: str) -> dict:
    """Best-effort reachability check: GET the connection's base URL with its
    token and report whether it authenticates. Never returns the token.
    Admin-gated: the probe sends the sealed token, so it's part of managing the
    credential, not an operator action."""
    require_permission(request, "admin")
    _require_connections()
    from maverick import connections
    rec = connections.get_connection(name)
    # Owner-scope like list/delete/create: a non-admin can only test their own
    # connection (else a name-guess is a cross-owner reachability oracle AND
    # sends another owner's token). A 404 (not 403) so it can't confirm the name.
    owner_filter = goal_owner_filter(request)
    if rec is None or (owner_filter is not None and rec.get("owner", "") != owner_filter):
        raise HTTPException(status_code=404, detail="no such connection")
    base = str(rec.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "detail": "no base URL set"}

    def _probe() -> dict:
        try:
            from maverick.tools import _ssrf
            from maverick.tools.enterprise_connectors import auth_headers_for
            # Speak the connector's saved-token auth dialect (basic / custom
            # scheme), but do not include operator environment-backed auxiliary
            # headers: saved connection tests use a caller-controlled base_url.
            headers = auth_headers_for(
                str(rec.get("connector") or ""), str(rec.get("token") or ""),
                include_extra_headers_env=False,
            )
            # This request carries the sealed token, so it MUST go through the
            # governed SSRF-safe path: safe_get pins the host to a validated
            # PUBLIC ip (blocks 169.254/127.0.0.1/private/link-local) and forces
            # follow_redirects=False, so a base_url or a 3xx can't turn this into
            # an internal-network probe or forward the token to another host.
            r = _ssrf.safe_get(base, headers=headers, timeout=10.0)
            # 2xx/3xx or 401/403 all mean "reachable"; only a connect error is a hard fail.
            ok = r.status_code < 500
            return {"ok": ok, "status": r.status_code,
                    "detail": ("authenticated" if r.status_code < 400
                               else f"reachable but returned {r.status_code}")}
        except _ssrf.BlockedHost:
            return {"ok": False, "detail": "base URL host is not permitted"}
        except Exception as e:  # pragma: no cover -- network varies
            return {"ok": False, "detail": f"could not reach host: {type(e).__name__}"}
    return await run_in_threadpool(_probe)


@router.delete("/connections/{name}")
async def delete_connection_endpoint(request: Request, name: str) -> dict:
    require_permission(request, "admin")  # managing credentials is admin-only
    _require_connections()
    from maverick import connections
    if not connections.delete_connection(name, owner=goal_owner_filter(request)):
        raise HTTPException(status_code=404, detail="no connection with that name")
    return {"deleted": connections.normalize_name(name)}


# ---- governance assessments: privacy / security / AI-risk register ------------
# Reading the register is audit-level; drafting and reviewing (which record a
# governance decision) require admin. Auto-draft heuristics live in
# maverick.assessments; this is thin plumbing over that engine.

def _assess_kind(kind: str) -> str:
    if kind not in ("agent", "flow"):
        raise HTTPException(status_code=400, detail="kind must be 'agent' or 'flow'")
    return kind


@router.get("/assessments")
async def list_assessments_endpoint(request: Request) -> dict:
    require_permission(request, "audit")
    from maverick import assessments
    return {"assessments": assessments.list_assessments(),
            "due": assessments.due_assessments(),
            "templates": assessments.list_templates()}


@router.get("/assessments/export")
async def export_assessments_endpoint(request: Request, format: str = "json") -> Response:
    """Audit export of every finding. ``?format=csv`` for a spreadsheet."""
    require_permission(request, "audit")
    from maverick import assessments
    rows = assessments.export_rows()
    if format == "csv":
        import csv
        import io
        cols = ["kind", "subject", "assessment_status", "lens", "lens_status",
                "control", "severity", "finding_status", "note", "mitigation",
                "evidence", "reviewer", "reviewed_at"]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return Response(content=buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=assessments.csv"})
    return JSONResponse({"rows": rows})


@router.post("/assessments/sweep")
async def sweep_assessments_endpoint(request: Request) -> dict:
    """Re-assess every subject now (catch drift) and return what needs attention.
    The same sweep the automation scheduler runs daily, on demand."""
    require_permission(request, "admin")
    from maverick import assessments
    return assessments.sweep_due()


@router.get("/assessments/{kind}/{name}")
async def get_assessment_endpoint(request: Request, kind: str, name: str) -> dict:
    require_permission(request, "audit")
    from maverick import assessments
    a = assessments.get_assessment(_assess_kind(kind), name)
    if a is None:
        raise HTTPException(status_code=404, detail="no assessment for that subject")
    return a


@router.post("/assessments/{kind}/{name}/refresh")
async def refresh_assessment_endpoint(
    request: Request, kind: str, name: str, payload: AssessmentRefreshIn
) -> dict:
    """Auto-draft (or re-draft, detecting drift) the assessment for a subject."""
    require_permission(request, "admin")
    from maverick import assessments
    a = assessments.refresh(_assess_kind(kind), name, template=payload.template)
    if a is None:
        raise HTTPException(status_code=404, detail=f"unknown {kind}: {name!r}")
    return a


@router.post("/assessments/{kind}/{name}/review")
async def review_assessment_endpoint(
    request: Request, kind: str, name: str, payload: AssessmentReviewIn
) -> dict:
    """Record a reviewer's accept / needs-work decision on one lens."""
    require_permission(request, "admin")
    from maverick import assessments
    try:
        a = assessments.record_review(
            _assess_kind(kind), name, payload.lens, payload.decision,
            reviewer=caller_principal(request) or "", note=payload.note or "",
            cadence_days=payload.cadence_days)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if a is None:
        raise HTTPException(status_code=404, detail="no assessment for that subject")
    return a


@router.post("/assessments/{kind}/{name}/evidence")
async def add_assessment_evidence_endpoint(
    request: Request, kind: str, name: str, payload: AssessmentEvidenceIn
) -> dict:
    require_permission(request, "admin")
    from maverick import assessments
    a = assessments.add_evidence(_assess_kind(kind), name, payload.lens,
                                 payload.control, payload.evidence)
    if a is None:
        raise HTTPException(status_code=404, detail="no such finding")
    return a


@router.delete("/assessments/{kind}/{name}", status_code=204)
async def delete_assessment_endpoint(request: Request, kind: str, name: str) -> Response:
    require_permission(request, "admin")
    from maverick import assessments
    assessments.delete_assessment(_assess_kind(kind), name)
    return Response(status_code=204)


# ---- flows: a deterministic skeleton over agentic + tool steps ---------------


def _require_flows() -> None:
    from maverick import flow
    if not flow.enabled():
        raise HTTPException(
            status_code=403,
            detail=("Flows aren't enabled on this server yet — an administrator "
                    "can switch on the flow engine (enable [flows] in the "
                    "server configuration, or set MAVERICK_FLOWS=1)."),
        )


def _can_access_flow(request: Request, flow) -> bool:
    """Whether the caller may read or mutate a saved flow definition.

    Auth-off and dashboard admins keep the historical unscoped behavior.
    Authenticated non-admins may only access flows they own; legacy ownerless
    flows remain reachable only from the auth-off/admin paths.
    """
    principal = caller_principal(request)
    if principal is None or is_dashboard_admin(principal):
        return True
    return getattr(flow, "owner", "") == principal


def _assert_flow_access(request: Request, flow) -> None:
    if not _can_access_flow(request, flow):
        raise HTTPException(status_code=404, detail="no such flow")


@router.get("/flows")
async def list_flows_endpoint(request: Request) -> dict:
    """The saved flow definitions (a summary per flow)."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    return {"flows": [
        {"id": f.id, "name": f.name, "nodes": len(f.nodes),
         "single_agent": f.is_single_agent(), "schedule": f.schedule,
         "inputs": f.inputs}
        for f in store.list_flows()
        if _can_access_flow(request, f)
    ]}


@router.post("/flows", status_code=201)
async def save_flow_endpoint(request: Request, payload: FlowSaveIn) -> dict:
    """Create or replace a flow definition (validated before it is stored)."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import Flow, store
    try:
        flow = Flow.from_dict(payload.flow)
    except Exception as e:  # a malformed graph
        raise HTTPException(status_code=400, detail=f"invalid flow: {e}") from e
    if not flow.id:
        raise HTTPException(status_code=400, detail="flow needs an 'id'")
    errs = flow.validate()
    if errs:
        raise HTTPException(status_code=400, detail="invalid flow: " + "; ".join(errs[:5]))
    # Stamp ownership: the creator on first save, preserved across later edits so a
    # re-save can't silently transfer ownership. Scheduled (cron) runs attribute to
    # this owner (they have no request principal of their own).
    prior = store.load_flow(flow.id)
    if prior is not None:
        _assert_flow_access(request, prior)
    flow.owner = prior.owner if (prior and prior.owner) else (caller_principal(request) or "")
    store.save_flow(flow)
    return {"id": flow.id, "nodes": len(flow.nodes)}


# Common connector tools to bias the NL drafter toward real action nodes; the
# operator edits the result in the designer, and unknown tools fail gracefully.
_FLOW_DRAFT_TOOLS = (
    "slack_bot", "discord_bot", "teams_tool", "email_tool", "gmail_tool",
    "twilio_tool", "github_issues", "github_actions", "jira", "linear",
    "gitlab_issues", "salesforce_tool", "hubspot_tool", "stripe_tool",
    "shopify_tool", "servicenow_tool", "zendesk_tool", "notion", "confluence_tool",
    "asana_tool", "trello_tool", "clickup_tool", "airtable_tool", "calendar_tool",
    "gdrive_tool", "dropbox_tool", "s3_tool", "http_fetch", "sql_query",
)


_TOOL_INDEX_CACHE: list[dict] | None = None
_TOOL_INDEX_LOCK = threading.Lock()


def _live_tool_index() -> list[dict]:
    """The full flattened tool list ({name, description, params}) built ONCE and
    memoized. The registry (built-ins + every enabled connector) is process-
    stable config, so rebuilding hundreds of tool objects on every picker
    request -- once per debounced keystroke on the search path -- was wasted
    work. Both the curated catalog and the ?q= search read this cache. Cleared
    only by a process restart, which is also when connector config changes."""
    global _TOOL_INDEX_CACHE
    if _TOOL_INDEX_CACHE is not None:
        return _TOOL_INDEX_CACHE
    try:
        from maverick.sandbox import LocalBackend
        from maverick.tools import base_registry
        live = base_registry(_world(), LocalBackend(), goal_id=None).all()
    except Exception:  # pragma: no cover -- registry build varies by env
        live = []
    from .tool_categories import categorize
    idx = []
    for t in live:
        params = []
        if isinstance(t.input_schema, dict):
            params = list((t.input_schema.get("properties") or {}).keys())[:12]
        desc = (t.description or "")[:200]
        idx.append({"name": t.name, "description": desc, "params": params,
                    "category": categorize(t.name, desc)})
    with _TOOL_INDEX_LOCK:
        if _TOOL_INDEX_CACHE is None:
            _TOOL_INDEX_CACHE = idx
    return _TOOL_INDEX_CACHE


def _flow_tool_catalog() -> list[dict]:
    """The curated connector catalog for the designer's action picker + the NL
    drafter's grounding: each tool's name, one-line description, and param names
    (from its input schema). Best-effort -- a tool absent from the live registry
    still appears by name so the picker/drafter can offer it."""
    from .tool_categories import categorize
    by_name = {t["name"]: t for t in _live_tool_index()}
    out = []
    for name in _FLOW_DRAFT_TOOLS:
        t = by_name.get(name)
        desc = t["description"] if t is not None else ""
        out.append({"name": name, "description": desc,
                    "params": (t["params"] if t is not None else []),
                    "category": (t["category"] if t is not None else categorize(name, desc))})
    return out


@router.post("/flows/draft")
async def draft_flow_endpoint(request: Request, payload: FlowDraftIn) -> dict:
    """Draft a first-pass flow from a plain-English description (the designer's
    ✨ button). Returns the flow graph (not saved) + any notes; falls back to a
    single agent step if the model can't produce a full graph. The drafter is
    grounded with each connector's description so action nodes name a real tool."""
    require_permission(request, "operate")
    _require_flows()
    from maverick_dashboard._shared import require_provider_or_400
    require_provider_or_400()
    from maverick.flow.draft import draft_flow

    def _draft():
        catalog = _flow_tool_catalog()
        tool_docs = {t["name"]: t["description"] for t in catalog if t["description"]}
        return draft_flow(payload.description, tools=_FLOW_DRAFT_TOOLS,
                          tool_docs=tool_docs, flow_id=payload.flow_id)
    flow, notes = await run_in_threadpool(_draft)
    return {"flow": flow.to_dict(), "notes": notes}


@router.post("/flows/chat")
async def flow_chat_endpoint(request: Request, payload: FlowChatIn) -> dict:
    """One turn of the designer's flow copilot: answer a question about the
    current canvas, or edit it via validated patches (never a whole-graph
    regeneration on a populated canvas). ``run_id`` grounds "why did this
    fail?" / "fix it" turns in that run's real per-node trace. The patched
    graph is returned to the canvas; nothing is saved until the user saves."""
    require_permission(request, "operate")
    _require_flows()
    from maverick_dashboard._shared import require_provider_or_400
    require_provider_or_400()
    from maverick.flow import Flow
    from maverick.flow.chat import chat_flow
    try:
        current = Flow.from_dict(payload.flow) if payload.flow else None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid flow: {e}") from e
    run = _run_out(_load_owned_run(request, payload.run_id)) if payload.run_id else None

    def _turn():
        catalog = _flow_tool_catalog()
        tool_docs = {t["name"]: t["description"] for t in catalog if t["description"]}
        return chat_flow(payload.message, flow=current, history=payload.history,
                         run=run, tools=_FLOW_DRAFT_TOOLS, tool_docs=tool_docs)
    res = await run_in_threadpool(_turn)
    return {
        "reply": res.reply,
        "flow": res.flow.to_dict() if res.flow is not None else None,
        "applied": res.applied,
        "notes": res.notes,
    }


@router.get("/flows/runs")
async def list_flow_runs_endpoint(request: Request, flow_id: str = "",
                                  limit: int = 50) -> dict:
    """Recent flow runs (owner-scoped). Defined before ``/flows/{flow_id}`` so the
    literal ``runs`` path isn't captured as a flow id."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    owner = goal_owner_filter(request)
    runs = store.list_runs(flow_id=flow_id or None, owner=owner,
                           limit=max(1, min(500, limit)))
    return {"runs": [_run_out(r) for r in runs]}


@router.get("/flows/{flow_id}/schema")
async def flow_schema_endpoint(request: Request, flow_id: str) -> dict:
    """The learned output-key shapes for a flow (from real runs): per key, its
    value type and, for objects/arrays-of-objects, the field names -- so the
    designer's data pills can offer nested keys like ``order.total``. Field
    names + types only; no values are ever stored or returned."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    return {"schema": store.load_flow_schema(flow_id)}


def _run_out(run) -> dict:
    """A flow run for the API with secret-looking values in its ``data`` masked,
    so a token a connector put into the run never leaves over HTTP."""
    from maverick.flow.redact import redact
    d = run.to_dict()
    d["data"] = redact(d.get("data") or {})
    return d


def _load_owned_run(request: Request, run_id: str):
    """A flow run the caller may see, or a 404 (indistinguishable from missing,
    so a run's existence isn't leaked across owners)."""
    from maverick.flow import store
    run = store.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    owner = goal_owner_filter(request)
    if owner is not None and run.owner != owner:
        raise HTTPException(status_code=404, detail="no such run")
    return run


@router.get("/flows/runs/{run_id}")
async def get_flow_run_endpoint(request: Request, run_id: str) -> dict:
    require_permission(request, "operate")
    _require_flows()
    return _run_out(_load_owned_run(request, run_id))


@router.get("/flows/runs/{run_id}/events")
async def flow_run_events_stream(request: Request, run_id: str) -> StreamingResponse:
    """Server-Sent Events stream of a flow run's live state: one snapshot per
    change (keyed on the run's ``updated`` stamp), ending with a terminal event
    when the run completes/fails/rejects or parks on a human/event pause. The
    canvas overlay and the run viewer prefer this over polling; both fall back
    to polling when SSE is unavailable. Same slot cap + lifetime bound as the
    goal-events stream, so open tabs can't exhaust the event loop."""
    import asyncio as _asyncio
    import json as _json

    require_permission(request, "operate")
    _require_flows()
    _load_owned_run(request, run_id)     # 404 before holding a stream slot
    from maverick.flow import store as flow_store

    from maverick_dashboard._shared import _get_sse_semaphore
    sem = _get_sse_semaphore()
    if sem.locked():
        raise HTTPException(status_code=503,
                            detail="too many concurrent event streams; retry shortly",
                            headers={"Retry-After": "5"})
    await sem.acquire()

    # completed/failed/rejected are final; a run parked on a human or an
    # external event won't change until someone acts -- close the stream and
    # let the page's actions (approve / resume) drive the next one.
    end_states = ("completed", "failed", "rejected", "paused_approval", "paused_event")
    poll, max_poll, max_seconds, heartbeat_every = 0.5, 5.0, 300.0, 30.0

    async def generate():
        started = _asyncio.get_running_loop().time()
        last_stamp = None
        interval, idle = poll, 0.0
        yield "retry: 3000\n\n"
        try:
            while True:
                if (_asyncio.get_running_loop().time() - started) >= max_seconds:
                    yield "event: timeout\ndata: {\"detail\": \"stream lifetime exceeded\"}\n\n"
                    return
                # Stop re-reading the run file the moment the client goes away
                # (a closed tab) instead of leaning solely on CancelledError
                # timing -- matches the goal-events stream.
                if await request.is_disconnected():
                    return
                run = await run_in_threadpool(flow_store.load_run, run_id)
                if run is None:
                    yield "event: error\ndata: {\"detail\": \"run vanished\"}\n\n"
                    return
                if run.updated != last_stamp:
                    last_stamp = run.updated
                    payload = _run_out(run)
                    kind = "terminal" if run.status in end_states else "snapshot"
                    yield f"event: {kind}\ndata: {_json.dumps(payload, default=str)}\n\n"
                    if kind == "terminal":
                        return
                    interval, idle = poll, 0.0
                else:
                    idle += interval
                    if idle >= heartbeat_every:
                        yield ": heartbeat\n\n"
                        idle = 0.0
                    interval = min(max_poll, interval * 1.5)
                await _asyncio.sleep(interval)
        except _asyncio.CancelledError:
            return
        finally:
            sem.release()

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/flows/runs/{run_id}/resume")
async def resume_flow_run_endpoint(request: Request, run_id: str,
                                   payload: FlowResumeIn) -> dict:
    """Resume a paused flow run with a human decision (approve/reject)."""
    require_permission(request, "operate")
    _require_flows()
    run = _load_owned_run(request, run_id)
    # Only a paused run may be resumed -- resuming a completed/rejected run would
    # re-run the flow or run a rejected node (409, not a silent no-op).
    from maverick.flow.runner import PAUSED_STATUSES
    if run.status not in PAUSED_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"run is not paused (status {run.status})")
    from maverick_dashboard import automation_queue as aq
    resume_payload = {
        "flow_id": run.flow_id, "run_id": run_id, "decision": payload.decision,
        "inputs": dict(payload.inputs or {}), "owner": run.owner}
    resume_payload.update(aq._job_tenant_payload())
    aq.queue().enqueue(aq.FLOW_RESUME_KIND, resume_payload)
    return {"run_id": run_id, "status": "resuming", "decision": payload.decision}


@router.post("/flows/runs/{run_id}/retry")
async def retry_flow_run_endpoint(request: Request, run_id: str,
                                  from_failure: bool = False) -> dict:
    """Re-run a finished/failed run from scratch with its ORIGINAL trigger inputs
    (a fresh run, so any partial side effects aren't re-driven mid-flow). This is
    the recovery path a dead-lettered run needs -- the run's input_data was kept
    for exactly this.

    ``?from_failure=1`` instead RESUMES a FAILED run in place, re-entering AT the
    node that failed with the data as of the failure -- the pre-failure nodes'
    side effects are not re-driven. Requires the run to be failed with a
    recorded failure cursor (409 otherwise)."""
    require_permission(request, "operate")
    _require_flows()
    run = _load_owned_run(request, run_id)
    # A still-in-flight run (queued/running or parked on ANY pause, including
    # paused_event) must not be retried-from-scratch -- that would spawn a
    # duplicate run alongside the live one. from_failure below is the only path
    # that acts on a failed run.
    from maverick.flow.runner import ACTIVE_STATUSES
    if run.status in ACTIVE_STATUSES:
        raise HTTPException(status_code=409,
                            detail=f"run is still active (status {run.status}); nothing to retry")
    from maverick.flow import store
    if store.load_flow(run.flow_id) is None:
        raise HTTPException(status_code=404, detail="flow no longer exists")
    from maverick_dashboard import automation_queue as aq
    if from_failure:
        if run.status != "failed" or not run.cursor:
            raise HTTPException(
                status_code=409,
                detail="only a failed run with a recorded failure node can be "
                       "resumed from its failure; use a plain retry instead")
        retry_payload = {"flow_id": run.flow_id, "run_id": run_id, "owner": run.owner,
                        "from_failure": True}
        retry_payload.update(aq._job_tenant_payload())
        aq.queue().enqueue(aq.FLOW_RESUME_KIND, retry_payload)
        return {"run_id": run_id, "status": "resuming", "from_node": run.cursor}
    new_id = aq.enqueue_flow_run(run.flow_id, dict(run.input_data or {}), run.owner,
                                 origin=f"retry:{run_id}")
    return {"run_id": new_id, "status": "queued", "retried_from": run_id}


@router.get("/flows/tools")
async def flow_tools_endpoint(request: Request, q: str = "", category: str = "") -> dict:
    """The connector/action catalog for the designer's categorized, searchable
    tool picker: name, description, param names, and a coarse ``category``.

    - no ``q``, no ``category``: the curated starter set.
    - ``category`` (no ``q``): every live tool in that bucket (built-ins + every
      enabled connector), so a user can browse one area instead of a 3k-name list.
    - ``q``: a substring search over the FULL live registry, optionally narrowed
      to ``category``.

    Every response also carries ``categories`` (the display-ordered bucket names)
    so the picker can offer the filter without a second round-trip. Declared
    before ``/flows/{flow_id}`` so the literal ``tools`` path isn't captured as a
    flow id."""
    require_permission(request, "operate")
    _require_flows()
    from .tool_categories import categories as _cat_names
    q = q.strip().lower()[:80]
    category = category.strip()[:40]
    cats = _cat_names()

    if not q and not category:
        return {"tools": await run_in_threadpool(_flow_tool_catalog),
                "categories": cats}

    def _filter():
        out = _live_tool_index()
        if category:
            out = [t for t in out if t.get("category") == category]
        if q:
            out = [t for t in out if q in f"{t['name']} {t['description']}".lower()]
            # name-prefix matches first, then name matches, then description-only
            out = sorted(out, key=lambda t: (not t["name"].lower().startswith(q),
                                             q not in t["name"].lower(), t["name"]))
        else:
            out = sorted(out, key=lambda t: t["name"])
        return out[:50]
    return {"tools": await run_in_threadpool(_filter), "categories": cats}


# Curated starter flows for the designer's gallery: small, runnable graphs a
# user edits (or asks the copilot to change) instead of starting from a blank
# canvas. Each is a plain Flow dict -- validated by the same save path as any
# hand-built graph when the user saves it.
_FLOW_GALLERY: tuple[dict, ...] = (
    {
        "id": "", "name": "Triage inbound issues", "start": "n0",
        "description": "Summarize a new issue, escalate urgent ones, ask before posting.",
        "nodes": [
            {"id": "n0", "kind": "agent", "brief": "Summarize this issue and rate its urgency high/normal: {{title}} — {{body}}", "output": "summary", "next": "n1"},
            {"id": "n1", "kind": "branch", "condition": "urgency == high", "if_true": "n2", "if_false": "n3"},
            {"id": "n2", "kind": "approval", "prompt": "Escalate this to the on-call channel?", "next": "n3"},
            {"id": "n3", "kind": "action", "tool": "slack_bot", "params": {"text": "{{summary}}"}},
        ],
    },
    {
        "id": "", "name": "Daily digest", "start": "n0",
        "description": "On a schedule, gather updates and email a one-paragraph digest.",
        "schedule": "0 9 * * *",
        "nodes": [
            {"id": "n0", "kind": "agent", "brief": "Gather yesterday's notable updates and write a one-paragraph digest.", "output": "digest", "next": "n1"},
            {"id": "n1", "kind": "action", "tool": "email_tool", "params": {"body": "{{digest}}"}},
        ],
    },
    {
        "id": "", "name": "Process a list with review", "start": "n0",
        "description": "Loop items concurrently, then a human signs off on the batch.",
        "nodes": [
            {"id": "n0", "kind": "foreach", "items": "rows", "var": "row", "concurrent": True, "output": "results", "next": "n1",
             "body": {"id": "", "name": "per row", "start": "b0",
                      "nodes": [{"id": "b0", "kind": "agent", "brief": "Handle {{row}}", "output": "r"}]}},
            {"id": "n1", "kind": "approval", "prompt": "Batch processed — publish the results?", "next": "n2"},
            {"id": "n2", "kind": "agent", "brief": "Publish the approved results: {{results}}"},
        ],
    },
    {
        "id": "", "name": "Wait for a callback", "start": "n0",
        "description": "Kick off work, wait for an external system to call back, then finish.",
        "nodes": [
            {"id": "n0", "kind": "agent", "brief": "Submit the request described in {{request}}", "output": "submitted", "next": "n1"},
            {"id": "n1", "kind": "wait_event", "prompt": "Waiting for the provider callback", "next": "n2"},
            {"id": "n2", "kind": "agent", "brief": "Reconcile the callback payload {{payload}} with {{submitted}}", "output": "done"},
        ],
    },
)


@router.get("/flows/gallery")
async def flow_gallery_endpoint(request: Request) -> dict:
    """Starter flow graphs for the designer's gallery. Declared before
    ``/flows/{flow_id}`` so the literal path isn't captured as a flow id."""
    require_permission(request, "operate")
    _require_flows()
    return {"flows": [dict(f) for f in _FLOW_GALLERY]}


@router.get("/flows/analytics")
async def flow_analytics_endpoint(request: Request, limit: int = 500) -> dict:
    """Per-flow run analytics over recent runs: volume, success rate, failure
    count, duration p50/p95, and the top recent error messages -- the aggregate
    view a single run page can't give. Owner-scoped. Declared before
    ``/flows/{flow_id}`` so the literal path isn't captured as a flow id."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import analytics
    owner = goal_owner_filter(request)
    # The aggregation reads + parses every run file in the window: keep that
    # off the event loop.
    return await run_in_threadpool(
        analytics.aggregate, owner=owner, limit=max(1, min(2000, limit)))


@router.get("/flows/insights")
async def flow_insights_endpoint(request: Request) -> dict:
    """Proactive automation intelligence across all flows: pending self-rewrite
    proposals and the measured before/after impact of changes already applied --
    so the automations page can surface 'N suggestions to improve your flows'
    without a human opening each one. Read-only. Declared before ``/flows/{flow_id}``
    so the literal ``insights`` path isn't captured as a flow id."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import evolution_log, evolve, store
    flows_out = []
    proposal_count = 0
    for f in store.list_flows():
        proposals = [p.to_dict() for p in evolve.maybe_propose(f)]
        changes = []
        for node_id in f.nodes:
            last = evolution_log.last_apply(f.id, node_id)
            if not last:
                continue
            changes.append({
                "node_id": node_id, "to_kind": last.get("to_kind"),
                "source": last.get("source"),
                "impact": evolve.measure(f.id, node_id, float(last.get("ts") or 0.0))})
        if proposals or changes:
            flows_out.append({"flow_id": f.id, "name": f.name,
                              "proposals": proposals, "changes": changes})
        proposal_count += len(proposals)
    return {"flows": flows_out, "proposal_count": proposal_count}


@router.get("/flows/{flow_id}")
async def get_flow_endpoint(request: Request, flow_id: str) -> dict:
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    _assert_flow_access(request, f)
    return f.to_dict()


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow_endpoint(request: Request, flow_id: str) -> None:
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    _assert_flow_access(request, f)
    store.delete_flow(flow_id)


@router.post("/flows/{flow_id}/run")
async def run_flow_endpoint(request: Request, flow_id: str, payload: FlowRunIn) -> dict:
    """Start a flow run as a durable job (agent nodes become goals, action nodes
    tool calls). Returns the run id to poll; the run may pause on an approval."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    from maverick.flow.ir import coerce_inputs
    flow = store.load_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="no such flow")
    _assert_flow_access(request, flow)
    owner = _supervisor(request)
    # Validate/coerce the payload against the flow's declared inputs (a no-op when
    # the flow declares none): reject a missing required input or a bad number/date
    # up front, and coerce number/bool/date so a node's condition sees typed values.
    data, input_errs = coerce_inputs(flow, dict(payload.data or {}))
    if input_errs:
        raise HTTPException(status_code=400, detail="; ".join(input_errs))
    # Idempotency: a caller-supplied key dedups a repeat submit (double-click, retry)
    # to the same run instead of firing a second one.
    idem = (payload.idempotency_key or "").strip()
    if idem and not payload.dry_run:
        existing = store.find_run_by_idem(flow_id, idem)
        if existing is not None:
            return {"run_id": existing.run_id, "status": existing.status,
                    "dry_run": False, "deduplicated": True}
    run_id = store.new_run_id()
    store.save_run(store.FlowRun(run_id=run_id, flow_id=flow_id, status="queued",
                                 owner=owner, data=dict(data), input_data=dict(data),
                                 idem_key=idem))
    from maverick_dashboard import automation_queue as aq
    aq.queue().enqueue(aq.FLOW_RUN_KIND, {
        "flow_id": flow_id, "run_id": run_id, "data": data, "owner": owner,
        "dry_run": bool(payload.dry_run)})
    return {"run_id": run_id, "status": "queued", "dry_run": bool(payload.dry_run)}


@router.get("/flows/{flow_id}/proposals")
async def flow_proposals_endpoint(request: Request, flow_id: str) -> dict:
    """Self-rewrite proposals for a flow: node-type swaps its grounded per-node
    outcomes justify (harden a reliable agent to an action, soften a flaky action
    to an agent). Proposals only -- never auto-applied."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import evolve, store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    return {"proposals": [p.to_dict() for p in evolve.maybe_propose(f)]}


@router.get("/flows/{flow_id}/signals")
async def flow_signals_endpoint(request: Request, flow_id: str) -> dict:
    """The grounded learning signals a flow has accumulated, for display:
    per-node success rate (work nodes), human accept-rate (approval nodes), and
    trigger reliability (the reserved ``__trigger__`` outcome). This is what the
    loop CAPTURED made visible -- "which gates humans keep rejecting", "which
    triggers produce runs that succeed" -- so a builder can act on it."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import execution, node_outcomes, store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    stats = node_outcomes.stats(flow_id)
    trigger = stats.get(execution.TRIGGER_NODE)
    nodes = {}
    for nid, s in stats.items():
        if nid == execution.TRIGGER_NODE:
            continue
        node = f.nodes.get(nid)
        nodes[nid] = {"n": s["n"], "mean": s["mean"], "kind": s.get("kind"),
                      "label": (node.label if node else "") or nid}
    return {"nodes": nodes,
            "trigger": ({"n": trigger["n"], "mean": trigger["mean"]} if trigger else None)}


@router.post("/flows/{flow_id}/apply")
async def apply_flow_proposal_endpoint(request: Request, flow_id: str,
                                       payload: FlowApplyIn) -> dict:
    """Apply a self-rewrite: swap a node between agent and action, producing a new
    flow version. This is the step that lets a flow *change itself* -- the applied
    definition is validated before it is saved, the prior version is retained (so
    it can be rolled back), and the apply is logged so its effect can be measured."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import evolution_log, evolve, store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    node = f.nodes.get(payload.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="no such node")
    if node.kind == payload.to_kind:
        raise HTTPException(status_code=400,
                            detail=f"node is already a {payload.to_kind}")
    from_kind = node.kind
    try:
        new_flow = evolve.apply_proposal(f, payload.node_id, payload.to_kind,
                                         tool=payload.tool, brief=payload.brief)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    errs = new_flow.validate()
    if errs:
        raise HTTPException(status_code=400,
                            detail="resulting flow invalid: " + "; ".join(errs[:5]))
    saved = store.save_flow(new_flow)
    evolution_log.record_apply(flow_id, payload.node_id, from_kind,
                               payload.to_kind, saved.version, source="manual")
    return {"id": flow_id, "version": saved.version, "node_id": payload.node_id,
            "from_kind": from_kind, "to_kind": payload.to_kind}


@router.get("/flows/{flow_id}/versions")
async def flow_versions_endpoint(request: Request, flow_id: str) -> dict:
    """Every stored version of a flow (newest first), each with a per-kind node
    count -- so the designer can show history and offer a rollback."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    if store.load_flow(flow_id) is None:
        raise HTTPException(status_code=404, detail="no such flow")

    def _kinds(f) -> dict:
        c: dict[str, int] = {}
        for n in f.nodes.values():
            c[n.kind] = c.get(n.kind, 0) + 1
        return c
    versions = store.list_versions(flow_id)
    versions.reverse()  # newest first
    return {"versions": [{"version": v.version, "name": v.name,
                          "nodes": len(v.nodes), "kinds": _kinds(v)} for v in versions]}


@router.post("/flows/{flow_id}/rollback")
async def rollback_flow_endpoint(request: Request, flow_id: str,
                                 payload: FlowRollbackIn) -> dict:
    """Roll a flow back to a prior version (default: the immediately previous),
    non-destructively -- the restore is itself a new version."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import store
    if store.load_flow(flow_id) is None:
        raise HTTPException(status_code=404, detail="no such flow")
    restored = store.rollback_flow(flow_id, payload.version)
    if restored is None:
        raise HTTPException(status_code=404, detail="no such version to roll back to")
    return {"id": flow_id, "version": restored.version}


@router.get("/flows/{flow_id}/nodes/{node_id}/impact")
async def flow_node_impact_endpoint(request: Request, flow_id: str,
                                    node_id: str) -> dict:
    """Whether a node was rewritten and, if so, how its grounded outcomes compare
    before vs. after the change -- the proof that a self-rewrite actually helped."""
    require_permission(request, "operate")
    _require_flows()
    from maverick.flow import evolution_log, evolve, store
    f = store.load_flow(flow_id)
    if f is None:
        raise HTTPException(status_code=404, detail="no such flow")
    if node_id not in f.nodes:
        raise HTTPException(status_code=404, detail="no such node")
    last = evolution_log.last_apply(flow_id, node_id)
    if last is None:
        return {"node_id": node_id, "changed": False, "applied": None, "impact": None}
    impact = evolve.measure(flow_id, node_id, float(last.get("ts") or 0.0))
    return {"node_id": node_id, "changed": True,
            "applied": {"from_kind": last.get("from_kind"), "to_kind": last.get("to_kind"),
                        "version": last.get("version"), "ts": last.get("ts"),
                        "source": last.get("source")},
            "impact": impact}


@router.get("/skills", response_model=list[SkillOut])
async def list_installed_skills() -> list[SkillOut]:
    from maverick.skills import load_skills
    return [
        SkillOut(name=s.name, triggers=s.triggers, tools_needed=s.tools_needed)
        for s in load_skills()
    ]


@router.get("/flywheel")
async def get_flywheel_state() -> dict:
    """What the Cognitive Data Engine has learned from the workforce's own outcomes.

    A read-only window into the moat: the **guardrails** it has mined (actions it
    causally shows lower outcomes, each with the effect that justifies it -- and
    auto-dropped when the harm is gone) and the **habits** it has consolidated
    (causally-beneficial actions, with a reinforcement strength). Empty until the
    flywheel has turned. Read-only; never mutates."""
    out: dict = {"guardrails": [], "habits": [], "signal": {}}
    try:
        from maverick.negative_knowledge import shared as _guardrails
        out["guardrails"] = [g.to_dict() for g in _guardrails().all()]
    except Exception:  # pragma: no cover -- observability never errors the API
        pass
    try:
        from maverick.procedural_memory import shared as _memory
        out["habits"] = [m.to_dict() for m in _memory().recall(top_k=20)]
    except Exception:  # pragma: no cover
        pass
    # The grounded signal accumulating under the moat: how many real outcomes
    # (failures, sign-offs, feedback, system-of-record results) and human verdicts
    # have landed. Makes "the workforce is learning from reality" a live number.
    try:
        from maverick import consequence

        from maverick_dashboard import goal_feedback_store
        out["signal"] = {
            "grounded_outcomes": consequence.count(),
            "human_feedback": goal_feedback_store.count(),
        }
    except Exception:  # pragma: no cover
        pass
    return out


@router.get("/codec")
async def get_codec_telemetry() -> dict:
    """What the token-aware emergent codec is saving on the LIVE coordination stream.

    Confirms the bench numbers against production: as the swarm runs, the blackboard
    measures (never applies) what the codec would compress each rendered coordination
    block to -- byte savings always, token savings when a tokenizer is registered in
    this process. In-process counters, so this reflects the runtime hosting the
    agents. Zeroed until ``[emergent_codec] enable`` is on and a codebook is learned.
    Read-only; never mutates."""
    try:
        from maverick.codec_telemetry import snapshot
        return snapshot().to_dict()
    except Exception:  # pragma: no cover -- observability never errors the API
        return {"n_blocks": 0, "tokens_measured": False}


def _require_skill_install_opt_in() -> None:
    if os.environ.get("MAVERICK_ALLOW_SKILL_INSTALL", "").lower() not in {"1", "true", "yes"}:
        raise HTTPException(
            status_code=403,
            detail=(
                "Installing skills from the dashboard is disabled on this "
                "server — an administrator can opt in (set "
                "MAVERICK_ALLOW_SKILL_INSTALL=1, or run "
                "`maverick skill install` on the host)."
            ),
        )


@router.post("/skills", response_model=SkillOut, status_code=201)
async def install_skill_endpoint(request: Request, payload: SkillInstallIn) -> SkillOut:
    """Install a skill from a URL or ``gh:org/repo[:path]``.

    Skill install runs untrusted code at the next agent invocation. The
    endpoint is gated behind ``MAVERICK_ALLOW_SKILL_INSTALL=1`` so a
    compromised dashboard token can't be turned into one-shot RCE; an
    operator opting in is taking explicit ownership of the supply
    chain. CLI ``maverick skill install`` remains available without
    the flag because it requires shell access on the host.

    RBAC: this is a control-plane change to the code the agent loads, so it
    requires ``admin`` -- matching ``/plugins/install``. The opt-in flag is
    process-wide, not per-user, so it can't stand in for a role check (a
    view-only principal must not install/replace agent code).
    """
    require_permission(request, "admin")
    _require_skill_install_opt_in()
    from maverick.skills import install_skill
    try:
        s = install_skill(payload.source, trusted_local=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SkillOut(name=s.name, triggers=s.triggers, tools_needed=s.tools_needed)


@router.post("/skills/create", response_model=SkillOut, status_code=201)
async def create_skill_endpoint(request: Request, payload: SkillCreateIn) -> SkillOut:
    """Author a skill from the dashboard form (name / triggers / tools /
    instructions) and install it.

    Lower risk than installing a remote source -- the content is the body the
    author typed, not fetched code -- but it still lands in agent prompts and is
    secret/shield-scanned, so it shares the same ``MAVERICK_ALLOW_SKILL_INSTALL``
    opt-in as install. 422 on invalid input (no trigger, empty body, ...).
    Requires ``admin`` (installs agent-loaded code), like install."""
    require_permission(request, "admin")
    _require_skill_install_opt_in()
    from maverick.skills import create_skill
    try:
        s = create_skill(payload.name, payload.instructions,
                         triggers=payload.triggers, tools_needed=payload.tools_needed)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return SkillOut(name=s.name, triggers=s.triggers, tools_needed=s.tools_needed)




@router.get("/diag/tail-latency")
async def diag_tail_latency(ratio: float = 3.0, min_count: int = 20) -> dict:
    """Tools with a fat latency tail (p99/p50 ≥ ratio) — the ones worth hunting.

    Reads this serving process's in-memory per-tool latency samples, so it's
    meaningful on a long-lived dashboard/worker, not a fresh CLI invocation."""
    from maverick.tail_latency import hunt
    return {"flagged": hunt(ratio_threshold=ratio, min_count=min_count)}


@router.get("/marketplace/stats")
async def marketplace_stats() -> dict:
    """Aggregate stats over the local ratings ledger (total / average / 1–5★
    distribution / per-kind / top-rated). Self-host-first: the operator's own
    ratings, the JSON face of the marketplace stats view."""
    from maverick.marketplace.ratings import RatingsLedger
    from maverick.marketplace.stats import summarize
    return summarize(RatingsLedger())


@router.get("/templates")
async def templates_catalog() -> dict:
    """The goal-template catalog with the operator's own ratings — the JSON
    face of the /templates marketplace page."""
    from maverick_dashboard.app import template_market_entries
    return {"templates": template_market_entries()}


@router.get("/templates/suggested")
async def templates_suggested(request: Request, k: int = 5) -> dict:
    """Personalized starter templates: the catalog ranked for THIS user from
    their goal-title history (pure scorer, no LLM — ``maverick.starter_templates``).
    Owner-scoped history: an authenticated non-admin is ranked on their own
    goals only."""
    from maverick.starter_templates import suggest
    k = max(1, min(int(k or 5), 20))
    return {
        "suggested": suggest(_world(), k=k, owner=goal_owner_filter(request)),
    }


def _voice_commands_enabled() -> bool:
    """Dashboard voice-command input. ON by default; off via
    MAVERICK_VOICE_COMMANDS=0 / [voice] dashboard_commands = false."""
    import os as _os
    raw = (_os.environ.get("MAVERICK_VOICE_COMMANDS") or "").strip().lower()
    if raw:
        return raw not in {"0", "false", "no", "off"}
    try:
        from maverick.config import load_config
        return bool((load_config().get("voice") or {}).get("dashboard_commands", True))
    except Exception:  # pragma: no cover -- config never blocks the endpoint
        return True


# Audio container suffixes the STT backends accept; browsers record webm/ogg.
_VOICE_SUFFIXES = {
    ".webm": ".webm", ".ogg": ".ogg", ".oga": ".ogg", ".mp3": ".mp3",
    ".wav": ".wav", ".m4a": ".m4a", ".mp4": ".mp4", ".flac": ".flac",
}
_VOICE_MAX_BYTES = 25 * 1024 * 1024


@router.post("/voice/transcribe")
async def voice_transcribe(
    request: Request,
    file: UploadFile = File(...),
    language: str = "",
) -> JSONResponse:
    """Transcribe a short voice recording to text (dashboard voice commands).

    The chat composer's mic button records audio in the browser and posts it
    here; the transcript goes back into the goal field so the user can review
    before submitting (speech never starts a goal unreviewed). Reuses the
    kernel STT backends (OpenAI / Groq Whisper, local faster-whisper) via
    ``maverick.tools.voice``; 503 with a setup hint when none is configured,
    which the client treats as "fall back to browser speech recognition".
    """
    if not _voice_commands_enabled():
        raise HTTPException(status_code=404, detail="voice commands are disabled")
    require_permission(request, "operate")

    import os as _os
    import tempfile as _tempfile

    max_bytes = _VOICE_MAX_BYTES
    try:
        max_bytes = int(_os.environ.get("MAVERICK_VOICE_MAX_BYTES", max_bytes))
    except (TypeError, ValueError):
        pass
    data = await file.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=400, detail="empty audio")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"audio too large: {len(data)} bytes (limit {max_bytes})",
        )

    suffix = _VOICE_SUFFIXES.get(
        _os.path.splitext(file.filename or "")[1].lower(), ".webm")
    lang = (language or "").strip()[:8] or None

    from maverick.tools.voice import _run_transcribe
    tmp = _tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        # sandbox=None: the path is our own temp file, not model-supplied.
        args: dict = {"source": tmp.name}
        if lang:
            args["language"] = lang
        text = await run_in_threadpool(_run_transcribe, args, None)
    finally:
        try:
            _os.unlink(tmp.name)
        except OSError:  # pragma: no cover
            pass
    if text.startswith("ERROR:"):
        raise HTTPException(
            status_code=503,
            detail=(
                "no speech-to-text backend available. Set OPENAI_API_KEY or "
                "GROQ_API_KEY, or install faster-whisper "
                "(pip install 'maverick-agent[voice]')."
            ),
        )
    return JSONResponse({"text": text.strip()})


@router.post("/voice/speak")
async def voice_speak(request: Request, payload: SpeakIn) -> Response:
    """Synthesize speech for a reply (dashboard read-aloud).

    Returns the mp3 bytes; the client plays them inline. Reuses the kernel
    TTS backends (OpenAI TTS / ElevenLabs via ``maverick.tools.voice``),
    including the voice-safety redaction pass — a secret must never be
    spoken aloud. 503 with a setup hint when no backend is configured.
    """
    if not _voice_commands_enabled():
        raise HTTPException(status_code=404, detail="voice commands are disabled")
    require_permission(request, "operate")
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    text = text[:4096]
    try:
        from maverick.safety.voice_safety import redact_for_speech
        text, _redactions = redact_for_speech(text)
    except Exception:  # pragma: no cover -- redaction is fail-open by design
        pass

    import os as _os
    import tempfile as _tempfile
    from pathlib import Path as _Path

    from maverick.tools.voice import _tts_elevenlabs, _tts_openai

    def _synthesize() -> bytes | None:
        tmp = _tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        out = _Path(tmp.name)
        try:
            if _tts_openai(text, payload.voice, out) or _tts_elevenlabs(
                    text, payload.voice, out):
                return out.read_bytes()
            return None
        finally:
            try:
                _os.unlink(tmp.name)
            except OSError:  # pragma: no cover
                pass

    audio = await run_in_threadpool(_synthesize)
    if not audio:
        raise HTTPException(
            status_code=503,
            detail=(
                "no text-to-speech backend available. Set OPENAI_API_KEY or "
                "ELEVENLABS_API_KEY."
            ),
        )
    return Response(content=audio, media_type="audio/mpeg")


@router.get("/voice/captions")
async def voice_captions(
    request: Request, source: str = "default", max_chars: int = 160,
) -> StreamingResponse:
    """Live captions (SSE) over the voice transcript seam.

    Streams one ``data: {caption, final, ts}`` frame per transcript segment
    from the named source in ``maverick.live_captions``'s source registry,
    then ``event: end`` when the source is exhausted. Default-off: the
    registry starts empty (no live mic — a deployment registers its ASR
    pipeline; tests register scripted sources), so an unregistered source
    404s.
    """
    principal = caller_principal(request)
    if principal is not None and not is_dashboard_admin(principal):
        raise HTTPException(status_code=404, detail="no such caption source")

    from maverick.live_captions import caption_stream, get_source
    factory = get_source(source)
    if factory is None:
        raise HTTPException(
            status_code=404,
            detail=f"no caption source registered as {source!r}; "
                   "register one via maverick.live_captions.register_source",
        )
    try:
        max_chars = max(16, min(int(max_chars), 500))
    except (TypeError, ValueError):
        max_chars = 160

    # Bound concurrent caption streams and release the slot on disconnect/error,
    # exactly like the goal-events stream. A live caption source never exhausts
    # on its own, so without this an abandoned (or maliciously opened-and-never-
    # read) connection would pin an async task + fd indefinitely, and unlimited
    # such connections would exhaust the event loop. Shares the SSE semaphore.
    sem = _get_sse_semaphore()
    if sem.locked():
        raise HTTPException(
            status_code=503,
            detail="too many concurrent caption streams; retry shortly",
            headers={"Retry-After": "5"},
        )
    await sem.acquire()

    async def _gen():
        try:
            yield ": captions\n\n"
            async for frame in caption_stream(factory(), max_chars=max_chars):
                if await request.is_disconnected():
                    return
                yield f"data: {json.dumps(frame)}\n\n"
            yield "event: end\ndata: {}\n\n"
        except asyncio.CancelledError:
            return
        finally:
            sem.release()

    return StreamingResponse(_gen(), media_type="text/event-stream")


@router.get("/catalog/{kind}")
async def catalog_list(kind: str) -> dict:
    """List federated catalog entries for a kind (skills/plugins/mcp/personas).

    Tolerates an unreachable index by returning an empty list, so a
    fresh install shows "no catalog entries" rather than 500ing.
    """
    from maverick.catalog import VALID_KINDS, load_catalog
    if kind not in VALID_KINDS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown kind {kind!r}; valid: {', '.join(VALID_KINDS)}",
        )
    entries = load_catalog(kind)
    return {"kind": kind, "entries": [e.to_dict() for e in entries]}


@router.post("/catalog/skills/install", response_model=SkillOut, status_code=201)
async def catalog_install_skill(request: Request, payload: CatalogInstallIn) -> SkillOut:
    """Install a catalog skill by name.

    Catalog metadata (source + hash) can come from remote indexes, so
    this endpoint keeps the same operator opt-in gate as free-text skill
    installs. Requires ``admin`` (installs agent-loaded code), like install.
    """
    require_permission(request, "admin")
    _require_skill_install_opt_in()
    from maverick.skills import install_from_catalog
    try:
        s = install_from_catalog(payload.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SkillOut(name=s.name, triggers=s.triggers, tools_needed=s.tools_needed)


@router.delete("/skills/{name}", status_code=204)
async def remove_skill_endpoint(request: Request, name: str) -> None:
    # Removing an installed skill is a control-plane mutation of agent-loaded
    # code; require admin (a view-only principal must not pull skills).
    require_permission(request, "admin")
    from maverick.skills import remove_skill
    if not remove_skill(name):
        raise HTTPException(status_code=404, detail="no such skill")


# ---------- self-learning: learned ledger + generated tools (#427) ----------


def _learned_snapshot(limit: int = 50) -> dict:
    """Read-only view of the self-learning ledger + on-disk generated tools.

    Powers the /learned page and GET /api/v1/learned. Degrades to empty
    lists if the self-learning module / ledger / dir is unavailable, so a
    fresh install (feature never enabled) renders "nothing learned yet"
    instead of 500ing.
    """
    learned: list[dict] = []
    tools: list[str] = []
    try:
        from maverick import self_learning
        # Pass the path explicitly: history()'s default arg is bound at
        # import time, so reading the module global here lets the live
        # LEARNED_PATH (and tests) take effect.
        learned = [
            e.to_dict()
            for e in self_learning.history(
                limit=limit, path=self_learning.LEARNED_PATH,
            )
        ]
        d = self_learning.GENERATED_TOOLS_DIR
        if d.exists():
            tools = sorted(
                p.name for p in d.glob("*.py")
                if not p.name.startswith((".", "_"))
            )
    except Exception as e:  # pragma: no cover -- never block the page
        log.debug("learned snapshot failed: %s", e)
    harness, harness_enabled = _harness_guidance()
    pending = _pending_corpus_items()
    return {
        "learned": learned, "generated_tools": tools,
        "harness": harness, "harness_enabled": harness_enabled,
        "pending_corpus": {k: len(v) for k, v in pending.items()},
        "pending_items": pending,
        "transfer_memory": _transfer_memory(),
    }


def _pending_corpus_items() -> dict[str, list[dict]]:
    """Harvested corpus candidates awaiting operator review, per corpus key --
    so staged ground truth is visible (and resolvable in-page), not
    forgotten. A CORPUS-key fact, kept apart from the learned-guidance
    snapshot: the section must show even when a key has staged cases but no
    learned lines yet (the bootstrap case), and a failure here must not blank
    the guidance list (or vice versa)."""
    try:
        from maverick.self_harness import settings
        from maverick.self_harness_eval import load_pending
        cpath = settings().get("eval_corpus")
        if cpath:
            return {k: v for k, v in load_pending(cpath).items() if v}
    except Exception as e:  # pragma: no cover -- pending items are advisory
        log.debug("pending corpus snapshot failed: %s", e)
    return {}


def _transfer_memory() -> dict:
    """Content-free transfer tried-memory stats (judged pairs + newest ts)
    for the /learned page -- how caught-up the nightly sweep is."""
    try:
        from maverick.self_harness import transfer_memory_stats
        return transfer_memory_stats()
    except Exception as e:  # pragma: no cover -- advisory
        log.debug("transfer memory snapshot failed: %s", e)
        return {"pairs": 0, "last_judged_at": None}


def _harness_guidance() -> tuple[list[dict], bool]:
    """The self-harness per-model operating guidance recalled into prompts.

    Returns ``([{model_id, lines, provenance, conflicts}], enabled)``. This is
    the self-harness loop's most prompt-impacting output, yet the /learned page
    surfaced only the capability ledger -- an operator could see it ONLY via
    `maverick self-harness show`. Read-only; ``list_learned`` reads the store
    regardless of the toggle (so paused guidance is still inspectable), so we
    return the ``enabled`` flag too -- stored guidance is NOT recalled into
    prompts until the feature is on. Kept in its own try so a self-harness
    failure never blanks the capability ledger, and vice versa."""
    try:
        from maverick.self_harness import (
            detect_store_conflicts,
            enabled,
            line_efficacy,
            line_provenance,
            list_canaries,
            list_learned,
        )
        conflicts_by_model: dict[str, list] = {}
        try:
            for m, a, b in detect_store_conflicts():
                conflicts_by_model.setdefault(m, []).append([a, b])
        except Exception:  # pragma: no cover -- conflicts are advisory
            pass

        def _enriched_provenance(m: str) -> list[dict]:
            # Fold the per-line outcome record (efficacy counters) and canary
            # flag into each provenance entry, keyed by (domain, text) so a line
            # that lives in two scopes isn't cross-attributed. Both are advisory
            # reads -- a failure here must not blank the guidance list.
            prov = line_provenance(m)
            try:
                eff = {(r.get("domain"), r["text"]): r for r in line_efficacy(m)}
            except Exception:  # pragma: no cover -- efficacy is advisory
                eff = {}
            try:
                canaries = set(list_canaries(m))
            except Exception:  # pragma: no cover -- canary state is advisory
                canaries = set()
            for rec in prov:
                e = eff.get((rec.get("domain"), rec["text"]))
                if e:
                    rec["success"], rec["failure"] = e["success"], e["failure"]
                    rec["rate"] = e["rate"]
                    rec["recent_success"] = e.get("recent_success")
                    rec["recent_failure"] = e.get("recent_failure")
                    rec["recent_rate"] = e.get("recent_rate")
                rec["canary"] = rec["text"] in canaries
            return prov

        rows = [{"model_id": m, "lines": lines,
                 "provenance": _enriched_provenance(m),
                 "conflicts": conflicts_by_model.get(m, [])}
                for m, lines in sorted(list_learned().items())]
        return rows, bool(enabled())
    except Exception as e:  # pragma: no cover -- never block the page
        log.debug("harness guidance snapshot failed: %s", e)
        return [], False


def _resolve_generated_tool(name: str):
    """Resolve ``name`` to a ``*.py`` file strictly inside GENERATED_TOOLS_DIR.

    Path-safety guard for the removal endpoint: the resolved target must be
    a direct child of the generated-tools dir and end in ``.py``. Rejects
    traversal (``..``), absolute paths, and subdirectory escapes by
    comparing the resolved parent against the resolved dir. Returns the
    Path or raises HTTPException(400).
    """
    from maverick import self_learning
    d = self_learning.GENERATED_TOOLS_DIR
    # A legitimate generated-tool filename is a bare ``<name>.py`` with no
    # separators; reject anything with a path separator or traversal token
    # before touching the filesystem.
    if "/" in name or "\\" in name or name in ("", ".", "..") or not name.endswith(".py"):
        raise HTTPException(status_code=400, detail="invalid generated-tool name")
    target = (d / name).resolve()
    base = d.resolve()
    if target.parent != base:
        raise HTTPException(status_code=400, detail="path outside generated_tools")
    return target


@router.get("/learned")
async def learned_api() -> dict:
    """Learned-capability ledger entries + on-disk generated tool filenames."""
    return _learned_snapshot()


@router.delete("/generated-tools/{name}", status_code=204)
async def remove_generated_tool(request: Request, name: str) -> None:
    """Delete a persisted generated tool so a bad one can be pulled.

    The valuable mutation of #427: removes ~/.maverick/generated_tools/<name>
    without filesystem access. The path is resolved strictly under the
    generated-tools dir (see ``_resolve_generated_tool``) so traversal /
    absolute / out-of-dir names are refused. Auth/same-origin is enforced
    centrally by the dashboard's bearer_auth middleware (DELETE is a
    mutating method, so the same-origin check applies), and RBAC requires
    ``admin`` -- deleting agent-loaded code is not a view-only action.
    """
    require_permission(request, "admin")
    target = _resolve_generated_tool(name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such generated tool")
    try:
        target.unlink()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not remove: {e}") from e


@router.post("/harness-corpus/review")
async def review_harness_corpus(request: Request, payload: dict) -> dict:
    """Resolve staged corpus candidates from the dashboard -- the same
    accept/reject verdicts as ``maverick self-harness corpus review``, so
    staged ground truth can be reviewed where it is displayed. Mutating the
    loop's ground truth is an ``admin`` action (same rung as pulling a
    generated tool); bearer/same-origin is enforced centrally by the
    middleware (POST is a mutating method)."""
    require_permission(request, "admin")
    from maverick import self_harness_eval as ev
    from maverick.self_harness import settings
    corpus_path = settings().get("eval_corpus")
    if not corpus_path:
        raise HTTPException(status_code=409, detail="no eval corpus configured")
    key = str(payload.get("key") or "")
    if not key:
        raise HTTPException(status_code=400, detail="missing corpus key")

    def _indexes(name: str) -> list[int]:
        v = payload.get(name) or []
        if not isinstance(v, list) or not all(
                isinstance(i, int) and not isinstance(i, bool) for i in v):
            raise HTTPException(status_code=400,
                                detail=f"{name} must be a list of 1-based indexes")
        return v

    accept, reject = _indexes("accept"), _indexes("reject")
    if not accept and not reject:
        raise HTTPException(status_code=400, detail="nothing to resolve")
    try:
        res = ev.resolve_pending(corpus_path, key, accept=accept, reject=reject)
    except ValueError as e:
        # Indexes rebased under the caller (another review/harvest landed):
        # a conflict, not a bad request -- the page reloads and re-lists.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"merged": res["merged"], "duplicates": res["duplicates"],
            "rejected": res["rejected"]}


@router.get("/spend")
async def get_spend(request: Request) -> dict:
    """Spend + recent run costs, scoped to the caller's own runs.

    ``goal_owner_filter`` returns the caller's principal (so each user sees
    only their own spend/episodes) or ``None`` for an admin / auth-off caller
    (the deployment-wide view). Previously this returned every user's runs and
    total spend to any authenticated caller.
    """
    owner = goal_owner_filter(request)
    w = _world()
    total = w.total_spend(owner=owner)
    episodes = w.list_episodes(limit=30, owner=owner)
    return {
        "total": total,
        "episodes": [
            {
                "id": e.id, "goal_id": e.goal_id, "started_at": e.started_at,
                "ended_at": e.ended_at, "outcome": e.outcome,
                "cost_dollars": e.cost_dollars,
                "input_tokens": e.input_tokens,
                "output_tokens": e.output_tokens,
                "tool_calls": e.tool_calls,
            }
            for e in episodes
        ],
    }


_SECURITY_REGISTER_CACHE_TTL_SECONDS = 60.0
_SECURITY_REGISTER_HUNT_DAYS = 7
_security_register_cache: tuple[float, dict] | None = None
_security_register_cache_lock = threading.Lock()


def _security_hunt_window() -> tuple[str, str]:
    today = _dt.datetime.now(_dt.timezone.utc).date()
    since = today - _dt.timedelta(days=_SECURITY_REGISTER_HUNT_DAYS - 1)
    return since.isoformat(), today.isoformat()


def _security_register_snapshot_uncached() -> dict:
    controls: list[dict] = []
    try:
        from maverick.compliance import compliance_report
        controls = [
            {"control": c.control, "status": c.status, "regulation": c.regulation,
             "detail": c.detail, "framework": c.framework}
            for c in compliance_report()
        ]
    except Exception:
        log.warning("security register: compliance probe failed", exc_info=True)

    threat: dict = {"risk": "clear", "events_scanned": 0, "findings": []}
    breaches: list[dict] = []
    try:
        from maverick.threat_hunt import hunt
        since, until = _security_hunt_window()
        r = hunt(all_days=False, since=since, until=until)
        findings = [
            {"kind": f.kind, "title": f.title, "severity": f.severity,
             "count": f.count, "agents": f.agents}
            for f in r.findings
        ]
        threat = {
            "risk": r.risk_rating, "events_scanned": r.events_scanned,
            "window": {"since": since, "until": until},
            "findings": findings,
        }
        breaches = [
            {"kind": f.kind, "title": f.title, "severity": f.severity, "count": f.count}
            for f in r.findings
        ]
    except Exception:
        log.warning("security register: threat hunt failed", exc_info=True)

    remediation: dict = {"auto_fix_enabled": False, "gaps": [], "breaches": breaches}
    try:
        from maverick.remediation import plan
        p = plan(include_breaches=False)
        remediation = {
            "auto_fix_enabled": p.auto_fix_enabled,
            "gaps": [
                {"control": g.control, "title": g.title, "auto": g.auto,
                 "rationale": g.rationale}
                for g in p.gaps
            ],
            "breaches": breaches,
        }
    except Exception:
        log.warning("security register: remediation plan failed", exc_info=True)

    return {"controls": controls, "threat_hunt": threat, "remediation": remediation}


def _security_register_snapshot() -> dict:
    global _security_register_cache
    with _security_register_cache_lock:
        now = time.monotonic()
        if (
            _security_register_cache is not None
            and now - _security_register_cache[0] < _SECURITY_REGISTER_CACHE_TTL_SECONDS
        ):
            return _security_register_cache[1]
        snapshot = _security_register_snapshot_uncached()
        _security_register_cache = (time.monotonic(), snapshot)
        return snapshot


@router.get("/security")
async def security_register() -> dict:
    """The privacy/security agent team's register, read-only and fail-soft.

    The audit hunt is bounded to a recent window, cached briefly, and run off
    the event loop so cross-site or repeated GETs cannot force unbounded
    synchronous audit-log scans on every request.
    """
    return await run_in_threadpool(_security_register_snapshot)


# ---------- council pass: control surface ----------



@router.get("/halt")
async def halt_status() -> dict:
    """Is the killswitch armed?

    Council round-2 capabilities-seat fix: round-1 only surfaced the
    file path. Now also returns the reason string (from the file body
    when the halt was set via POST) and the file's mtime as ``armed_at``
    so the UI can show "halted 3m ago for: <reason>".
    """
    from maverick.killswitch import _halt_file_path, is_active
    p = _halt_file_path()
    out: dict = {
        "active": is_active(),
        "file": str(p),
        "file_present": p.exists(),
        "reason": None,
        "armed_at": None,
    }
    if p.exists():
        try:
            body = p.read_text(errors="replace").strip()
            out["reason"] = body or None
        except OSError:
            pass
        try:
            out["armed_at"] = p.stat().st_mtime
        except OSError:
            pass
    # Reflect the cluster-wide halt too: on a shared backend it may be armed by
    # another replica with no local file here. Postgres only; best-effort.
    shared = None
    if _shared_halt_backend():
        try:
            shared = _world().active_halt()
        except Exception:
            shared = None
    if shared:
        out["active"] = True
        out["cluster_halt"] = True
        out["reason"] = out["reason"] or (shared.get("reason") or None)
        out["armed_at"] = out["armed_at"] or shared.get("armed_at")
        out["armed_by"] = shared.get("armed_by") or None
    return out


@router.post("/halt", status_code=204)
async def halt_set(request: Request, payload: HaltIn) -> None:
    """Arm the killswitch by touching ~/.maverick/HALT.

    Honoured by every agent at the next tool-call boundary. Use the
    DELETE endpoint or ``rm ~/.maverick/HALT`` to clear.
    """
    _require_halt_permission(request)
    from maverick.killswitch import _halt_file_path
    reason = payload.reason or "manual via dashboard"
    p = _halt_file_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(reason + "\n")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"cannot write halt file: {e}") from e
    # Also arm the cluster-wide halt in the shared store so the stop propagates
    # to every replica (the file only halts this one). Only on a shared backend
    # (Postgres): on single-host SQLite the file halt is the whole mechanism and
    # the killswitch doesn't consult the shared row. Best-effort -- a shared-store
    # error must not fail the local arm, which already took effect above.
    if _shared_halt_backend():
        try:
            _world().arm_halt(reason, source="dashboard",
                              armed_by=caller_principal(request) or "")
        except Exception:
            log.warning("cluster-wide halt arm failed (local file halt still set)",
                        exc_info=True)


@router.delete("/halt", status_code=204)
async def halt_clear(request: Request) -> None:
    """Clear the killswitch (delete ~/.maverick/HALT)."""
    _require_halt_permission(request)
    from maverick.killswitch import _halt_file_path, clear
    p = _halt_file_path()
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"cannot remove halt file: {e}") from e
    clear()
    # Clear the cluster-wide halt too (Postgres only; see halt_set). Best-effort
    # so a shared-store error doesn't block clearing the local halt.
    if _shared_halt_backend():
        try:
            _world().disarm_halt()
        except Exception:
            log.warning("cluster-wide halt clear failed", exc_info=True)


@router.post("/goals/{goal_id}/cancel", status_code=204)
async def cancel_goal(request: Request, goal_id: int) -> None:
    """Mark a goal as cancelled.

    The agent loop checks status at each tool-call boundary; setting
    'cancelled' here causes the next check to short-circuit the run.
    Already-done goals are a no-op.
    """
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    if g.status in ("done", "cancelled", "failed"):
        return
    w.set_goal_status(goal_id, "cancelled", result="cancelled via dashboard")
    # A human killing an in-progress run is real negative ground truth (the run
    # wasn't worth finishing) -- feed it to the learning loop, best-effort.
    _ground_outcome(w, goal_id, 0.0, kind="cancel")


@router.get("/goals/{goal_id}/open_questions")
async def goal_open_questions(request: Request, goal_id: int) -> dict:
    """List unanswered questions an agent has parked for this goal."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    qs = w.open_questions(goal_id=goal_id)
    return {
        "open_questions": [
            {"id": q.id, "question": q.question, "asked_at": q.asked_at}
            for q in qs
        ],
    }


def _goal_gate(domain: str) -> str | None:
    """The sign-off gate the goal's pack declares ('review'/'approval'), or
    ``None`` (no gate, or the factory layer is unavailable)."""
    if not domain:
        return None
    try:
        from maverick.domain import available_domains
        prof = available_domains().get(domain)
        return prof.output.gate if prof else None
    except Exception:  # pragma: no cover -- factory layer unavailable
        return None


def _goal_shape(domain: str) -> str:
    """The render shape the goal's pack declares, defaulting to 'prose'."""
    if not domain:
        return "prose"
    try:
        from maverick.domain import available_domains
        prof = available_domains().get(domain)
        return prof.output.shape if prof else "prose"
    except Exception:  # pragma: no cover -- factory layer unavailable
        return "prose"


@router.get("/goals/{goal_id}/signoff")
async def get_signoff(request: Request, goal_id: int) -> dict:
    """The current sign-off on a goal's deliverable, plus the gate its pack
    declares (so the UI knows whether a sign-off is even called for)."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    return {"gate": _goal_gate(g.domain), "signoff": w.signoff_for(goal_id)}


@router.post("/goals/{goal_id}/signoff")
async def post_signoff(request: Request, goal_id: int, payload: SignoffIn) -> dict:
    """Record a human's certify/reject decision on a finished deliverable -- the
    governed hand-off step (agents draft; humans certify). 400 if the pack
    declares no gate (there is nothing to sign off)."""
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    if _goal_gate(g.domain) is None:
        raise HTTPException(status_code=400, detail="this deliverable has no sign-off gate")
    who = _supervisor(request)
    w.record_signoff(goal_id, payload.decision, decided_by=who, note=payload.note)
    # Feed the human's certify/reject verdict back into the learning loop as a
    # GROUNDED outcome: a person judging the actual deliverable is the cleanest
    # ground-truth signal in the system, and it lands as exactly the
    # (goal_id, episode_id, value) tuple the Consequence Engine / data-engine
    # already consume. Both verdicts are signal -- an approval is a 1.0 reward,
    # a rejection a 0.0. Best-effort and gated: no-op unless [consequence] is on.
    _record_signoff_outcome(w, goal_id, payload.decision)
    if payload.decision == "approved":
        _handoff_approved_deliverable(g, who)
    return {"gate": _goal_gate(g.domain), "signoff": w.signoff_for(goal_id)}


def _ground_outcome(w, goal_id: int, value: float, *, kind: str) -> None:
    """Record a real human/world outcome for the goal's latest episode as
    grounded reward the learning flywheel prefers over its proxy. Delegates to
    the shared core helper (no-op unless ``[consequence]`` is enabled; never
    raises -- grounding is best-effort and must not fail the request)."""
    from maverick import consequence
    consequence.record_self_outcome(w, goal_id, value, kind=kind)


def _record_signoff_outcome(w, goal_id: int, decision: str) -> None:
    """A deliverable sign-off -> grounded outcome (approved 1.0 / rejected 0.0)."""
    _ground_outcome(w, goal_id, 1.0 if decision == "approved" else 0.0, kind="signoff")


@router.get("/goals/{goal_id}/feedback")
async def get_feedback(request: Request, goal_id: int) -> dict:
    """The current human thumbs-up/down on a goal's result (or ``null``)."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    from maverick_dashboard import goal_feedback_store
    return {"feedback": goal_feedback_store.latest_for_goal(goal_id)}


@router.post("/goals/{goal_id}/feedback")
async def post_feedback(request: Request, goal_id: int, payload: FeedbackIn) -> dict:
    """Record a human thumbs-up/down on a goal's result.

    The always-available verdict: unlike the sign-off gate (which only exists when
    a pack declares one), any finished result can be rated here, and the rating is
    fed back into the learning loop as a GROUNDED outcome -- an up is a 1.0 reward,
    a down a 0.0 -- exactly the ``(goal_id, episode_id, value)`` tuple the
    Consequence Engine / flywheel consume. Persisted for the UI + the "what your
    workforce learned" count. Best-effort grounding, gated on ``[consequence]``."""
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    value = 1.0 if payload.rating == "up" else 0.0
    who = _supervisor(request)
    from maverick_dashboard import goal_feedback_store
    row = goal_feedback_store.record(goal_id, g.owner or "", payload.rating, value=value,
                                     note=payload.note or "", by=who)
    _ground_outcome(w, goal_id, value, kind="feedback")
    return {"feedback": row}


@router.post("/goals/{goal_id}/deliverable/edit")
async def edit_deliverable(request: Request, goal_id: int,
                           payload: DeliverableEditIn) -> dict:
    """Save a human's revised deliverable and ground the edit as a learning signal.

    How much the human changed the agent's draft is graded ground truth: a
    verbatim keep is a strong positive (~1.0), a heavy rewrite a graded negative.
    The similarity becomes the grounded outcome (kind ``edit``), and the revised
    text replaces the goal's result (the human's version is now canonical).
    Best-effort grounding, gated on ``[consequence]``."""
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    import difflib
    original = g.result or ""
    revised = payload.text or ""
    w.set_goal_status(goal_id, g.status, result=revised)   # the edit is the deliverable now
    if not original:
        # Nothing to grade against: the agent produced no draft to keep or
        # rewrite. Grounding this as 0.0 would punish it for text it never wrote,
        # so we save the human's text but ground nothing.
        return {"similarity": None}
    similarity = difflib.SequenceMatcher(None, original, revised).ratio()
    _ground_outcome(w, goal_id, similarity, kind="edit")
    return {"similarity": round(similarity, 3)}


def _handoff_approved_deliverable(g, decided_by: str) -> None:
    """Push an approved deliverable to the configured system-of-record endpoint.

    Best-effort and never raises: the sign-off is already recorded, so a missing
    or failing hand-off endpoint must not fail the request. A no-op unless
    ``[deliverables] handoff_webhook`` is configured."""
    try:
        from maverick import webhooks
        from maverick.deliverable import render_deliverable
        rendered = render_deliverable(_goal_shape(g.domain), g.result)
        table = ({"headers": rendered.table.headers, "rows": rendered.table.rows}
                 if rendered.table else None)
        # Keep the outbound payload aligned with the reviewed artifact.
        # Structured table deliverables render only the parsed cells in the
        # dashboard, so do not include surrounding raw model text that the
        # reviewer did not approve. Prose fallbacks carry the rendered prose.
        webhooks.fire_deliverable_handoff({
            "goal_id": g.id,
            "domain": g.domain,
            "title": g.title,
            "shape": rendered.shape,
            "decided_by": decided_by,
            "table": table,
            "result": rendered.prose,
        })
    except Exception:  # pragma: no cover -- hand-off is best-effort
        log.warning("deliverable hand-off failed for goal %s", getattr(g, "id", "?"))


@router.get("/goals/{goal_id}/deliverable.csv")
async def export_deliverable_csv(request: Request, goal_id: int) -> Response:
    """Export a goal's deliverable as CSV -- the mechanical hand-off so an
    approved forecast/table can be loaded into a downstream system instead of
    re-keyed. 404 when the result carries no tabular deliverable."""
    import csv
    import io as _io

    from maverick.deliverable import render_deliverable
    from maverick.tools.spreadsheet import _neutralize_formula
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    rendered = render_deliverable(_goal_shape(g.domain), g.result)
    if rendered.table is None:
        raise HTTPException(status_code=404, detail="no tabular deliverable to export")
    # Gated deliverables must carry an approved human sign-off before they can be
    # exported (checked after the no-table 404 so an empty deliverable still 404s).
    gate = _goal_gate(g.domain)
    if gate is not None:
        signoff = w.signoff_for(goal_id)
        if signoff is None or signoff.get("decision") != "approved":
            raise HTTPException(
                status_code=403,
                detail=f"{gate} sign-off is required before exporting this deliverable",
            )
    buf = _io.StringIO()
    writer = csv.writer(buf)
    # Neutralize spreadsheet formulas so an opened CSV can't execute injected
    # formula cells (=, +, -, @) in Excel/Sheets.
    writer.writerow([_neutralize_formula(cell) for cell in rendered.table.headers])
    writer.writerows(
        [_neutralize_formula(cell) for cell in row] for row in rendered.table.rows
    )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="deliverable-{goal_id}.csv"'},
    )


@router.get("/goals/{goal_id}/artifacts")
async def list_goal_artifacts(request: Request, goal_id: int) -> dict:
    """The goal's latest artifacts -- the newest version of each titled output
    (markdown / code / table / text) it produced, with a per-title version
    count. Goal-access gated; content is decrypted for the owner."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    return {"artifacts": w.latest_artifacts(goal_id)}


@router.get("/goals/{goal_id}/artifacts/history")
async def goal_artifact_history(request: Request, goal_id: int, title: str) -> dict:
    """Every version of one titled artifact, oldest -> newest, each with a unified
    diff against the previous version. Powers the version/diff viewer."""
    import difflib
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    versions = [a for a in w.artifacts_for_goal(goal_id) if a["title"] == title]
    out: list[dict] = []
    prev: str | None = None
    for a in versions:  # ascending by version
        diff = ""
        if prev is not None:
            diff = "\n".join(difflib.unified_diff(
                prev.splitlines(), (a["content"] or "").splitlines(),
                fromfile=f"v{a['version'] - 1}", tofile=f"v{a['version']}", lineterm=""))
        out.append({"version": a["version"], "created_at": a["created_at"],
                    "content": a["content"], "diff": diff})
        prev = a["content"] or ""
    return {"title": title, "versions": out}


# Default share-link lifetime (7 days). Operators revoke early from the goal page.
_SHARE_TTL_SECONDS = 7 * 24 * 3600


@router.post("/goals/{goal_id}/share", status_code=201)
async def create_goal_share(request: Request, goal_id: int) -> dict:
    """Mint a read-only share link to a goal's deliverable (default 7-day
    expiry). Operator role + goal access. The clear token is returned ONCE --
    only its hash is stored, so it can't be re-fetched later, only revoked."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    require_permission(request, "operate")
    link_id, token = w.create_share_link(
        goal_id, created_by=caller_principal(request) or "", ttl_seconds=_SHARE_TTL_SECONDS)
    url = str(request.base_url).rstrip("/") + "/share/" + token
    return {"id": link_id, "url": url}


@router.post("/goals/{goal_id}/share/{link_id}/revoke")
async def revoke_goal_share(request: Request, goal_id: int, link_id: int) -> dict:
    """Revoke a share link. Operator role + goal access; the revoke is scoped to
    this goal so a caller can't revoke another goal's link by id."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    require_permission(request, "operate")
    return {"ok": w.revoke_share_link(link_id, goal_id=goal_id)}


@router.get("/plugins")
async def list_plugins() -> dict:
    """Discovered + allow-listed plugins, broken out by kind."""
    try:
        from maverick.plugins import _allowed_plugin_names, _entry_points
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"plugin discovery failed: {e}") from e
    allow = _allowed_plugin_names()
    out: dict[str, list[dict]] = {
        "tools": [], "channels": [], "skills": [], "personas": [],
    }
    for kind, group in (
        ("tools",    "maverick.tools"),
        ("channels", "maverick.channels"),
        ("skills",   "maverick.skills"),
        ("personas", "maverick.personas"),
    ):
        try:
            for ep in _entry_points(group):
                out[kind].append({
                    "name": ep.name,
                    "module": getattr(ep, "value", str(ep)),
                    "enabled": allow is None or ep.name in allow,
                })
        except Exception:
            continue
    return {"plugins": out, "allowlist_active": allow is not None}


@router.get("/mcp")
async def list_mcp_servers() -> dict:
    """Configured MCP servers from ~/.maverick/config.toml."""
    try:
        from maverick.config import load_config
        cfg = (load_config() or {}).get("mcp_servers") or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"config read failed: {e}") from e
    return {
        "servers": [
            {"name": name, "command": s.get("command"), "args": s.get("args", [])}
            for name, s in cfg.items()
        ],
    }


@router.get("/tools")
async def list_tools() -> dict:
    """Tools the agent currently has registered (post-ACL, post-rate-limit)."""
    try:
        from maverick.sandbox import build_sandbox
        from maverick.tools import base_registry
        wm = _world()  # honor the configured backend (SQLite or Postgres)
        sb = build_sandbox()
        reg = base_registry(world=wm, sandbox=sb)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"registry build failed: {e}") from e
    from maverick.safety.tool_risk import risk_map
    tools = reg.all()
    risks = risk_map([t.name for t in tools])
    return {
        "tools": [
            {"name": t.name, "description": (t.description or "")[:200],
             "risk": risks.get(t.name, "medium")}
            for t in tools
        ],
    }


# ---- schedules: arm a saved template (or a prompt) to run on a cron ----------
# A schedule enqueues a recurring "start_goal" job (worker.py) that mints a fresh
# goal on every fire. Nothing runs until `maverick worker` drains the queue. The
# mutating routes are gated by [features] scheduling (like pack/role editing); the
# read-only list is always available.


def _require_scheduling() -> None:
    from maverick.config import get_features
    if not get_features().get("scheduling", True):
        raise HTTPException(
            status_code=403,
            detail=("scheduling is disabled ([features] scheduling = false). "
                    "Re-enable it in config, or use `maverick schedule` on the host."),
        )


def _schedule_out(job) -> ScheduleOut:
    p = job.payload or {}
    return ScheduleOut(
        id=job.id,
        cron=str(p.get("__cron__") or ""),
        kind=job.kind,
        title=(str(p.get("title") or p.get("text") or ""))[:200],
        next_run=job.run_at,
        schedule_id=str(p.get("schedule_id") or ""),
    )


@router.get("/schedules")
async def list_schedules(request: Request) -> dict:
    """Armed recurring schedules: pending cron jobs in the worker queue.

    Owner-scoped like ``delete_schedule``: an authenticated non-admin sees only
    the schedules stamped with their own principal (``create_schedule`` records
    it in ``payload['owner']``), so one tenant can't enumerate another's
    schedule titles. Admin / auth-off callers see all."""
    from maverick.job_queue import JobQueue
    owner = goal_owner_filter(request)
    jobs = [j for j in JobQueue().list(status="pending") if (j.payload or {}).get("__cron__")]
    if owner is not None:
        jobs = [j for j in jobs if (j.payload or {}).get("owner") == owner]
    jobs.sort(key=lambda j: j.run_at)
    return {"schedules": [_schedule_out(j).model_dump() for j in jobs]}


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(request: Request, payload: ScheduleIn) -> ScheduleOut:
    require_permission(request, "operate")
    _require_scheduling()
    from maverick.scheduler import CronError, next_run, schedule_cron
    cron = (payload.cron or "").strip()
    try:
        next_run(cron)  # validate up front; CronError -> 400
    except CronError as e:
        raise HTTPException(status_code=400, detail=f"bad cron expression: {e}") from e
    # Resolve the goal text: render a saved template (with params), or a prompt.
    title = (payload.title or "").strip()
    if payload.template:
        from maverick.templates import load_template
        try:
            tpl = load_template(payload.template)
            rtitle, body = tpl.render(**(payload.params or {}))
        except ValueError as e:           # unknown template / missing params
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            # Don't echo the raw error: its str() carries the absolute on-disk
            # template path. Reflect the caller's own template name instead; the
            # original (with path) stays chained for server-side logs.
            raise HTTPException(
                status_code=404, detail=f"template not found: {payload.template!r}"
            ) from e
        text, title = body, (title or rtitle)
    else:
        text = (payload.text or "").strip()
        if not text:
            raise HTTPException(
                status_code=400, detail="provide a template or text to schedule")
    title = (title or text)[:200]
    # A stable id carried in the payload across cron re-arms (each occurrence is
    # a fresh job with a new id), so the worker can stamp provenance and the
    # Automations page can group this schedule's run history.
    from uuid import uuid4
    schedule_id = uuid4().hex
    job_payload = {"text": text, "title": title, "__cron__": cron,
                   "schedule_id": schedule_id}
    owner = caller_principal(request) or ""
    if owner:
        job_payload["owner"] = owner
    user_id = execution_user_id_from_request(request)
    if user_id:
        job_payload["channel"] = "api"
        job_payload["user_id"] = user_id

    from maverick.job_queue import JobQueue
    job_id, run_at = schedule_cron(
        JobQueue(), cron, "start_goal", job_payload,
    )
    return ScheduleOut(id=job_id, cron=cron, kind="start_goal", title=title,
                       next_run=run_at, schedule_id=schedule_id)


@router.delete("/schedules/{job_id}")
async def delete_schedule(request: Request, job_id: int) -> dict:
    require_permission(request, "operate")
    _require_scheduling()
    from maverick.job_queue import JobQueue
    q = JobQueue()
    # Owner scoping, mirroring the goal routes: an authenticated non-admin may
    # cancel only schedules stamped with their own principal (create_schedule
    # records it in payload["owner"]). 404, not 403, so a cross-user probe
    # can't distinguish "exists but not yours" from "does not exist".
    owner = goal_owner_filter(request)
    if owner is not None:
        job = q.get(job_id)
        if job is None or (job.payload or {}).get("owner") != owner:
            raise HTTPException(
                status_code=404, detail="no pending schedule with that id")
    if not q.cancel(job_id):
        raise HTTPException(
            status_code=404, detail="no pending schedule with that id")
    return {"cancelled": job_id}


# ---- triggers: bind a saved template to an inbound webhook (POST /webhook/run)
# These routes MANAGE triggers (dashboard-authed, operate-gated, feature-knobbed).
# The inbound firing route lives in app.py (/webhook/run) and authenticates with
# its own HMAC signature -- exactly like /webhook/start, but strictly narrower:
# it runs only an operator-registered template, never arbitrary text.

_WEBHOOK_RUN_PATH = "/webhook/run"
_IMPORT_MAX_DEFINITIONS = 25
_IMPORT_MAX_DEFINITION_BYTES = 64_000
_IMPORT_MAX_TOTAL_DEFINITION_BYTES = 512_000
_IMPORT_MAX_RENDERED_BODY_CHARS = 16_000


def _definition_size(raw: dict) -> int:
    return len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _bounded_import_definitions(raws: list[dict]) -> list[dict]:
    if len(raws) > _IMPORT_MAX_DEFINITIONS:
        raise HTTPException(
            status_code=413,
            detail=f"too many import definitions (max {_IMPORT_MAX_DEFINITIONS})",
        )
    total = 0
    for raw in raws:
        size = _definition_size(raw)
        if size > _IMPORT_MAX_DEFINITION_BYTES:
            raise HTTPException(
                status_code=413,
                detail=("import definition is too large "
                        f"(max {_IMPORT_MAX_DEFINITION_BYTES} bytes)"),
            )
        total += size
        if total > _IMPORT_MAX_TOTAL_DEFINITION_BYTES:
            raise HTTPException(
                status_code=413,
                detail=("import definitions are too large "
                        f"(max {_IMPORT_MAX_TOTAL_DEFINITION_BYTES} bytes total)"),
            )
    return raws


def _ensure_import_body_size(body: str) -> None:
    if len(body) > _IMPORT_MAX_RENDERED_BODY_CHARS:
        raise HTTPException(
            status_code=413,
            detail=("rendered import template is too large "
                    f"(max {_IMPORT_MAX_RENDERED_BODY_CHARS} characters)"),
        )


def _require_triggers() -> None:
    from maverick.config import get_features
    if not get_features().get("triggers", True):
        raise HTTPException(
            status_code=403,
            detail=("triggers are disabled ([features] triggers = false). "
                    "Re-enable it in config to manage inbound webhook triggers."),
        )


def _inbound_secret_set() -> bool:
    from maverick.webhooks import inbound_secret
    return bool(inbound_secret())


@router.get("/triggers")
async def list_triggers_endpoint(request: Request) -> dict:
    """Registered inbound webhook triggers (read-only; always available).

    Owner-scoped: an authenticated non-admin sees only the triggers it
    registered, so one tenant can't enumerate another's trigger names, bound
    templates, and default param values. Admin / auth-off callers see all."""
    from maverick_dashboard import triggers_store
    triggers = triggers_store.list_triggers()
    owner = goal_owner_filter(request)
    if owner is not None:
        triggers = [t for t in triggers if t.get("owner", "") == owner]
    return {
        "triggers": triggers,
        "webhook_url": _WEBHOOK_RUN_PATH,
        "secret_configured": _inbound_secret_set(),
    }


@router.post("/triggers", response_model=TriggerOut, status_code=201)
async def create_trigger(request: Request, payload: TriggerIn) -> TriggerOut:
    require_permission(request, "operate")
    _require_triggers()
    if bool(payload.template) == bool(payload.flow):
        raise HTTPException(
            status_code=400, detail="set exactly one of 'template' or 'flow'")
    if payload.flow:
        # A flow target: the flow engine must be on and the flow must exist, so
        # a trigger can't be armed against a missing graph.
        _require_flows()
        from maverick.flow import store as flow_store
        if flow_store.load_flow(payload.flow) is None:
            raise HTTPException(
                status_code=404, detail=f"flow not found: {payload.flow!r}")
    else:
        # Validate now: the template must exist and render with the given
        # defaults, so a trigger can't be armed against a missing/incompatible
        # template.
        from maverick.templates import load_template
        try:
            load_template(payload.template).render(**(payload.params or {}))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except FileNotFoundError as e:
            # Reflect the caller's template name, not the raw error (it carries
            # the absolute on-disk path). Original stays chained for server logs.
            raise HTTPException(
                status_code=404, detail=f"template not found: {payload.template!r}"
            ) from e
    from maverick_dashboard import triggers_store
    # Owner-scope the write like list/delete: a non-admin must not overwrite
    # (hijack) a trigger registered by another tenant just by reusing its slug.
    # set_trigger keys by slugify(name), so check the same slug for a foreign
    # owner before replacing it.
    default_name = payload.name or payload.template or payload.flow
    owner_filter = goal_owner_filter(request)
    if owner_filter is not None:
        existing = triggers_store.get_trigger(triggers_store.slugify(default_name))
        if existing is not None and existing.get("owner", "") != owner_filter:
            raise HTTPException(
                status_code=409, detail="a trigger with that name already exists")
    try:
        rec = triggers_store.set_trigger(
            default_name, payload.template, payload.params or {},
            owner=caller_principal(request) or "", flow=payload.flow)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return TriggerOut(
        name=rec["name"], template=rec["template"], flow=rec.get("flow") or "",
        params=rec["params"],
        webhook_url=_WEBHOOK_RUN_PATH, secret_configured=_inbound_secret_set(),
    )


@router.delete("/triggers/{name}")
async def delete_trigger_endpoint(request: Request, name: str) -> dict:
    require_permission(request, "operate")
    _require_triggers()
    from maverick_dashboard import triggers_store
    # Owner-scope like schedules: a non-admin may delete only its own triggers.
    # The ownership check happens atomically inside delete_trigger (one locked
    # read, no TOCTOU). 404 (not 403) so a cross-tenant probe can't distinguish
    # "exists but not yours" from "does not exist".
    if not triggers_store.delete_trigger(name, owner=goal_owner_filter(request)):
        raise HTTPException(status_code=404, detail="no trigger with that name")
    return {"deleted": name}


# ---- event triggers: fire a saved template when a source has new items -------
# The POLL half of workflow automation (the push half is /webhook/run).
# Management is dashboard-authed + operate-gated + feature-knobbed; a scheduled
# POST /event-triggers/poll drains each source and fires per new event.


def _require_event_triggers() -> None:
    from maverick.automation_events import enabled
    if not enabled():
        raise HTTPException(
            status_code=403,
            detail=("Event triggers aren't enabled on this server yet — an "
                    "administrator can switch them on (enable [event_triggers] "
                    "in the server configuration, or set MAVERICK_EVENT_TRIGGERS=1)."),
        )


def _event_trigger_out(rec: dict) -> EventTriggerOut:
    return EventTriggerOut(
        name=rec["name"], template=rec["template"], flow=rec.get("flow") or "",
        source=rec["source"],
        config=rec.get("config") or {}, params=rec.get("params") or {},
        cursor=rec.get("cursor") or "",
        interval_seconds=int(rec.get("interval_seconds") or 0),
    )


def _owned_event_triggers(request: Request) -> list[dict]:
    """Event triggers visible to the caller: their own when auth is on, all of
    them for auth-off/admin (``goal_owner_filter`` returns None)."""
    from maverick_dashboard import event_triggers_store
    triggers = event_triggers_store.list_triggers()
    owner = goal_owner_filter(request)
    if owner is not None:
        triggers = [t for t in triggers if t.get("owner", "") == owner]
    return triggers


@router.get("/event-triggers")
async def list_event_triggers_endpoint(request: Request) -> dict:
    """Registered polled event triggers (owner-scoped) + the available sources."""
    from maverick.automation_events import available_sources
    from maverick.automation_events import enabled as _ev_enabled

    triggers = _owned_event_triggers(request)
    return {
        "triggers": [_event_trigger_out(t).model_dump() for t in triggers],
        "sources": available_sources(),
        "enabled": _ev_enabled(),
    }


@router.post("/event-triggers", response_model=EventTriggerOut, status_code=201)
async def create_event_trigger(request: Request, payload: EventTriggerIn) -> EventTriggerOut:
    require_permission(request, "operate")
    _require_event_triggers()
    # Sources that read shared credentials and then connect to caller-supplied
    # endpoints must be ADMIN-only. Otherwise a low-priv operator could name a
    # shared secret (OAuth provider or process env var) and exfiltrate it to
    # their own host during polling.
    cfg = payload.config or {}
    reads_env_secret = payload.source == "imap_email" and cfg.get("password_env")
    if cfg.get("provider") or reads_env_secret:
        require_permission(request, "admin")
    from maverick.automation_events import EventSourceError, get_source
    try:
        get_source(payload.source)   # a registered source?
    except EventSourceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Exactly one target: a template (rendered into a goal) OR a flow (which runs
    # the graph with the event as its data).
    template, flow = (payload.template or "").strip(), (payload.flow or "").strip()
    if bool(template) == bool(flow):
        raise HTTPException(status_code=400,
                            detail="set exactly one of 'template' or 'flow'")
    if flow:
        from maverick import flow as flow_mod
        from maverick.flow import store as flow_store
        if not flow_mod.enabled():
            raise HTTPException(
                status_code=403,
                detail=("Flows aren't enabled on this server yet — an "
                        "administrator can switch on the flow engine ([flows] "
                        "in the server configuration, or MAVERICK_FLOWS=1)."))
        if flow_store.load_flow(flow) is None:
            raise HTTPException(status_code=404, detail=f"flow not found: {flow!r}")
    else:
        # The template must EXIST, but -- unlike a webhook trigger -- we don't
        # require it to render with the current defaults: each polled event fills
        # the declared params at fire time.
        from maverick.templates import load_template
        try:
            load_template(template)
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=404, detail=f"template not found: {template!r}") from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    from maverick_dashboard import event_triggers_store
    default_name = payload.name or template or flow
    owner_filter = goal_owner_filter(request)
    if owner_filter is not None:
        existing = event_triggers_store.get_trigger(
            event_triggers_store.slugify(default_name))
        if existing is not None and existing.get("owner", "") != owner_filter:
            raise HTTPException(
                status_code=409, detail="an event trigger with that name already exists")
    try:
        from maverick.paths import current_tenant_id
        rec = event_triggers_store.set_trigger(
            default_name, template, payload.source,
            config=payload.config or {}, params=payload.params or {},
            owner=caller_principal(request) or "", flow=flow,
            interval_seconds=payload.interval_seconds,
            tenant=current_tenant_id() or "")   # bind OAuth vault reads to this tenant
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _event_trigger_out(rec)


@router.delete("/event-triggers/{name}")
async def delete_event_trigger_endpoint(request: Request, name: str) -> dict:
    require_permission(request, "operate")
    _require_event_triggers()
    from maverick_dashboard import event_triggers_store
    if not event_triggers_store.delete_trigger(name, owner=goal_owner_filter(request)):
        raise HTTPException(status_code=404, detail="no event trigger with that name")
    return {"deleted": name}


@router.post("/event-triggers/poll")
async def poll_event_triggers(request: Request, bg: BackgroundTasks, name: str = "") -> dict:
    """Poll event sources and fire the bound template for each new item.

    Owner-scoped: a caller drains only its own triggers. Each source's cursor
    advances so an item fires at most once; the per-poll event count is bounded
    by the engine. A broken template still advances the cursor so it can't
    re-fire a backlog forever. This is the on-demand path (the "Poll now" button
    or an external cron); the app also polls on a background tick automatically
    when the feature is on."""
    require_permission(request, "operate")
    _require_event_triggers()
    from maverick.runner import run_goal_in_thread

    from maverick_dashboard import event_poll

    principal = caller_principal(request) or ""
    user_id = execution_user_id_from_request(request)
    triggers = _owned_event_triggers(request)
    if name:
        triggers = [t for t in triggers if t["name"] == name]

    def _fire(goal_id: int) -> None:
        if user_id:
            bg.add_task(run_goal_in_thread, goal_id, channel="api", user_id=user_id)
        else:
            bg.add_task(run_goal_in_thread, goal_id)

    def _fire_flow(flow_id: str, data: dict, owner: str, idem_key: str = "",
                   origin: str = "manual") -> str:
        # Flow targets go through the durable queue (same as the background tick),
        # so a flow run gets retry + a trackable run id even on the on-demand path.
        # The idempotency key dedups a re-delivered event; origin records the trigger.
        from maverick_dashboard import automation_queue
        return automation_queue.enqueue_flow_run(flow_id, data, owner, idem_key, origin)

    results = await run_in_threadpool(
        lambda: event_poll.poll_and_fire(_world(), triggers, principal=principal,
                                         fire=_fire, fire_flow=_fire_flow))
    return {"polled": len(results), "triggers": results}


@router.get("/event-triggers/history")
async def event_trigger_history(request: Request, name: str = "", limit: int = 50) -> dict:
    """Recent firings + errors for event triggers (owner-scoped). Complements
    ``/automation-runs?kind=event`` (which shows the goals a trigger spawned)
    with the no-goal outcomes: poll failures, missing templates, skipped events."""
    require_permission(request, "operate")
    _require_event_triggers()
    from maverick_dashboard import trigger_events_store
    owner = goal_owner_filter(request)
    rows = trigger_events_store.history(
        name=name or None, owner=owner, limit=max(1, min(1000, limit)))
    return {"history": rows}


@router.get("/event-triggers/outcomes")
async def event_trigger_outcomes(request: Request) -> dict:
    """Per-trigger goal-outcome counts (done / failed / blocked / active ...).

    Closes the attribution loop the firing history can't: a trigger that fires a
    *bad* goal shows up here as a rising ``failed``/``blocked`` count, so an
    operator can see which automations produce work that doesn't land -- and the
    same failed goals are grounded into the learning loop by the run itself.
    Sourced from the goal-origin join (``origin_status_counts('event', name)``)."""
    require_permission(request, "operate")
    _require_event_triggers()
    w = _world()
    outcomes = {
        t["name"]: w.origin_status_counts("event", t["name"])
        for t in _owned_event_triggers(request)
    }
    return {"outcomes": outcomes}


# ---- automation import: pull clients' existing automations into Lightwork -----


def _require_automation_import() -> None:
    from maverick.automation_import import enabled
    if not enabled():
        raise HTTPException(
            status_code=403,
            detail=("Automation import isn't enabled on this server yet — an "
                    "administrator can switch it on (enable [automation_import] "
                    "in the server configuration, or set MAVERICK_AUTOMATION_IMPORT=1)."),
        )


@router.get("/import/sources")
async def import_sources_endpoint() -> dict:
    """Automation platforms Lightwork can import from + each one's mode."""
    from maverick.automation_import import available_sources, get_importer
    sources = []
    for s in available_sources():
        imp = get_importer(s)
        sources.append({
            "source": s,
            "mode": "definition-import" if imp.can_fetch_definitions else "connect-and-trigger",
        })
    return {"sources": sources}


@router.post("/import/run", response_model=ImportRunOut)
async def import_run_endpoint(request: Request, payload: ImportRunIn) -> ImportRunOut:
    require_permission(request, "operate")
    _require_automation_import()
    from maverick.automation_import import ImporterError, get_importer, materialize, translate_all

    try:  # validate the source name -> 400, not a scrubbed 500
        get_importer(payload.source)
    except ImporterError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if payload.as_flows:
        _require_flows()          # importing to graphs needs the flow engine

    # Definitions from the request (offline/connect) or a live fetch (env creds).
    if payload.definitions is not None:
        raws = _bounded_import_definitions([
            d for d in payload.definitions if isinstance(d, dict)
        ])
    else:
        try:
            fetched = await run_in_threadpool(get_importer(payload.source).fetch)
        except ImporterError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        raws = _bounded_import_definitions([
            d for d in fetched if isinstance(d, dict)
        ])

    automations = translate_all(payload.source, raws)
    if not automations:
        raise HTTPException(
            status_code=400,
            detail=f"no importable automations found from {payload.source!r}",
        )
    # translate_all drops malformed/unsupported definitions (logging each). It's
    # 1:1, so the shortfall is the count that silently failed -- surface it so a
    # partial import doesn't read as a clean success.
    skipped = len(raws) - len(automations)

    # Resolve schedule activation: an explicit request field wins; when the UI
    # omits it (None), fall back to the [automation_import] create_schedules
    # config knob (previously a dead setting -- nothing read it). Off by default.
    activate = payload.activate_schedules
    if activate is None:
        from maverick.config import get_automation_import
        activate = bool(get_automation_import().get("create_schedules", False))
    queue = None
    if activate and not payload.dry_run:
        from maverick.job_queue import JobQueue
        queue = JobQueue()

    from maverick.config import get_features
    triggers_on = get_features().get("triggers", True)
    from maverick_dashboard import triggers_store
    owner = caller_principal(request) or ""
    user_id = execution_user_id_from_request(request)
    channel = "api" if user_id else None

    results: list[dict] = []
    for a in automations:
        _, body = a.render()
        _ensure_import_body_size(body)
        res = materialize(
            a,
            save=not payload.dry_run,
            queue=queue,
            owner=owner,
            channel=channel,
            user_id=user_id,
        )
        webhook_trigger = None
        # Wire the inbound webhook trigger when asked and the automation is
        # webhook-triggered (the one step the CLI can't do: triggers_store is
        # dashboard-side). Respect the [features] triggers gate -- if the
        # operator disabled triggers, don't silently create ones that bypass
        # that decision; import the template and say so.
        sug = res.suggested_trigger or {}
        want_webhook = (payload.create_webhook_triggers and not payload.dry_run
                        and res.created_template and sug.get("kind") == "webhook")
        if want_webhook and not triggers_on:
            res.notes.append("webhook trigger not created: [features] triggers is off")
        elif want_webhook:
            # Default params for the trigger come from the recovered automation
            # (sug["params"]); validate the template renders with them before
            # arming so we don't create a trigger that always 400s at fire time
            # on a required param the import couldn't supply.
            sug_params = sug.get("params")
            if not isinstance(sug_params, dict):
                sug_params = {}
            from maverick.templates import load_template
            try:
                load_template(res.template_name).render(**sug_params)
            except (ValueError, FileNotFoundError) as e:
                res.notes.append(
                    f"webhook trigger not created: template needs param values "
                    f"the import can't supply ({e}); create it in the builder")
            else:
                try:
                    rec = triggers_store.set_trigger(
                        res.template_name, res.template_name, sug_params, owner=owner)
                    webhook_trigger = rec["name"]
                except ValueError as e:
                    res.notes.append(f"could not create webhook trigger: {e}")
        flow_id = None
        fidelity: list[dict] = []
        if payload.as_flows:
            # Lower to a real graph too: structure preserved where the source
            # exposes it, each step's disposition logged -- the migration is
            # never silently lossy.
            from maverick.automation_import.to_flow import to_flow_with_report
            from maverick.flow import store as flow_store
            flow_graph, fidelity = to_flow_with_report(a)
            if not payload.dry_run:
                flow_store.save_flow(flow_graph)
            flow_id = flow_graph.id
        results.append({
            "source": a.source, "name": a.name, "template": res.template_name,
            "created": res.created_template, "trigger": a.trigger.kind,
            "webhook_trigger": webhook_trigger, "schedule": res.schedule,
            "tools": res.tool_hints, "notes": res.notes,
            "flow": flow_id, "fidelity": fidelity,
        })

    return ImportRunOut(
        imported=results,
        dry_run=payload.dry_run,
        webhook_url=_WEBHOOK_RUN_PATH,
        secret_configured=_inbound_secret_set(),
        skipped=skipped,
    )


# ---- automation run history (provenance): goals a schedule/trigger spawned ---
# Read-only, behind the dashboard middleware like the other GETs. Powers the
# "last N runs · X done / Y failed" summary on the Automations page.


@router.get("/automation-runs")
async def automation_runs(request: Request, kind: str, ref: str, limit: int = 8) -> dict:
    """Recent goals an automation spawned + a status summary. ``kind`` is
    'schedule', 'trigger', or 'event'; ``ref`` is the schedule_id or trigger name."""
    if kind not in ("schedule", "trigger", "event"):
        raise HTTPException(
            status_code=400, detail="kind must be 'schedule', 'trigger', or 'event'")
    ref = (ref or "").strip()
    if not ref:
        return {"runs": [], "summary": {}}
    w = _world()
    capped = max(1, min(int(limit), 50))
    # Owner-scope: a trigger name (enumerable via GET /triggers) is shared across
    # tenants, so an authenticated non-admin must not read another owner's goal
    # titles/history -- nor the cross-owner aggregate from origin_status_counts.
    # Auth-off / admin keep the original (unscoped) behaviour exactly.
    if goal_owner_filter(request) is None:
        goals = w.goals_for_origin(kind, ref, limit=capped)
        summary = w.origin_status_counts(kind, ref)
    else:
        accessible = [
            g for g in w.goals_for_origin(kind, ref, limit=10_000)
            if can_access_goal(request, g)
        ]
        summary = {}
        for g in accessible:
            summary[g.status] = summary.get(g.status, 0) + 1
        goals = accessible[:capped]
    runs = [
        {"goal_id": g.id, "title": g.title, "status": g.status,
         "created_at": g.created_at}
        for g in goals
    ]
    return {"runs": runs, "summary": summary}


# ---- agents (domain packs): per-client view + override editor ---------------
# GET is always available (read-only roster/inspector). The mutating routes are
# gated behind the [features] pack_editing knob so a governed deployment can
# lock the agent roster; write_override additionally refuses any override whose
# *merged* result fails lint, so editing can never weaken the safety envelope.


def pack_editing_denial(request: Request) -> str | None:
    """Why this caller may NOT edit domain packs (agent playbooks), or ``None``
    if it may. One predicate for both consumers -- the enforce path
    (:func:`_require_pack_editing`) raises it as a 403 detail, and the builder
    page reads ``is None`` to decide whether to offer playbook mode -- so the two
    can't drift. Mirrors the ``*_denial`` pattern used for enterprise egress."""
    principal = caller_principal(request)
    if principal is not None and not is_dashboard_admin(principal):
        return "pack editing requires a dashboard admin"
    from maverick.config import get_features
    if not get_features().get("pack_editing", True):
        return ("pack editing is disabled ([features] pack_editing = false). "
                "Edit override TOML on the host, or re-enable it in config.")
    return None


def _require_pack_editing(request: Request) -> None:
    denial = pack_editing_denial(request)
    if denial is not None:
        raise HTTPException(status_code=403, detail=denial)


@router.get("/agents")
async def list_agents_endpoint(request: Request) -> dict:
    """The agent roster: every pack, flagged by override/workflow status.

    Suite-scoped (job function): a caller with a department grant sees their
    departments' specialists plus generic packs (no suite); unscoped callers
    (auth off / admin / no grant) see the full catalog — matching /departments.
    """
    from maverick.domain_edit import list_agents
    allowed = caller_suites(request)
    agents = list_agents()
    if allowed is not None:
        agents = [a for a in agents
                  if a.get("suite") is None or a.get("suite") in allowed]
    return {"agents": agents}


@router.get("/agents/{name}")
async def get_agent_endpoint(request: Request, name: str) -> dict:
    """The merged pack the agent runs, plus provenance (overridden vs inherited)
    and lint findings -- the payload the editor renders."""
    from maverick.domain import suite_for
    from maverick.domain_edit import resolved_view
    view = resolved_view(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {name!r}")
    # Job-function scoping: pack config (persona, tool allowlist, risk ceiling)
    # for another department is not this caller's to read. 404 first (catalog
    # existence is public), 403 on grant, mirroring /departments/{key}.
    require_suite(request, suite_for(name))
    return view


@router.post("/agents/{name}/validate")
async def validate_agent_override(
    request: Request, name: str, payload: AgentOverrideIn
) -> dict:
    """Lint the merged result of a proposed override without persisting it."""
    # Same gate as save/delete override: pack editing is admin-only, so the
    # lint helper that previews a pack edit must not be reachable unauthenticated.
    _require_pack_editing(request)
    from maverick.domain_edit import validate_override
    errors, warnings = validate_override(name, payload.model_dump(exclude_unset=True))
    return {"ok": not errors, "errors": errors, "warnings": warnings}


@router.post("/agents/{name}/override")
async def save_agent_override(request: Request, name: str, payload: AgentOverrideIn) -> dict:
    """Persist a tenant override for ``name``. 403 if pack editing is disabled,
    422 if the merged pack fails lint (the override is rejected, not written)."""
    _require_pack_editing(request)
    from maverick.domain_edit import resolved_view, write_override
    try:
        write_override(name, payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return resolved_view(name)


@router.delete("/agents/{name}/override")
async def delete_agent_override(request: Request, name: str) -> dict:
    """Drop a tenant override, reverting the agent to its built-in pack."""
    _require_pack_editing(request)
    from maverick.domain_edit import remove_override, resolved_view
    removed = remove_override(name)
    view = resolved_view(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no such agent: {name!r}")
    return {"removed": removed, "agent": view}


# ---- roles: per-client editable system-prompt addendum -----------------------
# Same gate pattern as agents: GET is read-only; mutations require role_editing.
# Role model/effort routing is configured elsewhere ([models]/[effort]) and is
# read-only here.


def _require_role_editing(request: Request) -> None:
    principal = caller_principal(request)
    if principal is not None and not is_dashboard_admin(principal):
        raise HTTPException(status_code=403, detail="role editing requires dashboard admin")

    from maverick.config import get_features
    if not get_features().get("role_editing", True):
        raise HTTPException(
            status_code=403,
            detail=("role editing is disabled ([features] role_editing = false). "
                    "Edit roles.toml on the host, or re-enable it in config."),
        )


@router.get("/roles")
async def list_roles_endpoint() -> dict:
    """The core-role roster, flagged by override status."""
    from maverick.role_edit import list_roles
    return {"roles": list_roles()}


@router.get("/roles/{role}")
async def get_role_endpoint(role: str) -> dict:
    """A role's merged view: resolved model/effort, addendum, and provenance."""
    from maverick.role_edit import resolved_role
    view = resolved_role(role)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no such role: {role!r}")
    return view


@router.post("/roles/{role}/override")
async def save_role_override(role: str, payload: RoleOverrideIn, request: Request) -> dict:
    """Persist a role's system-prompt addendum. 403 unless role editing is
    enabled and the caller is an admin (auth-off local mode remains allowed),
    422 if validation fails (unknown role, over-long addendum)."""
    _require_role_editing(request)
    from maverick.role_edit import resolved_role, write_role_override
    try:
        write_role_override(role, payload.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return resolved_role(role)


@router.delete("/roles/{role}/override")
async def delete_role_override(role: str, request: Request) -> dict:
    """Drop a role's override, reverting it to the built-in template."""
    _require_role_editing(request)
    from maverick.role_edit import remove_role_override, resolved_role
    removed = remove_role_override(role)
    view = resolved_role(role)
    if view is None:
        raise HTTPException(status_code=404, detail=f"no such role: {role!r}")
    return {"removed": removed, "role": view}


@router.get("/channels")
async def list_channels() -> dict:
    """Enabled channels from ~/.maverick/config.toml."""
    try:
        from maverick.config import load_config
        cfg = (load_config() or {}).get("channels") or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"config read failed: {e}") from e
    return {
        "channels": [
            {"name": name, "enabled": bool(c.get("enabled", True))}
            for name, c in cfg.items()
        ],
    }


@router.get("/audit/tail")
async def audit_tail(request: Request, n: int = 100, day: str | None = None) -> dict:
    """Tail the audit log (NDJSON at ~/.maverick/audit/YYYY-MM-DD.ndjson).

    Audit-gated: the audit trail is the who-did-what-when record (it can name
    principals, tool inputs, costs), so reading it requires the "audit"
    permission -- held by the admin role and the dedicated read-only "auditor"
    role (separation of duties), never by operator/viewer. It is not an
    unauthenticated/operator surface."""
    require_permission(request, "audit")
    from maverick.audit import default_audit_log

    from maverick_dashboard.app import safe_audit_day
    n = max(1, min(int(n or 100), 1000))
    return {"events": default_audit_log().tail(n, day=safe_audit_day(day))}


@router.get("/audit/grep")
async def audit_grep(request: Request, pattern: str, day: str | None = None) -> dict:
    """Search recent audit events for the given literal pattern.

    Audit-gated (see :func:`audit_tail`). Intentionally uses bounded, literal
    (case-insensitive) matching rather than a user-supplied regex: a regex over
    the HTTP surface invites catastrophic-backtracking ReDoS that blocks the
    dashboard event loop. Bounds the scan to the most recent 1000 events and
    caps results at 200.
    """
    require_permission(request, "audit")
    if not pattern:
        raise HTTPException(status_code=400, detail="pattern is required")
    if len(pattern) > 200:
        raise HTTPException(status_code=400, detail="pattern too long")
    from maverick.audit import default_audit_log

    from maverick_dashboard.app import safe_audit_day
    events = default_audit_log().tail(1000, day=safe_audit_day(day))
    needle = pattern.lower()
    matches = [
        e for e in events
        if needle in json.dumps(e, ensure_ascii=False).lower()
    ]
    return {"events": matches[:200]}


@router.get("/replay/{goal_id}")
async def replay_json(request: Request, goal_id: int) -> dict:
    """Flight-recorder timeline + chain-verification verdict for one run."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    from .control_plane import build_replay, evidence_packet
    replay = build_replay(goal_id, window=(g.created_at, g.updated_at))
    return evidence_packet(g, replay)


@router.get("/replay/{goal_id}/evidence")
async def replay_evidence(request: Request, goal_id: int) -> Response:
    """Download the run's evidence packet as a standalone JSON artifact."""
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    from .control_plane import build_replay, evidence_packet
    replay = build_replay(goal_id, window=(g.created_at, g.updated_at))
    body = json.dumps(
        evidence_packet(g, replay), indent=2, ensure_ascii=False, default=str,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-goal-{goal_id}.json"',
        },
    )


@router.get("/trust/agents")
async def trust_agents(request: Request) -> dict:
    """The Agent Trust Plane registry: external agents + their tool/risk/budget
    ceilings and lifecycle status (the cross-agent permission graph as JSON)."""
    from .control_plane import trust_overview
    return trust_overview()


@router.post("/trust/agents", status_code=201)
async def trust_agent_register(request: Request, payload: TrustAgentIn) -> dict:
    """Register or replace an external agent from the app (admin).

    Writes the dashboard/CLI-managed overlay (``agent_trust.json``), never the
    operator's config file; a managed entry with the same id overrides the
    config-file one, so admins can also amend hand-edited entries here."""
    require_permission(request, "admin")
    from maverick.agent_trust import AgentTrustError, put_agent
    try:
        agent = put_agent(payload.model_dump())
    except AgentTrustError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "id": agent.id}


@router.post("/trust/agents/{agent_id}/revoke")
async def trust_agent_revoke(request: Request, agent_id: str,
                             payload: TrustRevokeIn) -> dict:
    """Revoke (or restore) a managed external agent (admin). Entries defined in
    the operator's config file can't be flipped here -- register the same id
    from the app first to take it under management."""
    require_permission(request, "admin")
    from maverick.agent_trust import set_revoked
    if not set_revoked(agent_id, payload.revoked):
        raise HTTPException(
            status_code=404,
            detail="this agent is defined in the server's config file, not in "
                   "the app-managed registry. Register the same id here to "
                   "override it, or ask your administrator to edit the config.",
        )
    return {"ok": True, "id": agent_id, "revoked": payload.revoked}


@router.delete("/trust/agents/{agent_id}", status_code=204)
async def trust_agent_remove(request: Request, agent_id: str) -> None:
    """Remove a managed external agent (admin). Config-file entries reappear
    after their managed override is deleted; the same 404 rule applies."""
    require_permission(request, "admin")
    from maverick.agent_trust import remove_agent
    if not remove_agent(agent_id):
        raise HTTPException(
            status_code=404,
            detail="this agent is not in the app-managed registry (it may be "
                   "defined in the server's config file).",
        )


@router.get("/discovery")
async def discovery(request: Request) -> dict:
    """Inventory of governable surfaces: tools (by risk), MCP servers (with
    supply-chain pins), configured providers, channels, and external agents."""
    from .control_plane import discovery_overview
    return discovery_overview()


@router.get("/simulate")
async def simulate(request: Request, surface: str, action: str, target: str = "") -> dict:
    """Dry-run a proposed action: classify its risk + report whether it would be
    gated, without executing it. surface = computer | browser | tool."""
    from .control_plane import simulate_action
    return simulate_action(surface, action, target)


def _build_compliance_packet_body() -> str:
    from .control_plane import compliance_packet

    return json.dumps(compliance_packet(), indent=2, ensure_ascii=False, default=str)


@router.get("/compliance/packet")
async def compliance_packet_download(request: Request) -> Response:
    """Download a one-click compliance evidence bundle (SOC 2 control snapshot +
    GDPR/EU-AI-Act control report + audit-chain verdict) as a JSON artifact."""
    require_permission(request, "audit")
    global _COMPLIANCE_PACKET_CACHE
    now = time.monotonic()
    if (
        _COMPLIANCE_PACKET_CACHE is not None
        and now - _COMPLIANCE_PACKET_CACHE[0] < _COMPLIANCE_PACKET_CACHE_TTL_SECONDS
    ):
        body = _COMPLIANCE_PACKET_CACHE[1]
    else:
        async with _COMPLIANCE_PACKET_LOCK:
            now = time.monotonic()
            if (
                _COMPLIANCE_PACKET_CACHE is not None
                and now - _COMPLIANCE_PACKET_CACHE[0] < _COMPLIANCE_PACKET_CACHE_TTL_SECONDS
            ):
                body = _COMPLIANCE_PACKET_CACHE[1]
            else:
                body = await run_in_threadpool(_build_compliance_packet_body)
                _COMPLIANCE_PACKET_CACHE = (time.monotonic(), body)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="lightwork-compliance-packet.json"',
        },
    )


@router.get("/permissions")
async def permissions() -> dict:
    """Everything the agent is currently allowed to do (read-only)."""
    from maverick_dashboard.app import _permissions_snapshot
    return _permissions_snapshot()


@router.post("/permissions/tools/{name}/disable", status_code=204)
async def disable_tool(request: Request, name: str) -> None:
    """Disable a tool via the dashboard runtime overlay.

    Writes ~/.maverick/runtime-overrides.toml (NOT config.toml), which
    the kernel unions into the deny-list. Takes effect on the next goal
    with no restart.
    """
    require_permission(request, "operate")
    from maverick.runtime_overrides import disable_tool as _disable
    try:
        _disable(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/permissions/tools/{name}/enable", status_code=204)
async def enable_tool(request: Request, name: str) -> None:
    """Clear a dashboard-set tool override.

    Only clears overrides set here; a tool denied in config.toml itself
    stays denied (the response is still 204 — the overlay no longer
    denies it, config does).
    """
    require_permission(request, "operate")
    from maverick.runtime_overrides import enable_tool as _enable
    try:
        _enable(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/approvals")
async def list_approvals(request: Request) -> dict:
    """Pending high-risk actions parked by safety.consent (dashboard mode).

    This is the operators' collaborative supervision queue (an approval has no
    per-user owner and may carry another user's goal content in ``detail``), so
    listing it requires the ``operate`` permission — the same gate as
    approve/deny/claim. Previously any authenticated caller, including a
    view-only user, could read every parked action.
    """
    require_permission(request, "operate")
    w = _world()
    return {
        "approvals": [
            {
                "id": a.id, "action": a.action, "risk": a.risk,
                "scope": a.scope, "detail": a.detail,
                "requested_at": a.requested_at,
                # Collaborative supervision: who is handling this review.
                "claimed_by": getattr(a, "claimed_by", None),
                "claimed_at": getattr(a, "claimed_at", None),
            }
            for a in w.pending_approvals()
        ],
    }


def _supervisor(request: Request) -> str:
    """The acting supervisor's identity for claims/attribution.

    The authenticated principal when auth is on; the shared "operator"
    identity in single-user/no-auth deployments (claims still prevent
    double-handling across that operator's browser tabs)."""
    from .auth import caller_principal
    return caller_principal(request) or "operator"


def _audit_approval_decision(approval_id: int, status: str, decided_by: str) -> None:
    """Anchor a human approve/deny in the signed audit chain (audit H28).

    The decision's integrity must not rest on the mutable `approvals` row alone;
    record it in the tamper-evident Ed25519 chain. Best-effort: an audit-write
    failure never blocks the oversight decision that already committed."""
    try:
        from maverick.audit import EventKind, record
        record(EventKind.APPROVAL_DECISION, approval_id=int(approval_id),
               status=status, decided_by=decided_by or "")
    except Exception:  # pragma: no cover - audit failure must not break the decision
        pass


def _record_vote_or_raise(world, approval_id: int, status: str, who: str) -> None:
    """Apply one approver's vote, mapping rejection to the right HTTP error.

    Under N-of-M dual control a vote can be refused for segregation-of-duties
    reasons (the requester can't self-approve, or an approver identity is needed)
    -- distinguish that (403) from an unknown/already-decided approval (404)."""
    if world.decide_approval(approval_id, status, decided_by=who):
        return
    st = world.approval_state(approval_id)
    if st is None or st.get("status") != "pending":
        raise HTTPException(status_code=404, detail="no such pending approval")
    raise HTTPException(
        status_code=403,
        detail="vote not accepted: the requester can't approve their own request "
               "(segregation of duties), or an approver identity is required for "
               "multi-party approval",
    )


@router.post("/approvals/{approval_id}/approve", status_code=204)
async def approve_approval(request: Request, approval_id: int) -> None:
    """Record an approval vote; once the required quorum of distinct approvers is
    met the polling consent path proceeds (N-of-M dual control)."""
    require_permission(request, "operate")
    who = _supervisor(request)
    _record_vote_or_raise(_world(), approval_id, "approved", who)
    _audit_approval_decision(approval_id, "approved", who)


@router.post("/approvals/{approval_id}/deny", status_code=204)
async def deny_approval(request: Request, approval_id: int) -> None:
    """Deny a parked action (a single deny rejects it); the polling consent path
    then refuses it."""
    require_permission(request, "operate")
    who = _supervisor(request)
    _record_vote_or_raise(_world(), approval_id, "denied", who)
    _audit_approval_decision(approval_id, "denied", who)


@router.get("/approvals/{approval_id}/state")
async def approval_state(request: Request, approval_id: int) -> dict:
    """N-of-M quorum progress for an approval (status, approvers so far vs.
    required) — the operator view of an in-flight multi-party decision."""
    require_permission(request, "operate")
    st = _world().approval_state(approval_id)
    if st is None:
        raise HTTPException(status_code=404, detail="no such approval")
    return st


@router.post("/approvals/{approval_id}/claim")
async def claim_approval(request: Request, approval_id: int) -> dict:
    """Claim a pending approval (collaborative supervision).

    Marks "I'm handling this" so two supervisors don't double-work the same
    review. 409 when another supervisor already holds the claim."""
    # Same governance gate as approve/deny: claiming/releasing mutates the
    # human-oversight queue, so a read-only `viewer` must not be able to lock
    # pending approvals (or learn the claiming supervisor's identity via the 409).
    require_permission(request, "operate")
    who = _supervisor(request)
    if _world().claim_approval(approval_id, who):
        return {"claimed_by": who}
    a = _world().get_approval(approval_id)
    if a is None or a.status != "pending":
        raise HTTPException(status_code=404, detail="no such pending approval")
    raise HTTPException(status_code=409,
                        detail=f"already claimed by {a.claimed_by}")


@router.post("/approvals/{approval_id}/release")
async def release_approval(request: Request, approval_id: int) -> dict:
    """Release a claim you hold. 409 when you don't hold it."""
    require_permission(request, "operate")
    who = _supervisor(request)
    if _world().release_approval(approval_id, who):
        return {"released": True}
    raise HTTPException(status_code=409, detail="you do not hold this claim")


@router.get("/oversight/active")
async def oversight_active(request: Request) -> dict:
    """Active agents right now: running goals + their latest activity.

    Owner-scoped (auth-off/admin -> all). Powers the live "Active now" panel on
    the oversight console -- polled client-side so the operator watches the
    fleet work without a full-page reload. Fail-soft per goal: a goal whose
    event tail can't be read still lists with an empty activity.
    """
    w = _world()
    goals = w.list_goals(
        status="active", owner=goal_owner_filter(request), limit=50, order="desc",
    )
    out = []
    for g in goals:
        activity = ""
        updated_at = g.updated_at
        try:
            # Public method (works on SQLite AND Postgres) instead of a raw
            # `w.conn` query with `?` placeholders, which silently blanked every
            # activity under the Postgres backend. content is already decoded.
            events = w.recent_goal_events(g.id, limit=1)
            if events:
                ev = events[-1]  # newest (chronological order)
                content = (ev.content or "")[:120]
                activity = f"{ev.kind or ''}: {content}".strip(": ").strip()
                updated_at = ev.ts
        except Exception:
            activity = ""
            updated_at = g.updated_at
        out.append({
            "id": g.id, "title": g.title, "status": g.status,
            "updated_at": updated_at, "activity": activity,
        })
    return {"goals": out}


@router.get("/oversight/why/{goal_id}")
async def oversight_why(request: Request, goal_id: int, limit: int = 40) -> dict:
    """Explain *why* an agent is doing what it's doing — the governance drill-down.

    For one goal: status, cost-so-far, a by-kind summary, and the most-recent
    event chain (plan → tool → decision) that led here, so a supervisor can
    answer "why is this agent acting / why is this approval being requested"
    inline on the oversight console without hopping to the trajectory page.
    Owner-scoped via ``assert_goal_access``; fail-soft on cost so a spend-read
    error never 500s the drill-down.
    """
    from collections import Counter

    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    limit = max(1, min(limit, 200))
    events = w.recent_goal_events(goal_id, limit=limit)
    by_kind: Counter = Counter(e.kind for e in events)
    cost = 0.0
    try:
        cost = sum(ep.cost_dollars for ep in w.list_episodes(goal_id=goal_id, limit=200))
    except Exception:  # pragma: no cover -- cost is best-effort, never blocks
        cost = 0.0
    return {
        "goal_id": goal_id,
        "title": g.title,
        "status": g.status,
        "result": g.result,
        "cost_dollars": round(cost, 4),
        "summary": dict(by_kind),
        "events": [
            {"id": e.id, "agent": e.agent, "kind": e.kind,
             "content": (e.content or "")[:400], "ts": e.ts}
            for e in events
        ],
    }


@router.get("/fleets")
async def list_fleets_api(request: Request) -> dict:
    """The operator console roster: each fleet, its owner, and its agents.

    Read-only mirror of the ``/fleets`` page (Layer C of the enterprise control
    plane). Fail-soft to an empty list so a missing registry never 500s.

    Owner-scoped: a non-admin authenticated caller sees only the fleets they
    own; auth-off and admin callers see all (``goal_owner_filter`` returns None).
    """
    try:
        from maverick.fleet import list_fleets
        fleets = list_fleets()
    except Exception:
        fleets = []
    owner = goal_owner_filter(request)
    if owner is not None:
        fleets = [f for f in fleets if f.owner == owner]
    return {"fleets": [f.to_dict() for f in fleets]}




@router.post("/fleets/{fleet_name}/run", status_code=201)
async def run_fleet_agent(
    request: Request, fleet_name: str, payload: FleetRunIn, bg: BackgroundTasks,
) -> dict:
    """Dispatch a governed goal AS a fleet agent, from the operator console.

    The agent runs least-privileged under both its RBAC role's capability and
    the dispatching user's capability, while keeping its own audit principal
    (``agent:<fleet>.<agent>``), so the oversight control plane
    governs the work automatically (mirrors ``maverick fleet run``). Owner-scoped:
    only the fleet's owner -- or an admin / auth-off caller -- may dispatch; a
    cross-owner (or missing) fleet 404s, never revealing existence.
    """
    # Dispatching a fleet agent queues a governed goal that spends provider
    # money and runs tools -- an "operate" action, exactly like compose/resume
    # and POST /goals. Gate on role BEFORE any provider/rate/owner check so a
    # read-only viewer is 403'd up front (owner-scoping alone is not authz: a
    # viewer can own a fleet yet must never run one).
    require_permission(request, "operate")
    if not _any_provider_key_set():
        raise HTTPException(
            status_code=400,
            detail=("No AI provider is connected yet — an administrator needs "
                    "to add a provider key before agents can run "
                    "(run 'maverick init')."),
        )
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request)

    from maverick.capability import (
        UnknownRoleError,
        capability_for_role,
        capability_from_config,
    )
    from maverick.fleet import governance_enabled, load_fleet, record_run
    from maverick.runner import run_goal_in_thread

    fleet = load_fleet(fleet_name)
    principal = caller_principal(request)
    is_admin = principal is not None and is_dashboard_admin(principal)
    if fleet is None or (
        principal is not None
        and not is_admin
        and fleet.owner != principal
    ):
        raise HTTPException(status_code=404, detail="no such fleet")
    agent = next((a for a in fleet.agents if a.name == payload.agent), None)
    if agent is None:
        raise HTTPException(status_code=404, detail="no such agent in fleet")
    # Job-function scoping via the KERNEL gate (maverick.fleet), so this HTTP
    # route and any other dispatcher share one resolution chain (explicit grant
    # -> SCIM-group grant -> configured default; admins and auth-off pass). A
    # department-deployed agent carries its specialist pack (agent.domain); a
    # caller granted other departments may not dispatch it, even if they own
    # the fleet (e.g. a grant narrowed after deploy).
    from maverick.fleet import ensure_dispatch_allowed
    from maverick.suite_grants import DepartmentAccessError
    try:
        ensure_dispatch_allowed(principal, agent)
    except DepartmentAccessError as exc:
        raise HTTPException(
            status_code=403,
            detail="insufficient department access for this action") from exc
    # Running an agent under the oversight control plane is the paid (Gold)
    # "fleet governance" capability. Fail-open: a no-op unless license enforcement
    # is on and the license lacks it. Checked after existence (404) so a missing
    # fleet never reveals licensing state.
    if not governance_enabled():
        raise HTTPException(
            status_code=403,
            detail="fleet governance is a paid (Gold) add-on not included in this license")
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    agent_principal = fleet.principal_for(agent.name)
    try:
        cap = capability_for_role(agent.role, principal=agent_principal)
    except UnknownRoleError:
        # A saved fleet may carry an undefined/empty role (created before role
        # validation, or with RBAC roles opt-out). The CLI rejects these at
        # `fleet create`, but the dashboard run endpoint stays lenient so a
        # previously-runnable fleet keeps working: fall back to the base grant,
        # which is attenuated to the caller below -- a non-admin can therefore
        # never exceed their own capability. (capability_for_role was made
        # strict in #931, which otherwise 500s this legacy path.)
        cap = capability_from_config(agent_principal, user_id=agent_principal)
    if principal is not None and not is_admin:
        caller_cap = capability_from_config(principal, user_id=principal)
        cap = cap.attenuate(
            allow=caller_cap.allow_tools or None,
            deny=caller_cap.deny_tools,
            max_risk=caller_cap.max_risk,
            allow_paths=caller_cap.allow_paths or None,
            allow_hosts=caller_cap.allow_hosts or None,
        )
    # Department-deployed agents carry their specialist pack: bind the run to
    # that pack's capability envelope (least privilege per domain), on top of
    # the role + caller grants. domain_capability only narrows (never broadens),
    # and an unknown/disabled pack leaves the grant unchanged.
    if agent.domain:
        from maverick.domain import available_domains, domain_capability
        prof = available_domains().get(agent.domain)
        if prof is not None:
            cap = domain_capability(prof, cap, agent_principal)
    max_dollars = (
        min(payload.max_dollars, DEFAULT_MAX_DOLLARS)
        if payload.max_dollars is not None else DEFAULT_MAX_DOLLARS
    )

    w = _world()
    goal_id = w.create_goal(prompt[:200], prompt, owner=fleet.owner)
    record_run(fleet_name, agent.name, goal_id)
    # Schedule against the authenticated caller (or the shared anonymous lane
    # when auth is off), not the fleet-agent audit principal.  Agent names are
    # user-created, so using them as scheduler principals lets one caller mint
    # many lanes and bypass MAVERICK_MAX_CONCURRENT_GOALS_PER_PRINCIPAL.
    bg.add_task(
        run_goal_in_thread, goal_id, max_dollars,
        channel="fleet", user_id=agent_principal, capability=cap,
        concurrency_principal=principal,
    )
    return {"goal_id": goal_id, "principal": agent_principal, "role": agent.role}






@router.post("/fleets", status_code=201)
async def create_fleet(request: Request, payload: FleetCreateIn) -> dict:
    """Create (or replace) a fleet from the operator console, owned by the caller.

    Mirrors ``maverick fleet create`` so a non-technical operator never needs the
    CLI. Owner-scoped: replacing a fleet owned by someone else 404s (never
    reveals it). Blank agent rows are dropped; each agent needs a valid name
    and a configured RBAC role when roles are configured.
    """
    # Mutating fleet config is an "operate" action; owner-scoping decides WHOSE
    # fleet, not WHETHER the role may write one. A read-only viewer must be 403'd
    # (otherwise it could self-own a fleet here and then run it).
    require_permission(request, "operate")
    from maverick.capability import configured_roles
    from maverick.fleet import Fleet, FleetAgent, load_fleet, save_fleet, valid_name

    if not valid_name(payload.name):
        raise HTTPException(status_code=400, detail="invalid fleet name")
    agents = tuple(
        FleetAgent(
            name=a.name.strip(), role=a.role.strip(), description=a.description.strip(),
        )
        for a in payload.agents if a.name.strip()
    )
    configured = configured_roles()
    for a in agents:
        if not valid_name(a.name):
            raise HTTPException(status_code=400, detail=f"invalid agent name: {a.name!r}")
        if not a.role:
            raise HTTPException(status_code=400, detail=f"missing role for agent: {a.name!r}")
        if configured and a.role not in configured:
            raise HTTPException(status_code=400, detail=f"unknown role for agent: {a.name!r}")

    principal = caller_principal(request)
    owner = principal or ""
    existing = load_fleet(payload.name)
    if (
        existing is not None and existing.owner != owner
        and principal is not None and not is_dashboard_admin(principal)
    ):
        raise HTTPException(status_code=404, detail="no such fleet")

    try:
        save_fleet(Fleet(name=payload.name, owner=owner, agents=agents))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"fleet": load_fleet(payload.name).to_dict()}


@router.delete("/fleets/{fleet_name}", status_code=204)
async def delete_fleet(request: Request, fleet_name: str) -> None:
    """Remove a fleet. Owner-scoped: a cross-owner or missing fleet 404s."""
    # Deleting a fleet is an "operate" action; gate on role before owner-scoping.
    require_permission(request, "operate")
    from maverick.fleet import load_fleet, remove_fleet

    fleet = load_fleet(fleet_name)
    principal = caller_principal(request)
    is_admin = principal is not None and is_dashboard_admin(principal)
    if fleet is None or (
        principal is not None
        and not is_admin
        and fleet.owner != principal
    ):
        raise HTTPException(status_code=404, detail="no such fleet")
    remove_fleet(fleet_name)


def _compliance_checks(framework: str):
    """Run the core control-coverage report, filtered like the CLI.

    Single source of truth for the /compliance page and these exports:
    ``maverick.compliance.compliance_report()`` (GDPR + EU AI Act + US
    frameworks). ``framework`` is one of ``eu``/``us``/``all``; anything else
    falls back to ``all``. Fail-soft to an empty list so a missing core install
    yields an empty (still-downloadable) report rather than a 500.
    """
    framework = framework if framework in {"eu", "us", "all"} else "all"
    try:
        from maverick.compliance import compliance_report
        checks = compliance_report()
    except Exception:  # pragma: no cover - never 500 the export if core is absent
        return framework, []
    if framework != "all":
        checks = [c for c in checks if c.framework == framework]
    return framework, checks


@router.get("/compliance/report.md")
async def compliance_report_md(framework: str = "all") -> Response:
    """Download the control-coverage report as Markdown for an auditor.

    Same data as the /compliance page (``maverick.compliance``). The
    ``?framework=eu|us|all`` filter mirrors ``maverick compliance``. Returned as
    an attachment so an operator can hand the file to an auditor.
    """
    from maverick.compliance import render_report_text
    framework, checks = _compliance_checks(framework)
    body = render_report_text(checks)
    fname = f"lightwork-compliance-{framework}.md"
    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/compliance/report.csv")
async def compliance_report_csv(framework: str = "all") -> Response:
    """Download the control-coverage report as CSV for an auditor.

    Same data source + ``?framework=`` filter as ``report.md``. One row per
    control: framework, control, regulation, status, detail.
    """
    import csv
    import io as _io

    from maverick.compliance import COMPLIANCE_DISCLAIMER
    framework, checks = _compliance_checks(framework)
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["framework", "control", "regulation", "status", "detail"])
    for c in checks:
        writer.writerow([c.framework, c.control, c.regulation, c.status, c.detail])
    writer.writerow([])
    writer.writerow(["disclaimer", COMPLIANCE_DISCLAIMER])
    fname = f"lightwork-compliance-{framework}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )




@router.post("/redact/preview")
async def redact_preview(request: Request, payload: RedactIn) -> dict:
    """Granular redaction preview: per-finding spans + kinds, nothing stored.

    ``kinds`` filters which detector classes to act on (e.g. only
    ``secret:*`` or only ``pii:email``) — the granular half; empty = all.
    The response carries each finding (kind + a safe preview of WHERE, never
    the raw value) and the fully-redacted text for the selected kinds.
    """
    # Gate behind auth: this runs the detector pipeline on caller-supplied text,
    # so it must not be an unauthenticated compute/probe surface.
    require_permission(request, "operate")
    from maverick.provable_redaction import redact_proven, verify_redacted
    from maverick.safety import pii_detector, secret_detector

    text = payload.text or ""
    findings = []
    for m in secret_detector.scan(text):
        findings.append({"kind": f"secret:{m.name}", "span": list(m.span)})
    for m in pii_detector.scan(text):
        findings.append({"kind": f"pii:{m.kind}", "span": list(m.span)})
    selected = set(payload.kinds or [])

    if not selected:
        proof = redact_proven(text)
        redacted, proven = proof.redacted, proof.proven
    else:
        # granular: replace only the selected kinds' spans (end-to-start)
        spans = [f for f in findings if f["kind"] in selected]
        redacted = text
        for f in sorted(spans, key=lambda f: f["span"][0], reverse=True):
            a, b = f["span"]
            redacted = redacted[:a] + f"[REDACTED:{f['kind'].split(':', 1)[1]}]" + redacted[b:]
        proven = not verify_redacted(redacted)

    return {
        "findings": findings,
        "redacted": redacted,
        "proven_clean": proven,
        "residual": verify_redacted(redacted),
    }


@router.get("/glance")
async def watch_glance(request: Request) -> dict:
    """The Apple Watch glance payload (tiny fixed shape; see maverick.glance)."""
    from maverick.glance import build_glance
    from maverick.world_model import open_world
    world = open_world()
    try:
        return build_glance(world, owner=goal_owner_filter(request))
    finally:
        try:
            world.close()
        except Exception:
            pass


@router.get("/offline/bundle")
async def offline_bundle(request: Request) -> dict:
    """Bounded, versioned snapshot (``maverick-offline/1``) for the mobile
    companion's offline cache. Read-only; owner-scoped like ``/goals``."""
    from maverick.offline_bundle import build_bundle
    return await run_in_threadpool(
        build_bundle, _world(), owner=goal_owner_filter(request),
    )


@router.get("/perf")
async def perf_dashboard() -> dict:
    """Public perf dashboard data: SLA measurements + benchmark history.

    One JSON face for the perf story (roadmap 2027-H1 "public perf
    dashboard"): the live perf-SLA measurements against their published
    thresholds (docs/perf-sla.md), the recorded benchmark score history with
    short-window regression verdicts, and the longitudinal era retrospective.
    Everything is measured/read locally -- nothing fabricated; sections with
    no recorded data say so.
    """
    out: dict = {"sla": [], "benchmarks": {}, "retrospective": None}
    try:
        sla, error = await _cached_perf_sla()
        out["sla"] = sla
        if error:
            out["sla_error"] = error
    except Exception as e:  # measurement/cache must never 500 the dashboard
        out["sla_error"] = f"{type(e).__name__}: {e}"
    try:
        import json as _json

        from maverick.benchmark_retrospective import analyze, coverage
        from maverick.continuous_benchmark import _store_path, detect_regression, load_history
        store = _store_path()
        history: list[dict] = []
        if store.is_dir():
            # Legacy layout: a directory of per-suite *.json files.
            files = sorted(
                store.glob("*.json"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0.0,
                reverse=True,
            )[:_PERF_HISTORY_MAX_FILES]
            for f in sorted(files):
                try:
                    rows = _json.loads(f.read_text(encoding="utf-8"))
                    if isinstance(rows, list):
                        history.extend(r for r in rows if isinstance(r, dict))
                except (OSError, ValueError):
                    continue
        else:
            # Production layout: the single history.json FILE the bench_track
            # tool writes via save_history -- the same store /benchmarks reads.
            history.extend(r for r in load_history(store) if isinstance(r, dict))
        names = sorted({r.get("name") for r in history if r.get("name")})
        for name in names:
            scores = [r["score"] for r in history if r.get("name") == name]
            verdict = detect_regression(history, name)
            out["benchmarks"][name] = {
                "runs": len(scores),
                "latest": scores[-1] if scores else None,
                "best": max(scores) if scores else None,
                "regression": verdict,
            }
        span = coverage(history)
        if span:
            retros = analyze(history)
            out["retrospective"] = {
                "coverage": list(span),
                "trends": {n: {"trend": r.trend,
                               "net_change": round(r.net_change, 4)}
                           for n, r in retros.items()},
            }
    except Exception as e:
        out["benchmarks_error"] = f"{type(e).__name__}: {e}"
    return out


async def _cached_perf_sla() -> tuple[list[dict], str | None]:
    """Return perf-SLA rows with a short single-flight cache.

    ``run_all()`` performs live CPU/IO probes.  The dashboard exposes this data
    via a GET endpoint, so cache the expensive portion and serialize refreshes
    to keep cross-site/simple GET floods from starting unbounded worker-thread
    measurements in no-token loopback mode.
    """
    global _PERF_SLA_CACHE

    now = time.monotonic()
    if _PERF_SLA_CACHE is not None:
        expires_at, rows, error = _PERF_SLA_CACHE
        if now < expires_at:
            return rows, error

    async with _PERF_SLA_LOCK:
        now = time.monotonic()
        if _PERF_SLA_CACHE is not None:
            expires_at, rows, error = _PERF_SLA_CACHE
            if now < expires_at:
                return rows, error

        try:
            from maverick.perf_sla import run_all

            # run_all's dispatch probe drives its own event loop; run it in a
            # worker thread so it never nests inside the server's running loop.
            results = await asyncio.to_thread(run_all)
            rows = [
                {
                    "name": r.name,
                    "measured": r.measured,
                    "threshold": r.threshold,
                    "unit": r.unit,
                    "passed": r.passed,
                }
                for r in results
            ]
            error = None
        except Exception as e:  # measurement must never 500 the dashboard
            rows = []
            error = f"{type(e).__name__}: {e}"
        _PERF_SLA_CACHE = (now + _PERF_SLA_CACHE_TTL_SECONDS, rows, error)
        return rows, error


@router.get("/cache/stats")
async def cache_stats() -> dict:
    """In-process cache sizes (file reads, repo-map, skill embeddings).

    Mirrors ``maverick cache stats`` — surfaced here so the dashboard
    Cache page can render without shelling out.
    """
    from maverick.cache import stats
    return stats()




@router.post("/cache/purge")
async def cache_purge(request: Request, payload: CachePurgeIn) -> dict:
    """Purge one or more cache scopes.

    Valid scopes (from maverick.cache._VALID_SCOPES): files, repo_map,
    skill_embeddings, all. Unknown scopes are ignored.
    """
    require_permission(request, "operate")
    from maverick.cache import purge
    return purge(payload.scopes or ["all"])


# ---------- goal graph: forest view + structural edits (graph editor) ----------


@router.get("/goal-tree")
async def goal_tree_api(request: Request, limit: int = 300) -> dict:
    """The caller's goal forest with a server-computed layered layout.

    Powers /graph-editor and /plan-tree-3d: nodes carry (x, y) pixel
    positions so the client JS is a thin renderer. Owner-scoped like
    GET /goals (auth-off/admin see all).
    """
    from .goal_tree import forest_view, goal_nodes
    nodes = goal_nodes(_world(), owner=goal_owner_filter(request), limit=limit)
    return forest_view(nodes)




@router.post("/goals/{goal_id}/retitle", status_code=204)
async def retitle_goal(request: Request, goal_id: int, payload: RetitleIn) -> None:
    """Rename a goal (graph editor).

    Uses the world model's public ``set_goal_title`` so it works identically on
    the SQLite and Postgres backends (the earlier raw-SQL path used SQLite-only
    internals and 500'd under Postgres).
    """
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    require_permission(request, "operate")
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    w.set_goal_title(goal_id, title[:200])




@router.post("/goals/{goal_id}/reparent", status_code=204)
async def reparent_goal(request: Request, goal_id: int, payload: ReparentIn) -> None:
    """Move a goal under a new parent — or to the root (``parent_id: null``).

    Refuses self-parenting and any move that would create a cycle (the new
    parent being a descendant of the goal). Both ends are access-checked.
    """
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    require_permission(request, "operate")
    new_parent = payload.parent_id
    if new_parent is not None:
        if new_parent == goal_id:
            raise HTTPException(status_code=400, detail="a goal cannot be its own parent")
        p = w.get_goal(new_parent)
        if p is None:
            raise HTTPException(status_code=404, detail="no such parent goal")
        assert_goal_access(request, p)
        from .goal_tree import descendant_ids
        if new_parent in descendant_ids(w.goal_parent_pairs(), goal_id):
            raise HTTPException(
                status_code=400,
                detail="cannot re-parent a goal under its own descendant",
            )
    w.set_goal_parent(goal_id, new_parent)




@router.post("/goals/{goal_id}/children", response_model=GoalOut, status_code=201)
async def create_child_goal(request: Request, goal_id: int, payload: ChildIn) -> GoalOut:
    """Create a child goal under ``goal_id`` (graph editor "add child").

    Structural only: the child is created ``pending`` and is NOT queued to
    run — start it later via chat or POST /api/v1/goals. It inherits the
    parent's owner so the subtree stays visible to the same principal.
    """
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request)
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    child_id = w.create_goal(
        title[:200], (payload.description or "")[:8000],
        parent_id=goal_id, owner=g.owner or (caller_principal(request) or ""),
    )
    c = w.get_goal(child_id)
    if c is None:
        raise HTTPException(status_code=500, detail="goal vanished after create")
    return _to_goal_out(c)


# ---------- goal builder: compose a goal from blocks ----------

_COMPOSE_PRIORITIES = ("low", "normal", "high")




@router.post("/goals/compose", response_model=GoalOut, status_code=201)
async def compose_goal(request: Request, payload: ComposeIn, bg: BackgroundTasks) -> GoalOut:
    """Goal-builder submit: blocks -> structured brief -> create + run.

    The goals table has no metadata columns (see ``WorldModel.create_goal``),
    so the budget/channel/priority blocks are folded into the description the
    agent reads; the budget block additionally becomes the run's real
    ``max_dollars`` cap. Steps become a markdown checklist.
    """
    require_permission(request, "operate")
    require_provider_or_400()
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request)
    title = (payload.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    steps = [s.strip()[:500] for s in payload.steps if s and s.strip()]
    if len(steps) > 50:
        raise HTTPException(status_code=400, detail="too many steps (max 50)")
    priority = (payload.priority or "").strip().lower() or None
    if priority is not None and priority not in _COMPOSE_PRIORITIES:
        raise HTTPException(
            status_code=400,
            detail=f"priority must be one of: {', '.join(_COMPOSE_PRIORITIES)}",
        )
    channel = (payload.channel or "").strip() or None

    parts: list[str] = []
    if steps:
        parts.append("## Steps\n" + "\n".join(f"- [ ] {s}" for s in steps))
    meta: list[str] = []
    if payload.budget_dollars is not None:
        meta.append(
            f"Budget cap: ${payload.budget_dollars:.2f} "
            "(also enforced as the run's max_dollars)"
        )
    if channel:
        meta.append(f"Announce progress on: {channel}")
    if priority:
        meta.append(f"Priority: {priority}")
    if meta:
        parts.append("\n".join(meta))
    description = "\n\n".join(parts) if parts else title

    w = _world()
    goal_id = w.create_goal(
        title[:200], description[:8000], owner=caller_principal(request) or "",
    )
    from maverick.runner import run_goal_in_thread
    max_dollars = (
        min(payload.budget_dollars, DEFAULT_MAX_DOLLARS)
        if payload.budget_dollars is not None else DEFAULT_MAX_DOLLARS
    )
    user_id = execution_user_id_from_request(request)
    if user_id:
        bg.add_task(run_goal_in_thread, goal_id, max_dollars,
                    channel="api", user_id=user_id)
    else:
        bg.add_task(run_goal_in_thread, goal_id, max_dollars)
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=500, detail="goal vanished after create")
    return _to_goal_out(g)


# ---------- AI workflow builder ----------
#
# Draft from a natural-language brief or an uploaded document, in one of two
# forms (selected by `form`): a reusable, parameterized **template** (saved as a
# user Template that runs like any other), or a specialist **agent playbook** (a
# domain pack with persona, tool allowlist, risk ceiling, and gated steps).
# Drafting is one budget-capped LLM call (see maverick_dashboard.workflow_ai);
# templates save here (writes ~/.maverick/templates/<name>.md), while a playbook
# saves via the existing POST /agents/<name>/override (write_override).

_WORKFLOW_DOC_MAX_BYTES = 512_000  # ample for a spec/runbook; the model sees a truncated head


def _extract_binary_doc_text(raw: bytes, filename: str) -> str | None:
    """Text of a binary document (PDF/DOCX/ODT) via the maverick-knowledge
    parsers, or None when they're not installed / the file is unparseable.
    Blocking — call through run_in_threadpool."""
    import tempfile as _tempfile
    from pathlib import Path as _Path
    suffix = _Path(filename).suffix.lower() or ".bin"
    if suffix not in {".pdf", ".docx", ".odt", ".rtf", ".html", ".htm"}:
        return None
    try:
        from maverick_knowledge.parse import extract_text
    except ImportError:
        return None
    tmp = _tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(raw)
        tmp.close()
        out = extract_text(_Path(tmp.name))
        return out.strip() or None
    except Exception:  # noqa: BLE001 -- unparseable: caller 400s with a hint
        return None
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:  # pragma: no cover
            pass


def _require_provider_for_drafting() -> None:
    if not _any_provider_key_set():
        raise HTTPException(
            status_code=400,
            detail=(
                "No AI provider is connected yet — drafting a workflow needs "
                "one, and an administrator can set it up (run 'maverick init', "
                "export a provider key such as ANTHROPIC_API_KEY / "
                "OPENAI_API_KEY / GEMINI_API_KEY, or set [providers.<name>] "
                "in the server configuration)."
            ),
        )


def _drafter_for(form: str):
    """The drafting function for the requested form (default: template)."""
    from .workflow_ai import draft_playbook, draft_workflow
    return draft_playbook if form == "playbook" else draft_workflow


@router.post("/workflows/draft")
async def draft_workflow_from_brief(request: Request, payload: WorkflowDraftIn) -> dict:
    """Chat path: a natural-language brief -> a drafted workflow or playbook
    (per ``form``; not saved)."""
    require_permission(request, "operate")
    _require_provider_for_drafting()
    # Drafting is a paid LLM call; throttle it under the same ceiling as goal
    # creation so it can't be spammed into a cost amplifier.
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request, source="workflow-draft")
    brief = (payload.description or "").strip()
    if not brief:
        raise HTTPException(status_code=400, detail="describe the workflow you want")
    try:
        # The drafter makes a synchronous LLM call; offload it so the single
        # event loop isn't frozen for the multi-second round-trip (which would
        # stall every other user's requests, SSE and health probes).
        return await run_in_threadpool(_drafter_for(payload.form), brief)
    except Exception as e:
        # ValueError is a parse failure; BudgetExceeded / provider / network
        # errors are NOT ValueError -- surface any of them as an upstream 502
        # rather than an unhandled 500.
        raise HTTPException(status_code=502, detail=f"workflow drafting failed: {e}") from e


@router.post("/workflows/draft-from-file")
async def draft_workflow_from_upload(
    request: Request,
    file: UploadFile = File(...),
    form: str = Form("template"),
) -> dict:
    """Upload path: extract a workflow or playbook (per ``form``) from a
    text / markdown / JSON document — or a PDF / Word / OpenDocument file
    when the maverick-knowledge parsers are installed."""
    require_permission(request, "operate")
    _require_provider_for_drafting()
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request, source="workflow-draft")
    raw = await file.read(_WORKFLOW_DOC_MAX_BYTES + 1)
    if len(raw) > _WORKFLOW_DOC_MAX_BYTES:
        raise HTTPException(
            status_code=400,
            detail="file too large to draft from; paste the key parts into the brief instead",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = await run_in_threadpool(
            _extract_binary_doc_text, raw, file.filename or "upload")
        if text is None:
            raise HTTPException(
                status_code=400,
                detail=("could not read this file as text — upload a .txt / .md / "
                        ".json / .pdf / .docx document, or describe the workflow "
                        "in the brief"),
            ) from None
    if not text.strip():
        raise HTTPException(status_code=400, detail="the uploaded file was empty")
    try:
        drafter = _drafter_for(form)
        return await run_in_threadpool(lambda: drafter("", source_text=text))
    except Exception as e:
        # See draft_workflow_from_brief: non-ValueError LLM/budget errors -> 502.
        raise HTTPException(status_code=502, detail=f"workflow drafting failed: {e}") from e


@router.post("/workflows/refine")
async def refine_workflow_draft(request: Request, payload: WorkflowRefineIn) -> dict:
    """Revise the current draft (template or playbook, per ``form``) with a
    natural-language follow-up — the iterative loop in the builder."""
    require_permission(request, "operate")
    _require_provider_for_drafting()
    from maverick_dashboard.app import check_goal_rate_limit
    check_goal_rate_limit(request, source="workflow-draft")
    instruction = (payload.instruction or "").strip()
    if not instruction:
        raise HTTPException(status_code=400, detail="describe the change you want")
    from .workflow_ai import refine_playbook, refine_workflow
    refiner = refine_playbook if payload.form == "playbook" else refine_workflow
    try:
        return await run_in_threadpool(refiner, payload.current or {}, instruction)
    except Exception as e:
        # See draft_workflow_from_brief: non-ValueError LLM/budget errors -> 502.
        raise HTTPException(status_code=502, detail=f"refining the draft failed: {e}") from e


@router.post("/workflows", status_code=201)
async def save_workflow(request: Request, payload: WorkflowSaveIn) -> dict:
    """Persist an (AI-drafted, edited) workflow as a runnable user template."""
    require_permission(request, "operate")
    from maverick.templates import save_user_template
    try:
        tpl = save_user_template(
            payload.name,
            title=payload.title,
            body=payload.body,
            params=payload.params,
            budget_dollars=payload.budget_dollars,
            budget_wall_seconds=payload.budget_wall_seconds,
            overwrite=payload.overwrite,
        )
    except FileExistsError as e:
        # A fresh save (overwrite=false) colliding with an existing template is a
        # 409 conflict, not a silent clobber. The ?edit= flow sets overwrite=true.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"name": tpl.name, "title": tpl.title, "params": tpl.params, "saved": True}


# ---------- continuous-benchmark history ----------


def _benchmark_snapshot() -> dict:
    """Recorded benchmark runs, grouped per suite, with regression verdicts.

    Reads the same store ``maverick.continuous_benchmark`` (the bench_track
    tool) persists to — this deployment's own recorded runs, nothing else.
    Malformed rows (hand-edited file) are skipped, not invented around.
    """
    from maverick import continuous_benchmark as cb
    path = cb._store_path()
    history: list[dict] = []
    for h in cb.load_history(path):
        if not isinstance(h, dict) or not h.get("name"):
            continue
        try:
            score = float(h.get("score"))
        except (TypeError, ValueError):
            continue
        history.append({"name": str(h["name"]), "score": score,
                        "commit": str(h.get("commit") or ""), "t": h.get("t")})
    names: list[str] = []
    for h in history:
        if h["name"] not in names:
            names.append(h["name"])
    suites = []
    for name in names:
        entries = [h for h in history if h["name"] == name]
        r = cb.detect_regression(history, name)
        suites.append({
            "name": name,
            "runs": len(entries),
            "entries": entries[-50:],
            "latest": r["latest"],
            "baseline_mean": r["baseline_mean"],
            "delta": r["delta"],
            "drop_pct": r["drop_pct"],
            "regressed": r["regressed"],
        })
    return {"suites": suites, "history_path": str(path)}


@router.get("/benchmarks")
async def benchmarks_api() -> dict:
    """Benchmark history for this deployment (see ``_benchmark_snapshot``)."""
    snap = _benchmark_snapshot()
    if not snap["suites"]:
        snap["note"] = (
            "no benchmark runs recorded — record one with the bench_track "
            "tool (op=record, name, score) or "
            "maverick.continuous_benchmark.record_result"
        )
    return snap


# ---------- walkthrough export (replay video into the walkthroughs dir) ----------


def _walkthroughs_dir():
    """Where the dashboard's exported walkthrough videos live.

    ``maverick.replay.video.render`` writes wherever the caller points it
    (there is no fixed dir in core), so the dashboard standardises on
    ``<maverick home>/walkthroughs`` for everything the /walkthroughs page
    lists and serves.
    """
    from maverick.paths import maverick_home
    return maverick_home() / "walkthroughs"


def _vtt_for_frames(frames) -> str:
    """A WebVTT captions track derived from the storyboard frames.

    One cue per frame, timed by the frames' real durations; cue text is the
    frame's (already secret-scrubbed) caption, flattened to one line.
    """
    def ts(sec: float) -> str:
        ms = int(round(sec * 1000))
        h, rem = divmod(ms, 3_600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    lines = ["WEBVTT", ""]
    t = 0.0
    for f in frames:
        end = t + f.seconds
        text = f"[{f.kind}] {f.caption}".replace("\n", " ").replace("-->", "→").strip()
        lines += [f"{ts(t)} --> {ts(end)}", text or "(no caption)", ""]
        t = end
    return "\n".join(lines)


@router.post("/goals/{goal_id}/walkthrough", status_code=201)
async def export_walkthrough(request: Request, goal_id: int) -> dict:
    """Export a run's replay video into the walkthroughs dir.

    Uses the real machinery (``maverick.replay.video.render``): always writes
    the frame manifest + a WebVTT captions track derived from the storyboard;
    the MP4 encode itself needs Pillow + ffmpeg and the response says honestly
    whether it happened (``encoded``/``detail``) and carries the exact ffmpeg
    command for out-of-band encoding when it didn't.
    """
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    events = [
        {"kind": e.kind, "ts": e.ts, "agent": e.agent, "content": e.content}
        for e in w.goal_events(goal_id, limit=5000)
    ]
    if not events:
        raise HTTPException(
            status_code=400,
            detail="no events recorded for this goal — run it first, then export",
        )
    from maverick.replay.video import render, storyboard
    out_dir = _walkthroughs_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = storyboard(goal_id, events=events)
    vtt_name = f"goal-{goal_id}.vtt"
    (out_dir / vtt_name).write_text(_vtt_for_frames(frames), encoding="utf-8")
    try:
        from maverick.sandbox import build_sandbox
        sandbox = build_sandbox()
    except Exception:  # render falls back to the scrubbed-env runner
        sandbox = None
    out_path = out_dir / f"goal-{goal_id}.mp4"
    result = await run_in_threadpool(
        render, goal_id, out_path, sandbox=sandbox, events=events,
    )
    return {
        "goal_id": goal_id,
        "frames": result.frames,
        "encoded": result.encoded,
        "detail": result.detail,
        "video": out_path.name if result.encoded else None,
        "captions": vtt_name,
        "ffmpeg_command": result.command,
    }


@router.post("/goals/{goal_id}/resume", status_code=204)
async def resume_goal(goal_id: int, request: Request, bg: BackgroundTasks) -> None:
    """Resume a blocked / cancelled goal.

    Capabilities-seat finding: the CLI has ``maverick resume`` but the
    dashboard's only way to flip a cancelled goal back was to start a
    brand-new one. This route flips status back to 'pending' and
    re-queues the runner, so the next goal-event poll picks it back up.
    """
    require_permission(request, "operate")
    w = _world()
    g = w.get_goal(goal_id)
    if g is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(request, g)
    # Block resuming things that have no parked work.
    if g.status not in ("blocked", "cancelled", "failed"):
        raise HTTPException(
            status_code=400,
            detail=f"goal is {g.status!r}; only blocked/cancelled/failed goals can resume",
        )
    w.set_goal_status(goal_id, "pending", result=None)
    from maverick.runner import run_goal_in_thread
    user_id = execution_user_id_from_request(request)
    if user_id:
        bg.add_task(run_goal_in_thread, goal_id, channel="api", user_id=user_id)
    else:
        bg.add_task(run_goal_in_thread, goal_id)


# ---------------------------------------------------------------------------
# Workforce packaging: departments, outcomes, marketplace, reviews.
#
# Read-only JSON over capabilities that already ship (maverick.departments /
# .outcomes / .worker_review / .marketplace.storefront). These present the
# 1,000+ specialist packs as buyable teams with delivery and a governed review
# — the buyer-facing surfaces, not new platform. No mutation, so no extra
# permission gate beyond the dashboard's read access (cf. /overview).
# ---------------------------------------------------------------------------
def _department_request_tenant() -> str | None:
    """Tenant id for department entitlement checks in dashboard requests."""
    try:
        from maverick.paths import current_tenant_id
        return current_tenant_id()
    except Exception:  # pragma: no cover - tenant lookup must not break self-host
        return None


def _has_managed_tenant_roster() -> bool:
    """Whether this install has provisioned tenants but no request tenant."""
    try:
        from maverick.tenant import registry as tenant_registry
        return bool(tenant_registry.list_tenants())
    except Exception:  # pragma: no cover - registry lookup must not break self-host
        return False


def _department_entitled_for_request(key: str) -> bool:
    """Department entitlement for this dashboard request.

    No active tenant remains fail-open for legacy self-host installs. Once a
    tenant roster exists, however, a dashboard request with no pinned tenant is
    an ambiguous managed request; report the paid add-on as unavailable rather
    than letting the core billing gate treat it as self-hosted.
    """
    from maverick.departments import department_entitled
    tenant = _department_request_tenant()
    if tenant is None and _has_managed_tenant_roster():
        return False
    return department_entitled(key, tenant=tenant)


@router.get("/departments")
async def list_departments_api(request: Request) -> list[dict]:
    """Departments (suites) as deployable teams: title, charter, headcount.

    Each entry carries ``entitled`` — whether the active tenant's plan includes
    the paid ``departments`` add-on, so the UI shows Deploy vs. Add-on-required.
    Suite-scoped (job function): a caller with a department grant sees only
    their departments; unscoped callers (auth off / admin / no grant) see all.
    """
    from maverick.departments import list_departments
    allowed = caller_suites(request)
    out = []
    for d in list_departments():
        if allowed is not None and d.key not in allowed:
            continue
        row = d.to_dict()
        row["entitled"] = _department_entitled_for_request(d.key)
        out.append(row)
    return out


@router.get("/departments/{key}")
async def get_department_api(request: Request, key: str) -> dict:
    """One department with its specialist roster (name + description + risk)."""
    from maverick.departments import get_department, roster
    dept = get_department(key)
    if dept is None:
        raise HTTPException(status_code=404, detail="no such department")
    # Job-function scoping: 403 (not 404) — the catalog is public product
    # surface, the denial is about THIS user's grant, mirroring role gates.
    require_suite(request, key)
    out = dept.to_dict()
    out["entitled"] = _department_entitled_for_request(key)
    out["roster"] = [
        {"name": p.name, "description": p.description or "",
         "max_risk": p.max_risk or "low"}
        for p in roster(key)
    ]
    return out


@router.post("/departments/{key}/deploy", status_code=201)
async def deploy_department_api(request: Request, key: str) -> dict:
    """Deploy a department as a fleet of its specialists — a PAID ADD-ON.

    Gated four ways: ``operate`` RBAC (a viewer is 403'd), the caller's
    department grant (a finance-scoped user deploying Legal is 403'd),
    owner-scoping (you cannot clobber another owner's fleet — that 404s), and
    the ``departments`` entitlement (a tenant without the add-on is 402'd).
    Mirrors ``maverick.departments.deploy_department`` so the CLI enforces the
    same entitlement gate.
    """
    require_permission(request, "operate")
    from maverick.departments import (
        EntitlementError,
        deploy_department,
        fleet_name_for,
        get_department,
    )
    from maverick.fleet import load_fleet
    from maverick.suite_grants import DepartmentAccessError

    if get_department(key) is None:
        raise HTTPException(status_code=404, detail="no such department")
    require_suite(request, key)

    principal = caller_principal(request)
    owner = principal or ""
    fleet_name = fleet_name_for(key, owner)
    existing = load_fleet(fleet_name)
    if (
        existing is not None and existing.owner != owner
        and principal is not None and not is_dashboard_admin(principal)
    ):
        raise HTTPException(status_code=404, detail="no such fleet")

    tenant = _department_request_tenant()
    if tenant is None and _has_managed_tenant_roster():
        raise HTTPException(
            status_code=402,
            detail="departments add-on requires an active tenant",
        )

    try:
        fleet = deploy_department(key, owner, tenant=tenant)
    except EntitlementError as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except DepartmentAccessError as e:
        # Kernel backstop (maverick.departments enforces the grant for every
        # caller); the require_suite gate above normally 403s first.
        raise HTTPException(
            status_code=403,
            detail="insufficient department access for this action") from e
    if fleet is None:  # suite disabled between the check and the deploy
        raise HTTPException(status_code=404, detail="no such department")
    return {"fleet": fleet.to_dict()}


@router.get("/departments/{key}/review")
async def department_review_api(request: Request, key: str) -> dict:
    """A governed performance review: delivery + authority + learning."""
    require_suite(request, key)
    from maverick.worker_review import review
    r = review(_world(), key, owner=goal_owner_filter(request))
    if r is None:
        raise HTTPException(status_code=404, detail="no such department")
    return r


@router.get("/outcomes")
async def outcomes_api(request: Request, top: int = 0) -> dict:
    """Per-worker delivery cards + a firm-wide rollup, from the Operating Record."""
    from maverick.operating_record import assemble
    from maverick.outcomes import firm_totals, worker_cards
    w = _world()
    owner = goal_owner_filter(request)
    cards = worker_cards(w, top=(max(0, int(top)) or None), owner=owner)
    return {
        "firm": firm_totals(assemble(w, owner=owner)).to_dict(),
        "workers": [c.to_dict() for c in cards],
    }


def _learning_snapshot(request: Request) -> dict:
    """Learning-engine status + accumulated moat. Shared by the JSON endpoint
    and the /learning page so both render identical numbers."""
    from maverick import consequence, dreaming, reflexion, trajectory_store
    from maverick.config import get_data_engine
    from maverick.operating_record import assemble, stats

    from maverick_dashboard.settings_store import LEARNING_SUBSYSTEMS

    def _data_engine_on() -> bool:
        try:
            return bool(get_data_engine().get("enable", False))
        except Exception:  # pragma: no cover - config never blocks the view
            return False

    # Live enabled() per subsystem key -- paired with the shared registry so the
    # label/how/config metadata stays single-sourced in settings_store.
    on_by_key = {
        "consequence": consequence.enabled(),
        "reflexion": reflexion.enabled(),
        "dreaming": dreaming.enabled(),
        "capture": trajectory_store.enabled(),
        "data_engine": _data_engine_on(),
    }
    components = [
        {"key": s["key"], "label": s["label"],
         "on": on_by_key.get(s["key"], False), "how": s["how"]}
        for s in LEARNING_SUBSYSTEMS
    ]

    w = _world()
    owner = goal_owner_filter(request)
    st = stats(assemble(w, owner=owner))
    accumulated = {
        "decisions": st.n_records,
        "human_decisions": st.n_human_decisions,
        "approvals": st.n_approvals,
        "departments": len(st.departments),
    }
    # Learned skills and grounded outcomes are deployment-level assets (neither is
    # stored per-tenant), so only surface them on an unscoped/admin view -- a
    # single tenant must never see deployment-wide totals as if they were its own.
    if owner is None:
        try:
            # Count the ledger directly -- _learned_snapshot would also build
            # the whole harness/provenance/pending view just to be discarded.
            # The stat is a TOTAL, so don't inherit the page's display limit
            # (the old path silently capped the count at 50).
            from maverick import self_learning
            learned_n = len(self_learning.history(
                limit=1_000_000, path=self_learning.LEARNED_PATH))
        except Exception:  # pragma: no cover - the moat view never 500s on a sub-read
            learned_n = 0
        accumulated["grounded_outcomes"] = consequence.count()
        accumulated["learned_capabilities"] = learned_n
        # Flows that improved themselves -- forward self-rewrites the loop enacted,
        # a tangible "your automations got better on their own" moat number.
        try:
            from maverick.flow import evolution_log
            improvements = evolution_log.improvement_count()
        except Exception:  # pragma: no cover -- the moat view never 500s on a sub-read
            improvements = 0
        if improvements:
            accumulated["flow_self_improvements"] = improvements
    # Autonomous flow self-improvement: its own opt-in, deliberately NOT folded
    # into the blanket "turn learning on" button -- autonomously rewriting a live
    # workflow is a heavier consequence than passive learning. Admin/unscoped view.
    flow_autonomy = None
    if owner is None:
        try:
            from maverick import flow as _flow_mod
            flow_autonomy = {
                "engine_on": _flow_mod.enabled(),
                "auto_evolve": _flow_mod.auto_evolve_enabled(),   # revert regressions
                "auto_apply": _flow_mod.auto_apply_enabled(),     # apply improvements
            }
        except Exception:  # pragma: no cover -- the moat view never 500s on a sub-read
            flow_autonomy = None
    return {
        "components": components,
        "flow_autonomy": flow_autonomy,
        "accumulated": accumulated,
        "components_on": sum(1 for c in components if c["on"]),
        "components_total": len(components),
    }


@router.get("/learning")
async def learning_api(request: Request) -> dict:
    """Learning-engine status + the durable, per-tenant value it has accumulated.

    ``components`` reflect each subsystem's own ``enabled()`` (the source of
    truth). ``accumulated`` is the non-portable moat: decisions and human
    judgements on the Operating Record, capabilities the workforce taught
    itself, and the grounded outcomes it has learned from -- what a customer
    would forfeit by leaving. Read-only and owner-scoped."""
    return _learning_snapshot(request)


@router.post("/learning")
async def set_learning_endpoint(request: Request, payload: LearningToggleIn) -> dict:
    """Turn the whole self-learning loop on or off in one action (writes the
    learning knobs to the config overlay). Returns the refreshed snapshot so the
    caller sees the new state.

    Admin-gated: this writes the deployment-wide dashboard config overlay (the
    same one [capabilities]/[features]/providers use, all admin-gated), which
    ``load_config`` merges for every tenant and worker -- not an owner-scoped
    action, so an operator must not flip it (e.g. force-enable trajectory
    capture deployment-wide, or disable the whole learning loop)."""
    require_permission(request, "admin")
    from maverick_dashboard import settings_store
    settings_store.set_learning(payload.enabled)
    return _learning_snapshot(request)


@router.post("/learning/flow-autonomy")
async def set_flow_autonomy_endpoint(request: Request, payload: FlowAutonomyIn) -> dict:
    """Turn the AUTONOMOUS flow self-improvement loop on/off -- its own opt-in,
    kept off the blanket learning button because autonomously rewriting a live
    workflow is a heavier consequence than passive learning.

    Two independent knobs: ``auto_evolve`` lets the loop revert a rewrite it
    measures as a regression; ``auto_apply`` (which needs auto_evolve on for the
    revert safety net) lets it apply an improvement forward without a human. Same
    admin gate + deployment-wide overlay as the blanket toggle."""
    require_global_permission(request, "admin")
    from maverick_dashboard import settings_store
    settings_store.set_flow_autonomy(auto_evolve=payload.auto_evolve,
                                     auto_apply=payload.auto_apply)
    from maverick.config import reset_config_cache
    reset_config_cache()   # so the refreshed snapshot reflects the new overlay
    return _learning_snapshot(request)


@router.get("/marketplace/packs")
async def marketplace_packs_api(request: Request, q: str = "") -> dict:
    """Pack marketplace grouped by department, or a flat search when ``q`` set.

    Suite-scoped like /departments: a caller with a department grant browses
    only their departments' shelves; unscoped callers see the full store.
    """
    from maverick.marketplace.storefront import pack_marketplace, search_packs
    allowed = caller_suites(request)
    query = (q or "").strip()
    if query:
        results = search_packs(query)
        if allowed is not None:
            results = [r for r in results if r.get("suite") in allowed]
        return {"query": query, "results": results}
    departments = pack_marketplace()
    if allowed is not None:
        departments = [d for d in departments if d.get("key") in allowed]
    return {"departments": departments}


@router.get("/marketplace/connectors")
async def marketplace_connectors_api(request: Request, q: str = "") -> dict:
    """Connector marketplace: honest total + optional substring search."""
    from maverick.marketplace.storefront import connector_marketplace
    return connector_marketplace(q or None)
