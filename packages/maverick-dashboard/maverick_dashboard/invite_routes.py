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
  current signed session must assert the invited email before the role is
  bound to that principal. SAML-only deployments use ``/saml/login``. The
  session comes from the IdP; the invite never mints one.
- **Local mode** (no IdP): accepting mints a signed ``mvk_session`` cookie for
  ``user:<invited email>`` and binds the role. The link is the credential —
  single-use, expiring, revocable.

Every route 404s while ``[dashboard] invites`` is off (fail-closed default).
"""
from __future__ import annotations

import html
import logging
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import invites

log = logging.getLogger(__name__)

router = APIRouter()


class _InviteBindFailed(RuntimeError):
    """The one-time link was redeemed, but the role store refused the grant."""


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    """A small self-contained page (no app nav/session context needed)."""
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)} · Lightwork</title>
<style>
  body{{font:15px/1.5 system-ui,sans-serif;display:flex;min-height:100vh;margin:0;
       align-items:center;justify-content:center;background:#0d1117;color:#e6edf3}}
  .card{{background:#161b22;border:1px solid #30363d;border-radius:14px;
        padding:34px 32px;max-width:420px}}
  h1{{font-size:19px;margin:0 0 10px}} p{{color:#9da7b3;margin:8px 0}}
  code{{color:#e6edf3}} a{{color:#58a6ff}}
  button{{background:#d4a72c;color:#3a2a06;border:none;border-radius:8px;
         padding:10px 18px;font:inherit;font-weight:700;cursor:pointer;margin-top:14px}}
</style></head><body><div class="card"><h1>{html.escape(title)}</h1>{body}</div>
</body></html>""", status_code=status)


def _gone() -> HTMLResponse:
    return _page("Invitation not available",
                 "<p>This invitation link is invalid, expired, already used, or "
                 "revoked.</p><p>Ask your administrator to send a new one.</p>",
                 status=410)


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
        rbac.set_tenant_role(inv.tenant, principal, inv.role, actor=inv.created_by)
    else:
        rbac.set_role(principal, inv.role, actor=inv.created_by)


def _workspace_invite_allowed(inv, principal: str) -> bool:
    """Recheck a pending workspace grant before it can change membership."""
    if not inv.tenant:
        return True
    from maverick.paths import tenant_by_user_enabled
    from maverick.tenant.registry import get_tenant

    from . import rbac

    workspace = get_tenant(inv.tenant)
    if tenant_by_user_enabled() or workspace is None or not workspace.active:
        return False
    members = rbac.list_tenant_roles(inv.tenant)
    current = members.get(principal)
    invited_permissions = rbac.permissions_for(inv.role)
    if inv.workspace_admin_invite:
        issuer_permissions = rbac.permissions_for(members.get(inv.created_by))
        if ("admin" not in issuer_permissions
                or not invited_permissions.issubset(issuer_permissions)
                or (current is not None
                    and not rbac.permissions_for(current).issubset(issuer_permissions))
                or (principal == inv.created_by and "admin" not in invited_permissions)):
            return False
    if (current is not None and "admin" in rbac.permissions_for(current)
            and "admin" not in invited_permissions
            and sum("admin" in rbac.permissions_for(role) for role in members.values()) <= 1):
        return False
    return True


def _global_invite_issuer_allowed(inv) -> bool:
    """A named installation admin must still have that authority at acceptance.

    Existing records already carry ``created_by``. ``caller_principal`` uses
    ``VerifiedPrincipal.principal`` (``user:<sub>``) for OIDC, SAML and proxy
    identities. Principal-less local and static-token invites have no durable
    person to recheck, so preserve their existing one-time-link contract.
    Scoped workspace issuers are checked by ``_workspace_invite_allowed``.
    """
    issuer = inv.created_by
    if inv.workspace_admin_invite or not issuer.startswith("user:"):
        return True
    from . import auth, rbac, scim_groups

    try:
        if scim_groups.active_for_principal(issuer) is False:
            return False
        permissions = rbac.permissions_for(auth.global_role_for_principal(issuer))
        return "admin" in permissions and rbac.permissions_for(inv.role).issubset(permissions)
    except Exception:
        return False


def _consume_and_bind(token: str, principal=None):
    """Redeem under the role-change fence, including the issuing admin check."""
    from maverick.authority_effects import authority_effect_fence

    with authority_effect_fence():
        pending = invites.peek_invite(token)
        if pending is None:
            return None
        target = principal.principal if principal is not None else f"user:{pending.email}"
        if ((principal is not None and not _matches_invited_account(principal, pending))
                or not _global_invite_issuer_allowed(pending)
                or not _workspace_invite_allowed(pending, target)):
            return None
        consumed = invites.consume_invite(token, used_by=target)
        if consumed is None:
            return None
        try:
            _bind_role(target, consumed)
        except Exception as exc:
            # Two distinct control-plane stores cannot commit atomically. Burn
            # the bearer token on uncertainty rather than let it grant another
            # identity; tell the person to request a replacement link.
            log.exception("invitation role binding failed after redemption")
            raise _InviteBindFailed from exc
        return consumed


