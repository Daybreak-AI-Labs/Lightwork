"""Employee requests to the actual policy owner; reviews never change grants."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from . import permission_requests as service

router = APIRouter(tags=["permission-access-requests"])


class ExplainIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["permission", "tool"]
    capability: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")


class SubmitIn(ExplainIn):
    reason: str = Field(min_length=10, max_length=1000)
    snapshot: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm_shared_with_reviewer: Literal[True]


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    decision: Literal["reviewing", "deny", "confirm_effective"]
    note: str = Field(min_length=10, max_length=1000)


def authority(request, *, write=False):
    auth.require_permission(request, "view")
    actor = auth.caller_principal(request)
    if not actor:
        raise HTTPException(403, "Use your named account for permission requests.")
    if write:
        from .app import _require_same_origin
        _require_same_origin(request)
        if not service.enabled():
            raise HTTPException(403, "Permission requests are disabled by your administrator.")
    return actor


def _call(operation):
    from maverick.privacy_ops import RecordConflict
    try:
        return operation()
    except RecordConflict as exc:
        raise HTTPException(409, "This request changed. Refresh before reviewing it.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, RuntimeError) as exc:
        raise HTTPException(503, "The current policy or saved outcome could not be confirmed. Refresh before retrying.") from exc


def state(request):
    actor = authority(request)
    return {"enabled": service.enabled(), "actor": actor,
            "examples": [service.explain(actor, "permission", value) for value in sorted(service.PERMISSIONS)] +
                        [service.explain(actor, "tool", value) for value in ("read_file", "write_file", "shell")],
            "records": [service.public(request, row) for row in service.STORE.list(limit=10001) if service.visible(request, row)]}


@router.get("/access-requests", response_class=HTMLResponse)
def page(request: Request):
    authority(request)
    from .app import templates
    return templates.TemplateResponse(request, "permission_requests.html", {})


@router.get("/api/v1/access-requests")
def status(request: Request):
    return _call(lambda: state(request))


@router.post("/api/v1/access-requests/explain")
def explain(request: Request, body: ExplainIn):
    actor = authority(request)
    return _call(lambda: service.explain(actor, body.kind, body.capability))


@router.post("/api/v1/access-requests")
def submit(request: Request, body: SubmitIn):
    return _call(lambda: service.submit(request, body.kind, body.capability, body.reason, body.snapshot))


@router.post("/api/v1/access-requests/{identifier}/decision")
def decide(request: Request, identifier: str, body: DecisionIn):
    return _call(lambda: service.decide(request, identifier, body.expected_revision, body.decision, body.note))
