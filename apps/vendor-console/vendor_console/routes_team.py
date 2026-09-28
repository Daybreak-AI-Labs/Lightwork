"""Team routes: who can use the console, and in which role.

Owners and administrators can see the team; only an owner can add people,
change roles, or turn sign-in off and on. Every change is audited. A new person
gets a single-use set-up link (shown once to the owner, stored only as a hash,
valid for three days) where they choose their own password and then set up
two-step sign-in. The owner never knows their password. Nobody can
change their own role or access, and the last active owner can't be removed.
"""
from __future__ import annotations

import secrets
import sqlite3
import time

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from starlette.exceptions import HTTPException

from . import audit, store
from .auth import require_owner, require_write
from .models import ROLES, Staff
from .render import render
from .util import hash_token, no_store

router = APIRouter()

LINK_HOURS = 72


def _page(request: Request, staff: Staff, *, error: str = "", notice: str = "",
          form: dict | None = None, status_code: int = 200):
    conn = request.app.state.conn
    resp = render(request, "team.html",
                  {"staff": staff, "team": store.list_staff(conn), "roles": ROLES,
                   "error": error, "notice": notice, "form": form or {},
                   "section": "team"})
    resp.status_code = status_code
    return resp


def _member(conn: sqlite3.Connection, staff_id: int) -> Staff:
    member = store.get_staff(conn, staff_id)
    if member is None:
        raise HTTPException(status_code=404, detail="no such team member")
    return member


@router.get("/team")
def team(request: Request, notice: str = "", staff: Staff = Depends(require_write)):
    messages = {"role": "Role changed. They will need to sign in again.",
                "off": "Access turned off. They have been signed out everywhere.",
                "on": "Access turned back on."}
    return _page(request, staff, notice=messages.get(notice, ""))


def _issue_link(request: Request, staff: Staff, member: Staff, *, reset: bool):
    """Create a set-up link, retire older ones, audit it (never the token) and
    show it once."""
    conn = request.app.state.conn
    token = secrets.token_urlsafe(32)
    expires = time.time() + LINK_HOURS * 3600
    store.create_invite(conn, staff_id=member.id, token_hash=hash_token(token),
                        expires_at=expires, created_by=staff.email, reset=reset)
    audit.record(conn, actor=staff.email, action="staff.invite", target=member.email,
                 detail={"expires_at": int(expires), "reissued": reset})
    return no_store(render(request, "team_link.html", {
        "staff": staff, "section": "team", "member": member, "reissued": reset,
        "days": LINK_HOURS // 24,
        "link": str(request.base_url).rstrip("/") + "/welcome/" + token}))


@router.post("/team")
def team_add(request: Request, email: str = Form(...), name: str = Form(""),
             role: str = Form("viewer"), staff: Staff = Depends(require_owner)):
    conn = request.app.state.conn
    form = {"email": email, "name": name, "role": role}
    email = email.strip().lower()
    if "@" not in email or len(email) > 254:
        return _page(request, staff, error="Enter the person's work email address.",
                     form=form, status_code=400)
    if role not in ROLES:
        return _page(request, staff, error="Choose a role from the list.", form=form,
                     status_code=400)
    if store.get_staff_by_email(conn, email) is not None:
        return _page(request, staff, error="Someone with that email is already on the team.",
                     form=form, status_code=400)
    try:
        # No password: nobody can sign in as this person until they use their link.
        member_id = store.create_staff(conn, email=email, name=name.strip(), role=role,
                                       pw_hash="")
    except sqlite3.IntegrityError:
        return _page(request, staff, error="Someone with that email is already on the team.",
                     form=form, status_code=400)
    audit.record(conn, actor=staff.email, action="staff.create", target=email,
                 detail={"role": role})
    return _issue_link(request, staff, _member(conn, member_id), reset=False)


