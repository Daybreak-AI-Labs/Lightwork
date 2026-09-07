"""One-time local owner enrollment, with existing invite-session authentication.

The private claim closes ambient access before any roster mutation. A failed
claim can be completed only with its original passphrase; login never restores
privileges to a completed owner whose role was subsequently changed.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import os
import secrets
import threading
import time

from fastapi import HTTPException, Request
from maverick import config, file_lock
from maverick.paths import maverick_home

_LOCK = threading.RLock()
_SCHEMA = "lightwork.local-owner.v1"


def path():
    return maverick_home() / "dashboard-owner.json"


def claimed() -> bool:
    # A malformed claim must still turn off anonymous administrator access.
    return path().exists()


def _read():
    try:
        file_lock.ensure_private_file(path())
        if path().stat().st_size > 8192:
            raise ValueError("owner claim exceeds its bound")
        row = json.loads(file_lock.atomic_read_text(path()))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Local owner authority is unavailable.") from exc
    if (not isinstance(row, dict) or row.get("schema") != _SCHEMA
            or row.get("status") not in {"pending", "ready"}
            or not isinstance(row.get("email"), str) or "@" not in row["email"]
            or len(row["email"]) > 320
            or not isinstance(row.get("salt"), str) or len(row["salt"]) != 32
            or not isinstance(row.get("password_hash"), str) or len(row["password_hash"]) != 64
            or type(row.get("failures")) is not int or not 0 <= row["failures"] <= 5
            or not isinstance(row.get("locked_until"), (int, float))
            or not math.isfinite(row["locked_until"])):
        raise HTTPException(503, "Local owner authority is unavailable.")
    try:
        bytes.fromhex(row["salt"])
        bytes.fromhex(row["password_hash"])
    except ValueError as exc:
        raise HTTPException(503, "Local owner authority is unavailable.") from exc
    return row


def _write(row):
    file_lock.atomic_write_text(path(), json.dumps(row, sort_keys=True), mode=0o600)


def _local(request: Request, *, mutate=False):
    try:
        peer = ipaddress.ip_address(request.client.host)
        host = request.url.hostname
        host_local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(403, "Open owner setup on this computer's loopback address.") from None
    if (not peer.is_loopback or not host_local
            or any(name in request.headers for name in (
                "forwarded", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip"))):
        raise HTTPException(403, "Owner setup does not accept remote or forwarded requests.")
    if mutate and request.headers.get("origin") != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "Open this Lightwork page before submitting the form.")


def _policy(*, bootstrap=False):
    from . import auth

    cfg = config.load_global_config()
    if config.config_source_errors() or not isinstance(cfg, dict):
        raise HTTPException(503, "Authentication policy is unavailable.")
    dashboard = cfg.get("dashboard") or {}
    if not isinstance(dashboard, dict):
        raise HTTPException(503, "Authentication policy is unavailable.")
    # Partial enterprise identity configuration also denies local enrollment.
    if (cfg.get("auth") or cfg.get("proxy_auth")
            or any(os.environ.get(name) for name in (
                "MAVERICK_DASHBOARD_TOKEN", "LIGHTWORK_DASHBOARD_TOKEN"))
            or auth.oidc_enabled() or auth.proxy_auth_enabled()
            or auth._saml_policy_configured()):
        raise HTTPException(409, "This installation uses managed sign-in. Use its configured sign-in method.")
    invite_override = config.env_flag("MAVERICK_DASHBOARD_INVITES")
    if invite_override is None:
        invite_override = config.env_flag("LIGHTWORK_DASHBOARD_INVITES")
    if invite_override is False or dashboard.get("invites") is False:
        raise HTTPException(409, "Deployment policy disables local invite sign-in.")
    if bootstrap:
        enabled = config.env_flag("MAVERICK_LOCAL_OWNER_SETUP")
        if enabled is None:
            enabled = config.env_flag("LIGHTWORK_LOCAL_OWNER_SETUP")
        if enabled is False or dashboard.get("local_owner_setup", True) is not True:
            raise HTTPException(409, "Deployment policy disables first-owner setup.")
        if not auth.anonymous_local_access_allowed():
            raise HTTPException(409, "First-owner setup is available only before sign-in is configured.")


def available(request: Request) -> bool:
    try:
        _local(request)
        if claimed():
            return False
        _policy(bootstrap=True)
        from maverick.suite_grants import admin_principals

        from . import invites, rbac
        return not rbac.list_users() and not invites.list_invites() and not admin_principals()
    except Exception:
        return False


def _password(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                          n=16384, r=8, p=1, dklen=32).hex()


def _credentials(email, password):
    email = str(email or "").strip().lower()
    if (len(email) > 320 or "@" not in email or any(c.isspace() for c in email)
            or any(ord(c) < 32 for c in email)):
        raise HTTPException(422, "Enter a valid email address for the local owner.")
    if not isinstance(password, str) or not 14 <= len(password) <= 1024:
        raise HTTPException(422, "Use a passphrase between 14 and 1,024 characters.")
    return email, password


def _finish(row):
    from . import invites, rbac
    if row["status"] == "pending":
        principal = "user:" + row["email"]
        existing = rbac.list_users()
        if existing and existing != {principal: "admin"}:
            raise HTTPException(409, "Owner setup conflicts with existing role assignments. No roles were changed.")
        rbac.set_role(principal, "admin", actor=principal)
        # Generate the session key before the claim is completed; any failure
        # retains pending ownership rather than silently reopening bootstrap.
        invites.local_session_secret()
        row["status"] = "ready"
        _write(row)
    elif rbac.get_stored_role("user:" + row["email"]) is None:
        raise HTTPException(403, "This local account no longer has an assigned role. Contact a current administrator.")
    return invites.mint_local_session(row["email"])


def enroll(request: Request, *, email: str, password: str, confirm: bool):
    _local(request, mutate=True)
    if confirm is not True:
        raise HTTPException(422, "Review and confirm the change to named sign-in.")
    email, password = _credentials(email, password)
    file_lock.ensure_private_directory(maverick_home())
    with _LOCK, file_lock.cross_process_lock(path(), strict=True):
        if claimed():
            raise HTTPException(409, "An owner has already been claimed. Sign in to complete or continue setup.")
        _policy(bootstrap=True)
        from maverick.suite_grants import admin_principals

        from . import invites, rbac
        if rbac.list_users() or invites.list_invites() or admin_principals():
            raise HTTPException(409, "Existing identity assignments prevent first-owner setup.")
        salt = secrets.token_hex(16)
        row = {"schema": _SCHEMA, "email": email, "salt": salt,
               "password_hash": _password(password, salt), "status": "pending",
               "created_at": time.time(), "failures": 0, "locked_until": 0}
        _write(row)
        return _finish(row)


def login(request: Request, *, email: str, password: str):
    _local(request, mutate=True)
    _policy()
    email, password = _credentials(email, password)
    with _LOCK, file_lock.cross_process_lock(path(), strict=True):
        row = _read()
        if row is None:
            raise HTTPException(404, "A local owner has not been set up.")
        if row["locked_until"] > time.time():
            raise HTTPException(429, "Wait one minute before trying owner sign-in again.")
        match = hmac.compare_digest(_password(password, row["salt"]), row["password_hash"])
        if not match or email != row["email"]:
            row["failures"] = min(5, row["failures"] + 1)
            row["locked_until"] = time.time() + 60 if row["failures"] == 5 else 0
            _write(row)
            raise HTTPException(401, "The email or passphrase was not accepted.")
        from maverick.oidc import VerifiedPrincipal

        from .auth import _enforce_scim_active
        _enforce_scim_active(VerifiedPrincipal(sub=email, issuer="invite-session", audience="", claims={}))
        row.update(failures=0, locked_until=0)
        _write(row)
        return _finish(row)
