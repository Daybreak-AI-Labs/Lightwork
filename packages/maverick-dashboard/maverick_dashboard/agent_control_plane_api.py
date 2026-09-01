"""Closed operator API for the Agent Control Plane.

Reads (status, roster, ledger) are open to viewers/auditors; the verbs mirror
``maverick control-plane`` and carry the caller's identity as the actor on the
signed audit row. The plane's own gate (``[control_plane] enable``) is
enforced by the core module, so a disabled deployment answers 403 here.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from maverick import agent_control_plane as acp
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_permission

Principal = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$"),
]
Reason = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=500)]
ToolName = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=64,
                           pattern=r"^[A-Za-z0-9_.-]{1,64}$"),
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReasonIn(_StrictModel):
    reason: Reason


class AttenuateIn(_StrictModel):
    deny_tools: list[ToolName] = Field(default_factory=list, max_length=64)
    max_risk: str | None = Field(default=None, pattern=r"^(low|medium|high)$")
    max_dollars: float | None = Field(default=None, gt=0, le=1_000_000)
    max_wall_seconds: float | None = Field(default=None, gt=0, le=31_536_000)


def _actor(request: Request) -> str:
    subject = caller_principal(request) or "authorized-local-operator"
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 512:
        raise HTTPException(status_code=401, detail="an authenticated principal is required")
    return subject.strip()


async def _call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except acp.ControlPlaneDisabled as exc:
        raise HTTPException(status_code=403, detail="control_plane_disabled") from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except acp.ControlPlaneError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=503,
                            detail="control plane dependency is unavailable") from exc


def create_agent_control_plane_router() -> APIRouter:
    router = APIRouter(prefix="/agent-control-plane", tags=["agent-control-plane"])

    @router.get("/status")
    async def status(request: Request):
        require_permission(request, "view")
        return await _call(acp.status)

    @router.get("/agents")
    async def agents(request: Request):
        require_permission(request, "view")
        rows = await _call(acp.roster)
        items = [r.to_dict() for r in rows]
        return {"items": items, "count": len(items)}

    @router.get("/agents/{principal}/ledger")
    async def agent_ledger(request: Request, principal: Principal,
                           limit: int = Query(default=50, ge=1, le=500)):
        require_permission(request, "audit")
        rows = await _call(acp.ledger, principal, limit=limit)
        return {"principal": principal, "items": rows, "count": len(rows)}

    @router.post("/agents/{principal}/suspend")
    async def suspend(request: Request, principal: Principal, body: ReasonIn):
        require_permission(request, "operate")
        result = await _call(acp.suspend, principal, reason=body.reason,
                             actor=_actor(request))
        return result.to_dict()

    @router.post("/agents/{principal}/resume")
    async def resume(request: Request, principal: Principal):
        require_permission(request, "operate")
        result = await _call(acp.resume, principal, actor=_actor(request))
        return result.to_dict()

    @router.post("/agents/{principal}/contain")
    async def contain(request: Request, principal: Principal, body: ReasonIn):
        require_permission(request, "admin")
        result = await _call(acp.contain, principal, reason=body.reason,
                             actor=_actor(request))
        return result.to_dict()

    @router.post("/agents/{principal}/release")
    async def release(request: Request, principal: Principal):
        require_permission(request, "admin")
        result = await _call(acp.release, principal, actor=_actor(request))
        return result.to_dict()

    @router.post("/agents/{principal}/attenuate")
    async def attenuate(request: Request, principal: Principal, body: AttenuateIn):
        require_permission(request, "operate")
        result = await _call(
            acp.attenuate, principal, deny_tools=body.deny_tools,
            max_risk=body.max_risk, max_dollars=body.max_dollars,
            max_wall_seconds=body.max_wall_seconds, actor=_actor(request))
        return result.to_dict()

    @router.post("/halt")
    async def halt(request: Request, body: ReasonIn):
        require_permission(request, "admin")
        result = await _call(acp.halt_all, body.reason, actor=_actor(request))
        return result.to_dict()

    @router.post("/clear-halt")
    async def clear_halt(request: Request):
        require_permission(request, "admin")
        result = await _call(acp.clear_halt, actor=_actor(request))
        return result.to_dict()

    return router


router = create_agent_control_plane_router()

__all__ = ["create_agent_control_plane_router", "router"]
