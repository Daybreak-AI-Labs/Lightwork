"""The owner's authenticator app: set-up, the second sign-in step, turning it off,
and recovery codes for a lost phone.

The state lives in the owner record (:mod:`local_owner`) under the same lock as
the passphrase and its failure count:

* ``totp``: the authenticator secret, sealed with the deployment at-rest key;
  ``totp_step``: the last time step that opened a sign-in, so one code cannot
  open two;
* ``totp_setup``: a secret shown during set-up, sealed, until it is confirmed;
* ``recovery``: unused recovery codes, each a salted scrypt digest;
* ``second_factor``: second-step tokens, kept as digests with their purpose,
  expiry and wrong-code count.

Rules:

* Sign-in asks for a code whenever the owner is enrolled, in every mode. The
  mode (``[auth.local_owner] totp``) only decides whether a owner who has not
  enrolled must set one up, whether set-up is offered, and whether it may be
  turned off.
* A correct passphrase does not clear the failure count while a code is due.
* Every wrong code counts toward the same lock as a wrong passphrase. A
  second-step token takes at most ``ATTEMPTS`` codes, then is discarded.
* A recovery code works once. An authenticator code's time step is remembered.
* Enabling, turning off, making new recovery codes and using a recovery code are
  recorded in the activity log first. If the log refuses, nothing changes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass

from fastapi import HTTPException
from maverick import config, file_lock

from . import owner_totp

log = logging.getLogger(__name__)

PENDING_SECONDS = 300  # the second step, counted from the passphrase
SETUP_SECONDS = 900  # a set-up secret that has not been confirmed yet
PENDING_LIMIT = 5  # second-step tokens kept at once
ATTEMPTS = 5  # wrong codes one second-step token takes
COOKIE = "mvk_owner_second_factor"
COOKIE_PATH = "/auth/owner-second-factor"
WRONG_CODE = ("That code was not accepted. Check the six-digit code in your authenticator app, "
              "or use one of your recovery codes.")
EXPIRED = "That sign-in step has expired. Sign in with your passphrase again."
SETUP_EXPIRED = "Start the set-up again. Its code has expired."
LOCKED = "Wait one minute before trying again."
REQUIRED = "This installation requires an authenticator app, so it cannot be turned off."
OFF = "This installation does not offer changes to the owner's authenticator app."
ALREADY_ON = "Your authenticator app is already on."
NOT_ON = "No authenticator app is set up."
SEALING = "An authenticator app needs encryption at rest, which is unavailable right now."


@dataclass(frozen=True)
class Challenge:
    """Returned instead of a session when a second factor is due.

    ``token`` goes to the browser in a short-lived cookie; the record keeps only
    its digest. ``secret`` and ``uri`` are set only for set-up (``enroll``).
    """

    token: str
    purpose: str
    secret: str = ""
    uri: str = ""


@dataclass(frozen=True)
class Finished:
    """A completed second step: the session, and recovery codes when set-up finished."""

    session: str
    recovery_codes: tuple[str, ...] = ()


def mode() -> str:
    return config.get_auth_local_owner_totp()


def _now() -> float:
    return time.time()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _strip(code: object) -> str:
    return "".join(str(code or "").split())


def _seal(text: str) -> str:
    from maverick.crypto_at_rest import EncryptionUnavailable, deployment_key_scope, seal_to_str

    try:
        with deployment_key_scope():
            return seal_to_str(text)
    except EncryptionUnavailable as exc:
        raise HTTPException(503, SEALING) from exc


def _secret_key(sealed: object) -> bytes | None:
    """The raw authenticator key behind a sealed value, or None when it cannot be read."""
    from maverick.crypto_at_rest import deployment_key_scope, is_sealed_str, unseal_from_str

    if not is_sealed_str(sealed):
        log.warning("The owner's authenticator secret is not sealed and was not used.")
        return None
    try:
        with deployment_key_scope():
            return owner_totp.decode_secret(unseal_from_str(sealed))
    except Exception:  # noqa: BLE001 - the cause is logged by class name only, never the value
        log.warning("The owner's authenticator secret could not be opened; recovery codes still work.")
        return None


def _issue(row: dict, purpose: str, *, now: float) -> str:
    """A new second-step token for ``row``. Only its digest is stored."""
    token = secrets.token_urlsafe(32)
    live = {d: e for d, e in row.get("second_factor", {}).items() if e["expires"] > now}
    live[_digest(token)] = {"purpose": purpose, "expires": now + PENDING_SECONDS, "attempts": 0}
    newest = sorted(live.items(), key=lambda item: item[1]["expires"])[-PENDING_LIMIT:]
    row["second_factor"] = dict(newest)
    return token


def _live_entry(row: dict, token: object, *, now: float) -> tuple[str, dict]:
    digest = _digest(token) if isinstance(token, str) and token else ""
    entry = row.get("second_factor", {}).get(digest) if digest else None
    if entry is None or entry["expires"] <= now:
        raise HTTPException(401, EXPIRED)
    return digest, entry


def _unlocked(row: dict, *, now: float) -> None:
    if row["locked_until"] > now:
        raise HTTPException(429, LOCKED)


def _audit(verb: str, email: str) -> None:
    """Record the change first. An explicit refusal by the activity log stops it."""
    from maverick.audit import AuditRefused, EventKind, audit_event

    actor = "user:" + email
    try:
        audit_event(EventKind.CONTROL_PLANE_ACTION, agent="dashboard", _global=True,
                    verb=verb, principal=actor, actor=actor, ok=True,
                    detail="owner authenticator app")
    except AuditRefused as exc:
        raise HTTPException(
            503, "The activity log refused to record this change, so nothing was changed. "
                 "Tell an administrator.") from exc


def _setup_key(row: dict, *, now: float) -> bytes:
    setup = row.get("totp_setup")
    if not isinstance(setup, dict) or now - setup["created"] > SETUP_SECONDS:
        raise HTTPException(409, SETUP_EXPIRED)
    key = _secret_key(setup["secret"])
    if key is None:
        raise HTTPException(503, "The set-up code could not be read. Start the set-up again.")
    return key


def _accept(row: dict, code: object, *, now: float) -> str | None:
    """Spend an authenticator code or a recovery code from ``row``.

    Returns ``"app"``, ``"recovery"`` or None. ``row`` changes only on acceptance.
    """
    if "totp" in row:
        key = _secret_key(row["totp"])
        if key is not None:
            step = owner_totp.verify(key, _strip(code), now=now, last_step=int(row.get("totp_step", 0)))
            if step is not None:
                row["totp_step"] = step
                return "app"
    index = owner_totp.recovery_index(row.get("recovery", []), code)
    if index is not None:
        del row["recovery"][index]
        return "recovery"
    return None


def _spend(row: dict, code: object, *, now: float) -> str | None:
    how = _accept(row, code, now=now)
    if how == "recovery":
        _audit("owner_recovery_code_used", row["email"])
    return how


def _passphrase_matches(row: dict, email: object, password: object) -> None:
    from . import local_owner

    email, password = local_owner._credentials(email, password)
    match = hmac.compare_digest(local_owner._password(password, row["salt"]), row["password_hash"])
    if not match or email != row["email"]:
        local_owner._count_failure(row)
        raise HTTPException(401, "The email or passphrase was not accepted.")


def _commit_enrollment(row: dict) -> list[str]:
    """Make the set-up secret the owner's authenticator and issue recovery codes."""
    codes = owner_totp.new_recovery_codes()
    row["totp"] = row["totp_setup"]["secret"]
    del row["totp_setup"]
    row["recovery"] = [owner_totp.new_recovery_entry(code) for code in codes]
    return codes


