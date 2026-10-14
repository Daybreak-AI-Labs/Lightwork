"""Shared dashboard identity authority primitives.

The dashboard's global role roster and invitation lifecycle are one logical
authorization boundary: redeeming an invitation must grant its promised role
in the same CAS commit.  This module owns that deployment-global document so
dashboard modules cannot accidentally select different rows or inherit an
ambient request tenant.

Only governed-Postgres callers use this module.  Community deployments retain
their existing private JSON files and formats in :mod:`maverick_dashboard`.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import threading
from typing import Any

from .authority_documents import AuthorityDocumentStore

DASHBOARD_ROLES = ("admin", "operator", "auditor", "viewer")
GLOBAL_SCHEMA_VERSION = 1
MAX_GLOBAL_ROLES = 100_000
MAX_INVITATIONS = 100_000
MAX_GLOBAL_DOCUMENT_BYTES = 16 * 1024 * 1024

_TOKEN_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_GLOBAL_AUTHORITY: AuthorityDocumentStore[Any] | None = None
_GLOBAL_AUTHORITY_LOCK = threading.Lock()


class IdentityAuthorityError(RuntimeError):
    """The shared dashboard identity authority is malformed or unavailable."""


def _new_global_authority() -> AuthorityDocumentStore[Any]:
    return AuthorityDocumentStore(
        "dashboard_identity_authority",
        "DIA",
        "dashboard_identity_authority",
        document_name="global",
        required_backend="postgres",
        authority_scope="deployment_global",
    )


def global_authority() -> AuthorityDocumentStore[Any]:
    """Return the process singleton for the deployment-global identity row."""
    global _GLOBAL_AUTHORITY

    with _GLOBAL_AUTHORITY_LOCK:
        if _GLOBAL_AUTHORITY is None:
            _GLOBAL_AUTHORITY = _new_global_authority()
        return _GLOBAL_AUTHORITY


def reset_global_authority_for_testing() -> None:
    """Clear only the in-process adapter cache (test isolation helper)."""
    global _GLOBAL_AUTHORITY

    with _GLOBAL_AUTHORITY_LOCK:
        _GLOBAL_AUTHORITY = None


def _bounded_json_size(value: object) -> int:
    try:
        raw = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise IdentityAuthorityError(
            "shared dashboard identity state is not bounded JSON"
        ) from exc
    if len(raw) > MAX_GLOBAL_DOCUMENT_BYTES:
        raise IdentityAuthorityError(
            "shared dashboard identity state exceeds its byte bound"
        )
    return len(raw)


def _finite_time(value: object, *, label: str, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise IdentityAuthorityError(f"shared invitation has invalid {label}")
    try:
        parsed = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise IdentityAuthorityError(
            f"shared invitation has invalid {label}"
        ) from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise IdentityAuthorityError(f"shared invitation has invalid {label}")
    return parsed


def _validated_roles(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict) or len(raw) > MAX_GLOBAL_ROLES:
        raise IdentityAuthorityError("shared dashboard role roster is invalid")
    roles: dict[str, str] = {}
    for principal, role in raw.items():
        if (
            not isinstance(principal, str)
            or not principal
            or principal != principal.strip()
            or len(principal) > 4096
            or role not in DASHBOARD_ROLES
        ):
            raise IdentityAuthorityError(
                "shared dashboard role roster contains an invalid assignment"
            )
        roles[principal] = str(role)
    return roles


def _validated_invitation(invite_id: object, raw: object) -> dict[str, Any]:
    if (
        not isinstance(invite_id, str)
        or not invite_id
        or invite_id != invite_id.strip()
        or len(invite_id) > 128
        or not isinstance(raw, dict)
        or raw.get("id") != invite_id
    ):
        raise IdentityAuthorityError("shared invitation has an invalid identity")
    if set(raw) != {
        "id",
        "email",
        "role",
        "created_by",
        "created_at",
        "expires_at",
        "token_sha256",
        "used_at",
        "used_by",
    }:
        raise IdentityAuthorityError("shared invitation has an invalid schema")
    email = raw.get("email")
    role = raw.get("role")
    created_by = raw.get("created_by")
    token_digest = raw.get("token_sha256")
    used_by = raw.get("used_by")
    if (
        not isinstance(email, str)
        or not email
        or len(email) > 320
        or email != email.strip().lower()
        or "@" not in email
        or any(char.isspace() for char in email)
    ):
        raise IdentityAuthorityError("shared invitation has an invalid email")
    if role not in DASHBOARD_ROLES:
        raise IdentityAuthorityError("shared invitation has an invalid role")
    if not isinstance(created_by, str) or len(created_by) > 4096:
        raise IdentityAuthorityError("shared invitation has an invalid creator")
    if not isinstance(token_digest, str) or not _TOKEN_DIGEST_RE.fullmatch(
        token_digest
    ):
        raise IdentityAuthorityError("shared invitation has an invalid token digest")
    created_at = _finite_time(raw.get("created_at"), label="created_at")
    expires_at = _finite_time(raw.get("expires_at"), label="expires_at")
    used_at = _finite_time(raw.get("used_at"), label="used_at", optional=True)
    assert created_at is not None and expires_at is not None
    if used_at is None:
        if used_by is not None:
            raise IdentityAuthorityError(
                "shared invitation use identity lacks a use timestamp"
            )
    elif (
        used_at < created_at
        or not isinstance(used_by, str)
        or not used_by
        or used_by != used_by.strip()
        or len(used_by) > 4096
    ):
        raise IdentityAuthorityError("shared invitation has invalid use evidence")
    clean = copy.deepcopy(raw)
    clean["created_at"] = created_at
    clean["expires_at"] = expires_at
    clean["used_at"] = used_at
    return clean


def _validated_invitations(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict) or len(raw) > MAX_INVITATIONS:
        raise IdentityAuthorityError("shared invitation roster is invalid")
    return {
        str(invite_id): _validated_invitation(invite_id, invitation)
        for invite_id, invitation in raw.items()
    }


def validate_global_document(
    document: object,
    *,
    initialize: bool,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Validate and normalize the complete shared global identity document."""
    if not isinstance(document, dict):
        raise IdentityAuthorityError(
            "shared dashboard identity state must be an object"
        )
    if not document:
        if not initialize:
            return {}, {}
        document.update(
            {
                "schema_version": GLOBAL_SCHEMA_VERSION,
                "roles": {},
                "invitations": {},
                "session_signing_sha256": None,
            }
        )
    if set(document) != {
        "schema_version",
        "roles",
        "invitations",
        "session_signing_sha256",
    }:
        raise IdentityAuthorityError(
            "shared dashboard identity state has an invalid schema"
        )
    if document.get("schema_version") != GLOBAL_SCHEMA_VERSION:
        raise IdentityAuthorityError(
            "shared dashboard identity schema is unsupported"
        )
    roles = _validated_roles(document.get("roles"))
    invitations = _validated_invitations(document.get("invitations"))
    signing_digest = document.get("session_signing_sha256")
    if signing_digest is not None and (
        not isinstance(signing_digest, str)
        or not _TOKEN_DIGEST_RE.fullmatch(signing_digest)
    ):
        raise IdentityAuthorityError(
            "shared dashboard session-signing digest is invalid"
        )
    normalized = {
        "schema_version": GLOBAL_SCHEMA_VERSION,
        "roles": roles,
        "invitations": invitations,
        "session_signing_sha256": signing_digest,
    }
    _bounded_json_size(normalized)
    return roles, invitations