def _matches_invited_account(principal, inv) -> bool:
    """An SSO invite can bind only the email asserted for this login."""
    from .oidc_login import _normalized_invite_email

    claims = principal.claims or {}
    asserted = _normalized_invite_email(claims.get("idp_email"))
    return bool(asserted) and asserted == inv.email.casefold()


def _wrong_account() -> HTMLResponse:
    return _page("Use the invited account",
                 "<p>Sign in with the email address on this invitation. Your "
                 "company sign-in must share that address with Lightwork. "
                 "The invitation link has not been used.</p>", status=403)


def _bind_failed() -> HTMLResponse:
    return _page("Invitation could not be completed",
                 "<p>Access could not be saved. Ask your workspace administrator "
                 "for a new invitation link.</p>", status=503)


def _scope_label(inv) -> str:
    if not inv.tenant:
        return "this Lightwork dashboard"
    from maverick.tenant.registry import get_tenant

    workspace = get_tenant(inv.tenant)
    name = (workspace.display_name or workspace.id) if workspace else inv.tenant
    return f"the {html.escape(name)} workspace"


@router.get("/auth/invite/{token}")
async def invite_confirm(request: Request, token: str):
    """Peek-only confirmation page — inert for mail-scanner prefetches."""
    if not invites.invites_enabled():
        return _page("Not found", "<p>Nothing here.</p>", status=404)
    inv = invites.peek_invite(token)
    if inv is None:
        return _gone()
    email = html.escape(inv.email)
    scope = _scope_label(inv)
    if invites.local_login_mode():
        return _page("You're invited",
                     f"<p>This invitation signs you in to {scope} "
                     f"as <code>{email}</code> with the "
                     f"<code>{html.escape(inv.role)}</code> role.</p>"
                     f"<form method='post'><button type='submit'>"
                     f"Accept invitation</button></form>")
    principal = _session_principal(request)
    if principal is None:
        # SSO mode, not signed in yet: sign in first, then land back here.
        target = quote(f"/auth/invite/{token}", safe="/")
        from maverick.oidc import login_enabled

        from .saml import saml_enabled

        login_path = "/auth/login" if login_enabled() else "/saml/login" if saml_enabled() else "/auth/login"
        return _page("You're invited",
                     f"<p>This invitation (for <code>{email}</code>) grants the "
                     f"<code>{html.escape(inv.role)}</code> role in "
                     f"{scope}.</p>"
                     f"<p><a href='{login_path}?return_to={target}'>Sign in with "
                     f"your company account to accept</a></p>")
    if not _matches_invited_account(principal, inv):
        return _wrong_account()
    return _page("Accept invitation",
                 f"<p>Signed in as <code>{html.escape(principal.sub)}</code>. "
                 f"Accepting binds this account to the "
                 f"<code>{html.escape(inv.role)}</code> role "
                 f"in {scope} (invitation sent to <code>{email}</code>).</p>"
                 f"<form method='post'><button type='submit'>"
                 f"Accept invitation</button></form>")


@router.post("/auth/invite/{token}")
async def invite_accept(request: Request, token: str):
    """Consume the invite: bind the role and (in local mode) sign the browser in."""
    if not invites.invites_enabled():
        return _page("Not found", "<p>Nothing here.</p>", status=404)
    if not _same_origin(request):
        return _page("Blocked", "<p>Cross-site request blocked — open the "
                                "invitation link directly and try again.</p>",
                     status=400)
    if invites.local_login_mode():
        try:
            inv = _consume_and_bind(token)
        except _InviteBindFailed:
            return _bind_failed()
        if inv is None:
            return _gone()
        log.info("invite accepted (local): %s as %s", inv.email, inv.role)
        response = RedirectResponse("/", status_code=303)
        from .oidc_login import _cookie_secure, _set_cookie
        _set_cookie(response, "mvk_session", invites.mint_local_session(inv.email, request=request),
                    max_age=invites.session_ttl_seconds(),
                    secure=_cookie_secure(request))
        return response
    principal = _session_principal(request)
    if principal is None:
        return _page("Sign in required",
                     "<p>Sign in with your company account first, then reopen "
                     "the invitation link.</p>", status=401)
    pending = invites.peek_invite(token)
    if pending is None:
        return _gone()
    if not _matches_invited_account(principal, pending):
        return _wrong_account()
    try:
        inv = _consume_and_bind(token, principal)
    except _InviteBindFailed:
        return _bind_failed()
    if inv is None:
        return _gone()
    log.info("invite accepted (sso): %s bound to %s (invite for %s)",
             principal.principal, inv.role, inv.email)
    return RedirectResponse("/", status_code=303)
