"""Practice mode in the dashboard: the switch, the marker and the guard.

A person turns practice on for their own browser session. While it is on,
every request they make runs in practice (:mod:`maverick.practice`): tasks,
results and approvals use the separate practice workspace, and anything that
would leave Lightwork is recorded instead. Changes to live settings, agents,
people or connections are refused until they choose Back to live, because
those would affect real work.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

COOKIE = "lw_practice"
router = APIRouter(prefix="/api/v1/practice", tags=["practice"])

# Changes a practising person may make: their practice tasks, answers,
# approvals and attachments, signing in and out, and the switch itself.
ALLOWED_CHANGES = (
    "/chat/", "/goals/", "/api/goal/", "/api/v1/goals", "/api/v1/approvals", "/approvals/",
    "/answer-receipts/", "/attachments/", "/first-outcome/", "/start", "/auth/",
    "/api/v1/practice", "/api/v1/preferences/display",
)
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
REFUSED = ("This would change live work, so it can't be done in practice mode. "
           "Choose Back to live, then try again.")


def wants_practice(request: Request) -> bool:
    return request.cookies.get(COOKIE) == "1"


def change_allowed(method: str, path: str) -> bool:
    return method.upper() in _SAFE_METHODS or path.startswith(ALLOWED_CHANGES)


async def middleware(request: Request, call_next):
    """Run a practising person's requests in practice, and keep live work untouched."""
    if not wants_practice(request):
        return await call_next(request)
    from maverick import practice

    if await run_in_threadpool(practice.unavailable_reason):
        return await call_next(request)  # practice switched off since: the person is live
    if not change_allowed(request.method, request.url.path):
        return JSONResponse({"detail": REFUSED}, status_code=409,
                            headers={"X-Lightwork-Reason": "practice"})
    request.state.practice = True
    token = practice.activate(True)
    try:
        return await call_next(request)
    finally:
        practice.reset(token)


def context(request: Request) -> dict:
    """Template flags for the practice marker on every screen and the switch."""
    from maverick import practice

    try:
        available = not practice.unavailable_reason()
    except Exception:  # pragma: no cover -- shell rendering stays fail-soft: no switch offered
        available = False
    return {"practice_mode": bool(getattr(request.state, "practice", False)),
            "practice_available": available}


class PracticeSwitch(BaseModel):
    on: bool


def _state(request: Request, on: bool) -> dict:
    from maverick import practice

    reason = practice.unavailable_reason()
    return {"on": on and not reason, "available": not reason, "reason": reason}


@router.get("")
async def practice_state(request: Request) -> dict:
    require_permission(request, "view")
    from maverick import practice

    on = bool(getattr(request.state, "practice", False))
    state = await run_in_threadpool(_state, request, on)
    state["outbox"] = await run_in_threadpool(practice.outbox, 100) if on else []
    return state


@router.post("")
async def switch_practice(request: Request, body: PracticeSwitch) -> JSONResponse:
    """Start or leave practice for this browser session."""
    require_permission(request, "operate")
    state = await run_in_threadpool(_state, request, body.on)
    if body.on and not state["available"]:
        return JSONResponse({"detail": state["reason"]}, status_code=409)
    response = JSONResponse(state)
    if body.on:
        response.set_cookie(COOKIE, "1", httponly=True, samesite="lax", path="/",
                            secure=request.url.scheme == "https")
    else:
        response.delete_cookie(COOKIE, path="/")
    return response


__all__ = ["ALLOWED_CHANGES", "COOKIE", "REFUSED", "change_allowed", "context", "middleware", "router",
           "wants_practice"]
