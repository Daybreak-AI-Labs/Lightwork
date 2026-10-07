"""When the browser's sign-in ends, and staying signed in (WCAG 2.2.1).

Pages carry the signed session's end time so ``lightwork-session.js`` can warn
before it ends and offer to stay signed in. A local (passphrase) sign-in is
kept going here: the session is replaced by a new one of the usual length and
the old one is ended, so the Sessions page still lists exactly one. A company
(OIDC or SAML) sign-in is never extended by Lightwork: its length is the
identity provider's policy, so "Stay signed in" signs in there again and comes
back to the same page.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()
_HEADERS = {"Cache-Control": "no-store"}


def _payload(request: Request) -> tuple[dict, str] | None:
    """The verified session cookie and how it was signed ("local" or "company")."""
    from maverick.web_session import verify_session

    from .oidc_login import SESSION_COOKIE

    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    try:
        from . import invites

        if invites.local_login_mode():
            payload = verify_session(raw, invites.local_session_secret())
            return (payload, "local") if isinstance(payload, dict) else None
        from maverick.oidc import load_oidc_config

        secret = load_oidc_config().session_secret
        payload = verify_session(raw, secret) if secret else None
        return (payload, "company") if isinstance(payload, dict) else None
    except Exception:  # an unreadable sign-in configuration shows no timer
        return None


def _ends(payload: dict) -> int | None:
    value = payload.get("exp")
    return int(value) if isinstance(value, (int, float)) and value > time.time() else None


def sign_in_path() -> str:
    """Where this installation signs people in (``return_to`` is added by the page)."""
    try:
        from maverick.oidc import login_enabled

        if login_enabled():
            return "/auth/login"
        from .saml import saml_enabled

        if saml_enabled():
            return "/saml/login"
    except Exception:
        pass
    return "/auth/local-login"


def page_session(request: Request) -> dict | None:
    """For the page shell: the session's end, the server clock and how to keep it."""
    found = _payload(request)
    ends = _ends(found[0]) if found else None
    if ends is None:
        return None
    return {"expires_at": ends, "now": int(time.time()),
            "renew": "renew" if found[1] == "local" else "sign-in", "sign_in": sign_in_path()}


@router.get("/auth/session/status")
def session_status(request: Request) -> JSONResponse:
    """The current end time (another tab may have kept the session going)."""
    found = page_session(request)
    if found is None:
        return JSONResponse({"signed_in": False}, headers=_HEADERS)
    return JSONResponse({"signed_in": True, "expires_at": found["expires_at"], "now": found["now"],
                         "renew": found["renew"]}, headers=_HEADERS)


@router.post("/auth/session/renew")
def renew_session(request: Request) -> JSONResponse:
    """Replace a live local sign-in with a new one; end the old one."""
    from .app import _require_same_origin
    from .invites import local_session_principal, mint_local_session, session_ttl_seconds
    from .oidc_login import SESSION_COOKIE, _cookie_secure, _set_cookie

    _require_same_origin(request)
    found = _payload(request)
    if found is None or _ends(found[0]) is None:
        raise HTTPException(401, "Your sign-in has ended. Sign in again to continue.")
    if found[1] != "local":
        return JSONResponse({"renew": "sign-in", "sign_in": sign_in_path()}, status_code=409, headers=_HEADERS)
    principal = local_session_principal(request)
    if principal is None:
        raise HTTPException(401, "Your sign-in has ended. Sign in again to continue.")
    token = mint_local_session(principal.sub, request=request)
    old_sid = (principal.claims or {}).get("sid") or ""
    if old_sid:
        from . import session_registry

        session_registry.revoke(old_sid, by="renewed")
    from maverick.web_session import verify_session

    from .invites import local_session_secret

    ttl = session_ttl_seconds()
    minted = verify_session(token, local_session_secret()) or {}
    response = JSONResponse({"expires_at": int(minted.get("exp") or time.time() + ttl)}, headers=_HEADERS)
    _set_cookie(response, SESSION_COOKIE, token, max_age=ttl, secure=_cookie_secure(request))
    return response
