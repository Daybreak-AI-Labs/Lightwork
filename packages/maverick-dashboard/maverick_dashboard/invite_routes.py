"""Invite-link accept flow: ``GET/POST /auth/invite/{token}``.

Security mechanics (see :mod:`maverick_dashboard.invites` for the store):

- **Scanner-proof**: the GET only *peeks* (renders a confirmation page); the
  POST consumes. Corporate mail scanners that prefetch every link therefore
  can't burn a single-use invite.
- **CSRF on the consume**: the POST requires a same-origin Origin/Referer
  (this path is exempt from the app-wide auth/CSRF middleware because the
  invitee has no credential yet — the token in the path is the credential).
- **SSO mode** (OIDC browser login configured): accepting requires signing in
  first — the page links to ``/auth/login?return_to=<this invite>`` — and the
  invite then binds the IdP-verified principal to the invited role. The
  session comes from the IdP; the invite never mints one and no Lightwork
  passphrase is asked for.
- **Local mode** (no IdP): accepting sets the colleague's passphrase (the
  owner's rules, hashing and lockout; see :mod:`local_passphrases`), mints a
  signed ``mvk_session`` cookie for ``user:<invited email>`` and binds the
  role. The passphrase is saved under the same lock that marks the link used,
  so a link is never used up without it: after signing out, the colleague
  signs in again with their email and passphrase.

Every route 404s while ``[dashboard] invites`` is off (fail-closed default).
"""
from __future__ import annotations

import logging
from urllib.parse import parse_qs, quote, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from . import invites, local_passphrases

log = logging.getLogger(__name__)

router = APIRouter()

# What each built-in role can do, in the words the People page uses.
ROLE_HELP = {
    "viewer": "look at dashboards and results but change nothing",
    "operator": "run work and approve actions",
    "auditor": "review the activity record and results without changing anything",
    "admin": "manage people, settings and spending",
}


def _page(request: Request, state: str, *, status: int = 200, inv=None, token: str = "",
          message: str = "", principal: str = "") -> HTMLResponse:
    """The invitation page, in the shared public shell (no app nav or session)."""
    from .app import templates

    invite = None
    if inv is not None:
        invite = {"email": inv.email, "role": inv.role, "role_help": ROLE_HELP.get(inv.role, ""),
                  "workspace": inv.tenant, "token": token,
                  "sign_in_href": "/auth/login?return_to=" + quote(f"/auth/invite/{token}", safe="/")}
    response = templates.TemplateResponse(
        request, "invite_accept.html",
        {"state": state, "invite": invite, "message": message, "principal": principal,
         "passphrase_sign_in": local_passphrases.available()},
        status_code=status)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    # The accept form needs its same-origin Origin header (see local_owner_routes).
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _gone(request: Request) -> HTMLResponse:
    return _page(request, "gone", status=410)


def _same_origin(request: Request) -> bool:
    """Fail-closed same-origin check for the consuming POST: the accept form is
    served by this host, so a genuine submit carries a matching Origin (or
    Referer). Cross-site or headerless posts are rejected."""
    expected = request.url.netloc
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            return urlparse(value).netloc == expected
    return False


def _session_principal(request: Request):
    """The signed-in identity, from whichever session mode is active."""
    if invites.local_login_mode():
        return invites.local_session_principal(request)
    try:
        from .oidc_login import _principal_from_request_session
        return _principal_from_request_session(request)
    except Exception:  # pragma: no cover - login module import never breaks invites
        return None


def _bind_role(principal: str, inv) -> None:
    """Grant the invited role: inside the invite's workspace when it names one
    (a client's first administrator), otherwise dashboard-wide."""
    from . import rbac
    if inv.tenant:
        rbac.set_tenant_role(inv.tenant, principal, inv.role)
    else:
        rbac.set_role(principal, inv.role)


@router.get("/auth/invite/{token}")
async def invite_confirm(request: Request, token: str):
    """Peek-only confirmation page — inert for mail-scanner prefetches."""
    if not invites.invites_enabled():
        return _page(request, "not_found", status=404)
    inv = invites.peek_invite(token)
    if inv is None:
        return _gone(request)
    if invites.local_login_mode():
        return _page(request, "local", inv=inv, token=token)
    principal = _session_principal(request)
    if principal is None:
        # SSO mode, not signed in yet: sign in first, then land back here.
        return _page(request, "sso_sign_in", inv=inv, token=token)
    return _page(request, "sso_accept", inv=inv, token=token, principal=principal.sub)


async def _passphrase_fields(request: Request) -> tuple[str, str]:
    """The two passphrase boxes from the accept form (bounded, one value each)."""
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Open the invitation link and submit the form from that page.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, "The form is larger than this page sends.")
    data = parse_qs(bytes(body).decode("utf-8"), max_num_fields=3, keep_blank_values=True)
    if set(data) - {"password", "password2"} or any(len(v) != 1 for v in data.values()):
        raise HTTPException(422, "Submit one value for each field.")
    password, password2 = data.get("password", [""])[0], data.get("password2", [""])[0]
    if password != password2:
        raise HTTPException(422, "The passphrases do not match. Type the same passphrase in both boxes.")
    return password, password2


async def _accept_locally(request: Request, token: str) -> HTMLResponse | RedirectResponse:
    """Local mode: set the passphrase, use up the link, sign the browser in."""
    inv = invites.peek_invite(token)
    if inv is None:
        return _gone(request)
    try:
        password, _ = await _passphrase_fields(request)
        # Slow on purpose (scrypt): off the event loop, before the link is used.
        prepared = await run_in_threadpool(local_passphrases.prepare, inv.email, password)
    except (UnicodeError, ValueError):
        return _page(request, "local", status=422, inv=inv, token=token,
                     message="Check the passphrase and try again.")
    except HTTPException as exc:
        return _page(request, "local", status=exc.status_code, inv=inv, token=token,
                     message=str(exc.detail))
    try:
        used = await run_in_threadpool(
            invites.consume_invite, token,
            before_commit=lambda current: local_passphrases.store(prepared, tenant=current.tenant))
    except HTTPException as exc:
        return _page(request, "local", status=exc.status_code, inv=inv, token=token,
                     message=str(exc.detail))
    if used is None:
        return _gone(request)
    try:
        _bind_role(f"user:{used.email}", used)
    except ValueError:  # pragma: no cover - role validated at mint time
        return _gone(request)
    log.info("invite accepted (local): %s as %s", used.email, used.role)
    response = RedirectResponse("/", status_code=303)
    from .oidc_login import _cookie_secure, _set_cookie
    _set_cookie(response, "mvk_session", invites.mint_local_session(used.email, request=request),
                max_age=invites.session_ttl_seconds(),
                secure=_cookie_secure(request))
    return response


@router.post("/auth/invite/{token}")
async def invite_accept(request: Request, token: str):
    """Consume the invite: bind the role and (in local mode) sign the browser in."""
    if not invites.invites_enabled():
        return _page(request, "not_found", status=404)
    if not _same_origin(request):
        return _page(request, "blocked", status=400)
    if invites.local_login_mode():
        return await _accept_locally(request, token)
    principal = _session_principal(request)
    if principal is None:
        return _page(request, "sign_in_required", status=401)
    inv = invites.consume_invite(token, used_by=principal.principal)
    if inv is None:
        return _gone(request)
    try:
        _bind_role(principal.principal, inv)
    except ValueError:  # pragma: no cover - role validated at mint time
        return _gone(request)
    log.info("invite accepted (sso): %s bound to %s (invite for %s)",
             principal.principal, inv.role, inv.email)
    return RedirectResponse("/", status_code=303)
