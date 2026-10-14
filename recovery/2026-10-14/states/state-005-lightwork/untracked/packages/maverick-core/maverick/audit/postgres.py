"""Canonical tenant-scoped audit authority for Postgres deployments.

This backend deliberately does not use ``GovernedRecordStore``: governed-record
commits themselves publish audit receipts, so doing so would recurse.  The two
audit tables are a narrow infrastructure authority with a per-tenant locked
head, a gap-free sequence, an encrypted payload, and an Ed25519 hash chain.

The runtime role is intentionally not allowed to provision or repair this
schema.  A deployment migration must create it and grant only SELECT/INSERT on
events and SELECT/INSERT/UPDATE on heads.  Ambiguous or drifted schemas are
refused rather than normalized into authority by application code.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .events import AuditEvent, is_valid_day

_HEAD_TABLE = "canonical_audit_heads"
_EVENT_TABLE = "canonical_audit_events"
_SIGNER_STATE_TABLE = "canonical_audit_signer_state"
_SIGNER_TABLE = "canonical_audit_signers"
_RECOVERY_TABLE = "canonical_audit_signer_recoveries"
_POLICY = "mvk_canonical_audit_tenant_isolation"
_APPEND_TRIGGER = "mvk_canonical_audit_events_append_only"
_APPEND_FUNCTION = "mvk_reject_canonical_audit_event_mutation()"
_GLOBAL_TENANT = "__maverick_deployment_audit__"
_MAX_EVENT_JSON_BYTES = 1024 * 1024
_MAX_SEALED_EVENT_BYTES = 2 * 1024 * 1024
_MAX_QUERY_EVENTS = 1_000_000
_EVENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_KEY_ID_RE = re.compile(r"[0-9a-f]{16}\Z")
_SIG_RE = re.compile(r"[0-9a-f]{128}\Z")
_TRUST_ROOT_RE = re.compile(r"sha256:([0-9a-f]{64})\Z")
_TRUST_AUTHORITY = "maverick-canonical-audit-signers/v1"
_TRUST_STATE_ID = "canonical-audit"

_COLUMNS = {
    _HEAD_TABLE: (
        ("tenant_id", "text", "NO"),
        ("next_sequence", "bigint", "NO"),
        ("head_sha256", "text", "YES"),
        ("event_count", "bigint", "NO"),
        ("updated_at", "double precision", "NO"),
    ),
    _EVENT_TABLE: (
        ("tenant_id", "text", "NO"),
        ("sequence", "bigint", "NO"),
        ("event_id", "text", "NO"),
        ("event_kind", "text", "NO"),
        ("event_day", "text", "NO"),
        ("event_ts", "double precision", "NO"),
        ("idempotency_sha256", "text", "NO"),
        ("payload_sha256", "text", "NO"),
        ("prev_sha256", "text", "YES"),
        ("event_sha256", "text", "NO"),
        ("signature", "text", "NO"),
        ("key_id", "text", "NO"),
        ("public_key", "text", "NO"),
        ("event_sealed", "text", "NO"),
        ("created_at", "double precision", "NO"),
        ("signer_epoch", "bigint", "NO"),
    ),
    _SIGNER_STATE_TABLE: (
        ("authority_id", "text", "NO"),
        ("current_epoch", "bigint", "NO"),
        ("active_key_id", "text", "NO"),
        ("pending_key_id", "text", "YES"),
        ("root_sha256", "text", "NO"),
        ("history_sha256", "text", "NO"),
        ("recovery_count", "bigint", "NO"),
        ("recovery_sha256", "text", "NO"),
        ("updated_at", "double precision", "NO"),
    ),
    _SIGNER_TABLE: (
        ("key_id", "text", "NO"),
        ("public_key", "text", "NO"),
        ("activated_epoch", "bigint", "NO"),
        ("status", "text", "NO"),
        ("deactivated_epoch", "bigint", "YES"),
        ("predecessor_key_id", "text", "YES"),
        ("predecessor_status", "text", "YES"),
        ("authorization_signature", "text", "YES"),
        ("activation_signature", "text", "YES"),
        ("transition_sha256", "text", "NO"),
        ("created_at", "double precision", "NO"),
    ),
    _RECOVERY_TABLE: (
        ("recovery_sequence", "bigint", "NO"),
        ("signer_epoch", "bigint", "NO"),
        ("active_key_id", "text", "NO"),
        ("history_sha256", "text", "NO"),
        ("cancelled_key_id", "text", "NO"),
        ("cancelled_public_key", "text", "NO"),
        ("cancelled_predecessor_status", "text", "NO"),
        ("cancelled_authorization_signature", "text", "NO"),
        ("cancelled_transition_sha256", "text", "NO"),
        ("replacement_key_id", "text", "NO"),
        ("replacement_public_key", "text", "NO"),
        ("replacement_predecessor_status", "text", "NO"),
        ("replacement_authorization_signature", "text", "NO"),
        ("replacement_transition_sha256", "text", "NO"),
        ("previous_recovery_sha256", "text", "NO"),
        ("recovery_sha256", "text", "NO"),
        ("recovery_signature", "text", "NO"),
        ("created_at", "double precision", "NO"),
    ),
}
_PRIMARY_KEYS = {
    _HEAD_TABLE: ("tenant_id",),
    _EVENT_TABLE: ("tenant_id", "sequence"),
    _SIGNER_STATE_TABLE: ("authority_id",),
    _SIGNER_TABLE: ("key_id",),
    _RECOVERY_TABLE: ("recovery_sequence",),
}
_CHECK_NAMES = {
    _HEAD_TABLE: {
        "ck_mvk_audit_head_tenant",
        "ck_mvk_audit_head_next",
        "ck_mvk_audit_head_count",
        "ck_mvk_audit_head_sequence_count",
        "ck_mvk_audit_head_hash",
        "ck_mvk_audit_head_updated",
    },
    _EVENT_TABLE: {
        "ck_mvk_audit_event_tenant",
        "ck_mvk_audit_event_sequence",
        "ck_mvk_audit_event_id",
        "ck_mvk_audit_event_kind",
        "ck_mvk_audit_event_day",
        "ck_mvk_audit_event_ts",
        "ck_mvk_audit_idempotency_hash",
        "ck_mvk_audit_payload_hash",
        "ck_mvk_audit_prev_hash",
        "ck_mvk_audit_event_hash",
        "ck_mvk_audit_signature",
        "ck_mvk_audit_key_id",
        "ck_mvk_audit_public_key",
        "ck_mvk_audit_event_sealed",
        "ck_mvk_audit_event_created",
        "ck_mvk_audit_signer_epoch",
    },
    _SIGNER_STATE_TABLE: {
        "ck_mvk_audit_trust_authority",
        "ck_mvk_audit_trust_epoch",
        "ck_mvk_audit_trust_active_key",
        "ck_mvk_audit_trust_pending_key",
        "ck_mvk_audit_trust_root",
        "ck_mvk_audit_trust_history",
        "ck_mvk_audit_trust_recovery_count",
        "ck_mvk_audit_trust_recovery_hash",
        "ck_mvk_audit_trust_updated",
    },
    _SIGNER_TABLE: {
        "ck_mvk_audit_signer_key_id",
        "ck_mvk_audit_signer_public_key",
        "ck_mvk_audit_signer_activated",
        "ck_mvk_audit_signer_status",
        "ck_mvk_audit_signer_deactivated",
        "ck_mvk_audit_signer_predecessor",
        "ck_mvk_audit_signer_predecessor_status",
        "ck_mvk_audit_signer_authorization",
        "ck_mvk_audit_signer_activation",
        "ck_mvk_audit_signer_transition",
        "ck_mvk_audit_signer_created",
    },
    _RECOVERY_TABLE: {
        "ck_mvk_audit_recovery_sequence",
        "ck_mvk_audit_recovery_epoch",
        "ck_mvk_audit_recovery_active_key",
        "ck_mvk_audit_recovery_history",
        "ck_mvk_audit_recovery_cancelled_key",
        "ck_mvk_audit_recovery_cancelled_public",
        "ck_mvk_audit_recovery_cancelled_status",
        "ck_mvk_audit_recovery_cancelled_authorization",
        "ck_mvk_audit_recovery_cancelled_transition",
        "ck_mvk_audit_recovery_replacement_key",
        "ck_mvk_audit_recovery_replacement_public",
        "ck_mvk_audit_recovery_replacement_status",
        "ck_mvk_audit_recovery_replacement_authorization",
        "ck_mvk_audit_recovery_replacement_transition",
        "ck_mvk_audit_recovery_previous",
        "ck_mvk_audit_recovery_hash",
        "ck_mvk_audit_recovery_signature",
        "ck_mvk_audit_recovery_distinct",
        "ck_mvk_audit_recovery_created",
    },
}


class AuditAuthorityError(RuntimeError):
    """The configured canonical audit authority cannot preserve its contract."""


class AuditIntegrityError(AuditAuthorityError):
    """Stored audit state is malformed, incomplete, or fails verification."""


class AuditIdempotencyConflict(AuditAuthorityError):
    """An event id was reused for different canonical content."""


def _value(row: object, index: int, name: str) -> Any:
    if row is None:
        return None
    if isinstance(row, Mapping):
        return row.get(name)
    try:
        return row[index]  # type: ignore[index]
    except (IndexError, KeyError, TypeError):
        return None


def _pg_array(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    return tuple(part.strip().strip('"') for part in text.split(",") if part.strip())


def _selection() -> Any:
    """Resolve the same deployment-global authority used by governed records."""
    try:
        from ..governed_records import _selection as governed_selection

        return governed_selection()
    except AuditAuthorityError:
        raise
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditAuthorityError("canonical audit backend policy is invalid") from exc


def shared_audit_selected() -> bool:
    """Whether deployment policy selects the Postgres canonical authority."""
    return str(_selection().kind) == "postgres"


def _dsn_token(dsn: str) -> str:
    return hashlib.sha256(dsn.encode("utf-8")).hexdigest()


def _canonical_tenant(raw: str | None, *, global_scope: bool) -> str:
    if global_scope:
        return _GLOBAL_TENANT
    if raw is None:
        try:
            from ..paths import current_tenant_id_strict

            raw = current_tenant_id_strict()
        except Exception as exc:  # failure-policy: fail_closed
            raise AuditAuthorityError("canonical audit tenant could not be resolved") from exc
    if not raw:
        raise AuditAuthorityError(
            "Postgres canonical audit writes require an active tenant"
        )
    try:
        from ..paths import canonical_tenant_id

        return canonical_tenant_id(raw)
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditAuthorityError("canonical audit tenant is invalid") from exc


def _legacy_audit_dir(tenant: str, *, global_scope: bool) -> Path:
    from ..paths import data_dir

    if global_scope:
        return data_dir("audit", tenant=None)
    return data_dir("audit", tenant=tenant)


def _legacy_canonical_state_exists(path: Path) -> bool:
    """Detect non-empty legacy audit authorities without interpreting them.

    A corrupt or unreadable candidate is treated as live state: a cutover must
    never depend on successfully parsing evidence an attacker can corrupt.
    """
    if not path.exists():
        return False
    try:
        candidates = (
            list(path.glob("*.ndjson"))
            + list(path.glob("*.jsonl"))
            + list(path.glob("*.sqlite"))
            + list(path.glob("*.sqlite3"))
            + list(path.glob("*.db"))
        )
        for candidate in candidates:
            if candidate.name.startswith(".enterprise-verify-probe"):
                continue
            if not candidate.is_file() or candidate.stat().st_size > 0:
                return True
    except OSError:
        return True
    return False


def assert_no_legacy_local_audit(tenant: str, *, global_scope: bool = False) -> None:
    path = _legacy_audit_dir(tenant, global_scope=global_scope)
    if _legacy_canonical_state_exists(path):
        raise AuditAuthorityError(
            "legacy local canonical audit state exists; migrate/import it into "
            "Postgres and archive the local JSONL/SQLite authority before cutover"
        )


def refuse_local_audit_in_shared_mode() -> None:
    """Guard every explicit filesystem audit operation against backend drift."""
    if shared_audit_selected():
        raise AuditAuthorityError(
            "Postgres is the configured canonical audit authority; explicit local "
            "audit JSONL/SQLite access is refused"
        )


@contextlib.contextmanager
def _tenant_context(tenant: str):
    """Bind encryption to the same tenant as the database row."""
    from ..paths import reset_tenant, set_tenant

    token = set_tenant(tenant)
    try:
        yield
    finally:
        reset_tenant(token)


def _require_encryption() -> None:
    try:
        from ..config import without_shared_dashboard_settings
        from ..crypto_at_rest import at_rest_enabled

        with without_shared_dashboard_settings():
            enabled = at_rest_enabled()
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditAuthorityError(
            "canonical audit encryption policy could not be verified"
        ) from exc
    if not enabled:
        raise AuditAuthorityError(
            "Postgres canonical audit requires application at-rest encryption"
        )


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        # Round-tripping through JSON freezes ``default=str`` compatibility into
        # a plain bounded JSON value before any hash, seal, or retry begins.
        text = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
            ensure_ascii=False,
        )
        parsed = json.loads(text)
    except (TypeError, ValueError, RecursionError) as exc:
        raise AuditAuthorityError("audit event is not valid bounded JSON") from exc
    if not isinstance(parsed, dict):
        raise AuditAuthorityError("audit event must be a JSON object")
    if len(text.encode("utf-8")) > _MAX_EVENT_JSON_BYTES:
        raise AuditAuthorityError("audit event exceeds the 1 MiB JSON limit")
    return text


def _event_id(payload: dict[str, Any], event: AuditEvent) -> str:
    raw = payload.get("event_id") or getattr(event, "_canonical_event_id", "")
    value = str(raw or uuid.uuid4().hex)
    if not _EVENT_ID_RE.fullmatch(value):
        raise AuditAuthorityError("audit event_id is invalid")
    # Reusing the same AuditEvent instance after an ambiguous response preserves
    # its id.  No database or external side effect occurs in retry callbacks.
    event._canonical_event_id = value  # type: ignore[attr-defined]
    payload["event_id"] = value
    return value


def _validated_payload(event: AuditEvent, tenant: str) -> tuple[dict[str, Any], str]:
    from .writer import _redact_event

    payload = _redact_event(event.to_dict())
    if not isinstance(payload, dict):
        raise AuditAuthorityError("redacted audit event must remain an object")
    event_id = _event_id(payload, event)
    text = _canonical_json(payload)
    canonical = json.loads(text)
    version = canonical.get("v")
    ts = canonical.get("ts")
    kind = canonical.get("kind")
    agent = canonical.get("agent")
    goal_id = canonical.get("goal_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise AuditAuthorityError("audit schema version is invalid")
    if (
        not isinstance(ts, (int, float))
        or isinstance(ts, bool)
        or not math.isfinite(float(ts))
        or float(ts) < 0
    ):
        raise AuditAuthorityError("audit timestamp is invalid")
    if not isinstance(kind, str) or not kind or len(kind) > 128:
        raise AuditAuthorityError("audit kind is invalid")
    if not isinstance(agent, str) or not agent or len(agent) > 512:
        raise AuditAuthorityError("audit agent is invalid")
    if goal_id is not None and (
        not isinstance(goal_id, int) or isinstance(goal_id, bool) or goal_id < 0
    ):
        raise AuditAuthorityError("audit goal_id is invalid")
    claimed_tenant = canonical.get("tenant")
    if claimed_tenant not in (None, "", tenant):
        raise AuditAuthorityError("audit payload tenant contradicts authority scope")
    occurred_at = canonical.get("occurred_at")
    if occurred_at is not None and (
        not isinstance(occurred_at, (int, float))
        or isinstance(occurred_at, bool)
        or not math.isfinite(float(occurred_at))
        or float(occurred_at) < 0
    ):
        raise AuditAuthorityError("audit occurred_at is invalid")
    return canonical, event_id


def _idempotency_digest(payload: Mapping[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "ts"}
    return hashlib.sha256(_canonical_json(stable).encode("utf-8")).hexdigest()


def _chain_body(
    *,
    tenant: str,
    sequence: int,
    event_id: str,
    event_kind: str,
    event_day: str,
    event_ts: float,
    idempotency_sha256: str,
    payload_sha256: str,
    prev_sha256: str | None,
    key_id: str,
    public_key: str,
    signer_epoch: int,
) -> dict[str, Any]:
    body = {
        "tenant_id": tenant,
        "sequence": sequence,
        "event_id": event_id,
        "event_kind": event_kind,
        "event_day": event_day,
        "event_ts": event_ts,
        "idempotency_sha256": idempotency_sha256,
        "payload_sha256": payload_sha256,
        "prev_sha256": prev_sha256,
        "key_id": key_id,
        "public_key": public_key,
    }
    # v36 rows predate the explicit epoch column. Migration v38 backfills them
    # to epoch one; preserve their signed bytes while still binding that epoch
    # transitively through the root-pinned, one-key-per-epoch trust registry.
    # Every rotated epoch is explicit in the signed event body.
    if signer_epoch > 1:
        body["signer_epoch"] = signer_epoch
    return body


def _hash_chain_body(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _signing_material() -> tuple[Any, str, str]:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519

        from .signing import _load_or_create_keypair

        private, public, key_id = _load_or_create_keypair()
        signer = ed25519.Ed25519PrivateKey.from_private_bytes(private)
        if hashlib.sha256(public).hexdigest()[:16] != key_id:
            raise ValueError("audit signing key id does not match public key")
        return signer, key_id, public.hex()
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditAuthorityError(
            "Postgres canonical audit signing key is unavailable"
        ) from exc


def _verify_signature(public_key: str, signature: str, event_sha256: str) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519

        if (
            not re.fullmatch(r"[0-9a-f]{64}", public_key)
            or not _SIG_RE.fullmatch(signature)
            or not _HASH_RE.fullmatch(event_sha256)
        ):
            raise ValueError("malformed signature material")
        public = bytes.fromhex(public_key)
        ed25519.Ed25519PublicKey.from_public_bytes(public).verify(
            bytes.fromhex(signature), bytes.fromhex(event_sha256)
        )
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditIntegrityError("canonical audit signature verification failed") from exc


@dataclass(frozen=True)
class _TrustedSigner:
    key_id: str
    public_key: str
    epoch: int
    status: str
    deactivated_epoch: int | None


@dataclass(frozen=True)
class _RotationAttempt:
    key_id: str
    public_key: str
    predecessor_status: str
    authorization_signature: str
    transition_sha256: str


@dataclass(frozen=True)
class _TrustState:
    current_epoch: int
    active_key_id: str
    active_public_key: str
    pending: _TrustedSigner | None
    pending_predecessor_status: str | None
    pending_authorization_signature: str | None
    pending_transition_sha256: str | None
    root_sha256: str
    history_sha256: str
    recovery_count: int
    recovery_sha256: str
    by_epoch: Mapping[int, _TrustedSigner]
    by_key_id: Mapping[str, _TrustedSigner]
    attempted_key_ids: frozenset[str]


def _public_key_identity(public_key: str) -> tuple[str, str]:
    """Return the canonical public key and its fixed-width fingerprint."""
    try:
        if not re.fullmatch(r"[0-9a-f]{64}", public_key):
            raise ValueError("public key must be 32 lowercase-hex bytes")
        public = bytes.fromhex(public_key)
        # Constructing the key rejects encodings cryptography does not accept.
        from cryptography.hazmat.primitives.asymmetric import ed25519

        ed25519.Ed25519PublicKey.from_public_bytes(public)
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditAuthorityError("canonical audit public key is invalid") from exc
    return public_key, hashlib.sha256(public).hexdigest()[:16]


def _bootstrap_body(key_id: str, public_key: str) -> dict[str, Any]:
    return {
        "authority": _TRUST_AUTHORITY,
        "epoch": 1,
        "key_id": key_id,
        "public_key": public_key,
    }


def canonical_audit_trust_root_digest(public_key: str) -> str:
    """Digest operators pin before the first shared signer is trusted."""
    public_key, key_id = _public_key_identity(public_key.strip().lower())
    digest = hashlib.sha256(
        _canonical_json(_bootstrap_body(key_id, public_key)).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def _configured_trust_root() -> str:
    """Resolve the external bootstrap pin; ambiguity always fails closed."""
    import os

    env_name = "MAVERICK_AUDIT_TRUST_ROOT_SHA256"
    raw = os.environ.get(env_name)
    if raw is None:
        try:
            from ..config import load_config

            audit = (load_config() or {}).get("audit") or {}
            if not isinstance(audit, Mapping):
                raise TypeError("[audit] must be a table")
            raw = audit.get("trust_root_sha256")
        except Exception as exc:  # failure-policy: fail_closed
            raise AuditAuthorityError(
                "canonical audit trust-root policy could not be resolved"
            ) from exc
    if not isinstance(raw, str):
        raise AuditAuthorityError(
            "Postgres canonical audit requires [audit] trust_root_sha256 or "
            f"{env_name} before signer bootstrap"
        )
    match = _TRUST_ROOT_RE.fullmatch(raw.strip())
    if match is None:
        raise AuditAuthorityError(
            "canonical audit trust_root_sha256 must be sha256:<64 lowercase hex>"
        )
    return match.group(1)


def _rotation_body(
    *,
    epoch: int,
    previous_history_sha256: str,
    predecessor: _TrustedSigner,
    predecessor_status: str,
    key_id: str,
    public_key: str,
) -> dict[str, Any]:
    return {
        "authority": _TRUST_AUTHORITY,
        "epoch": epoch,
        "previous_history_sha256": previous_history_sha256,
        "predecessor_key_id": predecessor.key_id,
        "predecessor_public_key": predecessor.public_key,
        "predecessor_status": predecessor_status,
        "key_id": key_id,
        "public_key": public_key,
    }


def _transition_hash(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(body).encode("utf-8")).hexdigest()


def _activation_body(
    *,
    epoch: int,
    previous_history_sha256: str,
    transition_sha256: str,
    key_id: str,
    public_key: str,
) -> dict[str, Any]:
    return {
        "authority": _TRUST_AUTHORITY,
        "action": "activate",
        "epoch": epoch,
        "previous_history_sha256": previous_history_sha256,
        "transition_sha256": transition_sha256,
        "key_id": key_id,
        "public_key": public_key,
    }


def _history_hash(
    previous: str,
    transition: str,
    authorization_signature: str,
    activation_signature: str,
) -> str:
    return hashlib.sha256(
        (
            f"{previous}:{transition}:{authorization_signature}:"
            f"{activation_signature}"
        ).encode("ascii")
    ).hexdigest()


def _recovery_body(
    *,
    recovery_sequence: int,
    signer_epoch: int,
    active_signer: _TrustedSigner,
    history_sha256: str,
    cancelled: _RotationAttempt,
    replacement: _RotationAttempt,
    previous_recovery_sha256: str,
) -> dict[str, Any]:
    """Canonical evidence for replacing an unusable pending signer."""
    return {
        "authority": _TRUST_AUTHORITY,
        "action": "replace-pending",
        "recovery_sequence": recovery_sequence,
        "signer_epoch": signer_epoch,
        "active_key_id": active_signer.key_id,
        "active_public_key": active_signer.public_key,
        "history_sha256": history_sha256,
        "cancelled": {
            "key_id": cancelled.key_id,
            "public_key": cancelled.public_key,
            "predecessor_status": cancelled.predecessor_status,
            "authorization_signature": cancelled.authorization_signature,
            "transition_sha256": cancelled.transition_sha256,
        },
        "replacement": {
            "key_id": replacement.key_id,
            "public_key": replacement.public_key,
            "predecessor_status": replacement.predecessor_status,
            "authorization_signature": replacement.authorization_signature,
            "transition_sha256": replacement.transition_sha256,
        },
        "previous_recovery_sha256": previous_recovery_sha256,
    }


def _trust_lock_key() -> int:
    digest = hashlib.blake2b(
        b"maverick:canonical-audit:signer-trust", digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


def _validate_recovery_history(  # noqa: C901 - explicit fail-closed parser
    conn: Any,
    *,
    expected_count: int,
    expected_digest: str,
    root_sha256: str,
    by_epoch: Mapping[int, _TrustedSigner],
    history_by_epoch: Mapping[int, str],
    final_attempt_by_epoch: Mapping[int, _RotationAttempt],
    trusted_key_ids: set[str],
) -> frozenset[str]:
    """Verify the complete append-only pending-key replacement ledger."""
    recovery_rows = conn.execute(
        f"SELECT recovery_sequence, signer_epoch, active_key_id, history_sha256, "
        "cancelled_key_id, cancelled_public_key, "
        "cancelled_predecessor_status, cancelled_authorization_signature, "
        "cancelled_transition_sha256, replacement_key_id, "
        "replacement_public_key, replacement_predecessor_status, "
        "replacement_authorization_signature, replacement_transition_sha256, "
        "previous_recovery_sha256, recovery_sha256, recovery_signature, created_at "
        f"FROM {_RECOVERY_TABLE} ORDER BY recovery_sequence"
    ).fetchall()
    if len(recovery_rows) != expected_count:
        raise AuditIntegrityError(
            "canonical audit signer recovery history is incomplete"
        )
    computed_recovery = root_sha256
    last_replacement: dict[int, _RotationAttempt] = {}
    attempted_key_ids = set(trusted_key_ids)
    for expected_sequence, row in enumerate(recovery_rows, start=1):
        sequence = _value(row, 0, "recovery_sequence")
        signer_epoch = _value(row, 1, "signer_epoch")
        active_key_id_row = _value(row, 2, "active_key_id")
        recovery_history = _value(row, 3, "history_sha256")
        cancelled = _RotationAttempt(
            key_id=str(_value(row, 4, "cancelled_key_id") or ""),
            public_key=str(_value(row, 5, "cancelled_public_key") or ""),
            predecessor_status=str(
                _value(row, 6, "cancelled_predecessor_status") or ""
            ),
            authorization_signature=str(
                _value(row, 7, "cancelled_authorization_signature") or ""
            ),
            transition_sha256=str(
                _value(row, 8, "cancelled_transition_sha256") or ""
            ),
        )
        replacement = _RotationAttempt(
            key_id=str(_value(row, 9, "replacement_key_id") or ""),
            public_key=str(_value(row, 10, "replacement_public_key") or ""),
            predecessor_status=str(
                _value(row, 11, "replacement_predecessor_status") or ""
            ),
            authorization_signature=str(
                _value(row, 12, "replacement_authorization_signature") or ""
            ),
            transition_sha256=str(
                _value(row, 13, "replacement_transition_sha256") or ""
            ),
        )
        previous_recovery = _value(row, 14, "previous_recovery_sha256")
        recovery_digest = _value(row, 15, "recovery_sha256")
        recovery_signature = _value(row, 16, "recovery_signature")
        created_at = _value(row, 17, "created_at")
        valid_epoch = (
            isinstance(signer_epoch, int)
            and not isinstance(signer_epoch, bool)
            and signer_epoch >= 1
        )
        predecessor = by_epoch.get(signer_epoch) if valid_epoch else None
        if (
            not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence != expected_sequence
            or predecessor is None
            or active_key_id_row != predecessor.key_id
            or recovery_history != history_by_epoch.get(signer_epoch)
            or previous_recovery != computed_recovery
            or not isinstance(recovery_digest, str)
            or not _HASH_RE.fullmatch(recovery_digest)
            or not isinstance(recovery_signature, str)
            or not _SIG_RE.fullmatch(recovery_signature)
            or not isinstance(created_at, (int, float))
            or isinstance(created_at, bool)
            or not math.isfinite(float(created_at))
            or float(created_at) < 0
        ):
            raise AuditIntegrityError(
                "canonical audit signer recovery row is malformed"
            )
        assert isinstance(signer_epoch, int)  # narrowed by ``predecessor``
        for attempt in (cancelled, replacement):
            if (
                not _KEY_ID_RE.fullmatch(attempt.key_id)
                or not re.fullmatch(r"[0-9a-f]{64}", attempt.public_key)
                or hashlib.sha256(bytes.fromhex(attempt.public_key)).hexdigest()[:16]
                != attempt.key_id
                or attempt.predecessor_status not in {"retired", "revoked"}
                or not _SIG_RE.fullmatch(attempt.authorization_signature)
                or not _HASH_RE.fullmatch(attempt.transition_sha256)
            ):
                raise AuditIntegrityError(
                    "canonical audit signer recovery attempt is malformed"
                )
            expected_transition = _transition_hash(
                _rotation_body(
                    epoch=signer_epoch + 1,
                    previous_history_sha256=str(recovery_history),
                    predecessor=predecessor,
                    predecessor_status=attempt.predecessor_status,
                    key_id=attempt.key_id,
                    public_key=attempt.public_key,
                )
            )
            if expected_transition != attempt.transition_sha256:
                raise AuditIntegrityError(
                    "canonical audit signer recovery transition is inconsistent"
                )
            _verify_signature(
                predecessor.public_key,
                attempt.authorization_signature,
                attempt.transition_sha256,
            )
        prior_replacement = last_replacement.get(signer_epoch)
        if prior_replacement is None:
            if cancelled.key_id in attempted_key_ids:
                raise AuditIntegrityError(
                    "canonical audit signer recovery reuses a trusted key"
                )
            attempted_key_ids.add(cancelled.key_id)
        elif cancelled != prior_replacement:
            raise AuditIntegrityError(
                "canonical audit signer recovery sequence is discontinuous"
            )
        final_attempt = final_attempt_by_epoch.get(signer_epoch + 1)
        if replacement.key_id in attempted_key_ids and replacement != final_attempt:
            raise AuditIntegrityError(
                "canonical audit signer recovery reuses an attempted key"
            )
        if replacement.key_id == cancelled.key_id:
            raise AuditIntegrityError(
                "canonical audit signer recovery replacement is unchanged"
            )
        body = _recovery_body(
            recovery_sequence=expected_sequence,
            signer_epoch=signer_epoch,
            active_signer=predecessor,
            history_sha256=str(recovery_history),
            cancelled=cancelled,
            replacement=replacement,
            previous_recovery_sha256=computed_recovery,
        )
        expected_recovery = _transition_hash(body)
        if recovery_digest != expected_recovery:
            raise AuditIntegrityError(
                "canonical audit signer recovery digest is inconsistent"
            )
        _verify_signature(
            predecessor.public_key, recovery_signature, expected_recovery
        )
        attempted_key_ids.add(replacement.key_id)
        last_replacement[signer_epoch] = replacement
        computed_recovery = expected_recovery
    for signer_epoch, replacement in last_replacement.items():
        if final_attempt_by_epoch.get(signer_epoch + 1) != replacement:
            raise AuditIntegrityError(
                "canonical audit signer recovery does not match its final replacement"
            )
    if computed_recovery != expected_digest:
        raise AuditIntegrityError(
            "canonical audit signer recovery digest is inconsistent"
        )
    return frozenset(attempted_key_ids)


def _load_trust_state(conn: Any, *, for_update: bool = False) -> _TrustState:
    """Load and cryptographically validate the complete shared trust history."""
    suffix = " FOR UPDATE" if for_update else " FOR SHARE"
    state = conn.execute(
        f"SELECT current_epoch, active_key_id, pending_key_id, root_sha256, "
        "history_sha256, recovery_count, recovery_sha256 "
        f"FROM {_SIGNER_STATE_TABLE} WHERE authority_id=%s{suffix}",
        (_TRUST_STATE_ID,),
    ).fetchone()
    if state is None:
        raise AuditAuthorityError(
            "canonical audit signer trust is not bootstrapped; run the explicit "
            "trust-bootstrap ceremony"
        )
    current_epoch = _value(state, 0, "current_epoch")
    active_key_id = _value(state, 1, "active_key_id")
    pending_key_id = _value(state, 2, "pending_key_id")
    root_sha256 = _value(state, 3, "root_sha256")
    history_sha256 = _value(state, 4, "history_sha256")
    recovery_count = _value(state, 5, "recovery_count")
    recovery_sha256 = _value(state, 6, "recovery_sha256")
    if (
        not isinstance(current_epoch, int)
        or isinstance(current_epoch, bool)
        or current_epoch < 1
        or not isinstance(active_key_id, str)
        or not _KEY_ID_RE.fullmatch(active_key_id)
        or (
            pending_key_id is not None
            and (
                not isinstance(pending_key_id, str)
                or not _KEY_ID_RE.fullmatch(pending_key_id)
                or pending_key_id == active_key_id
            )
        )
        or not isinstance(root_sha256, str)
        or not _HASH_RE.fullmatch(root_sha256)
        or not isinstance(history_sha256, str)
        or not _HASH_RE.fullmatch(history_sha256)
        or not isinstance(recovery_count, int)
        or isinstance(recovery_count, bool)
        or recovery_count < 0
        or not isinstance(recovery_sha256, str)
        or not _HASH_RE.fullmatch(recovery_sha256)
        or root_sha256 != _configured_trust_root()
    ):
        raise AuditIntegrityError("canonical audit signer trust state is malformed")
    rows = conn.execute(
        f"SELECT key_id, public_key, activated_epoch, status, deactivated_epoch, "
        "predecessor_key_id, predecessor_status, authorization_signature, "
        f"activation_signature, transition_sha256 FROM {_SIGNER_TABLE} "
        "ORDER BY activated_epoch"
    ).fetchall()
    expected_rows = current_epoch + (1 if pending_key_id is not None else 0)
    if len(rows) != expected_rows:
        raise AuditIntegrityError("canonical audit signer trust history is incomplete")
    by_epoch: dict[int, _TrustedSigner] = {}
    by_key_id: dict[str, _TrustedSigner] = {}
    history_by_epoch: dict[int, str] = {}
    final_attempt_by_epoch: dict[int, _RotationAttempt] = {}
    computed_history = root_sha256
    previous: _TrustedSigner | None = None
    pending: _TrustedSigner | None = None
    pending_predecessor_status: str | None = None
    pending_authorization_signature: str | None = None
    pending_transition_sha256: str | None = None
    for expected_epoch, row in enumerate(rows, start=1):
        key_id = _value(row, 0, "key_id")
        public_key = _value(row, 1, "public_key")
        epoch = _value(row, 2, "activated_epoch")
        status = _value(row, 3, "status")
        deactivated_epoch = _value(row, 4, "deactivated_epoch")
        predecessor_key_id = _value(row, 5, "predecessor_key_id")
        predecessor_status = _value(row, 6, "predecessor_status")
        authorization_signature = _value(row, 7, "authorization_signature")
        activation_signature = _value(row, 8, "activation_signature")
        transition = _value(row, 9, "transition_sha256")
        if (
            not isinstance(key_id, str)
            or not _KEY_ID_RE.fullmatch(key_id)
            or not isinstance(public_key, str)
            or not re.fullmatch(r"[0-9a-f]{64}", public_key)
            or hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16] != key_id
            or epoch != expected_epoch
            or status not in {"active", "pending", "retired", "revoked"}
            or not isinstance(transition, str)
            or not _HASH_RE.fullmatch(transition)
            or key_id in by_key_id
        ):
            raise AuditIntegrityError("canonical audit signer trust row is malformed")
        signer = _TrustedSigner(
            key_id=key_id,
            public_key=public_key,
            epoch=expected_epoch,
            status=str(status),
            deactivated_epoch=(
                deactivated_epoch if isinstance(deactivated_epoch, int) else None
            ),
        )
        if expected_epoch == 1:
            expected_root = canonical_audit_trust_root_digest(public_key)[7:]
            if (
                expected_root != root_sha256
                or transition != root_sha256
                or predecessor_key_id is not None
                or predecessor_status is not None
                or authorization_signature is not None
                or activation_signature is not None
            ):
                raise AuditIntegrityError(
                    "canonical audit signer bootstrap does not match its external pin"
                )
        else:
            if previous is None or predecessor_status not in {"retired", "revoked"}:
                raise AuditIntegrityError("canonical audit signer transition is malformed")
            body = _rotation_body(
                epoch=expected_epoch,
                previous_history_sha256=computed_history,
                predecessor=previous,
                predecessor_status=str(predecessor_status),
                key_id=key_id,
                public_key=public_key,
            )
            expected_transition = _transition_hash(body)
            if (
                predecessor_key_id != previous.key_id
                or transition != expected_transition
                or not isinstance(authorization_signature, str)
                or not _SIG_RE.fullmatch(authorization_signature)
            ):
                raise AuditIntegrityError("canonical audit signer transition is inconsistent")
            _verify_signature(
                previous.public_key, authorization_signature, transition
            )
            if expected_epoch <= current_epoch:
                if (
                    previous.status != predecessor_status
                    or previous.deactivated_epoch != expected_epoch
                    or not isinstance(activation_signature, str)
                    or not _SIG_RE.fullmatch(activation_signature)
                ):
                    raise AuditIntegrityError(
                        "canonical audit signer activation is inconsistent"
                    )
                activation = _activation_body(
                    epoch=expected_epoch,
                    previous_history_sha256=computed_history,
                    transition_sha256=expected_transition,
                    key_id=key_id,
                    public_key=public_key,
                )
                activation_digest = _transition_hash(activation)
                _verify_signature(public_key, activation_signature, activation_digest)
                computed_history = _history_hash(
                    computed_history,
                    expected_transition,
                    authorization_signature,
                    activation_signature,
                )
        if expected_epoch < current_epoch:
            if status not in {"retired", "revoked"} or deactivated_epoch != expected_epoch + 1:
                raise AuditIntegrityError(
                    "canonical audit historical signer lifecycle is inconsistent"
                )
        elif expected_epoch == current_epoch and (
            status != "active"
            or deactivated_epoch is not None
            or key_id != active_key_id
        ):
            raise AuditIntegrityError("canonical audit active signer is inconsistent")
        elif expected_epoch == current_epoch + 1:
            if (
                pending_key_id != key_id
                or status != "pending"
                or deactivated_epoch is not None
                or activation_signature is not None
                or previous is None
                or previous.key_id != active_key_id
            ):
                raise AuditIntegrityError("canonical audit pending signer is inconsistent")
            pending = signer
            pending_predecessor_status = str(predecessor_status)
            pending_authorization_signature = str(authorization_signature)
            pending_transition_sha256 = str(transition)
        if expected_epoch <= current_epoch:
            by_epoch[expected_epoch] = signer
            history_by_epoch[expected_epoch] = computed_history
        if expected_epoch > 1:
            final_attempt_by_epoch[expected_epoch] = _RotationAttempt(
                key_id=key_id,
                public_key=public_key,
                predecessor_status=str(predecessor_status),
                authorization_signature=str(authorization_signature),
                transition_sha256=transition,
            )
        by_key_id[key_id] = signer
        previous = signer
    active = by_epoch[current_epoch]
    if computed_history != history_sha256:
        raise AuditIntegrityError("canonical audit signer history digest is inconsistent")
    attempted_key_ids = _validate_recovery_history(
        conn,
        expected_count=recovery_count,
        expected_digest=recovery_sha256,
        root_sha256=root_sha256,
        by_epoch=by_epoch,
        history_by_epoch=history_by_epoch,
        final_attempt_by_epoch=final_attempt_by_epoch,
        trusted_key_ids=set(by_key_id),
    )
    return _TrustState(
        current_epoch=current_epoch,
        active_key_id=active_key_id,
        active_public_key=active.public_key,
        pending=pending,
        pending_predecessor_status=pending_predecessor_status,
        pending_authorization_signature=pending_authorization_signature,
        pending_transition_sha256=pending_transition_sha256,
        root_sha256=root_sha256,
        history_sha256=history_sha256,
        recovery_count=recovery_count,
        recovery_sha256=recovery_sha256,
        by_epoch=by_epoch,
        by_key_id=by_key_id,
        attempted_key_ids=attempted_key_ids,
    )


def _require_active_signer(
    trust: _TrustState, key_id: str, public_key: str
) -> None:
    if key_id != trust.active_key_id or public_key != trust.active_public_key:
        raise AuditAuthorityError(
            "canonical audit signer is stale or has not completed an authorized rotation"
        )


def _trusted_event_signer(
    trust: _TrustState,
    *,
    signer_epoch: object,
    key_id: object,
    public_key: object,
) -> _TrustedSigner:
    if (
        not isinstance(signer_epoch, int)
        or isinstance(signer_epoch, bool)
        or signer_epoch < 1
        or not isinstance(key_id, str)
        or not isinstance(public_key, str)
    ):
        raise AuditIntegrityError("canonical audit event signer identity is malformed")
    signer = trust.by_epoch.get(signer_epoch)
    if signer is None or signer.key_id != key_id or signer.public_key != public_key:
        raise AuditIntegrityError("canonical audit row uses an untrusted signing key")
    return signer


def _database_identity(conn: Any) -> tuple[str, str, str]:
    row = conn.execute(
        "SELECT current_database(), current_schema(), current_user"
    ).fetchone()
    values = tuple(_value(row, index, name) for index, name in enumerate(
        ("database_name", "schema_name", "role_name")
    ))
    if not all(isinstance(item, str) and item for item in values):
        raise AuditAuthorityError("canonical audit database identity is unavailable")
    return str(values[0]), str(values[1]), str(values[2])


def _role_is_safe(conn: Any) -> bool:
    row = conn.execute(
        "SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, "
        "r.rolcreatedb, r.rolreplication, "
        "has_database_privilege(current_user, current_database(), 'CREATE'), "
        "has_database_privilege(current_user, current_database(), 'TEMP') "
        "FROM pg_roles r WHERE r.rolname=current_user"
    ).fetchone()
    return bool(
        row is not None
        and not bool(_value(row, 0, "rolsuper"))
        and not bool(_value(row, 1, "rolbypassrls"))
        and not bool(_value(row, 2, "rolcreaterole"))
        and not bool(_value(row, 3, "rolcreatedb"))
        and not bool(_value(row, 4, "rolreplication"))
        and not bool(_value(row, 5, "can_create"))
        and not bool(_value(row, 6, "can_temp"))
    )


def _schema_access_is_exact(conn: Any) -> bool:
    row = conn.execute(
        "SELECT n.nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
        "pg_has_role(current_user, n.nspowner, 'MEMBER'), "
        "has_schema_privilege(current_user, n.oid, 'USAGE'), "
        "has_schema_privilege(current_user, current_schema(), 'CREATE') "
        "FROM pg_namespace n WHERE n.nspname=current_schema()"
    ).fetchone()
    return bool(
        row is not None
        and not bool(_value(row, 0, "role_owns"))
        and not bool(_value(row, 1, "owner_member"))
        and bool(_value(row, 2, "can_use"))
        and not bool(_value(row, 3, "can_create"))
    )


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        "SELECT to_regclass(current_schema() || '.' || %s)", (table,)
    ).fetchone()
    return _value(row, 0, "table_name") is not None


def _table_schema_is_exact(conn: Any, table: str) -> bool:
    shape = conn.execute(
        "SELECT c.relkind, c.relpersistence, NOT c.relhassubclass, "
        "NOT EXISTS (SELECT 1 FROM pg_rewrite rw WHERE rw.ev_class=c.oid), "
        "NOT EXISTS (SELECT 1 FROM pg_inherits inh WHERE inh.inhrelid=c.oid "
        "OR inh.inhparent=c.oid) FROM pg_class c "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s)",
        (table,),
    ).fetchone()
    if not (
        shape is not None
        and str(_value(shape, 0, "relkind")) == "r"
        and str(_value(shape, 1, "relpersistence")) == "p"
        and bool(_value(shape, 2, "no_subclasses"))
        and bool(_value(shape, 3, "no_rules"))
        and bool(_value(shape, 4, "no_inheritance"))
    ):
        return False
    rows = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=%s "
        "ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    columns = tuple(
        (
            str(_value(row, 0, "column_name")),
            str(_value(row, 1, "data_type")).lower(),
            str(_value(row, 2, "is_nullable")).upper(),
        )
        for row in rows
    )
    if columns != _COLUMNS[table]:
        return False
    primary = conn.execute(
        "SELECT array_agg(a.attname ORDER BY keys.ordinality) FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indrelid "
        "JOIN unnest(i.indkey) WITH ORDINALITY keys(attnum, ordinality) ON true "
        "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=keys.attnum "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s) "
        "AND i.indisprimary GROUP BY i.indexrelid",
        (table,),
    ).fetchone()
    if primary is None or _pg_array(_value(primary, 0, "columns")) != _PRIMARY_KEYS[table]:
        return False
    checks = conn.execute(
        "SELECT conname, convalidated FROM pg_constraint WHERE conrelid="
        "to_regclass(current_schema() || '.' || %s) AND contype='c' "
        "ORDER BY conname",
        (table,),
    ).fetchall()
    if {
        str(_value(row, 0, "conname")) for row in checks
    } != _CHECK_NAMES[table] or not all(
        bool(_value(row, 1, "convalidated")) for row in checks
    ):
        return False
    foreign_keys = conn.execute(
        "SELECT conname, pg_get_constraintdef(oid), convalidated "
        "FROM pg_constraint WHERE conrelid="
        "to_regclass(current_schema() || '.' || %s) AND contype='f' "
        "ORDER BY conname",
        (table,),
    ).fetchall()
    expected_foreign = {
        _HEAD_TABLE: (),
        _EVENT_TABLE: (
            (
                "fk_mvk_audit_event_head",
                "foreign key (tenant_id)",
                "canonical_audit_heads(tenant_id)",
            ),
        ),
        _SIGNER_STATE_TABLE: (
            (
                "fk_mvk_audit_trust_active_signer",
                "foreign key (active_key_id)",
                "canonical_audit_signers(key_id)",
            ),
            (
                "fk_mvk_audit_trust_pending_signer",
                "foreign key (pending_key_id)",
                "canonical_audit_signers(key_id)",
            ),
        ),
        _SIGNER_TABLE: (
            (
                "fk_mvk_audit_signer_predecessor",
                "foreign key (predecessor_key_id)",
                "canonical_audit_signers(key_id)",
            ),
        ),
        _RECOVERY_TABLE: (
            (
                "fk_mvk_audit_recovery_active_signer",
                "foreign key (active_key_id)",
                "canonical_audit_signers(key_id)",
            ),
        ),
    }[table]
    if len(foreign_keys) != len(expected_foreign):
        return False
    for foreign, expected_foreign_row in zip(
        foreign_keys, expected_foreign, strict=True
    ):
        definition = " ".join(
            str(_value(foreign, 1, "definition")).lower().replace('"', "").split()
        )
        name, local_columns, remote_columns = expected_foreign_row
        if not (
            str(_value(foreign, 0, "conname")) == name
            and bool(_value(foreign, 2, "convalidated"))
            and local_columns in definition
            and remote_columns in definition
            and "on delete restrict" in definition
        ):
            return False
    rls = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE oid=to_regclass(current_schema() || '.' || %s)",
        (table,),
    ).fetchone()
    tenant_scoped = table in {_HEAD_TABLE, _EVENT_TABLE}
    if not rls or bool(_value(rls, 0, "relrowsecurity")) != tenant_scoped or bool(
        _value(rls, 1, "relforcerowsecurity")
    ) != tenant_scoped:
        return False
    policies = conn.execute(
        "SELECT policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname=current_schema() AND tablename=%s "
        "ORDER BY policyname",
        (table,),
    ).fetchall()
    if not tenant_scoped:
        if policies:
            return False
    elif len(policies) != 1:
        return False
    if tenant_scoped:
        policy = policies[0]

        def normalized(value: object) -> str:
            return (
                "".join(str(value or "").lower().split())
                .replace('"', "")
                .replace("::text", "")
            )

        expected = "tenant_id=nullif(current_setting('maverick.tenant',true),'')"
        if not (
            str(_value(policy, 0, "policyname")) == _POLICY
            and str(_value(policy, 1, "permissive")).upper() in {"PERMISSIVE", "TRUE"}
            and _pg_array(_value(policy, 2, "roles")) in {("public",), ("PUBLIC",)}
            and str(_value(policy, 3, "cmd")).upper() == "ALL"
            and normalized(_value(policy, 4, "qual")) in {expected, f"({expected})"}
            and normalized(_value(policy, 5, "with_check")) in {
                expected, f"({expected})"
            }
        ):
            return False
    unique_expected = {
        _EVENT_TABLE: {("tenant_id", "event_id")},
        _SIGNER_TABLE: {("public_key",), ("activated_epoch",)},
        _RECOVERY_TABLE: {("recovery_sha256",)},
    }.get(table)
    if unique_expected is not None:
        unique = conn.execute(
            "SELECT array_agg(a.attname ORDER BY keys.ordinality) FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN unnest(i.indkey) WITH ORDINALITY keys(attnum, ordinality) ON true "
            "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=keys.attnum "
            "WHERE c.oid=to_regclass(current_schema() || '.' || %s) "
            "AND i.indisunique AND NOT i.indisprimary GROUP BY i.indexrelid",
            (table,),
        ).fetchall()
        if {_pg_array(_value(row, 0, "columns")) for row in unique} != unique_expected:
            return False
    return True


def _access_is_exact(conn: Any, table: str) -> bool:
    expected = {
        _HEAD_TABLE: {"SELECT", "INSERT", "UPDATE"},
        _EVENT_TABLE: {"SELECT", "INSERT"},
        _SIGNER_STATE_TABLE: {"SELECT", "INSERT", "UPDATE"},
        _SIGNER_TABLE: {"SELECT", "INSERT", "UPDATE"},
        _RECOVERY_TABLE: {"SELECT", "INSERT"},
    }[table]
    privileges = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    expressions = ", ".join(
        f"has_table_privilege(current_user, c.oid, '{item}')" for item in privileges
    )
    grant_expressions = ", ".join(
        "has_table_privilege(current_user, c.oid, "
        f"'{item} WITH GRANT OPTION')" for item in privileges
    )
    row = conn.execute(
        "SELECT c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
        "pg_has_role(current_user, c.relowner, 'MEMBER'), "
        + expressions
        + ", "
        + grant_expressions
        + " "
        "FROM pg_class c WHERE c.oid=to_regclass(current_schema() || '.' || %s)",
        (table,),
    ).fetchone()
    if row is None or bool(_value(row, 0, "role_owns")) or bool(
        _value(row, 1, "owner_member")
    ):
        return False
    granted = {
        item for index, item in enumerate(privileges, start=2) if bool(_value(row, index, item))
    }
    grant_offset = 2 + len(privileges)
    if granted != expected or any(
        bool(_value(row, grant_offset + index, f"grant_{item.lower()}"))
        for index, item in enumerate(privileges)
    ):
        return False
    public = conn.execute(
        "SELECT privilege_type FROM pg_class c, "
        "LATERAL aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) acl "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s) AND acl.grantee=0",
        (table,),
    ).fetchall()
    if public:
        return False
    column_acl = conn.execute(
        "SELECT 1 FROM pg_attribute WHERE attrelid="
        "to_regclass(current_schema() || '.' || %s) AND attnum > 0 "
        "AND NOT attisdropped AND attacl IS NOT NULL LIMIT 1",
        (table,),
    ).fetchone()
    return column_acl is None


def _append_trigger_is_exact(conn: Any) -> bool:
    for table in (
        _HEAD_TABLE,
        _SIGNER_TABLE,
        _SIGNER_STATE_TABLE,
        _RECOVERY_TABLE,
    ):
        triggers = conn.execute(
            "SELECT tgname FROM pg_trigger WHERE "
            "tgrelid=to_regclass(current_schema() || '.' || %s) "
            "AND NOT tgisinternal",
            (table,),
        ).fetchall()
        if triggers:
            return False
    rows = conn.execute(
        "SELECT tgname, tgenabled, pg_get_triggerdef(t.oid) FROM pg_trigger t "
        "WHERE tgrelid=to_regclass(current_schema() || '.canonical_audit_events') "
        "AND NOT tgisinternal ORDER BY tgname"
    ).fetchall()
    if len(rows) != 1:
        return False
    row = rows[0]
    definition = " ".join(str(_value(row, 2, "definition")).lower().split())
    trigger_ok = bool(
        str(_value(row, 0, "tgname")) == _APPEND_TRIGGER
        and str(_value(row, 1, "tgenabled")) == "O"
        and "before update or delete or truncate" in definition
        and "canonical_audit_events" in definition
        and "for each statement execute function" in definition
        and _APPEND_FUNCTION in definition
    )
    if not trigger_ok:
        return False
    function = conn.execute(
        "SELECT p.prosecdef, p.proconfig, p.prosrc, l.lanname, "
        "p.prorettype::regtype::text, "
        "p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
        "pg_has_role(current_user, p.proowner, 'MEMBER'), "
        "has_function_privilege(current_user, p.oid, 'EXECUTE'), "
        "has_function_privilege(current_user, p.oid, 'EXECUTE WITH GRANT OPTION') "
        "FROM pg_proc p JOIN pg_language l ON l.oid=p.prolang "
        "WHERE p.oid=to_regprocedure(%s)",
        (_APPEND_FUNCTION,),
    ).fetchone()
    if function is None:
        return False
    body = " ".join(str(_value(function, 2, "prosrc")).lower().split())
    config = _pg_array(_value(function, 1, "proconfig"))
    if not (
        not bool(_value(function, 0, "prosecdef"))
        and config == ("search_path=pg_catalog",)
        and body
        == "begin raise exception 'canonical audit events are append-only'; end"
        and str(_value(function, 3, "lanname")) == "plpgsql"
        and str(_value(function, 4, "return_type")) == "trigger"
        and not bool(_value(function, 5, "role_owns"))
        and not bool(_value(function, 6, "owner_member"))
        and not bool(_value(function, 7, "can_execute"))
        and not bool(_value(function, 8, "grant_execute"))
    ):
        return False
    public = conn.execute(
        "SELECT privilege_type FROM pg_proc p, "
        "LATERAL aclexplode(COALESCE(p.proacl, acldefault('f', p.proowner))) acl "
        "WHERE p.oid=to_regprocedure(%s) AND acl.grantee=0",
        (_APPEND_FUNCTION,),
    ).fetchall()
    return not public


def _schema_status(conn: Any) -> str:
    if not _role_is_safe(conn) or not _schema_access_is_exact(conn):
        return "unsafe-role"
    tables = (
        _HEAD_TABLE,
        _EVENT_TABLE,
        _SIGNER_TABLE,
        _SIGNER_STATE_TABLE,
        _RECOVERY_TABLE,
    )
    existence = tuple(_table_exists(conn, table) for table in tables)
    if not any(existence):
        return "missing"
    if not all(existence):
        return "incompatible"
    if not all(_table_schema_is_exact(conn, table) for table in tables):
        return "incompatible"
    if not all(_access_is_exact(conn, table) for table in tables):
        return "unsafe-role"
    if not _append_trigger_is_exact(conn):
        return "incompatible"
    return "ready"


def _tenant_lock_key(tenant: str) -> int:
    digest = hashlib.blake2b(
        f"maverick:canonical-audit:{tenant}".encode(), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big", signed=True)


class _PostgresAuditBackend:
    """One-connection-per-operation Postgres authority."""

    def __init__(
        self,
        dsn: str,
        tenant: str,
        *,
        connect: Callable[[], Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._token = _dsn_token(dsn)
        self._tenant = tenant
        self._connect_override = connect
        self._database_identity: tuple[str, str, str] | None = None
        self._identity_lock = threading.Lock()

    def _connect(self) -> Any:
        try:
            if self._connect_override is not None:
                return self._connect_override()
            import psycopg

            return psycopg.connect(self._dsn, autocommit=False)
        except Exception as exc:  # failure-policy: fail_closed
            raise AuditAuthorityError(
                "Postgres canonical audit authority is unavailable"
            ) from exc

    @contextlib.contextmanager
    def transaction(self, action: str, *, lock_key: int | None = None):
        conn = None
        try:
            conn = self._connect()
            conn.execute("BEGIN")
            identity = _database_identity(conn)
            with self._identity_lock:
                if self._database_identity is not None and identity != self._database_identity:
                    raise AuditAuthorityError(
                        "canonical audit database authority changed after construction"
                    )
                self._database_identity = identity
            status = _schema_status(conn)
            if status == "unsafe-role":
                raise AuditAuthorityError(
                    "canonical audit requires a least-privilege runtime database role"
                )
            if status == "missing":
                raise AuditAuthorityError(
                    "canonical audit schema must be provisioned by an owner migration"
                )
            if status != "ready":
                raise AuditAuthorityError("canonical audit schema is incompatible")
            conn.execute(
                "SELECT set_config('maverick.tenant', %s, true)", (self._tenant,)
            )
            conn.execute(
                "SELECT pg_advisory_xact_lock(%s)",
                (
                    _tenant_lock_key(self._tenant)
                    if lock_key is None
                    else lock_key,
                ),
            )
            yield conn
            conn.commit()
        except AuditAuthorityError:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.rollback()
            raise
        except Exception as exc:  # failure-policy: fail_closed
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.rollback()
            raise AuditAuthorityError(
                f"Postgres canonical audit {action} failed"
            ) from exc
        finally:
            if conn is not None:
                with contextlib.suppress(Exception):
                    conn.close()

    @staticmethod
    def _head(conn: Any, tenant: str) -> tuple[int, str | None, int]:
        conn.execute(
            f"INSERT INTO {_HEAD_TABLE} "
            "(tenant_id, next_sequence, head_sha256, event_count, updated_at) "
            "VALUES (%s, 1, NULL, 0, %s) ON CONFLICT (tenant_id) DO NOTHING",
            (tenant, time.time()),
        )
        row = conn.execute(
            f"SELECT next_sequence, head_sha256, event_count FROM {_HEAD_TABLE} "
            "WHERE tenant_id=%s FOR UPDATE",
            (tenant,),
        ).fetchone()
        if row is None:
            raise AuditIntegrityError("canonical audit tenant head could not be locked")
        next_sequence = _value(row, 0, "next_sequence")
        head = _value(row, 1, "head_sha256")
        event_count = _value(row, 2, "event_count")
        if (
            not isinstance(next_sequence, int)
            or isinstance(next_sequence, bool)
            or next_sequence < 1
            or not isinstance(event_count, int)
            or isinstance(event_count, bool)
            or event_count < 0
            or next_sequence != event_count + 1
            or (head is not None and (not isinstance(head, str) or not _HASH_RE.fullmatch(head)))
            or ((event_count == 0) != (head is None))
        ):
            raise AuditIntegrityError("canonical audit head is corrupt")
        summary = conn.execute(
            f"SELECT COUNT(*), COALESCE(MIN(sequence), 0), "
            f"COALESCE(MAX(sequence), 0) FROM {_EVENT_TABLE} WHERE tenant_id=%s",
            (tenant,),
        ).fetchone()
        count = _value(summary, 0, "count")
        minimum = _value(summary, 1, "minimum")
        maximum = _value(summary, 2, "maximum")
        if count != event_count or (
            event_count and (minimum != 1 or maximum != event_count)
        ):
            raise AuditIntegrityError("canonical audit sequence or head count is corrupt")
        if event_count:
            tip = conn.execute(
                f"SELECT event_sha256 FROM {_EVENT_TABLE} "
                "WHERE tenant_id=%s AND sequence=%s",
                (tenant, event_count),
            ).fetchone()
            if tip is None or _value(tip, 0, "event_sha256") != head:
                raise AuditIntegrityError("canonical audit head does not match its event tip")
        return next_sequence, head, event_count

    def bootstrap_signer_trust(self) -> str:
        """Explicitly bind epoch one to the configured external root pin."""
        _signer, key_id, public_key = _signing_material()
        expected_root = canonical_audit_trust_root_digest(public_key)[7:]
        if expected_root != _configured_trust_root():
            raise AuditAuthorityError(
                "active audit signer does not match the configured trust-root pin"
            )
        with self.transaction("signer trust bootstrap", lock_key=_trust_lock_key()) as conn:
            existing = conn.execute(
                f"SELECT authority_id FROM {_SIGNER_STATE_TABLE} "
                "WHERE authority_id=%s FOR UPDATE",
                (_TRUST_STATE_ID,),
            ).fetchone()
            if existing is not None:
                trust = _load_trust_state(conn, for_update=True)
                _require_active_signer(trust, key_id, public_key)
                return trust.active_key_id
            count_row = conn.execute(
                f"SELECT COUNT(*) FROM {_SIGNER_TABLE}"
            ).fetchone()
            if _value(count_row, 0, "count") != 0:
                raise AuditIntegrityError(
                    "canonical audit signer rows exist without a trust-state root"
                )
            recovery_count_row = conn.execute(
                f"SELECT COUNT(*) FROM {_RECOVERY_TABLE}"
            ).fetchone()
            if _value(recovery_count_row, 0, "count") != 0:
                raise AuditIntegrityError(
                    "canonical audit signer recoveries exist without a "
                    "trust-state root"
                )
            now = time.time()
            inserted = conn.execute(
                f"INSERT INTO {_SIGNER_TABLE} "
                "(key_id, public_key, activated_epoch, status, deactivated_epoch, "
                "predecessor_key_id, predecessor_status, authorization_signature, "
                "activation_signature, transition_sha256, created_at) "
                "VALUES (%s, %s, 1, 'active', NULL, NULL, NULL, NULL, NULL, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (key_id, public_key, expected_root, now),
            )
            if inserted.rowcount != 1:
                raise AuditAuthorityError("canonical audit signer bootstrap conflicted")
            state_inserted = conn.execute(
                f"INSERT INTO {_SIGNER_STATE_TABLE} "
                "(authority_id, current_epoch, active_key_id, pending_key_id, "
                "root_sha256, history_sha256, recovery_count, recovery_sha256, "
                "updated_at) "
                "VALUES (%s, 1, %s, NULL, %s, %s, 0, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    _TRUST_STATE_ID,
                    key_id,
                    expected_root,
                    expected_root,
                    expected_root,
                    now,
                ),
            )
            if state_inserted.rowcount != 1:
                raise AuditAuthorityError("canonical audit trust bootstrap conflicted")
            trust = _load_trust_state(conn, for_update=True)
            _require_active_signer(trust, key_id, public_key)
            return key_id

    def authorize_signer_rotation(
        self,
        next_public_key: str,
        *,
        revoke_previous: bool = False,
        replace_pending_key_id: str | None = None,
    ) -> str:
        """Stage a signed next key while the current signer remains active.

        A stranded pending key may be replaced only when the operator supplies
        its exact key id.  The active signer authorizes both the new transition
        and an append-only recovery record before the row is changed.
        """
        next_public_key, next_key_id = _public_key_identity(
            next_public_key.strip().lower()
        )
        if replace_pending_key_id is not None:
            replace_pending_key_id = replace_pending_key_id.strip().lower()
            if not _KEY_ID_RE.fullmatch(replace_pending_key_id):
                raise AuditAuthorityError(
                    "replace_pending_key_id must be 16 lowercase hex characters"
                )
        predecessor_status = "revoked" if revoke_previous else "retired"
        with self.transaction("signer trust rotation", lock_key=_trust_lock_key()) as conn:
            trust = _load_trust_state(conn, for_update=True)
            signer, key_id, public_key = _signing_material()
            _require_active_signer(trust, key_id, public_key)
            if trust.pending is not None:
                if (
                    trust.pending.key_id == next_key_id
                    and trust.pending.public_key == next_public_key
                    and trust.pending_predecessor_status == predecessor_status
                ):
                    return next_key_id
                if replace_pending_key_id != trust.pending.key_id:
                    raise AuditAuthorityError(
                        "canonical audit already has a pending signer rotation; "
                        "replace it only by naming its exact pending key_id"
                    )
            elif replace_pending_key_id is not None:
                raise AuditAuthorityError(
                    "canonical audit has no pending signer rotation to replace"
                )
            if (
                next_key_id in trust.attempted_key_ids
                or next_public_key == trust.active_public_key
            ):
                raise AuditAuthorityError(
                    "canonical audit rotation cannot reuse a trusted or attempted "
                    "signer key"
                )
            next_epoch = trust.current_epoch + 1
            predecessor = trust.by_epoch[trust.current_epoch]
            body = _rotation_body(
                epoch=next_epoch,
                previous_history_sha256=trust.history_sha256,
                predecessor=predecessor,
                predecessor_status=predecessor_status,
                key_id=next_key_id,
                public_key=next_public_key,
            )
            transition = _transition_hash(body)
            signature = signer.sign(bytes.fromhex(transition)).hex()
            _verify_signature(public_key, signature, transition)
            replacement = _RotationAttempt(
                key_id=next_key_id,
                public_key=next_public_key,
                predecessor_status=predecessor_status,
                authorization_signature=signature,
                transition_sha256=transition,
            )
            now = time.time()
            if trust.pending is not None:
                if (
                    trust.pending_predecessor_status not in {"retired", "revoked"}
                    or trust.pending_authorization_signature is None
                    or trust.pending_transition_sha256 is None
                ):
                    raise AuditIntegrityError(
                        "canonical audit pending signer state is incomplete"
                    )
                cancelled = _RotationAttempt(
                    key_id=trust.pending.key_id,
                    public_key=trust.pending.public_key,
                    predecessor_status=trust.pending_predecessor_status,
                    authorization_signature=trust.pending_authorization_signature,
                    transition_sha256=trust.pending_transition_sha256,
                )
                recovery_sequence = trust.recovery_count + 1
                recovery_body = _recovery_body(
                    recovery_sequence=recovery_sequence,
                    signer_epoch=trust.current_epoch,
                    active_signer=predecessor,
                    history_sha256=trust.history_sha256,
                    cancelled=cancelled,
                    replacement=replacement,
                    previous_recovery_sha256=trust.recovery_sha256,
                )
                recovery_digest = _transition_hash(recovery_body)
                recovery_signature = signer.sign(bytes.fromhex(recovery_digest)).hex()
                _verify_signature(public_key, recovery_signature, recovery_digest)
                evidence = conn.execute(
                    f"INSERT INTO {_RECOVERY_TABLE} "
                    "(recovery_sequence, signer_epoch, active_key_id, history_sha256, "
                    "cancelled_key_id, cancelled_public_key, "
                    "cancelled_predecessor_status, "
                    "cancelled_authorization_signature, cancelled_transition_sha256, "
                    "replacement_key_id, replacement_public_key, "
                    "replacement_predecessor_status, "
                    "replacement_authorization_signature, "
                    "replacement_transition_sha256, previous_recovery_sha256, "
                    "recovery_sha256, recovery_signature, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (
                        recovery_sequence,
                        trust.current_epoch,
                        predecessor.key_id,
                        trust.history_sha256,
                        cancelled.key_id,
                        cancelled.public_key,
                        cancelled.predecessor_status,
                        cancelled.authorization_signature,
                        cancelled.transition_sha256,
                        replacement.key_id,
                        replacement.public_key,
                        replacement.predecessor_status,
                        replacement.authorization_signature,
                        replacement.transition_sha256,
                        trust.recovery_sha256,
                        recovery_digest,
                        recovery_signature,
                        now,
                    ),
                )
                if evidence.rowcount != 1:
                    raise AuditAuthorityError(
                        "canonical audit signer recovery evidence conflicted"
                    )
                cleared = conn.execute(
                    f"UPDATE {_SIGNER_STATE_TABLE} SET pending_key_id=NULL, "
                    "recovery_count=%s, recovery_sha256=%s, updated_at=%s "
                    "WHERE authority_id=%s AND current_epoch=%s "
                    "AND active_key_id=%s AND pending_key_id=%s "
                    "AND history_sha256=%s AND recovery_count=%s "
                    "AND recovery_sha256=%s",
                    (
                        recovery_sequence,
                        recovery_digest,
                        now,
                        _TRUST_STATE_ID,
                        trust.current_epoch,
                        predecessor.key_id,
                        cancelled.key_id,
                        trust.history_sha256,
                        trust.recovery_count,
                        trust.recovery_sha256,
                    ),
                )
                if cleared.rowcount != 1:
                    raise AuditAuthorityError(
                        "canonical audit signer recovery state conflicted"
                    )
                replaced = conn.execute(
                    f"UPDATE {_SIGNER_TABLE} SET key_id=%s, public_key=%s, "
                    "predecessor_status=%s, authorization_signature=%s, "
                    "transition_sha256=%s, created_at=%s WHERE key_id=%s "
                    "AND public_key=%s AND activated_epoch=%s AND status='pending' "
                    "AND deactivated_epoch IS NULL AND predecessor_key_id=%s "
                    "AND predecessor_status=%s AND authorization_signature=%s "
                    "AND activation_signature IS NULL AND transition_sha256=%s",
                    (
                        replacement.key_id,
                        replacement.public_key,
                        replacement.predecessor_status,
                        replacement.authorization_signature,
                        replacement.transition_sha256,
                        now,
                        cancelled.key_id,
                        cancelled.public_key,
                        next_epoch,
                        predecessor.key_id,
                        cancelled.predecessor_status,
                        cancelled.authorization_signature,
                        cancelled.transition_sha256,
                    ),
                )
                if replaced.rowcount != 1:
                    raise AuditAuthorityError(
                        "canonical audit pending signer replacement conflicted"
                    )
                updated = conn.execute(
                    f"UPDATE {_SIGNER_STATE_TABLE} SET pending_key_id=%s, "
                    "updated_at=%s WHERE authority_id=%s AND current_epoch=%s "
                    "AND active_key_id=%s AND pending_key_id IS NULL "
                    "AND history_sha256=%s AND recovery_count=%s "
                    "AND recovery_sha256=%s",
                    (
                        replacement.key_id,
                        now,
                        _TRUST_STATE_ID,
                        trust.current_epoch,
                        predecessor.key_id,
                        trust.history_sha256,
                        recovery_sequence,
                        recovery_digest,
                    ),
                )
                if updated.rowcount != 1:
                    raise AuditAuthorityError(
                        "canonical audit signer recovery publication conflicted"
                    )
                staged = _load_trust_state(conn, for_update=True)
                if (
                    staged.active_key_id != predecessor.key_id
                    or staged.current_epoch != trust.current_epoch
                    or staged.pending is None
                    or staged.pending.key_id != replacement.key_id
                    or staged.recovery_count != recovery_sequence
                    or staged.recovery_sha256 != recovery_digest
                ):
                    raise AuditIntegrityError(
                        "canonical audit signer recovery was not staged"
                    )
                return replacement.key_id
            inserted = conn.execute(
                f"INSERT INTO {_SIGNER_TABLE} "
                "(key_id, public_key, activated_epoch, status, deactivated_epoch, "
                "predecessor_key_id, predecessor_status, authorization_signature, "
                "activation_signature, transition_sha256, created_at) "
                "VALUES (%s, %s, %s, 'pending', NULL, %s, %s, %s, NULL, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    next_key_id,
                    next_public_key,
                    next_epoch,
                    predecessor.key_id,
                    predecessor_status,
                    signature,
                    transition,
                    now,
                ),
            )
            if inserted.rowcount != 1:
                raise AuditAuthorityError("canonical audit signer rotation conflicted")
            updated = conn.execute(
                f"UPDATE {_SIGNER_STATE_TABLE} SET pending_key_id=%s, updated_at=%s "
                "WHERE authority_id=%s AND current_epoch=%s AND active_key_id=%s "
                "AND pending_key_id IS NULL AND history_sha256=%s "
                "AND recovery_count=%s AND recovery_sha256=%s",
                (
                    next_key_id,
                    now,
                    _TRUST_STATE_ID,
                    trust.current_epoch,
                    predecessor.key_id,
                    trust.history_sha256,
                    trust.recovery_count,
                    trust.recovery_sha256,
                ),
            )
            if updated.rowcount != 1:
                raise AuditAuthorityError("canonical audit trust rotation conflicted")
            staged = _load_trust_state(conn, for_update=True)
            if (
                staged.active_key_id != predecessor.key_id
                or staged.current_epoch != trust.current_epoch
                or staged.pending is None
                or staged.pending.key_id != next_key_id
            ):
                raise AuditIntegrityError("canonical audit signer rotation was not staged")
            return next_key_id

    @staticmethod
    def _trust_for_append(
        conn: Any, *, key_id: str, public_key: str
    ) -> tuple[_TrustState, bool]:
        peek = conn.execute(
            f"SELECT active_key_id, pending_key_id FROM {_SIGNER_STATE_TABLE} "
            "WHERE authority_id=%s",
            (_TRUST_STATE_ID,),
        ).fetchone()
        # Unknown/new keys take an exclusive state lock: a rotation may have
        # staged them between this observation and the authoritative read.
        active_hint = _value(peek, 0, "active_key_id")
        trust = _load_trust_state(conn, for_update=active_hint != key_id)
        if trust.active_key_id == key_id and trust.active_public_key == public_key:
            return trust, False
        if (
            trust.pending is not None
            and trust.pending.key_id == key_id
            and trust.pending.public_key == public_key
        ):
            return trust, True
        raise AuditAuthorityError(
            "canonical audit signer is stale or has not completed an authorized rotation"
        )

    @staticmethod
    def _activate_pending_signer(
        conn: Any,
        *,
        trust: _TrustState,
        signer: Any,
    ) -> _TrustState:
        pending = trust.pending
        predecessor_status = trust.pending_predecessor_status
        authorization = trust.pending_authorization_signature
        transition = trust.pending_transition_sha256
        if (
            pending is None
            or predecessor_status not in {"retired", "revoked"}
            or authorization is None
            or transition is None
        ):
            raise AuditIntegrityError("canonical audit pending signer state is incomplete")
        activation = _activation_body(
            epoch=pending.epoch,
            previous_history_sha256=trust.history_sha256,
            transition_sha256=transition,
            key_id=pending.key_id,
            public_key=pending.public_key,
        )
        activation_digest = _transition_hash(activation)
        activation_signature = signer.sign(bytes.fromhex(activation_digest)).hex()
        _verify_signature(
            pending.public_key, activation_signature, activation_digest
        )
        next_history = _history_hash(
            trust.history_sha256,
            transition,
            authorization,
            activation_signature,
        )
        predecessor = trust.by_epoch[trust.current_epoch]
        retired = conn.execute(
            f"UPDATE {_SIGNER_TABLE} SET status=%s, deactivated_epoch=%s "
            "WHERE key_id=%s AND status='active' AND deactivated_epoch IS NULL",
            (predecessor_status, pending.epoch, predecessor.key_id),
        )
        if retired.rowcount != 1:
            raise AuditAuthorityError("canonical audit signer retirement conflicted")
        activated = conn.execute(
            f"UPDATE {_SIGNER_TABLE} SET status='active', activation_signature=%s "
            "WHERE key_id=%s AND status='pending' AND deactivated_epoch IS NULL "
            "AND activation_signature IS NULL",
            (activation_signature, pending.key_id),
        )
        if activated.rowcount != 1:
            raise AuditAuthorityError("canonical audit signer activation conflicted")
        updated = conn.execute(
            f"UPDATE {_SIGNER_STATE_TABLE} SET current_epoch=%s, active_key_id=%s, "
            "pending_key_id=NULL, history_sha256=%s, updated_at=%s "
            "WHERE authority_id=%s AND current_epoch=%s AND active_key_id=%s "
            "AND pending_key_id=%s AND history_sha256=%s "
            "AND recovery_count=%s AND recovery_sha256=%s",
            (
                pending.epoch,
                pending.key_id,
                next_history,
                time.time(),
                _TRUST_STATE_ID,
                trust.current_epoch,
                predecessor.key_id,
                pending.key_id,
                trust.history_sha256,
                trust.recovery_count,
                trust.recovery_sha256,
            ),
        )
        if updated.rowcount != 1:
            raise AuditAuthorityError("canonical audit trust activation conflicted")
        activated_trust = _load_trust_state(conn, for_update=True)
        _require_active_signer(
            activated_trust, pending.key_id, pending.public_key
        )
        return activated_trust

    def append(self, payload: Mapping[str, Any], event_id: str) -> int:
        payload_text = _canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        idempotency_sha256 = _idempotency_digest(payload)
        event_ts = float(payload["ts"])
        event_day = time.strftime("%Y-%m-%d", time.gmtime(event_ts))
        event_kind = str(payload["kind"])
        with _tenant_context(self._tenant):
            from ..crypto_at_rest import seal_to_str

            sealed = seal_to_str(payload_text)
        if len(sealed.encode("utf-8")) > _MAX_SEALED_EVENT_BYTES:
            raise AuditAuthorityError("sealed audit event exceeds the 2 MiB limit")
        signer, key_id, public_key = _signing_material()
        with self.transaction("append") as conn:
            trust, pending_activation = self._trust_for_append(
                conn, key_id=key_id, public_key=public_key
            )
            sequence, previous, event_count = self._head(conn, self._tenant)
            prior = conn.execute(
                f"SELECT sequence, event_kind, event_day, event_ts, "
                f"idempotency_sha256, payload_sha256, prev_sha256, event_sha256, "
                f"signature, key_id, public_key, signer_epoch "
                f"FROM {_EVENT_TABLE} WHERE tenant_id=%s AND event_id=%s",
                (self._tenant, event_id),
            ).fetchone()
            if prior is not None:
                if pending_activation:
                    raise AuditAuthorityError(
                        "a pending audit signer must activate with a new event_id"
                    )
                if _value(prior, 4, "idempotency_sha256") != idempotency_sha256:
                    raise AuditIdempotencyConflict(
                        "audit event_id was already committed with different content"
                    )
                # Validate the duplicate row itself before acknowledging it.
                row_sequence = _value(prior, 0, "sequence")
                row_kind = _value(prior, 1, "event_kind")
                row_day = _value(prior, 2, "event_day")
                row_ts = _value(prior, 3, "event_ts")
                row_payload_hash = _value(prior, 5, "payload_sha256")
                row_previous = _value(prior, 6, "prev_sha256")
                row_hash = _value(prior, 7, "event_sha256")
                row_signature = _value(prior, 8, "signature")
                row_key_id = _value(prior, 9, "key_id")
                row_public = _value(prior, 10, "public_key")
                row_signer_epoch = _value(prior, 11, "signer_epoch")
                if not all(isinstance(value, str) for value in (
                    row_kind, row_day, row_payload_hash, row_hash, row_signature,
                    row_key_id, row_public,
                )) or not isinstance(row_sequence, int) or not isinstance(
                    row_ts, (int, float)
                ):
                    raise AuditIntegrityError("duplicate canonical audit row is malformed")
                if (
                    not re.fullmatch(r"[0-9a-f]{64}", str(row_public))
                    or hashlib.sha256(bytes.fromhex(str(row_public))).hexdigest()[:16]
                    != row_key_id
                ):
                    raise AuditIntegrityError("duplicate canonical audit key id is corrupt")
                _trusted_event_signer(
                    trust,
                    signer_epoch=row_signer_epoch,
                    key_id=row_key_id,
                    public_key=row_public,
                )
                duplicate_body = _chain_body(
                    tenant=self._tenant,
                    sequence=row_sequence,
                    event_id=event_id,
                    event_kind=str(row_kind),
                    event_day=str(row_day),
                    event_ts=float(row_ts),
                    idempotency_sha256=idempotency_sha256,
                    payload_sha256=str(row_payload_hash),
                    prev_sha256=row_previous,
                    key_id=str(row_key_id),
                    public_key=str(row_public),
                    signer_epoch=int(row_signer_epoch),
                )
                if _hash_chain_body(duplicate_body) != row_hash:
                    raise AuditIntegrityError("duplicate canonical audit row hash is corrupt")
                _verify_signature(str(row_public), str(row_signature), str(row_hash))
                # Always verify the encrypted body before acknowledging an
                # idempotent duplicate. Matching caller content must not hide a
                # corrupted ciphertext or a body/metadata substitution.
                existing_payload = self._load_payload(conn, self._tenant, event_id)
                existing_payload_hash = hashlib.sha256(
                    _canonical_json(existing_payload).encode("utf-8")
                ).hexdigest()
                if (
                    existing_payload_hash != row_payload_hash
                    or existing_payload.get("event_id") != event_id
                    or _idempotency_digest(existing_payload) != idempotency_sha256
                ):
                    raise AuditIntegrityError(
                        "duplicate canonical audit payload is inconsistent"
                    )
                # Only append-attempt ``ts`` may differ for an idempotent retry.
                # Its stored payload remains the first successfully committed
                # body, while the stable digest proves all semantic fields match.
                return row_sequence
            if pending_activation:
                trust = self._activate_pending_signer(
                    conn, trust=trust, signer=signer
                )
            signer_epoch = trust.current_epoch
            body = _chain_body(
                tenant=self._tenant,
                sequence=sequence,
                event_id=event_id,
                event_kind=event_kind,
                event_day=event_day,
                event_ts=event_ts,
                idempotency_sha256=idempotency_sha256,
                payload_sha256=payload_sha256,
                prev_sha256=previous,
                key_id=key_id,
                public_key=public_key,
                signer_epoch=signer_epoch,
            )
            event_sha256 = _hash_chain_body(body)
            signature = signer.sign(bytes.fromhex(event_sha256)).hex()
            inserted = conn.execute(
                f"INSERT INTO {_EVENT_TABLE} "
                "(tenant_id, sequence, event_id, event_kind, event_day, event_ts, "
                "idempotency_sha256, payload_sha256, prev_sha256, event_sha256, "
                "signature, key_id, public_key, event_sealed, created_at, signer_epoch) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    self._tenant, sequence, event_id, event_kind, event_day,
                    event_ts, idempotency_sha256, payload_sha256, previous,
                    event_sha256, signature, key_id, public_key, sealed, time.time(),
                    signer_epoch,
                ),
            )
            if inserted.rowcount != 1:
                raise AuditAuthorityError("canonical audit append conflicted")
            updated = conn.execute(
                f"UPDATE {_HEAD_TABLE} SET next_sequence=%s, head_sha256=%s, "
                "event_count=%s, updated_at=%s WHERE tenant_id=%s "
                "AND next_sequence=%s AND event_count=%s "
                "AND ((head_sha256 IS NULL AND %s IS NULL) OR head_sha256=%s)",
                (
                    sequence + 1, event_sha256, event_count + 1, time.time(),
                    self._tenant, sequence, event_count, previous, previous,
                ),
            )
            if updated.rowcount != 1:
                raise AuditAuthorityError("canonical audit head commit conflicted")
            return sequence

    @staticmethod
    def _load_payload(conn: Any, tenant: str, event_id: str) -> dict[str, Any]:
        row = conn.execute(
            f"SELECT event_sealed FROM {_EVENT_TABLE} "
            "WHERE tenant_id=%s AND event_id=%s",
            (tenant, event_id),
        ).fetchone()
        if row is None:
            raise AuditIntegrityError("canonical audit event disappeared")
        return _open_payload(str(_value(row, 0, "event_sealed")), tenant)

    def read(
        self,
        *,
        day: str | None = None,
        since: str | None = None,
        until: str | None = None,
        tail: int | None = None,
    ) -> list[dict[str, Any]]:
        if day is not None and not is_valid_day(day):
            raise ValueError(f"invalid audit day {day!r}: expected YYYY-MM-DD")
        if since is not None and not is_valid_day(since):
            raise ValueError(f"invalid audit since {since!r}: expected YYYY-MM-DD")
        if until is not None and not is_valid_day(until):
            raise ValueError(f"invalid audit until {until!r}: expected YYYY-MM-DD")
        if tail is not None and (isinstance(tail, bool) or not isinstance(tail, int) or tail < 0):
            raise ValueError("audit tail limit must be a non-negative integer")
        with self.transaction("query") as conn:
            trust = _load_trust_state(conn)
            head = conn.execute(
                f"SELECT next_sequence, head_sha256, event_count FROM {_HEAD_TABLE} "
                "WHERE tenant_id=%s FOR SHARE",
                (self._tenant,),
            ).fetchone()
            if head is None:
                return []
            metadata = conn.execute(
                f"SELECT sequence, event_id, event_kind, event_day, event_ts, "
                "idempotency_sha256, payload_sha256, prev_sha256, event_sha256, "
                f"signature, key_id, public_key, signer_epoch FROM {_EVENT_TABLE} "
                "WHERE tenant_id=%s ORDER BY sequence",
                (self._tenant,),
            ).fetchall()
            if len(metadata) > _MAX_QUERY_EVENTS:
                raise AuditAuthorityError("canonical audit query exceeds the event limit")
            self._verify_metadata(metadata, head, trust)
            clauses = ["tenant_id=%s"]
            params: list[Any] = [self._tenant]
            if day is not None:
                clauses.append("event_day=%s")
                params.append(day)
            else:
                if since is not None:
                    clauses.append("event_day>=%s")
                    params.append(since)
                if until is not None:
                    clauses.append("event_day<=%s")
                    params.append(until)
            order = "ORDER BY sequence"
            if tail is not None:
                order = "ORDER BY sequence DESC LIMIT %s"
                params.append(tail)
            rows = conn.execute(
                f"SELECT sequence, event_id, payload_sha256, event_sealed, "
                f"prev_sha256, event_sha256, signature, key_id, public_key, "
                f"signer_epoch "
                f"FROM {_EVENT_TABLE} WHERE {' AND '.join(clauses)} {order}",
                tuple(params),
            ).fetchall()
            if tail is not None:
                rows = list(reversed(rows))
            return [self._decode_row(row) for row in rows]

    def _verify_metadata(
        self, rows: list[Any], head: Any, trust: _TrustState
    ) -> None:
        previous: str | None = None
        for expected_sequence, row in enumerate(rows, start=1):
            sequence = _value(row, 0, "sequence")
            event_id = _value(row, 1, "event_id")
            event_kind = _value(row, 2, "event_kind")
            event_day = _value(row, 3, "event_day")
            event_ts = _value(row, 4, "event_ts")
            idempotency_sha256 = _value(row, 5, "idempotency_sha256")
            payload_sha256 = _value(row, 6, "payload_sha256")
            row_previous = _value(row, 7, "prev_sha256")
            event_sha256 = _value(row, 8, "event_sha256")
            signature = _value(row, 9, "signature")
            key_id = _value(row, 10, "key_id")
            public_key = _value(row, 11, "public_key")
            signer_epoch = _value(row, 12, "signer_epoch")
            if (
                sequence != expected_sequence
                or not isinstance(event_id, str)
                or not _EVENT_ID_RE.fullmatch(event_id)
                or not isinstance(event_kind, str)
                or not event_kind
                or not isinstance(event_day, str)
                or not is_valid_day(event_day)
                or not isinstance(event_ts, (int, float))
                or isinstance(event_ts, bool)
                or not math.isfinite(float(event_ts))
                or not isinstance(idempotency_sha256, str)
                or not _HASH_RE.fullmatch(idempotency_sha256)
                or not isinstance(payload_sha256, str)
                or not _HASH_RE.fullmatch(payload_sha256)
                or row_previous != previous
                or not isinstance(event_sha256, str)
                or not _HASH_RE.fullmatch(event_sha256)
                or not isinstance(signature, str)
                or not isinstance(key_id, str)
                or not _KEY_ID_RE.fullmatch(key_id)
                or not isinstance(public_key, str)
                or not re.fullmatch(r"[0-9a-f]{64}", public_key)
                or hashlib.sha256(bytes.fromhex(public_key)).hexdigest()[:16] != key_id
                or not isinstance(signer_epoch, int)
                or isinstance(signer_epoch, bool)
                or signer_epoch < 1
            ):
                raise AuditIntegrityError("canonical audit sequence metadata is corrupt")
            body = _chain_body(
                tenant=self._tenant,
                sequence=sequence,
                event_id=event_id,
                event_kind=event_kind,
                event_day=event_day,
                event_ts=float(event_ts),
                idempotency_sha256=idempotency_sha256,
                payload_sha256=payload_sha256,
                prev_sha256=previous,
                key_id=key_id,
                public_key=public_key,
                signer_epoch=signer_epoch,
            )
            if _hash_chain_body(body) != event_sha256:
                raise AuditIntegrityError("canonical audit metadata hash chain is corrupt")
            _trusted_event_signer(
                trust,
                signer_epoch=signer_epoch,
                key_id=key_id,
                public_key=public_key,
            )
            _verify_signature(public_key, signature, event_sha256)
            previous = event_sha256
        next_sequence = _value(head, 0, "next_sequence")
        head_hash = _value(head, 1, "head_sha256")
        event_count = _value(head, 2, "event_count")
        if (
            next_sequence != len(rows) + 1
            or event_count != len(rows)
            or head_hash != previous
        ):
            raise AuditIntegrityError("canonical audit head and event chain disagree")

    def _decode_row(self, row: Any) -> dict[str, Any]:
        sequence = _value(row, 0, "sequence")
        event_id = str(_value(row, 1, "event_id"))
        payload_sha256 = str(_value(row, 2, "payload_sha256"))
        payload = _open_payload(str(_value(row, 3, "event_sealed")), self._tenant)
        if hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest() != payload_sha256:
            raise AuditIntegrityError("canonical audit encrypted payload hash is corrupt")
        if payload.get("event_id") != event_id:
            raise AuditIntegrityError("canonical audit event identity is inconsistent")
        payload.update({
            "sequence": sequence,
            "prev_hash": _value(row, 4, "prev_sha256") or "",
            "hash": _value(row, 5, "event_sha256"),
            "sig": _value(row, 6, "signature"),
            "key_id": _value(row, 7, "key_id"),
            "signer_epoch": _value(row, 9, "signer_epoch"),
        })
        return payload


def _open_payload(sealed: str, tenant: str) -> dict[str, Any]:
    try:
        if not sealed.startswith("MVKAR1:"):
            raise ValueError("payload is not sealed")
        with _tenant_context(tenant):
            from ..crypto_at_rest import unseal_from_str

            raw = unseal_from_str(sealed)

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("duplicate JSON key")
                out[key] = value
            return out

        payload = json.loads(raw, object_pairs_hook=reject_duplicates)
        if not isinstance(payload, dict):
            raise ValueError("payload is not an object")
        _canonical_json(payload)
        return payload
    except AuditAuthorityError:
        raise
    except Exception as exc:  # failure-policy: fail_closed
        raise AuditIntegrityError("canonical audit encrypted payload is corrupt") from exc


class PostgresAuditLog:
    """AuditLog-compatible facade over the canonical shared authority."""

    def __init__(
        self,
        *,
        tenant: str | None = None,
        global_scope: bool = False,
        connect: Callable[[], Any] | None = None,
        backend: _PostgresAuditBackend | None = None,
    ) -> None:
        selection = _selection()
        if str(selection.kind) != "postgres" or not str(selection.dsn):
            raise AuditAuthorityError("Postgres canonical audit is not configured")
        self._tenant = _canonical_tenant(tenant, global_scope=global_scope)
        self._global_scope = global_scope
        self._authority_token = _dsn_token(str(selection.dsn))
        self._backend = backend or _PostgresAuditBackend(
            str(selection.dsn), self._tenant, connect=connect
        )

    @property
    def tenant(self) -> str:
        return self._tenant

    def _admit(self) -> None:
        selection = _selection()
        if (
            str(selection.kind) != "postgres"
            or _dsn_token(str(selection.dsn)) != self._authority_token
        ):
            raise AuditAuthorityError(
                "canonical audit authority changed after log construction"
            )
        _require_encryption()
        assert_no_legacy_local_audit(
            self._tenant, global_scope=self._global_scope
        )
        # Re-evaluate replica policy on every admission. A cached writer must
        # not retain the weaker single-node custody rule after an operator
        # enables a shared-required deployment in the same process.
        if bool(selection.shared_required):
            try:
                from .signing import active_key_is_offhost

                offhost = active_key_is_offhost()
            except Exception as exc:  # failure-policy: fail_closed
                raise AuditAuthorityError(
                    "shared canonical audit signing custody could not be verified"
                ) from exc
            if not offhost:
                raise AuditAuthorityError(
                    "multi-replica canonical audit requires an off-host shared signing key"
                )

    def record(self, event: AuditEvent) -> bool:
        self._admit()
        payload, event_id = _validated_payload(event, self._tenant)
        self._backend.append(payload, event_id)
        return True

    def tail(self, n: int = 50, day: str | None = None) -> list[dict[str, Any]]:
        self._admit()
        if day is None:
            day = time.strftime("%Y-%m-%d", time.gmtime())
        return self._backend.read(day=day, tail=n)

    def grep(self, pattern: str, day: str | None = None) -> list[dict[str, Any]]:
        self._admit()
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern {pattern!r}: {exc}") from exc
        if day is None:
            day = time.strftime("%Y-%m-%d", time.gmtime())
        rows = self._backend.read(day=day)
        return [
            row for row in rows
            if rx.search(json.dumps(row, sort_keys=True, default=str))
        ]

    def iter_events(
        self,
        *,
        day: str | None = None,
        all_days: bool = False,
        since: str | None = None,
        until: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        self._admit()
        if day is not None:
            rows = self._backend.read(day=day)
        elif since is not None or until is not None:
            rows = self._backend.read(since=since, until=until)
        elif all_days:
            rows = self._backend.read()
        else:
            rows = self._backend.read(day=time.strftime("%Y-%m-%d", time.gmtime()))
        yield from rows

    def reanchor_after_erase(self) -> int:
        raise AuditAuthorityError(
            "shared canonical audit is append-only; use an authorized redaction "
            "event/migration workflow instead of local re-anchoring"
        )


class PostgresAuditTrust:
    """Explicit operator ceremony for shared audit signer bootstrap/rotation."""

    def __init__(self, *, connect: Callable[[], Any] | None = None) -> None:
        selection = _selection()
        if str(selection.kind) != "postgres" or not str(selection.dsn):
            raise AuditAuthorityError("Postgres canonical audit is not configured")
        self._authority_token = _dsn_token(str(selection.dsn))
        self._backend = _PostgresAuditBackend(
            str(selection.dsn), _GLOBAL_TENANT, connect=connect
        )

    def _admit(self) -> None:
        selection = _selection()
        if (
            str(selection.kind) != "postgres"
            or _dsn_token(str(selection.dsn)) != self._authority_token
        ):
            raise AuditAuthorityError(
                "canonical audit authority changed during signer ceremony"
            )
        _configured_trust_root()
        if bool(selection.shared_required):
            try:
                from .signing import active_key_is_offhost

                offhost = active_key_is_offhost()
            except Exception as exc:  # failure-policy: fail_closed
                raise AuditAuthorityError(
                    "shared canonical audit signing custody could not be verified"
                ) from exc
            if not offhost:
                raise AuditAuthorityError(
                    "multi-replica canonical audit signer ceremonies require an "
                    "off-host shared signing key"
                )

    def bootstrap(self) -> str:
        self._admit()
        key_id = self._backend.bootstrap_signer_trust()
        self._admit()
        return key_id

    def rotate(
        self,
        next_public_key: str,
        *,
        revoke_previous: bool = False,
        replace_pending_key_id: str | None = None,
    ) -> str:
        self._admit()
        key_id = self._backend.authorize_signer_rotation(
            next_public_key,
            revoke_previous=revoke_previous,
            replace_pending_key_id=replace_pending_key_id,
        )
        self._admit()
        return key_id


__all__ = [
    "AuditAuthorityError",
    "AuditIdempotencyConflict",
    "AuditIntegrityError",
    "PostgresAuditLog",
    "PostgresAuditTrust",
    "assert_no_legacy_local_audit",
    "canonical_audit_trust_root_digest",
    "refuse_local_audit_in_shared_mode",
    "shared_audit_selected",
]