def _row_or_refuse(*, ready: bool) -> dict:
    from . import local_owner

    row = local_owner._read()
    if row is None or (ready and row["status"] != "ready"):
        raise HTTPException(409, "Complete this computer's owner setup before using the authenticator app.")
    return row


def after_passphrase(row: dict) -> Challenge | None:
    """Called under the owner lock once the passphrase is right. None means sign in now."""
    now = _now()
    if "totp" in row:
        return Challenge(token=_issue(row, "sign_in", now=now), purpose="sign_in")
    if mode() != "required":
        return None
    text = owner_totp.encode_secret(owner_totp.new_secret())
    row["totp_setup"] = {"secret": _seal(text), "created": now}
    return Challenge(token=_issue(row, "enroll", now=now), purpose="enroll", secret=text,
                     uri=owner_totp.otpauth_uri(text, row["email"]))


def _end_unused_session(session: str) -> None:
    """End a session minted for a change that was then refused. Its cookie was never sent."""
    from maverick.web_session import verify_session

    from . import invites, session_registry

    payload = verify_session(session, invites.local_session_secret()) or {}
    if isinstance(payload.get("sid"), str) and payload["sid"]:
        session_registry.revoke(payload["sid"], by="not-completed")


def complete(request, *, token: object, code: object) -> Finished:
    """The second sign-in step: an authenticator code (or set-up code) or a recovery code."""
    from maverick.oidc import VerifiedPrincipal

    from . import local_owner
    from .auth import _enforce_scim_active

    now = _now()
    # The managed sign-in policy and the deprovisioning check apply to this step too:
    # a token begun before either changed does not finish a sign-in.
    local_owner._policy()
    with local_owner._LOCK, file_lock.cross_process_lock(local_owner.path(), strict=True):
        row = _row_or_refuse(ready=False)
        _unlocked(row, now=now)
        _enforce_scim_active(VerifiedPrincipal(sub=row["email"], issuer="invite-session", audience="", claims={}))
        digest, entry = _live_entry(row, token, now=now)
        enrolling = entry["purpose"] == "enroll"
        if enrolling:
            step = owner_totp.verify(_setup_key(row, now=now), _strip(code), now=now)
            how = "app" if step is not None else None
            if how is not None:
                row["totp_step"] = step
        else:
            how = _spend(row, code, now=now)
        if how is None:
            entry["attempts"] += 1
            if entry["attempts"] >= ATTEMPTS:
                del row["second_factor"][digest]
            local_owner._count_failure(row)
            raise HTTPException(401, WRONG_CODE)
        del row["second_factor"][digest]
        row.update(failures=0, locked_until=0)
        codes: list[str] = []
        if enrolling:
            codes = _commit_enrollment(row)
        # The session is made before anything is written: a set-up that cannot
        # finish stores no secret and no codes, and the same code can be tried again.
        session = local_owner._finish(row, request, mfa_at=now)
        if enrolling:
            try:
                _audit("owner_authenticator_enabled", row["email"])
            except HTTPException:
                _end_unused_session(session)
                raise
        local_owner._write(row)
        return Finished(session=session, recovery_codes=tuple(codes))