@router.get("/team/{staff_id}/link")
def team_link_confirm(request: Request, staff_id: int, staff: Staff = Depends(require_owner)):
    member = _member(request.app.state.conn, staff_id)
    who = member.name or member.email
    return render(request, "confirm.html", {
        "staff": staff, "section": "team",
        "title": f"Send {who} a new set-up link?",
        "consequence": (f"Any earlier link for {who} stops working, and if they already chose "
                        "a password it is cleared. They finish setting up with the new link."),
        "action": f"/team/{staff_id}/link", "hidden": {},
        "button": "Create new link", "danger": False, "cancel": "/team"})


@router.post("/team/{staff_id}/link")
def team_link(request: Request, staff_id: int, staff: Staff = Depends(require_owner)):
    conn = request.app.state.conn
    member = _member(conn, staff_id)
    if member.id == staff.id:
        return _page(request, staff, status_code=400,
                     error="You can't send yourself a set-up link. Use Change my password instead.")
    if member.totp_enrolled:
        return _page(request, staff, status_code=400,
                     error="This person has already finished setting up their account.")
    if member.disabled:
        return _page(request, staff, status_code=400,
                     error="Turn this person's access back on before sending a set-up link.")
    return _issue_link(request, staff, member, reset=True)


def _guard_change(conn: sqlite3.Connection, staff: Staff, member: Staff, *,
                  removes_owner: bool) -> str:
    if member.id == staff.id:
        return "You can't change your own role or access. Ask another owner."
    if removes_owner and member.role == "owner" and not member.disabled \
            and store.count_active_owners(conn) <= 1:
        return "This is the last owner. Make someone else an owner first."
    return ""


@router.post("/team/{staff_id}/role")
def team_role(request: Request, staff_id: int, role: str = Form(...),
              staff: Staff = Depends(require_owner)):
    conn = request.app.state.conn
    member = _member(conn, staff_id)
    if role not in ROLES:
        return _page(request, staff, error="Choose a role from the list.", status_code=400)
    if role == member.role:
        return RedirectResponse("/team", status_code=303)
    problem = _guard_change(conn, staff, member, removes_owner=role != "owner")
    if problem:
        return _page(request, staff, error=problem, status_code=400)
    store.set_staff_role(conn, staff_id, role)
    audit.record(conn, actor=staff.email, action="staff.role", target=member.email,
                 detail={"from": member.role, "to": role})
    return RedirectResponse("/team?notice=role", status_code=303)


@router.get("/team/{staff_id}/access")
def team_access_confirm(request: Request, staff_id: int,
                        staff: Staff = Depends(require_owner)):
    conn = request.app.state.conn
    member = _member(conn, staff_id)
    turning_off = not member.disabled
    who = member.name or member.email
    return render(request, "confirm.html", {
        "staff": staff, "section": "team",
        "title": f"Turn off access for {who}?" if turning_off else f"Turn access back on for {who}?",
        "consequence": (f"{who} will be signed out everywhere and won't be able to sign in "
                        "until an owner turns access back on. Their past activity stays in the audit log.")
                       if turning_off else
                       f"{who} will be able to sign in again with their existing password and code.",
        "action": f"/team/{staff_id}/access",
        "hidden": {"disabled": "1" if turning_off else "0"},
        "button": "Turn off access" if turning_off else "Turn access back on",
        "danger": turning_off, "cancel": "/team"})


@router.post("/team/{staff_id}/access")
def team_access(request: Request, staff_id: int, disabled: str = Form(...),
                staff: Staff = Depends(require_owner)):
    conn = request.app.state.conn
    member = _member(conn, staff_id)
    turn_off = disabled == "1"
    problem = _guard_change(conn, staff, member, removes_owner=turn_off)
    if problem:
        return _page(request, staff, error=problem, status_code=400)
    store.set_staff_disabled(conn, staff_id, turn_off)
    audit.record(conn, actor=staff.email, action="staff.disable" if turn_off else "staff.enable",
                 target=member.email)
    return RedirectResponse(f"/team?notice={'off' if turn_off else 'on'}", status_code=303)
