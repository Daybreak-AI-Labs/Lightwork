"""Shared types for durable, request-bound API idempotency.

The storage implementations live beside the SQLite and Postgres world models
so a goal row and its public API result commit in one transaction.  This module
contains only backend-neutral validation and error types used by the dashboard.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

DEFAULT_IDEMPOTENCY_TTL_SECONDS = 30 * 24 * 60 * 60
IDEMPOTENT_GOAL_RESULT_SCHEMA = "lightwork.api-idempotent-goal-result.v1"
MAX_IDEMPOTENT_RESULT_BYTES = 64 * 1024
MAX_IDEMPOTENCY_OPERATION_LENGTH = 256
MAX_IDEMPOTENCY_PRINCIPAL_LENGTH = 512
MAX_IDEMPOTENCY_KEY_LENGTH = 255
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class ApiIdempotencyError(RuntimeError):
    """A durable API idempotency record cannot be honored safely."""


class ApiIdempotencyConflict(ApiIdempotencyError):
    """A key was already bound to a different normalized request."""


class ApiIdempotencyExpired(ApiIdempotencyError):
    """A retained key is past its replay window and cannot be reused."""


class ApiIdempotencyIndeterminate(ApiIdempotencyError):
    """The durable result record is incomplete or refers to missing state."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Decode authority records without silently accepting duplicate keys."""
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = item
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


@dataclass(frozen=True)
class IdempotentGoalResult:
    """The exact public result and private dispatch outbox for one request."""

    goal_id: int
    response: dict[str, Any]
    replayed: bool
    expires_at: float
    dispatch: dict[str, Any] | None = None
    delivery_pending: bool = False


def _validate_exact_text(
    value: object,
    *,
    field: str,
    max_length: int,
) -> str:
    """Validate an identity-bearing value without normalizing its bytes.

    Principal and key equality is a security boundary.  Converting arbitrary
    objects with ``str()`` or stripping edge whitespace can make two caller
    identities share one durable replay claim.  The API/authentication layer
    may impose a narrower syntax, but storage always compares the exact text it
    was given.
    """
    if not isinstance(value, str):
        raise ValueError(f"idempotency {field} is invalid")
    if not value or not value.strip() or len(value) > max_length:
        raise ValueError(f"idempotency {field} is invalid")
    if not value.isprintable():
        raise ValueError(f"idempotency {field} is invalid")
    return value


def validate_operation(value: object) -> str:
    """Return one bounded internal operation name."""
    return _validate_exact_text(
        value,
        field="operation",
        max_length=MAX_IDEMPOTENCY_OPERATION_LENGTH,
    )


def validate_principal_id(value: object) -> str:
    """Return an exact, bounded authenticated-principal identifier."""
    return _validate_exact_text(
        value,
        field="principal",
        max_length=MAX_IDEMPOTENCY_PRINCIPAL_LENGTH,
    )


def validate_idempotency_key(value: object) -> str:
    """Return an exact, bounded idempotency key."""
    return _validate_exact_text(
        value,
        field="key",
        max_length=MAX_IDEMPOTENCY_KEY_LENGTH,
    )


def identity_sha256(value: str) -> str:
    """Hash exact UTF-8 identity text for a privacy-minimized tombstone."""
    if not isinstance(value, str):
        raise ValueError("idempotency identity is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def idempotency_scope_sha256(
    operation: object,
    principal_id: object,
    idempotency_key: object,
) -> str:
    """Fingerprint one exact replay scope without retaining its identifiers.

    The three values are framed before hashing, so delimiter choices cannot
    create collisions.  Keeping only this combined digest in an erasure
    tombstone avoids a reusable principal hash while still permanently
    preventing the retired paid operation from being recreated.
    """
    parts = (
        b"lightwork.api-idempotency-scope.v1",
        validate_operation(operation).encode("utf-8"),
        validate_principal_id(principal_id).encode("utf-8"),
        validate_idempotency_key(idempotency_key).encode("utf-8"),
    )
    framed = b"".join(len(part).to_bytes(4, "big") + part for part in parts)
    return hashlib.sha256(framed).hexdigest()


def canonical_request(value: Mapping[str, Any]) -> str:
    """Return strict canonical JSON for an already-normalized API request."""
    try:
        encoded = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ApiIdempotencyError("API request is not canonical JSON") from exc
    if not isinstance(decoded, dict):  # pragma: no cover - mapping encoded above
        raise ApiIdempotencyError("API request must be an object")
    return encoded


