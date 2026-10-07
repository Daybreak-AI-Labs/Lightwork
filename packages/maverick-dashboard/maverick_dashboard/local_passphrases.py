"""Passphrases for invited colleagues, so they can sign back in.

When invitations sign people in locally (invites on, no SSO), accepting an
invitation sets the colleague's passphrase. After signing out they sign in
again with their email and passphrase, from any computer; the owner's own
sign-in still works only on the computer running Lightwork (``local_owner``).

The rules, hashing and lockout are the owner's: 14 to 1,024 characters,
scrypt (n=16384, r=8, p=1) with a random per-account salt, and after five
wrong passphrases the account waits one minute before the next try. The hash
runs outside the store lock, and each client address may make ten sign-in
attempts a minute (refused before any hashing). Only the
hash is stored, in ``~/.maverick/dashboard-passphrases.json`` (0600). There is
no reset by email: an administrator sends a new invitation, and accepting it
replaces the passphrase. Any unreadable or malformed store fails closed.
"""
from __future__ import annotations

import hmac
import json
import math
import secrets
import threading
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from maverick import file_lock
from maverick.paths import maverick_home

from . import local_owner

_LOCK = threading.RLock()
_SCHEMA = "lightwork.local-passphrases.v1"
_MAX_ACCOUNTS = 5000
_MAX_BYTES = 4 * 1024 * 1024
# Same cost as a real check, so an unknown email answers no faster.
_DUMMY_SALT = secrets.token_hex(16)

NOT_ACCEPTED = (
    "The email or passphrase was not accepted. If you joined with an invitation and have "
    "no passphrase yet, or have forgotten it, ask your administrator for a new invite."
)
LOCKED = "Wait one minute before trying to sign in again."
UNAVAILABLE = "Sign-in is unavailable right now. Ask your administrator to check Lightwork."
NO_ACCESS = ("This account no longer has access to this workspace. "
             "Ask your administrator for a new invite.")
TOO_MANY = "Too many sign-in attempts. Wait a minute and try again."
ATTEMPTS_PER_MINUTE = 10  # per client address, owner and colleagues alike


def path():
    return maverick_home() / "dashboard-passphrases.json"


def available() -> bool:
    """Whether colleagues sign in with a passphrase here: invitations sign
    people in locally, and no company sign-in (OIDC, SAML or a trusted proxy)
    is configured to do it instead."""
    try:
        from . import auth, invites

        if not invites.local_login_mode():
            return False
        return not (auth.proxy_auth_enabled() or auth._saml_policy_configured())
    except Exception:  # sign-in policy unreadable: no new way in
        return False


def require_same_origin(request: Request) -> None:
    """A sign-in form submits from this site; cross-site or headerless posts
    are refused (the same check the invitation acceptance uses)."""
    expected = request.url.netloc
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            if urlparse(value).netloc == expected:
                return
            break
    raise HTTPException(403, "Open this Lightwork page before submitting the form.")


def _normal(email: object) -> str:
    return str(email or "").strip().lower()


def _valid_row(email: object, row: object) -> bool:
    if (not isinstance(email, str) or "@" not in email or len(email) > 320
            or email != email.strip().lower() or any(c.isspace() for c in email)):
        return False
    if not isinstance(row, dict) or set(row) != {
            "salt", "password_hash", "failures", "locked_until", "tenant", "updated_at"}:
        return False
    try:
        bytes.fromhex(row["salt"])
        bytes.fromhex(row["password_hash"])
    except (TypeError, ValueError):
        return False
    return (len(row["salt"]) == 32 and len(row["password_hash"]) == 64
            and type(row["failures"]) is int and 0 <= row["failures"] <= 5
            and isinstance(row["locked_until"], (int, float)) and math.isfinite(row["locked_until"])
            and isinstance(row["updated_at"], (int, float)) and math.isfinite(row["updated_at"])
            and isinstance(row["tenant"], str) and len(row["tenant"]) <= 128
            and row["tenant"] == row["tenant"].strip())


def _read() -> dict[str, dict]:
    try:
        file_lock.ensure_private_file(path())
        if path().stat().st_size > _MAX_BYTES:
            raise ValueError("passphrase store exceeds its bound")
        data = json.loads(file_lock.atomic_read_text(path()))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise HTTPException(503, UNAVAILABLE) from exc
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get("schema") != _SCHEMA
            or not isinstance(accounts, dict) or len(accounts) > _MAX_ACCOUNTS
            or not all(_valid_row(email, row) for email, row in accounts.items())):
        raise HTTPException(503, UNAVAILABLE)
    return accounts


def _write(accounts: dict[str, dict]) -> None:
    file_lock.atomic_write_text(path(), json.dumps({"schema": _SCHEMA, "accounts": accounts},
                                                    sort_keys=True), mode=0o600)


def prepare(email: str, password: str) -> dict:
    """Check a new passphrase against the owner's rules and hash it.

    Slow on purpose (scrypt): call it off the event loop. Raises 422 with a
    plain message for a passphrase that breaks the rules."""
    email, password = local_owner._credentials(email, password)
    salt = secrets.token_hex(16)
    return {"email": email, "salt": salt, "password_hash": local_owner._password(password, salt)}