def challenge_for(token: object) -> Challenge | None:
    """The page data for a live second-step token. No attempt is counted."""
    from . import local_owner

    now = _now()
    row = local_owner._read()
    if row is None:
        return None
    try:
        _, entry = _live_entry(row, token, now=now)
        if entry["purpose"] == "sign_in":
            return Challenge(token=str(token), purpose="sign_in")
        text = owner_totp.encode_secret(_setup_key(row, now=now))
    except HTTPException:
        return None
    return Challenge(token=str(token), purpose="enroll", secret=text,
                     uri=owner_totp.otpauth_uri(text, row["email"]))


def start_setup(password: object) -> tuple[str, str]:
    """The signed-in owner re-enters the passphrase; a new secret is shown for set-up."""
    from . import local_owner

    now = _now()
    with local_owner._LOCK, file_lock.cross_process_lock(local_owner.path(), strict=True):
        row = _row_or_refuse(ready=True)
        _unlocked(row, now=now)
        _passphrase_matches(row, row["email"], password)
        if "totp" in row:
            raise HTTPException(409, ALREADY_ON)
        if mode() == "off":
            raise HTTPException(409, OFF)
        text = owner_totp.encode_secret(owner_totp.new_secret())
        row["totp_setup"] = {"secret": _seal(text), "created": now}
        local_owner._write(row)
        return text, owner_totp.otpauth_uri(text, row["email"])