def request_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_request(value).encode("utf-8")).hexdigest()


def encode_goal_result(
    response: Mapping[str, Any],
    dispatch: Mapping[str, Any] | None,
) -> str:
    """Encode the public response and private durable-dispatch outbox together.

    The caller encrypts the returned JSON before storage. Keeping both values
    in the idempotency row lets the goal, exact replay result, and delivery
    intent commit in one database transaction even though the worker queue is
    a separate durable store.
    """
    public_json = canonical_request(response)
    dispatch_json = None if dispatch is None else canonical_request(dispatch)
    envelope = {
        "dispatch": None if dispatch_json is None else json.loads(dispatch_json),
        "response": json.loads(public_json),
        "schema": IDEMPOTENT_GOAL_RESULT_SCHEMA,
    }
    encoded = json.dumps(
        envelope,
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded.encode("utf-8")) > MAX_IDEMPOTENT_RESULT_BYTES:
        raise ApiIdempotencyError("idempotency result exceeds 64 KiB")
    return encoded


def decode_goal_result(value: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Decode one committed result envelope, accepting legacy response-only rows."""
    try:
        if not isinstance(value, str):
            raise TypeError("stored idempotency result must be text")
        if len(value.encode("utf-8")) > MAX_IDEMPOTENT_RESULT_BYTES:
            raise ValueError("stored idempotency result exceeds its bound")
        decoded = json.loads(
            value,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ApiIdempotencyIndeterminate(
            "idempotency result cannot be decoded"
        ) from exc
    if not isinstance(decoded, dict):
        raise ApiIdempotencyIndeterminate("idempotency result is not an object")
    if decoded.get("schema") != IDEMPOTENT_GOAL_RESULT_SCHEMA:
        # Rolling-upgrade compatibility for v35/v36 rows written before the
        # transactional dispatch outbox was added to the sealed result JSON.
        return decoded, None
    if set(decoded) != {"dispatch", "response", "schema"}:
        raise ApiIdempotencyIndeterminate("idempotency result schema is invalid")
    response = decoded.get("response")
    dispatch = decoded.get("dispatch")
    if not isinstance(response, dict) or (
        dispatch is not None and not isinstance(dispatch, dict)
    ):
        raise ApiIdempotencyIndeterminate("idempotency result schema is invalid")
    return response, dispatch


def decode_stored_goal_result(
    value: object,
    unseal: Callable[[object], object],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Unseal and decode a result without leaking storage/crypto failures.

    A missing historical key, malformed ciphertext, or invalid envelope is an
    indeterminate durable claim.  Callers must surface the same retryable,
    content-free contract for all three instead of allowing backend-specific
    exceptions to escape as an untyped 500 response.
    """
    try:
        plaintext = unseal(value)
        return decode_goal_result(plaintext)
    except ApiIdempotencyIndeterminate:
        raise
    except Exception as exc:
        raise ApiIdempotencyIndeterminate(
            "idempotency result is unavailable"
        ) from exc


def validate_request_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ApiIdempotencyError("API request digest is invalid")
    digest = value
    if not _SHA256_RE.fullmatch(digest):
        raise ApiIdempotencyError("API request digest is invalid")
    return digest


__all__ = [
    "ApiIdempotencyConflict",
    "ApiIdempotencyError",
    "ApiIdempotencyExpired",
    "ApiIdempotencyIndeterminate",
    "DEFAULT_IDEMPOTENCY_TTL_SECONDS",
    "IDEMPOTENT_GOAL_RESULT_SCHEMA",
    "IdempotentGoalResult",
    "MAX_IDEMPOTENCY_KEY_LENGTH",
    "MAX_IDEMPOTENCY_OPERATION_LENGTH",
    "MAX_IDEMPOTENCY_PRINCIPAL_LENGTH",
    "MAX_IDEMPOTENT_RESULT_BYTES",
    "canonical_request",
    "decode_goal_result",
    "decode_stored_goal_result",
    "encode_goal_result",
    "idempotency_scope_sha256",
    "identity_sha256",
    "request_sha256",
    "validate_idempotency_key",
    "validate_operation",
    "validate_principal_id",
    "validate_request_sha256",
]