def store(prepared: dict, *, tenant: str = "") -> None:
    """Save or replace one colleague's passphrase hash (and a clean lockout).

    Called while the invitation is being consumed, so a link is never used up
    without the passphrase that lets its colleague return."""
    file_lock.ensure_private_directory(maverick_home())
    with _LOCK, file_lock.cross_process_lock(path(), strict=True):
        accounts = _read()
        email = prepared["email"]
        if email not in accounts and len(accounts) >= _MAX_ACCOUNTS:
            raise HTTPException(409, "This workspace has reached its limit of passphrase accounts.")
        accounts[email] = {"salt": prepared["salt"], "password_hash": prepared["password_hash"],
                           "failures": 0, "locked_until": 0, "tenant": (tenant or "").strip(),
                           "updated_at": time.time()}
        _write(accounts)


def owner_of(email: str) -> bool:
    """Whether ``email`` names this installation's owner (or the owner record
    cannot be read, which the owner's own sign-in then reports)."""
    try:
        row = local_owner._read()
    except HTTPException:
        return True
    return row is not None and row["email"] == _normal(email)


def login(request: Request, *, email: str, password: str) -> str:
    """Verify a colleague's passphrase and return a new session cookie value.

    The slow hash never runs under the store lock, so one sign-in cannot queue
    every other sign-in (and the server thread each one holds) behind it. The
    attempt is reserved under the lock first -- counted, and the account locked
    on the fifth -- so guesses racing in parallel still stop at five per
    lock window. A correct passphrase clears the count afterwards."""
    if not available():
        raise HTTPException(404, "Passphrase sign-in is not used in this workspace.")
    email = _normal(email)
    if not isinstance(password, str) or not 14 <= len(password) <= 1024 or not email or len(email) > 320:
        raise HTTPException(401, NOT_ACCEPTED)
    file_lock.ensure_private_directory(maverick_home())
    with _LOCK, file_lock.cross_process_lock(path(), strict=True):
        accounts = _read()
        row = accounts.get(email)
        if row is not None:
            if row["locked_until"] > time.time():
                raise HTTPException(429, LOCKED)
            row["failures"] = min(5, row["failures"] + 1)
            row["locked_until"] = time.time() + 60 if row["failures"] == 5 else 0
            _write(accounts)
            salt, expected, tenant = row["salt"], row["password_hash"], row["tenant"]
    if row is None:
        local_owner._password(password, _DUMMY_SALT)  # same cost, outside the lock
        raise HTTPException(401, NOT_ACCEPTED)
    if not hmac.compare_digest(local_owner._password(password, salt), expected):
        raise HTTPException(401, NOT_ACCEPTED)  # the reserved attempt stands
    with _LOCK, file_lock.cross_process_lock(path(), strict=True):
        accounts = _read()
        current = accounts.get(email)
        if current is None or current["salt"] != salt or current["password_hash"] != expected:
            raise HTTPException(401, NOT_ACCEPTED)  # replaced by a new invitation meanwhile
        current.update(failures=0, locked_until=0)
        _write(accounts)
    principal = "user:" + email
    from . import rbac

    # The default role for a signed-in person with no assignment is not
    # "nothing", so a removed colleague must not get a session at all.
    if rbac.get_stored_role(principal) is None and not (tenant and rbac.get_tenant_role(tenant, principal)):
        raise HTTPException(403, NO_ACCESS)
    from maverick.oidc import VerifiedPrincipal

    from .auth import _enforce_scim_active
    from .invites import mint_local_session

    _enforce_scim_active(VerifiedPrincipal(sub=email, issuer="invite-session", audience="", claims={}))
    return mint_local_session(email, request=request)


_ATTEMPTS = None


def limit_attempts(request: Request) -> None:
    """At most ATTEMPTS_PER_MINUTE sign-in attempts per client address per
    minute, refused before any passphrase is hashed. The address is the same
    proxy-aware one the public rights-request form limits: a forwarded-for
    chain counts only behind a pinned trusted proxy, so the header cannot be
    used to dodge the limit."""
    global _ATTEMPTS
    from maverick.public_rights_form import SlidingWindowLimit

    from .ip_allowlist import client_ip

    if _ATTEMPTS is None:
        _ATTEMPTS = SlidingWindowLimit(60.0)
    address = client_ip(request)
    if not _ATTEMPTS.hit(f"sign-in:{address if address is not None else 'unknown'}",
                         ATTEMPTS_PER_MINUTE, time.time()):
        raise HTTPException(429, TOO_MANY, headers={"Retry-After": "60"})


def reset_attempt_limit() -> None:
    """Tests only."""
    if _ATTEMPTS is not None:
        _ATTEMPTS.reset()


__all__ = ["NOT_ACCEPTED", "TOO_MANY", "available", "limit_attempts", "login", "owner_of", "prepare",
           "require_same_origin", "reset_attempt_limit", "store"]
