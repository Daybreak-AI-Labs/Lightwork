"""Session + RBAC glue for the FastAPI app.

The session cookie is an HMAC-signed token (see :mod:`.security`) carrying the
staff id, role, and a login **stage** (``totp`` / ``enroll`` / ``full``); only a
``full`` session — password AND TOTP passed — reaches the app. Route protection
is two dependencies: :func:`require_staff` (logged in) and :func:`require_write`
(owner/admin). A 401 from a browser redirects to ``/login``; from the API it's
JSON.
"""
from __future__ import annotations

import sqlite3

from fastapi import HTTPException, Request

from . import security, store
from .models import Staff

SESSION_COOKIE = "dbc_session"


def get_conn(request: Request) -> sqlite3.Connection:
    return request.app.state.conn


def _session(request: Request) -> dict | None:
    return security.verify_session(
        request.cookies.get(SESSION_COOKIE, ""), security.session_key())


def load_staff(request: Request, *, stage: str = "full") -> Staff | None:
    """The Staff behind a session at (at least) the given stage, or None."""
    payload = _session(request)
    if not payload or payload.get("stage") != stage:
        return None
    st = store.get_staff(get_conn(request), int(payload.get("sid", 0)))
    if st is None or st.disabled:
        return None
    return st


def set_session(response, *, staff_id: int, stage: str, role: str = "",
                https: bool = False) -> None:
    token = security.sign_session(
        {"sid": staff_id, "stage": stage, "role": role}, security.session_key(),
        ttl=12 * 3600 if stage == "full" else 600)
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=https,
        max_age=12 * 3600 if stage == "full" else 600, path="/")


def clear_session(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


# ---- FastAPI dependencies --------------------------------------------------

def require_staff(request: Request) -> Staff:
    st = load_staff(request)
    if st is None:
        # 401; the app's handler redirects browsers to /login.
        raise HTTPException(status_code=401, detail="login required")
    return st


def require_write(request: Request) -> Staff:
    st = require_staff(request)
    if not st.can_write:
        raise HTTPException(status_code=403,
                            detail="requires owner or admin role")
    return st
