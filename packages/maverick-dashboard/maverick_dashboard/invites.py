"""Email invite links — how a client admin adds people to the dashboard.

An **invite** is a single-use, expiring link an admin mints for a colleague's
email address with a pre-assigned RBAC role. It solves onboarding for both
customer postures:

- **SSO deployments** (Azure AD/Entra, Okta, … via the built-in OIDC login):
  the invitee clicks the link, signs in with their company IdP, and the invite
  binds their verified principal to the invited role in the RBAC roster — no
  admin copy-pasting opaque ``user:<sub>`` strings.
- **No-IdP deployments** (the "just get us working" path): accepting the
  invite itself signs the browser in — a signed ``mvk_session`` cookie for
  ``user:<email>``, minted with a locally-held secret. The link IS the
  credential (a magic link), so treat it like a password reset email.

Default **OFF** (``[dashboard] invites = true`` / ``MAVERICK_DASHBOARD_INVITES``
to enable) and fail-closed: every route 404s while disabled, so an unconfigured
deployment is byte-identical. Tokens are stored **hashed** (sha256) — the store
never contains a usable link — and consumption is atomic under the same
cross-process lock discipline as the RBAC roster, so a link can't be redeemed
twice. The accept flow is GET-shows-confirmation / POST-consumes, so a mail
scanner prefetching the link cannot burn it, and the POST is same-origin-gated.

Store: ``~/.maverick/dashboard-invites.json`` (0600) — control-plane data,
one global file, like ``dashboard-users.json``.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

INVITE_TTL_HOURS = 7 * 24          # default link lifetime
LOCAL_SESSION_DAYS = 30            # no-IdP session lifetime (re-invite to renew)
_MAX_PENDING = 500                 # cap the store; an admin never needs more

_INVITES_LOCK = threading.Lock()


def store_path() -> Path:
    return Path.home() / ".maverick" / "dashboard-invites.json"


def _locked(path: Path):
    from contextlib import ExitStack

    from maverick.file_lock import cross_process_lock
    stack = ExitStack()
    stack.enter_context(_INVITES_LOCK)
    stack.enter_context(cross_process_lock(path))
    return stack


# ---- feature gate ------------------------------------------------------------

def invites_enabled() -> bool:
    """Opt-in, off by default: ``MAVERICK_DASHBOARD_INVITES=1`` or
    ``[dashboard] invites = true``. Off → every invite route 404s and the
    local-session path never runs."""
    from maverick.config import env_flag
    v = env_flag("MAVERICK_DASHBOARD_INVITES")
    if v is not None:
        return v
    try:
        from maverick.config import load_config
        return bool(((load_config() or {}).get("dashboard") or {}).get("invites"))
    except Exception:  # pragma: no cover - config read never gates auth
        return False


# ---- invite lifecycle ----------------------------------------------------------

@dataclass(frozen=True)
class Invite:
    id: str
    email: str
    role: str
    created_by: str
    created_at: float
    expires_at: float
    used_at: float | None = None
    used_by: str | None = None

    @property
    def pending(self) -> bool:
        return self.used_at is None and time.time() < self.expires_at


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load() -> dict[str, dict]:
    p = store_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict[str, dict]) -> None:
    from maverick.file_lock import atomic_write_text
    atomic_write_text(store_path(), json.dumps(data, indent=2, sort_keys=True))


def _to_invite(rec: dict) -> Invite:
    return Invite(id=rec["id"], email=rec["email"], role=rec["role"],
                  created_by=rec.get("created_by", ""),
                  created_at=float(rec.get("created_at", 0)),
                  expires_at=float(rec.get("expires_at", 0)),
                  used_at=rec.get("used_at"), used_by=rec.get("used_by"))


def create_invite(email: str, role: str, *, created_by: str,
                  ttl_hours: float = INVITE_TTL_HOURS) -> tuple[Invite, str]:
    """Mint an invite; returns ``(invite, token)``. The token is shown ONCE —
    only its sha256 is stored, so a copied store file yields no working links."""
    from . import rbac
    email = (email or "").strip().lower()
    if not email or "@" not in email or any(c.isspace() for c in email):
        raise ValueError("a valid email address is required")
    if role not in rbac.ROLES:
        raise ValueError("unknown role")
    token = "inv_" + secrets.token_urlsafe(32)
    inv_id = "invite_" + secrets.token_hex(8)
    now = time.time()
    rec = {"id": inv_id, "email": email, "role": role, "created_by": created_by,
           "created_at": now, "expires_at": now + float(ttl_hours) * 3600,
           "token_sha256": _hash(token), "used_at": None, "used_by": None}
    with _locked(store_path()):
        data = _load()
        pending = sum(1 for r in data.values()
                      if not r.get("used_at") and r.get("expires_at", 0) > now)
        if pending >= _MAX_PENDING:
            raise ValueError("too many pending invites — revoke some first")
        data[inv_id] = rec
        _write(data)
    return _to_invite(rec), token


def list_invites() -> list[Invite]:
    """All stored invites, newest first (used/expired included for the audit
    trail; the UI filters)."""
    return sorted((_to_invite(r) for r in _load().values()),
                  key=lambda i: i.created_at, reverse=True)


def revoke_invite(invite_id: str) -> bool:
    with _locked(store_path()):
        data = _load()
        if data.pop((invite_id or "").strip(), None) is None:
            return False
        _write(data)
        return True


def peek_invite(token: str) -> Invite | None:
    """The pending invite for ``token``, WITHOUT consuming it (the GET
    confirmation page — inert for link scanners). None when unknown/used/expired."""
    h = _hash(token or "")
    for rec in _load().values():
        if secrets.compare_digest(rec.get("token_sha256", ""), h):
            inv = _to_invite(rec)
            return inv if inv.pending else None
    return None


def consume_invite(token: str, *, used_by: str | None = None) -> Invite | None:
    """Atomically redeem ``token``: marks it used and returns the invite, or
    None when unknown, expired, or already used (single-use is enforced under
    the store lock, so two racing accepts can't both win). ``used_by`` defaults
    to the invited email's own principal (the local-login case)."""
    h = _hash(token or "")
    with _locked(store_path()):
        data = _load()
        for rec in data.values():
            if secrets.compare_digest(rec.get("token_sha256", ""), h):
                inv = _to_invite(rec)
                if not inv.pending:
                    return None
                rec["used_at"] = time.time()
                rec["used_by"] = used_by or f"user:{inv.email}"
                _write(data)
                return _to_invite(rec)
    return None


# ---- emailing the link ---------------------------------------------------------

def invite_email_enabled() -> bool:
    """Whether minting an invite should also email the link (default ON when a
    sending account is configured; ``[dashboard] invite_email = false`` opts
    out and returns the UI to copy-the-link-yourself)."""
    try:
        from maverick.config import load_config
        v = ((load_config() or {}).get("dashboard") or {}).get("invite_email")
        if v is not None:
            return bool(v)
    except Exception:  # pragma: no cover - config read never gates sending
        pass
    return True


def send_invite_email(invite: Invite, link: str, *, invited_by: str) -> tuple[bool, str]:
    """Best-effort: email the invite link to the invitee. Returns
    ``(sent, reason)`` and NEVER raises — the minting flow stays fail-soft (the
    admin always still gets the copyable link). ``reason`` is a short operator
    hint when not sent ("no sending account configured", an SMTP error, …)."""
    from maverick import mailer
    if not invite_email_enabled():
        return False, "invite emails disabled ([dashboard] invite_email = false)"
    if not mailer.configured():
        return False, "no sending account configured ([email] / EMAIL_USER)"
    days = max(1, int((invite.expires_at - invite.created_at) / 86400))
    body = (
        f"You've been invited to a Lightwork dashboard as {invite.role} "
        f"(invited by {invited_by}).\n\n"
        f"Open this link to accept:\n\n    {link}\n\n"
        f"The link works once and expires in {days} day{'s' if days != 1 else ''}. "
        f"If you weren't expecting this invitation, ignore this email.\n"
    )
    try:
        mailer.send(invite.email, "You're invited to Lightwork", body)
    except mailer.MailerError as e:
        return False, str(e)
    return True, "sent"


# ---- local (no-IdP) sessions ---------------------------------------------------

def _session_secret_path() -> Path:
    return Path.home() / ".maverick" / "dashboard-session.key"


def local_session_secret() -> str:
    """The HMAC key for locally-minted ``mvk_session`` cookies.

    ``MAVERICK_DASHBOARD_SESSION_SECRET`` / ``[dashboard] session_secret`` when
    the operator manages it; otherwise generated once to
    ``~/.maverick/dashboard-session.key`` (0600) — the zero-config path the
    no-IdP customer needs. Distinct from the OIDC ``[auth.oidc] session_secret``
    so enabling SSO later can't silently re-validate old local sessions."""
    import os
    env = os.environ.get("MAVERICK_DASHBOARD_SESSION_SECRET", "").strip()
    if env:
        return env
    try:
        from maverick.config import load_config
        cfg = str(((load_config() or {}).get("dashboard") or {})
                  .get("session_secret") or "").strip()
        if cfg:
            return cfg
    except Exception:  # pragma: no cover - config read never gates auth
        pass
    path = _session_secret_path()
    with _locked(path):
        if path.exists():
            secret = path.read_text(encoding="utf-8").strip()
            if secret:
                return secret
        secret = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - best effort on non-POSIX
            pass
        return secret


def local_login_mode() -> bool:
    """True when invite acceptance must itself sign the browser in: invites are
    on and no OIDC browser login is configured (with SSO, the IdP signs in and
    the invite only binds the role)."""
    if not invites_enabled():
        return False
    try:
        from maverick.oidc import login_enabled
        return not login_enabled()
    except Exception:  # pragma: no cover - oidc config never blocks this gate
        return True


def session_ttl_seconds() -> int:
    try:
        from maverick.config import load_config
        days = ((load_config() or {}).get("dashboard") or {}).get("invite_session_days")
        if days is not None:
            return max(1, int(float(days) * 86400))
    except Exception:  # pragma: no cover
        pass
    return LOCAL_SESSION_DAYS * 86400


def mint_local_session(email: str) -> str:
    """A signed session cookie value for ``user:<email>`` (no-IdP mode)."""
    from maverick.web_session import sign_session
    now = int(time.time())
    return sign_session({"sub": email, "iat": now, "exp": now + session_ttl_seconds()},
                        local_session_secret())


def local_session_principal(request):
    """The :class:`~maverick.oidc.VerifiedPrincipal` for a locally-minted
    ``mvk_session`` cookie, or None. Only active in local-login mode, so it can
    never shadow the OIDC session path; revocation epochs apply exactly as they
    do for SSO sessions ("log out everywhere" / deprovision)."""
    if not local_login_mode():
        return None
    raw = request.cookies.get("mvk_session")
    if not raw:
        return None
    from maverick.web_session import verify_session
    payload = verify_session(raw, local_session_secret())
    if not payload:
        return None
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub:
        return None
    from .session_revocation import is_revoked
    if is_revoked(sub, payload.get("iat")):
        return None
    from maverick.oidc import VerifiedPrincipal
    return VerifiedPrincipal(sub=sub, issuer="invite-session", audience="",
                             claims={"via": "invite"})