def pending_setup() -> tuple[str, str] | None:
    """The secret of a set-up that is under way, for showing it again."""
    from . import local_owner

    row = local_owner._read()
    if row is None or "totp_setup" not in row or "totp" in row:
        return None
    try:
        text = owner_totp.encode_secret(_setup_key(row, now=_now()))
    except HTTPException:
        return None
    return text, owner_totp.otpauth_uri(text, row["email"])


def confirm_setup(code: object) -> list[str]:
    """Turn the authenticator app on once a code from it is confirmed."""
    from . import local_owner

    now = _now()
    with local_owner._LOCK, file_lock.cross_process_lock(local_owner.path(), strict=True):
        row = _row_or_refuse(ready=True)
        _unlocked(row, now=now)
        if "totp" in row:
            raise HTTPException(409, ALREADY_ON)
        if mode() == "off":
            raise HTTPException(409, OFF)
        step = owner_totp.verify(_setup_key(row, now=now), _strip(code), now=now)
        if step is None:
            local_owner._count_failure(row)
            raise HTTPException(401, WRONG_CODE)
        row["totp_step"] = step
        _audit("owner_authenticator_enabled", row["email"])
        codes = _commit_enrollment(row)
        row.update(failures=0, locked_until=0)
        local_owner._write(row)
        return codes


def disable(*, password: object, code: object) -> None:
    """Turn the authenticator app off. Needs the passphrase and a current code or a recovery code."""
    from . import local_owner

    now = _now()
    with local_owner._LOCK, file_lock.cross_process_lock(local_owner.path(), strict=True):
        row = _row_or_refuse(ready=True)
        _unlocked(row, now=now)
        if "totp" not in row:
            raise HTTPException(409, NOT_ON)
        if mode() == "required":
            raise HTTPException(409, REQUIRED)
        if mode() == "off":
            raise HTTPException(409, OFF)
        _passphrase_matches(row, row["email"], password)
        if _spend(row, code, now=now) is None:
            local_owner._count_failure(row)
            raise HTTPException(401, WRONG_CODE)
        _audit("owner_authenticator_disabled", row["email"])
        for key in ("totp", "totp_step", "totp_setup", "recovery"):
            row.pop(key, None)
        row.update(failures=0, locked_until=0)
        local_owner._write(row)


def regenerate(*, code: object) -> list[str]:
    """New recovery codes. The old ones stop working. Needs a current code or a recovery code."""
    from . import local_owner

    now = _now()
    with local_owner._LOCK, file_lock.cross_process_lock(local_owner.path(), strict=True):
        row = _row_or_refuse(ready=True)
        _unlocked(row, now=now)
        if "totp" not in row:
            raise HTTPException(409, NOT_ON)
        if mode() == "off":
            raise HTTPException(409, OFF)
        if _spend(row, code, now=now) is None:
            local_owner._count_failure(row)
            raise HTTPException(401, WRONG_CODE)
        _audit("owner_recovery_codes_regenerated", row["email"])
        codes = owner_totp.new_recovery_codes()
        row["recovery"] = [owner_totp.new_recovery_entry(code_text) for code_text in codes]
        row.update(failures=0, locked_until=0)
        local_owner._write(row)
        return codes


def status(row: dict) -> dict:
    return {"mode": mode(), "enrolled": "totp" in row, "unused_recovery": len(row.get("recovery", []))}
