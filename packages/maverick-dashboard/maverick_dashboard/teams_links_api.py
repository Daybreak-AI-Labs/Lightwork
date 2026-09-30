"""Channels page: link Microsoft Teams accounts to Lightwork users.

An administrator maps a person's Microsoft Entra object ID (or sign-in address)
to a Lightwork user and says whether that person may decide approvals from
Teams, or issues a one-time code the person sends to the bot as
``link <code>``. Global admin only, same-origin checked; every change is
audited by :mod:`maverick.teams_identity`.
"""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_global_permission

router = APIRouter()


def _admin(request: Request) -> str:
    from . import app as app_module
    app_module._require_same_origin(request)
    require_global_permission(request, "admin")
    return caller_principal(request) or "operator"


def _back(result: str, **params: str) -> RedirectResponse:
    query = "".join(f"&{key}={quote(value)}" for key, value in params.items() if value)
    return RedirectResponse(f"/channels?saved={result}{query}&channel=teams#channel-teams",
                            status_code=303)


@router.post("/channels/teams/link")
async def teams_link(request: Request, teams_id: str = Form(""), principal: str = Form(""),
                     approver: str = Form("")) -> RedirectResponse:
    actor = _admin(request)
    from maverick.teams_identity import TeamsIdentityError, link

    try:
        await run_in_threadpool(link, teams_id, principal, approver=approver != "",
                                actor=actor)
    except TeamsIdentityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _back("linked")


@router.post("/channels/teams/unlink")
async def teams_unlink(request: Request, teams_id: str = Form("")) -> RedirectResponse:
    actor = _admin(request)
    from maverick.teams_identity import TeamsIdentityError, unlink

    try:
        removed = await run_in_threadpool(unlink, teams_id, actor=actor)
    except TeamsIdentityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="That Teams account isn't linked.")
    return _back("unlinked")


@router.post("/channels/teams/link-code")
async def teams_link_code(request: Request, principal: str = Form(""),
                          approver: str = Form("")) -> RedirectResponse:
    actor = _admin(request)
    from maverick.teams_identity import TeamsIdentityError, issue_link_code

    try:
        code = await run_in_threadpool(issue_link_code, principal, approver=approver != "",
                                       actor=actor)
    except TeamsIdentityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _back("link_code", code=code)


def page_context(code: str = "") -> dict:
    """What the Channels page shows under Microsoft Teams."""
    from maverick.teams_identity import TeamsIdentityError, list_links

    from . import rbac

    try:
        links = list_links()
    except TeamsIdentityError:
        links = []
    try:
        users = sorted(rbac.list_users())
    except Exception:  # noqa: BLE001 -- the roster is a convenience list only
        users = []
    return {"teams_links": links, "teams_users": users,
            "teams_link_code": code if code.isalnum() and len(code) <= 16 else ""}


__all__ = ["page_context", "router"]
