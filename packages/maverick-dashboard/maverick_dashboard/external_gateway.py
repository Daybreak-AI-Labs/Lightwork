"""The agent-facing half of the bring-your-own-agent gateway.

Agents that run on OTHER platforms (Agentforce, Bedrock, Copilot Studio,
LangChain, custom runtimes) call these routes to be governed and accounted by
Lightwork while doing their work elsewhere:

* ``POST /api/v1/external/screen``     — ask before acting (trust ceilings,
  budget cutoff, Shield input scan, approval floor).
* ``POST /api/v1/external/runs``       — report a completed run; it lands as
  a first-class Operating Record goal owned by ``agent:<id>``.
* ``GET  /api/v1/external/approvals/{id}`` — poll a parked high-risk action.
* ``GET  /api/v1/external/openapi.json``  — the importable description of the
  three routes above (drops into Agentforce External Services / Bedrock
  action groups as-is).

Auth is the per-agent ``rest`` bearer minted on the /external-agents page —
NOT the dashboard session/bearer. The ``/api/v1/external/`` prefix is
therefore registered as self-authenticating (see ``auth.py`` and the
dashboard-token middleware in ``app.py``), same pattern as ``/a2a/v1`` and
the HMAC webhooks: an external platform has no dashboard credential, and
pressuring operators to share one across two trust domains would be worse.
Every route self-gates on ``[external_agents] enable`` (404 when off) and the
Gold entitlement (403), so including the router unconditionally is inert.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

log = logging.getLogger("maverick.dashboard.external_gateway")

router = APIRouter(prefix="/api/v1/external", tags=["external-agents"])

# Per-caller request throttles (window: 60s). Budgets gate dollars; these
# gate volume, so a misbehaving or looping agent cannot hammer the gateway.
# Keyed by verified agent id — except the unauthenticated schema route,
# which keys by client address.
_RATE_LIMITS = {"screen": 120, "runs": 60, "approvals": 120, "openapi": 30,
                "heartbeat": 240}
_RATE_BUCKET_CAP = 10_000
_rate_times: dict[tuple[str, str], deque[float]] = {}
_rate_lock = threading.Lock()


def _rate_limit(key: str, action: str) -> None:
    now = time.monotonic()
    limit = _RATE_LIMITS[action]
    bucket = (action, key)
    with _rate_lock:
        if bucket not in _rate_times and len(_rate_times) >= _RATE_BUCKET_CAP:
            for candidate, window in tuple(_rate_times.items()):
                while window and now - window[0] >= 60:
                    window.popleft()
                if not window:
                    _rate_times.pop(candidate, None)
            if len(_rate_times) >= _RATE_BUCKET_CAP:
                raise HTTPException(
                    status_code=429,
                    detail="gateway rate-limit capacity reached",
                    headers={"Retry-After": "60"})
        window = _rate_times.setdefault(bucket, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit reached for {action} "
                       f"({limit}/minute); slow down and retry",
                headers={"Retry-After": "60"})
        window.append(now)


class ExternalRunIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    outcome: str = Field(max_length=20)
    summary: str = Field(default="", max_length=4000)
    steps: list[str] = Field(default_factory=list, max_length=50)
    cost_dollars: float = Field(default=0.0, ge=0, le=1_000_000)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0)
    department: str = Field(default="", max_length=100)
    # Send a stable key per logical run and retries become exactly-once:
    # a replay returns the original goal instead of double-counting.
    idempotency_key: str = Field(default="", max_length=128)


class ExternalScreenIn(BaseModel):
    tool: str = Field(min_length=1, max_length=128)
    detail: str = Field(default="", max_length=4000)
    risk: str = Field(default="", max_length=10)


class ExternalRunStartIn(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=4000)
    department: str = Field(default="", max_length=100)


class ExternalRunFinishIn(BaseModel):
    outcome: str = Field(max_length=20)
    summary: str = Field(default="", max_length=4000)
    steps: list[str] = Field(default_factory=list, max_length=50)
    cost_dollars: float = Field(default=0.0, ge=0, le=1_000_000)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0.0, ge=0)
    idempotency_key: str = Field(default="", max_length=128)


def _gate() -> None:
    """Self-gate: 404 while the plane is off (the route doesn't exist for
    this deployment), 403 when the license lacks the paid capability."""
    from maverick import external_agents as xa
    if not xa.enabled():
        raise HTTPException(status_code=404, detail="not found")
    if not xa.entitled():
        raise HTTPException(
            status_code=403,
            detail="external-agent governance is a paid (Gold) add-on not "
                   "included in this license")


def _require_agent(request: Request) -> str:
    """Dependency: resolve the caller's per-agent ``rest`` bearer to its
    enrolled id — solved BEFORE the body model, so an unauthenticated caller
    gets 401, never a 422 that leaks the request schema.

    The token is the identity: no session, no CSRF surface, no fallback to
    the dashboard credential. 401 (not 403) on failure so a rotated token
    reads as "re-authenticate", and the reply never says whether the id
    exists."""
    _gate()
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    from maverick.agent_trust import agent_for_token
    agent = agent_for_token(token, "rest")
    if agent is None:
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return agent.id


def _shield():
    """The deployment's Shield instance for input scanning, or ``None``.

    Kernel rule: the platform never *requires* agent-shield. Screening
    degrades to the redaction + injection-tripwire layer when it is absent;
    when it IS present, a scanner error denies (enforced in core)."""
    try:
        from maverick_shield import Shield
        return Shield()
    except Exception:
        return None


@router.post("/runs", status_code=201)
async def external_run_report(
    payload: ExternalRunIn, agent_id: str = Depends(_require_agent),
) -> dict:
    """Ingest one completed external run onto the Operating Record."""
    _rate_limit(agent_id, "runs")
    from maverick import external_agents as xa
    try:
        out = xa.record_run(agent_id, payload.model_dump(), shield=_shield())
    except xa.ExternalAgentsError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"ok": True, **out}


@router.post("/runs/start", status_code=201)
async def external_run_start(
    payload: ExternalRunStartIn, agent_id: str = Depends(_require_agent),
) -> dict:
    """Open a LIVE run: the goal is visible on the Operating Record while
    the work happens, and the reply names the heartbeat interval the agent
    must honor or the run is reclaimed."""
    _rate_limit(agent_id, "runs")
    from maverick import external_agents as xa
    try:
        out = xa.start_run(agent_id, payload.model_dump(), shield=_shield())
    except xa.ExternalAgentsError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"ok": True, **out}


@router.post("/runs/{goal_id}/heartbeat")
async def external_run_heartbeat(
    goal_id: int, agent_id: str = Depends(_require_agent),
) -> dict:
    """Keep a live run alive and learn whether to continue — ``continue:
    false`` is the kill switch reaching mid-flight work."""
    _rate_limit(agent_id, "heartbeat")
    from maverick import external_agents as xa
    return xa.heartbeat(agent_id, goal_id)


@router.post("/runs/{goal_id}/finish")
async def external_run_finish(
    goal_id: int, payload: ExternalRunFinishIn,
    agent_id: str = Depends(_require_agent),
) -> dict:
    """Close a live run with its outcome, steps, and cost."""
    _rate_limit(agent_id, "runs")
    from maverick import external_agents as xa
    try:
        out = xa.finish_run(agent_id, goal_id, payload.model_dump(),
                            shield=_shield())
    except xa.ExternalAgentsError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"ok": True, **out}


@router.post("/screen")
async def external_screen(
    payload: ExternalScreenIn, agent_id: str = Depends(_require_agent),
) -> dict:
    """Decide a proposed action before the external agent takes it."""
    _rate_limit(agent_id, "screen")
    from maverick import external_agents as xa
    return xa.screen(agent_id, payload.tool, detail=payload.detail,
                     risk=payload.risk or None, shield=_shield())


@router.get("/approvals/{approval_id}")
async def external_approval_status(
    approval_id: int, agent_id: str = Depends(_require_agent),
) -> dict:
    """Poll a parked approval (the human decides in the dashboard queue)."""
    _rate_limit(agent_id, "approvals")
    from maverick import external_agents as xa
    try:
        out = xa.approval_status(approval_id)
    except xa.ExternalAgentsError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    # The approval row itself is cross-checked to this provenance in core;
    # any enrolled agent may poll only external_agents approvals.
    log.debug("external approval poll by %s: %s", agent_id, out["status"])
    return out


#: Hand-maintained on purpose: FastAPI's generated schema spans the whole
#: dashboard; external platforms need exactly these three operations, with
#: nothing to leak. Served without auth like /openapi.json (pure shape, no
#: data) so an operator can import it before any credential exists.
_OPENAPI: dict = {
    "openapi": "3.0.3",
    "info": {
        "title": "Lightwork External Agent Gateway",
        "version": "1",
        "description": (
            "Governance and run accounting for agents built on other "
            "platforms. Authenticate every call with the per-agent rest "
            "bearer minted on the Lightwork /external-agents page."),
    },
    "components": {
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer"},
        },
    },
    "security": [{"bearer": []}],
    "paths": {
        "/api/v1/external/screen": {
            "post": {
                "operationId": "screenAction",
                "summary": "Ask before acting: is this action within my "
                           "ceilings, budget, and approval floor?",
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {
                        "type": "object", "required": ["tool"],
                        "properties": {
                            "tool": {"type": "string",
                                     "description": "Action/tool name"},
                            "detail": {"type": "string",
                                       "description": "What will be done"},
                            "risk": {"type": "string",
                                     "enum": ["low", "medium", "high"]},
                        }}}}},
                "responses": {"200": {"description": "Decision", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "allowed": {"type": "boolean"},
                            "rule": {"type": "string"},
                            "reason": {"type": "string"},
                            "requires_approval": {"type": "boolean"},
                            "approval_id": {"type": "integer"},
                        }}}}}},
            },
        },
        "/api/v1/external/runs": {
            "post": {
                "operationId": "reportRun",
                "summary": "Report a completed run for the Operating Record",
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {
                        "type": "object", "required": ["title", "outcome"],
                        "properties": {
                            "title": {"type": "string"},
                            "outcome": {"type": "string",
                                        "enum": ["success", "failure"]},
                            "summary": {"type": "string"},
                            "steps": {"type": "array",
                                      "items": {"type": "string"}},
                            "cost_dollars": {"type": "number"},
                            "input_tokens": {"type": "integer"},
                            "output_tokens": {"type": "integer"},
                            "tool_calls": {"type": "integer"},
                            "duration_seconds": {"type": "number"},
                            "department": {"type": "string"},
                            "idempotency_key": {
                                "type": "string",
                                "description": "Stable key per logical run; "
                                               "retries return the original "
                                               "record instead of "
                                               "double-counting."},
                        }}}}},
                "responses": {"201": {"description": "Recorded", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "ok": {"type": "boolean"},
                            "goal_id": {"type": "integer"},
                            "over_budget": {"type": "boolean"},
                        }}}}}},
            },
        },
        "/api/v1/external/runs/start": {
            "post": {
                "operationId": "startRun",
                "summary": "Open a LIVE run (visible while working; "
                           "heartbeat required)",
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {
                        "type": "object", "required": ["title"],
                        "properties": {
                            "title": {"type": "string"},
                            "summary": {"type": "string"},
                            "department": {"type": "string"},
                        }}}}},
                "responses": {"201": {"description": "Opened", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "ok": {"type": "boolean"},
                            "goal_id": {"type": "integer"},
                            "heartbeat_seconds": {
                                "type": "number",
                                "description": "Beat at least this often "
                                               "or the run is reclaimed."},
                        }}}}}},
            },
        },
        "/api/v1/external/runs/{goal_id}/heartbeat": {
            "post": {
                "operationId": "heartbeatRun",
                "summary": "Keep a live run alive; continue=false is the "
                           "kill switch — stop working when you see it",
                "parameters": [{"name": "goal_id", "in": "path",
                                "required": True,
                                "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "Liveness", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "continue": {"type": "boolean"},
                            "reason": {"type": "string"},
                        }}}}}},
            },
        },
        "/api/v1/external/runs/{goal_id}/finish": {
            "post": {
                "operationId": "finishRun",
                "summary": "Close a live run with outcome, steps, and cost",
                "parameters": [{"name": "goal_id", "in": "path",
                                "required": True,
                                "schema": {"type": "integer"}}],
                "requestBody": {"required": True, "content": {
                    "application/json": {"schema": {
                        "type": "object", "required": ["outcome"],
                        "properties": {
                            "outcome": {"type": "string",
                                        "enum": ["success", "failure"]},
                            "summary": {"type": "string"},
                            "steps": {"type": "array",
                                      "items": {"type": "string"}},
                            "cost_dollars": {"type": "number"},
                            "input_tokens": {"type": "integer"},
                            "output_tokens": {"type": "integer"},
                            "tool_calls": {"type": "integer"},
                            "duration_seconds": {"type": "number"},
                            "idempotency_key": {"type": "string"},
                        }}}}},
                "responses": {"200": {"description": "Closed", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "ok": {"type": "boolean"},
                            "goal_id": {"type": "integer"},
                            "over_budget": {"type": "boolean"},
                            "over_wall": {"type": "boolean"},
                        }}}}}},
            },
        },
        "/api/v1/external/approvals/{approval_id}": {
            "get": {
                "operationId": "approvalStatus",
                "summary": "Poll a parked high-risk action",
                "parameters": [{"name": "approval_id", "in": "path",
                                "required": True,
                                "schema": {"type": "integer"}}],
                "responses": {"200": {"description": "Status", "content": {
                    "application/json": {"schema": {
                        "type": "object",
                        "properties": {
                            "approval_id": {"type": "integer"},
                            "status": {"type": "string"},
                            "risk": {"type": "string"},
                        }}}}}},
            },
        },
    },
}


@router.get("/openapi.json")
async def external_openapi(request: Request) -> dict:
    """The importable gateway description (Agentforce External Services /
    Bedrock action groups consume this file directly). No bearer required —
    pure shape, no data, and the operator needs it before credentials exist —
    but it still 404s while the plane is off."""
    from maverick import external_agents as xa
    if not xa.enabled():
        raise HTTPException(status_code=404, detail="not found")
    client = request.client.host if request.client else "unknown"
    _rate_limit(client, "openapi")
    return _OPENAPI