def bind_session_signing_key(secret: str) -> str:
    """Bind one deployment-wide invite-session signing key fingerprint.

    The secret remains deployment-external.  Persisting only its SHA-256 digest
    in the global CAS row lets every replica prove it received the same key and
    fail closed on accidental key skew.
    """
    if not isinstance(secret, str) or not secret:
        raise IdentityAuthorityError(
            "dashboard session-signing secret must be configured"
        )
    try:
        secret_bytes = secret.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise IdentityAuthorityError(
            "dashboard session-signing secret is invalid"
        ) from exc
    if not 32 <= len(secret_bytes) <= 4096:
        raise IdentityAuthorityError(
            "dashboard session-signing secret must be 32 to 4096 UTF-8 bytes"
        )
    digest = hashlib.sha256(secret_bytes).hexdigest()
    store = global_authority()
    try:
        document = store.read()
        validate_global_document(document, initialize=False)
        existing = document.get("session_signing_sha256")
        if isinstance(existing, str):
            if not hmac.compare_digest(existing, digest):
                raise IdentityAuthorityError(
                    "dashboard session-signing key does not match the shared authority"
                )
            return digest

        def _bind(current: dict[str, Any]) -> str:
            validate_global_document(current, initialize=True)
            bound = current.get("session_signing_sha256")
            if bound is None:
                current["session_signing_sha256"] = digest
            elif not isinstance(bound, str) or not hmac.compare_digest(bound, digest):
                raise IdentityAuthorityError(
                    "dashboard session-signing key does not match the shared authority"
                )
            validate_global_document(current, initialize=False)
            return digest

        return store.mutate(
            _bind,
            action="bind_session_signing_key",
            actor="dashboard-auth",
        )
    except IdentityAuthorityError:
        raise
    except Exception as exc:
        raise IdentityAuthorityError(
            "dashboard session-signing key could not be bound"
        ) from exc


__all__ = [
    "bind_session_signing_key",
    "DASHBOARD_ROLES",
    "global_authority",
    "IdentityAuthorityError",
    "MAX_GLOBAL_ROLES",
    "MAX_INVITATIONS",
    "reset_global_authority_for_testing",
    "validate_global_document",
]
