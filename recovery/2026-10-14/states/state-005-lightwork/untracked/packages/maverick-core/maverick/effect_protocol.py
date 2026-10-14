"""Durable, idempotent transaction protocol for external tool effects.

An approval proves that a human allowed an action; it does not prove that a
timed-out provider call did or did not happen.  This module supplies that second
boundary.  A logical action is claimed durably before dispatch, transitions by
compare-and-swap, and can be reconciled through a typed provider callback.

The protocol is opt-in through :class:`EffectSpec` outside strict deployments.
Enterprise/strict governed writes require the metadata so no write-capable
connector can silently bypass the durable claim.  The store persists only a
digest and a bounded, non-content summary of provider results; raw response
bodies remain in the live return path and are never written here.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_IDENTITY_LIMIT = 256
_RESULT_SUMMARY_LIMIT = 256
_RECEIPT_LIMIT = 16_384
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}\Z")
_SENSITIVE_RECEIPT_KEY = re.compile(
    r"(?:secret|token|password|passwd|credential|authorization|api[_-]?key|"
    r"private[_-]?key|cookie)",
    re.IGNORECASE,
)
_MAX_RECEIPT_DEPTH = 6
_MAX_RECEIPT_ITEMS = 128
_PREPARED_CLAIM_TTL_SECONDS = 300.0
_POSTGRES_EFFECT_SCHEMA_LOCK = 0x6D766B656666  # ``mvkeff``
_POSTGRES_EFFECT_COLUMN_SHAPES = {
    "logical_key": ("text", "NO"),
    "tenant": ("text", "NO"),
    "principal": ("text", "NO"),
    "run_id": ("text", "NO"),
    "node_id": ("text", "NO"),
    "connector": ("text", "NO"),
    "operation": ("text", "NO"),
    "request_digest": ("text", "NO"),
    "state": ("text", "NO"),
    "claim_token_hash": ("text", "NO"),
    "compensation_claim_hash": ("text", "YES"),
    "compensation_state": ("text", "NO"),
    "compensation_result": ("text", "NO"),
    "compensation_result_digest": ("text", "NO"),
    "compensation_receipt_json": ("text", "NO"),
    "result": ("text", "NO"),
    "result_digest": ("text", "NO"),
    "receipt_json": ("text", "NO"),
    "created_at": ("double precision", "NO"),
    "updated_at": ("double precision", "NO"),
}
_POSTGRES_EFFECT_COLUMNS = frozenset(_POSTGRES_EFFECT_COLUMN_SHAPES)
_POSTGRES_EFFECT_CHECKS = {
    "ck_mvk_effect_state": (
        "(state = 'PREPARED' OR state = 'SENT' OR state = 'COMMITTED' OR "
        "state = 'ABORTED' OR state = 'INDETERMINATE' OR state = 'COMPENSATED')"
    ),
    "ck_mvk_effect_compensation_state": (
        "(compensation_state = 'NOT_STARTED' OR "
        "compensation_state = 'COMPENSATING' OR "
        "compensation_state = 'INDETERMINATE' OR "
        "compensation_state = 'COMPENSATED')"
    ),
    "ck_mvk_effect_tenant_length": (
        "(char_length(tenant) >= 1 AND char_length(tenant) <= 256)"
    ),
    "ck_mvk_effect_principal_length": (
        "(char_length(principal) >= 1 AND char_length(principal) <= 256)"
    ),
    "ck_mvk_effect_run_id_length": (
        "(char_length(run_id) >= 1 AND char_length(run_id) <= 256)"
    ),
    "ck_mvk_effect_node_id_length": (
        "(char_length(node_id) >= 1 AND char_length(node_id) <= 256)"
    ),
    "ck_mvk_effect_connector_length": (
        "(char_length(connector) >= 1 AND char_length(connector) <= 256)"
    ),
    "ck_mvk_effect_operation_length": (
        "(char_length(operation) >= 1 AND char_length(operation) <= 256)"
    ),
    "ck_mvk_effect_logical_key": "(logical_key ~ '^[0-9a-f]{64}$')",
    "ck_mvk_effect_request_digest": "(request_digest ~ '^[0-9a-f]{64}$')",
    "ck_mvk_effect_claim_token": "(claim_token_hash ~ '^[0-9a-f]{64}$')",
    "ck_mvk_effect_compensation_claim": (
        "(compensation_claim_hash IS NULL OR "
        "compensation_claim_hash ~ '^[0-9a-f]{64}$')"
    ),
    "ck_mvk_effect_result_digest": (
        "(result_digest = '' OR result_digest ~ '^[0-9a-f]{64}$')"
    ),
    "ck_mvk_effect_compensation_result_digest": (
        "(compensation_result_digest = '' OR "
        "compensation_result_digest ~ '^[0-9a-f]{64}$')"
    ),
    "ck_mvk_effect_result_bytes": "(octet_length(result) <= 4096)",
    "ck_mvk_effect_compensation_result_bytes": (
        "(octet_length(compensation_result) <= 4096)"
    ),
    "ck_mvk_effect_receipt_bytes": "(octet_length(receipt_json) <= 65536)",
    "ck_mvk_effect_compensation_receipt_bytes": (
        "(octet_length(compensation_receipt_json) <= 65536)"
    ),
}


class EffectProtocolError(RuntimeError):
    """Base class for effect-protocol failures."""


class EffectConflict(EffectProtocolError):
    """A logical action key was reused for a different request digest."""


class InvalidTransition(EffectProtocolError):
    """A transaction attempted a transition outside the state machine."""


class CompensationUnavailable(EffectProtocolError):
    """No safe compensation is declared, or another compensator owns it."""


class EffectKind(str, Enum):
    READ = "read"
    WRITE = "write"


class IdempotencyMode(str, Enum):
    NONE = "none"
    # A kernel-owned key is sent, but the provider has not been certified to
    # deduplicate it.  It is useful for correlation, never sufficient for retry.
    KEYED = "keyed"
    # The provider contract guarantees that the same key cannot duplicate the
    # operation.  Only this mode can make a write retry-safe.
    NATIVE = "native"


class EffectState(str, Enum):
    PREPARED = "PREPARED"
    SENT = "SENT"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    INDETERMINATE = "INDETERMINATE"
    COMPENSATED = "COMPENSATED"


class CompensationState(str, Enum):
    """Durable lifecycle of an explicitly requested compensation.

    Compensation has its own state because the original provider effect remains
    committed while an undo call is in flight or ambiguous.  Collapsing those
    conditions into ``COMMITTED`` made a crash after the compensation claim
    indistinguishable from an unstarted compensation.
    """

    NOT_STARTED = "NOT_STARTED"
    COMPENSATING = "COMPENSATING"
    INDETERMINATE = "INDETERMINATE"
    COMPENSATED = "COMPENSATED"


class ClaimOutcome(str, Enum):
    OWNER = "owner"
    CACHED = "cached"
    IN_PROGRESS = "in_progress"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class EffectIdentity:
    """Stable identity of one logical external action.

    ``node_id`` may be a flow node id or a provider tool-call id.  It must be
    stable across delivery retries; the request digest is deliberately excluded
    so reuse of a logical identity for different content can be rejected.
    """

    tenant: str
    principal: str
    run_id: str
    node_id: str
    connector: str
    operation: str

    def __post_init__(self) -> None:
        for name in (
            "tenant", "principal", "run_id", "node_id", "connector", "operation",
        ):
            value = str(getattr(self, name)).strip()
            if not value or len(value) > _IDENTITY_LIMIT or "\x00" in value:
                raise ValueError(f"effect identity {name} is missing or invalid")
            if name in {"connector", "operation"}:
                value = value.lower()
            object.__setattr__(self, name, value)

    @property
    def logical_key(self) -> str:
        payload = {
            "connector": self.connector,
            "node": self.node_id,
            "operation": self.operation,
            "principal": self.principal,
            "run": self.run_id,
            "tenant": self.tenant,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ProviderReceipt:
    """Provider-owned identifier and bounded, JSON-safe receipt metadata."""

    provider_id: str = ""
    status: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        metadata = _sanitize_receipt_value(dict(self.metadata))
        if not isinstance(metadata, dict):  # pragma: no cover - mapping invariant
            raise ValueError("provider receipt metadata must be an object")
        raw = json.dumps(
            {
                "provider_id": _redact_receipt_text(
                    str(self.provider_id)[:_IDENTITY_LIMIT]
                ),
                "status": _redact_receipt_text(str(self.status)[:_IDENTITY_LIMIT]),
                "metadata": metadata,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(raw.encode("utf-8")) > _RECEIPT_LIMIT:
            raise ValueError("provider receipt exceeds its storage limit")
        return raw

    @classmethod
    def from_json(cls, raw: str) -> ProviderReceipt:
        data = json.loads(raw or "{}")
        if not isinstance(data, dict):
            raise ValueError("invalid provider receipt")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("invalid provider receipt")
        return cls(
            provider_id=str(data.get("provider_id") or ""),
            status=str(data.get("status") or ""),
            metadata=metadata,
        )


@dataclass(frozen=True)
class ProviderStatus:
    """A typed reconciliation answer from the external provider."""

    state: EffectState
    receipt: ProviderReceipt | None = None
    result: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", EffectState(self.state))
        if self.state not in {
            EffectState.COMMITTED,
            EffectState.ABORTED,
            EffectState.INDETERMINATE,
        }:
            raise ValueError("provider status must resolve to committed, aborted, or unknown")


@dataclass(frozen=True)
class ProviderCompensationStatus:
    """Typed provider answer used to reconcile an ambiguous compensation.

    ``NOT_STARTED`` is accepted as bounded negative evidence for diagnostics,
    but it is not terminal proof and never authorizes replay of an undo whose
    prior owner may still be executing.
    """

    state: CompensationState
    receipt: ProviderReceipt | None = None
    result: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", CompensationState(self.state))
        if self.state not in {
            CompensationState.NOT_STARTED,
            CompensationState.INDETERMINATE,
            CompensationState.COMPENSATED,
        }:
            raise ValueError(
                "provider compensation status must resolve to not started, "
                "compensated, or unknown"
            )
        if self.state is CompensationState.COMPENSATED and self.receipt is None:
            raise ValueError("a compensated provider status requires a receipt")


ProviderStatusResolver = Callable[["EffectRecord"], ProviderStatus]
ProviderCompensator = Callable[["EffectRecord"], ProviderReceipt]
ProviderCompensationStatusResolver = Callable[
    ["EffectRecord"], ProviderCompensationStatus
]
OperationResolver = Callable[[Mapping[str, Any]], str]


def _redact_receipt_text(value: str) -> str:
    try:
        from .safety.secret_detector import redact

        return redact(str(value))[0]
    except Exception:  # failure-policy: fail_closed
        return "[REDACTED:detector-unavailable]" if value else ""


def _sanitize_receipt_value(value: Any, *, depth: int = 0) -> Any:
    """Return bounded JSON data that cannot persist credential-shaped fields."""
    if depth > _MAX_RECEIPT_DEPTH:
        return "[TRUNCATED:depth]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else "[REDACTED:non-finite]"
    if isinstance(value, str):
        return _redact_receipt_text(value)[:4096]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:_MAX_RECEIPT_ITEMS]:
            key = str(raw_key)[:256]
            if _SENSITIVE_RECEIPT_KEY.search(key):
                result[key] = "[REDACTED:sensitive-key]"
            else:
                result[key] = _sanitize_receipt_value(raw_value, depth=depth + 1)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_receipt_value(item, depth=depth + 1)
            for item in list(value)[:_MAX_RECEIPT_ITEMS]
        ]
    return "[REDACTED:unsupported-type]"


@dataclass(frozen=True)
class EffectSpec:
    """Effect semantics attached to a :class:`maverick.tools.Tool`.

    Callbacks run only from explicit reconciliation/compensation entry points;
    normal model execution cannot invoke them directly.
    """

    connector: str
    kind: EffectKind = EffectKind.WRITE
    operation_arg: str = "op"
    operation_resolver: OperationResolver | None = field(default=None, repr=False)
    idempotency: IdempotencyMode = IdempotencyMode.NONE
    idempotency_header: str = "Idempotency-Key"
    retry_safe: bool = False
    status_resolver: ProviderStatusResolver | None = field(default=None, repr=False)
    compensator: ProviderCompensator | None = field(default=None, repr=False)
    compensation_status_resolver: ProviderCompensationStatusResolver | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", EffectKind(self.kind))
        object.__setattr__(self, "idempotency", IdempotencyMode(self.idempotency))
        connector = str(self.connector).strip().lower()
        if not connector or len(connector) > _IDENTITY_LIMIT:
            raise ValueError("effect connector is missing or invalid")
        object.__setattr__(self, "connector", connector)
        if self.operation_resolver is not None and not callable(self.operation_resolver):
            raise TypeError("effect operation_resolver must be callable")
        if self.status_resolver is not None and not callable(self.status_resolver):
            raise TypeError("effect status_resolver must be callable")
        if self.compensator is not None and not callable(self.compensator):
            raise TypeError("effect compensator must be callable")
        if (
            self.compensation_status_resolver is not None
            and not callable(self.compensation_status_resolver)
        ):
            raise TypeError("effect compensation_status_resolver must be callable")
        header = str(self.idempotency_header).strip()
        if header and not _HEADER_NAME.fullmatch(header):
            raise ValueError("effect idempotency header is invalid")
        object.__setattr__(self, "idempotency_header", header)
        if self.idempotency is not IdempotencyMode.NONE and not header:
            raise ValueError("keyed effect idempotency requires a protected header")
        if (
            self.kind is EffectKind.WRITE
            and self.retry_safe
            and self.idempotency is not IdempotencyMode.NATIVE
        ):
            raise ValueError("write retry safety requires certified native idempotency")

    def operation_for(self, args: Mapping[str, Any]) -> str:
        value = (
            self.operation_resolver(args)
            if self.operation_resolver is not None
            else args.get(self.operation_arg)
        )
        operation = str(value or self.kind.value).strip().lower()
        if not operation or len(operation) > _IDENTITY_LIMIT or "\x00" in operation:
            raise ValueError("effect operation is missing or invalid")
        return operation


@dataclass(frozen=True)
class EffectContext:
    tenant: str = ""
    principal: str = ""
    run_id: str = ""
    node_id: str = ""


_EFFECT_CONTEXT: contextvars.ContextVar[EffectContext | None] = contextvars.ContextVar(
    "maverick_effect_context", default=None,
)
_IDEMPOTENCY_KEY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "maverick_effect_idempotency_key", default="",
)
_IDEMPOTENCY_HEADER: contextvars.ContextVar[str] = contextvars.ContextVar(
    "maverick_effect_idempotency_header", default="",
)


@contextlib.contextmanager
def bind_effect_context(
    *,
    tenant: str = "",
    principal: str = "",
    run_id: str = "",
    node_id: str = "",
) -> Iterator[EffectContext]:
    """Bind stable execution identity, retaining unspecified outer fields."""

    outer = _EFFECT_CONTEXT.get() or EffectContext()
    value = EffectContext(
        tenant=str(tenant or outer.tenant),
        principal=str(principal or outer.principal),
        run_id=str(run_id or outer.run_id),
        node_id=str(node_id or outer.node_id),
    )
    token = _EFFECT_CONTEXT.set(value)
    try:
        yield value
    finally:
        _EFFECT_CONTEXT.reset(token)


def current_effect_context() -> EffectContext:
    return _EFFECT_CONTEXT.get() or EffectContext()


@contextlib.contextmanager
def bind_idempotency_key(
    value: str,
    *,
    header: str = "Idempotency-Key",
) -> Iterator[str]:
    """Bind the kernel-owned provider key for a single outbound call."""

    key = str(value).strip()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("provider idempotency key must be a logical effect digest")
    header_name = str(header).strip()
    if header_name and not _HEADER_NAME.fullmatch(header_name):
        raise ValueError("provider idempotency header is invalid")
    key_token = _IDEMPOTENCY_KEY.set(key)
    header_token = _IDEMPOTENCY_HEADER.set(header_name)
    try:
        yield key
    finally:
        _IDEMPOTENCY_HEADER.reset(header_token)
        _IDEMPOTENCY_KEY.reset(key_token)


def current_idempotency_key() -> str:
    return _IDEMPOTENCY_KEY.get()


def current_idempotency_header() -> str:
    return _IDEMPOTENCY_HEADER.get()


def request_digest(args: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(
            dict(args),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("effect request must be canonical JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class EffectRecord:
    identity: EffectIdentity
    request_digest: str
    state: EffectState
    result: str = ""
    result_digest: str = ""
    receipt: ProviderReceipt | None = None
    compensation_state: CompensationState = CompensationState.NOT_STARTED
    compensation_result: str = ""
    compensation_result_digest: str = ""
    compensation_receipt: ProviderReceipt | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def logical_key(self) -> str:
        return self.identity.logical_key

    @property
    def idempotency_key(self) -> str:
        return self.logical_key


@dataclass(frozen=True)
class EffectClaim:
    outcome: ClaimOutcome
    record: EffectRecord
    claim_token: str = field(default="", repr=False)


def _token_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _result_evidence(value: str, *, outcome: str) -> tuple[str, str]:
    """Return ``(safe_summary, sha256)`` without retaining provider content.

    A redactor is useful for display, but it is not a proof that arbitrary
    provider bodies contain no credentials, PII, or customer data.  Durable
    effect state therefore stores a fixed, internally-authored summary and a
    digest of the exact result.  The raw result is still returned to the live
    caller on the first execution; cached replays expose only this evidence.
    """

    raw = str(value)
    encoded = raw.encode("utf-8", errors="replace")
    digest = hashlib.sha256(encoded).hexdigest()
    normalized = str(outcome).strip().lower().replace("_", "-")
    if normalized not in {
        "aborted",
        "committed",
        "compensated",
        "compensation-indeterminate",
        "compensation-not-started",
        "indeterminate",
    }:
        normalized = "provider"
    summary = f"{normalized} result content withheld; bytes={len(encoded)}"
    if len(summary.encode("utf-8")) > _RESULT_SUMMARY_LIMIT:  # pragma: no cover
        raise EffectProtocolError("internal effect result summary exceeded its limit")
    return summary, digest


def _storage_encode(value: str) -> str:
    if not value:
        return ""
    from .crypto_at_rest import at_rest_enabled, seal_to_str

    return seal_to_str(value) if at_rest_enabled() else value


def _storage_decode(value: str) -> str:
    if not value:
        return ""
    from .crypto_at_rest import unseal_from_str

    return unseal_from_str(value)


def _effect_backend_posture() -> tuple[bool, bool]:
    """Return ``(postgres_selected, shared_store_required)`` fail-closed.

    The effect ledger is authority state.  A deployment-global enterprise
    posture or more than one declared replica may not silently create a
    per-node SQLite ledger.  Configuration errors therefore raise the shared
    floor instead of being interpreted as community/single-host mode.
    """

    config: Mapping[str, Any] = {}
    source_errors: object = ()
    try:
        from .config import config_source_errors, load_global_config

        loaded = load_global_config()
        if not isinstance(loaded, Mapping):
            source_errors = ("invalid configuration root",)
        else:
            config = loaded
        source_errors = source_errors or config_source_errors(include_tenant=False)
    except Exception:
        source_errors = ("configuration unavailable",)

    world = config.get("world_model") or {}
    governed = config.get("governed_records") or {}
    configured_backend = (
        str(world.get("backend") or "").strip().lower()
        if isinstance(world, Mapping)
        else ""
    )
    env_backend = os.environ.get("MAVERICK_WORLD_BACKEND", "").strip().lower()
    selected_backend = env_backend or configured_backend or "sqlite"
    backend_invalid = selected_backend not in {"sqlite", "postgres"}
    governed_backend = (
        str(governed.get("backend") or "auto").strip().lower()
        if isinstance(governed, Mapping)
        else ""
    )
    governed_invalid = governed_backend not in {"auto", "local", "postgres"}
    postgres_selected = (
        selected_backend == "postgres" or governed_backend == "postgres"
    )

    try:
        from .enterprise import deployment_enterprise_enabled

        enterprise = deployment_enterprise_enabled(
            config=config,
            source_errors=source_errors,
        )
    except Exception:
        enterprise = True

    replica_raw = os.environ.get("MAVERICK_REPLICA_COUNT", "1").strip()
    try:
        replica_count = int(replica_raw)
        replica_invalid = replica_count < 1 or str(replica_count) != replica_raw
    except (TypeError, ValueError, OverflowError):
        replica_count = 0
        replica_invalid = True
    shared_required = bool(
        enterprise
        or backend_invalid
        or governed_invalid
        or replica_invalid
        or replica_count > 1
        or source_errors
    )
    return postgres_selected, shared_required


def _postgres_effect_dsn() -> str:
    """Resolve the deployment-global world DSN without returning it in errors."""

    dsn = os.environ.get("MAVERICK_PG_DSN", "").strip()
    if not dsn:
        try:
            from .config import load_global_config

            config = load_global_config()
            world = config.get("world_model") or {}
            if isinstance(world, Mapping):
                dsn = str(world.get("dsn") or "").strip()
        except Exception:
            dsn = ""
    if not dsn:
        raise EffectProtocolError(
            "shared Postgres effect storage is selected but no DSN is configured"
        )
    return dsn


_POSTGRES_EFFECT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS effect_transactions (
    logical_key TEXT NOT NULL,
    tenant TEXT NOT NULL,
    principal TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    connector TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    claim_token_hash TEXT NOT NULL,
    compensation_claim_hash TEXT,
    compensation_state TEXT NOT NULL DEFAULT 'NOT_STARTED',
    compensation_result TEXT NOT NULL DEFAULT '',
    compensation_result_digest TEXT NOT NULL DEFAULT '',
    compensation_receipt_json TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    result_digest TEXT NOT NULL DEFAULT '',
    receipt_json TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant, logical_key),
    CONSTRAINT ck_mvk_effect_state
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_state"]},
    CONSTRAINT ck_mvk_effect_compensation_state
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_compensation_state"]},
    CONSTRAINT ck_mvk_effect_tenant_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_tenant_length"]},
    CONSTRAINT ck_mvk_effect_principal_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_principal_length"]},
    CONSTRAINT ck_mvk_effect_run_id_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_run_id_length"]},
    CONSTRAINT ck_mvk_effect_node_id_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_node_id_length"]},
    CONSTRAINT ck_mvk_effect_connector_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_connector_length"]},
    CONSTRAINT ck_mvk_effect_operation_length
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_operation_length"]},
    CONSTRAINT ck_mvk_effect_logical_key
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_logical_key"]},
    CONSTRAINT ck_mvk_effect_request_digest
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_request_digest"]},
    CONSTRAINT ck_mvk_effect_claim_token
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_claim_token"]},
    CONSTRAINT ck_mvk_effect_compensation_claim
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_compensation_claim"]},
    CONSTRAINT ck_mvk_effect_result_digest
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_result_digest"]},
    CONSTRAINT ck_mvk_effect_compensation_result_digest
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_compensation_result_digest"]},
    CONSTRAINT ck_mvk_effect_result_bytes
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_result_bytes"]},
    CONSTRAINT ck_mvk_effect_compensation_result_bytes
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_compensation_result_bytes"]},
    CONSTRAINT ck_mvk_effect_receipt_bytes
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_receipt_bytes"]},
    CONSTRAINT ck_mvk_effect_compensation_receipt_bytes
        CHECK {_POSTGRES_EFFECT_CHECKS["ck_mvk_effect_compensation_receipt_bytes"]}
)
"""
_POSTGRES_EFFECT_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_pg_effect_transactions_state "
    "ON effect_transactions(tenant, state, updated_at)"
)
_POSTGRES_EFFECT_POLICY_SQL = (
    "CREATE POLICY mvk_effect_tenant_isolation ON effect_transactions "
    "USING (tenant = nullif(current_setting('maverick.tenant', true), '')) "
    "WITH CHECK (tenant = nullif(current_setting('maverick.tenant', true), ''))"
)


def _row_item(row: object, key: str, index: int) -> object:
    if isinstance(row, Mapping):
        return row.get(key)
    try:
        keys = row.keys()  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        keys = ()
    if key in keys:
        return row[key]  # type: ignore[index]
    return row[index]  # type: ignore[index]


def _policy_expression_is_exact(value: object) -> bool:
    text = "".join(str(value or "").lower().split()).replace("::text", "")
    expected = "tenant=nullif(current_setting('maverick.tenant',true),'')"
    return text in {expected, f"({expected})"}


def _policy_roles(value: object) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(str(item).strip().lower() for item in value)
    return tuple(
        item.strip().strip("\"'").lower()
        for item in str(value or "").strip("{}").split(",")
        if item.strip()
    )


def _policies_are_exact(rows: list[object]) -> bool:
    """Postgres permissive policies OR together, so extras are a bypass."""

    if len(rows) != 1:
        return False
    row = rows[0]
    return (
        str(_row_item(row, "policyname", 0)) == "mvk_effect_tenant_isolation"
        and str(_row_item(row, "permissive", 1)).upper() == "PERMISSIVE"
        and _policy_roles(_row_item(row, "roles", 2)) == ("public",)
        and str(_row_item(row, "cmd", 3)).upper() == "ALL"
        and _policy_expression_is_exact(_row_item(row, "qual", 4))
        and _policy_expression_is_exact(_row_item(row, "with_check", 5))
    )


def _canonical_postgres_check(value: object) -> str:
    expression = "".join(str(value or "").lower().replace('"', "").split())
    for cast in ("::text", "::bigint", "::integer"):
        expression = expression.replace(cast, "")
    return expression.replace("~~", "like").replace("(", "").replace(")", "")


def _verify_postgres_effect_schema(conn: Any) -> None:
    role_cursor = conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles "
        "WHERE rolname = current_user"
    )
    role_row = role_cursor.fetchone()
    if role_row is None or (
        bool(_row_item(role_row, "rolsuper", 0))
        or bool(_row_item(role_row, "rolbypassrls", 1))
    ):
        # FORCE RLS still does not constrain superusers or roles carrying the
        # BYPASSRLS attribute.  Accepting either would turn the database tenant
        # boundary into documentation rather than enforcement.
        raise EffectProtocolError(
            "shared effect storage requires a non-bypass Postgres role"
        )
    columns_cursor = conn.execute(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = current_schema() "
        "AND table_name = 'effect_transactions'"
    )
    columns = {
        str(_row_item(row, "column_name", 0)): (
            str(_row_item(row, "data_type", 1)).lower(),
            str(_row_item(row, "is_nullable", 2)).upper(),
        )
        for row in columns_cursor.fetchall()
    }
    if columns != _POSTGRES_EFFECT_COLUMN_SHAPES:
        raise EffectProtocolError("shared effect table has an incompatible schema")

    pk_cursor = conn.execute(
        "SELECT a.attname AS column_name FROM pg_index i "
        "JOIN pg_class c ON c.oid = i.indrelid "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "JOIN pg_attribute a ON a.attrelid = c.oid "
        "AND a.attnum = ANY(i.indkey) "
        "WHERE n.nspname = current_schema() "
        "AND c.relname = 'effect_transactions' AND i.indisprimary"
    )
    pk_columns = {
        str(_row_item(row, "column_name", 0)) for row in pk_cursor.fetchall()
    }
    if pk_columns != {"tenant", "logical_key"}:
        raise EffectProtocolError(
            "shared effect table lacks tenant-bound effect-key uniqueness"
        )

    check_cursor = conn.execute(
        "SELECT c.conname, pg_get_expr(c.conbin, c.conrelid) AS expression, "
        "c.convalidated FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = current_schema() "
        "AND t.relname = 'effect_transactions' AND c.contype = 'c'"
    )
    actual_checks = {
        str(_row_item(row, "conname", 0)): (
            _canonical_postgres_check(_row_item(row, "expression", 1)),
            bool(_row_item(row, "convalidated", 2)),
        )
        for row in check_cursor.fetchall()
    }
    expected_checks = {
        name: (_canonical_postgres_check(expression), True)
        for name, expression in _POSTGRES_EFFECT_CHECKS.items()
    }
    if actual_checks != expected_checks:
        raise EffectProtocolError(
            "shared effect table constraints are incompatible"
        )

    rls_cursor = conn.execute(
        "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() "
        "AND c.relname = 'effect_transactions'"
    )
    rls_row = rls_cursor.fetchone()
    if rls_row is None or not (
        bool(_row_item(rls_row, "relrowsecurity", 0))
        and bool(_row_item(rls_row, "relforcerowsecurity", 1))
    ):
        raise EffectProtocolError("shared effect table does not force tenant RLS")

    policy_cursor = conn.execute(
        "SELECT policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_policies "
        "WHERE schemaname = current_schema() "
        "AND tablename = 'effect_transactions'"
    )
    if not _policies_are_exact(list(policy_cursor.fetchall())):
        raise EffectProtocolError("shared effect tenant policy is not fail-closed")


def _initialize_postgres_effect_schema(conn: Any) -> None:
    """Create/verify the shared table under a cross-process advisory lock.

    The DDL is additive.  It is deliberately serialized because concurrent
    ``CREATE TABLE/INDEX IF NOT EXISTS`` is not safe against PostgreSQL catalog
    races.  A pre-existing incompatible table or permissive policy is refused;
    this routine never replaces an unknown policy in place.
    """

    try:
        conn.execute("BEGIN")
        conn.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_POSTGRES_EFFECT_SCHEMA_LOCK,),
        )
        exists_cursor = conn.execute(
            "SELECT to_regclass(current_schema() || '.effect_transactions') "
            "IS NOT NULL AS table_exists"
        )
        exists_row = exists_cursor.fetchone()
        table_exists = bool(
            _row_item(exists_row, "table_exists", 0)
            if exists_row is not None
            else False
        )
        if table_exists:
            try:
                _verify_postgres_effect_schema(conn)
            except EffectProtocolError:
                # An owner/migration role may be able to complete an additive
                # partial bootstrap below.  Incompatible state still fails the
                # final verification; no permissive policy is overwritten.
                pass
            else:
                conn.commit()
                return
        conn.execute(_POSTGRES_EFFECT_TABLE_SQL)
        conn.execute(_POSTGRES_EFFECT_INDEX_SQL)
        conn.execute(
            "ALTER TABLE effect_transactions ENABLE ROW LEVEL SECURITY"
        )
        conn.execute(
            "ALTER TABLE effect_transactions FORCE ROW LEVEL SECURITY"
        )
        policy_cursor = conn.execute(
            "SELECT policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies "
            "WHERE schemaname = current_schema() "
            "AND tablename = 'effect_transactions'"
        )
        policies = list(policy_cursor.fetchall())
        if not policies:
            conn.execute(_POSTGRES_EFFECT_POLICY_SQL)
        elif not _policies_are_exact(policies):
            raise EffectProtocolError(
                "shared effect tenant policy is not fail-closed"
            )
        _verify_postgres_effect_schema(conn)
        conn.commit()
    except EffectProtocolError:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise EffectProtocolError(
            "shared Postgres effect schema initialization failed"
        ) from None


class _NoopCursor:
    rowcount = -1

    @staticmethod
    def fetchone() -> None:
        return None

    @staticmethod
    def fetchall() -> list[object]:
        return []


class _PostgresConnectionAdapter:
    """Small DB-API adapter so the state machine stays backend-identical."""

    def __init__(self, connection: Any):
        self._connection = connection
        self._active = False

    @property
    def in_transaction(self) -> bool:
        return self._active

    def begin(self) -> None:
        if not self._active:
            self._connection.execute("BEGIN")
            self._active = True

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        normalized = " ".join(sql.strip().upper().split())
        if normalized in {"BEGIN", "BEGIN IMMEDIATE"}:
            self.begin()
            return _NoopCursor()
        if normalized == "COMMIT":
            self._connection.commit()
            self._active = False
            return _NoopCursor()
        if normalized == "ROLLBACK":
            self._connection.rollback()
            self._active = False
            return _NoopCursor()
        return self._connection.execute(sql.replace("?", "%s"), params)

    def commit_if_active(self) -> None:
        if self._active:
            self._connection.commit()
            self._active = False

    def rollback_if_active(self) -> None:
        if self._active:
            self._connection.rollback()
            self._active = False


class EffectStore:
    """Backend-selecting CAS store for logical external actions.

    Explicit ``path`` values retain the single-host SQLite compatibility path.
    With no path, a configured Postgres world or governed-record backend
    selects :class:`PostgresEffectStore`. Enterprise and multi-replica
    deployments refuse an implicit SQLite store.
    """

    def __new__(
        cls,
        path: str | Path | None = None,
        *,
        require_encryption: bool = False,
        require_shared: bool | None = None,
        **backend_options: Any,
    ) -> EffectStore:
        del require_encryption, backend_options
        if cls is not EffectStore:
            return super().__new__(cls)
        postgres_selected, deployment_requires_shared = _effect_backend_posture()
        shared_required = deployment_requires_shared or require_shared is True
        if postgres_selected and path is None:
            return super().__new__(PostgresEffectStore)
        if shared_required or (postgres_selected and path is not None):
            raise EffectProtocolError(
                "node-local effect storage is refused; use the configured "
                "Postgres effect backend for enterprise or multiple replicas"
            )
        return super().__new__(cls)

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        require_encryption: bool = False,
        require_shared: bool | None = None,
    ):
        del require_shared
        self._path_override = Path(path).expanduser() if path is not None else None
        self._require_encryption = bool(require_encryption)
        self._encryption_validated = False

    @property
    def backend(self) -> str:
        return "sqlite"

    @property
    def shared(self) -> bool:
        return False

    @staticmethod
    def _transaction_time(_connection: object) -> float:
        """Return the clock that orders mutations in this store transaction."""

        return time.time()

    def authoritative_time(self, tenant: str) -> float:
        """Return the store clock used for cross-worker lease decisions."""

        with self._connect(tenant) as connection:
            return self._transaction_time(connection)

    def require_encryption(self) -> None:
        """Raise this store's floor to encrypted writes and reads.

        The floor is monotonic because a store shared by strict and non-strict
        wrappers must keep the strictest posture once admitted.
        """

        self._require_encryption = True

    def assert_storage_policy(self) -> None:
        """Prove strict effect storage can seal data before provider dispatch."""

        if not self._require_encryption:
            return
        from .crypto_at_rest import at_rest_enabled, seal_to_str, unseal_from_str

        if not at_rest_enabled():
            raise EffectProtocolError(
                "strict effect storage requires at-rest encryption"
            )
        if self._encryption_validated:
            return
        probe = "maverick-effect-storage-policy-check"
        sealed = seal_to_str(probe)
        if sealed == probe or unseal_from_str(sealed) != probe:
            raise EffectProtocolError("strict effect storage encryption is unavailable")
        self._encryption_validated = True

    def _encode(self, value: str) -> str:
        if not value:
            return ""
        if self._require_encryption:
            self.assert_storage_policy()
            from .crypto_at_rest import seal_to_str

            return seal_to_str(value)
        return _storage_encode(value)

    def _path(self, tenant: str) -> Path:
        if self._path_override is not None:
            return self._path_override
        from .paths import data_dir

        storage_tenant = None if tenant == "<shared>" else tenant
        return data_dir("effects", "transactions.sqlite3", tenant=storage_tenant)

    @contextlib.contextmanager
    def _connect(self, tenant: str) -> Iterator[sqlite3.Connection]:
        from .file_lock import ensure_private_directory, ensure_private_file

        path = self._path(tenant)
        ensure_private_directory(path.parent)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            ensure_private_file(path, 0o600)
        else:
            os.close(fd)
            ensure_private_file(path, 0o600)

        conn = sqlite3.connect(str(path), timeout=10.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA trusted_schema=OFF")
            conn.execute("PRAGMA secure_delete=ON")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS effect_transactions (
                    logical_key TEXT PRIMARY KEY,
                    tenant TEXT NOT NULL,
                    principal TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    connector TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN (
                        'PREPARED', 'SENT', 'COMMITTED', 'ABORTED',
                        'INDETERMINATE', 'COMPENSATED'
                    )),
                    claim_token_hash TEXT NOT NULL,
                    compensation_claim_hash TEXT,
                    compensation_state TEXT NOT NULL DEFAULT 'NOT_STARTED'
                        CHECK (compensation_state IN (
                            'NOT_STARTED', 'COMPENSATING', 'INDETERMINATE',
                            'COMPENSATED'
                        )),
                    compensation_result TEXT NOT NULL DEFAULT '',
                    compensation_result_digest TEXT NOT NULL DEFAULT '',
                    compensation_receipt_json TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    result_digest TEXT NOT NULL DEFAULT '',
                    receipt_json TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS effect_transactions_state
                    ON effect_transactions(tenant, state, updated_at);
            """)
            # Development builds may already have created the pre-compensation-
            # lifecycle schema.  Additive columns preserve those local records
            # without weakening their original effect-state CHECK constraint.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(effect_transactions)")
            }
            if "compensation_state" not in columns:
                conn.execute(
                    "ALTER TABLE effect_transactions ADD COLUMN "
                    "compensation_state TEXT NOT NULL DEFAULT 'NOT_STARTED' "
                    "CHECK (compensation_state IN ('NOT_STARTED', 'COMPENSATING', "
                    "'INDETERMINATE', 'COMPENSATED'))"
                )
            if "compensation_result" not in columns:
                conn.execute(
                    "ALTER TABLE effect_transactions ADD COLUMN "
                    "compensation_result TEXT NOT NULL DEFAULT ''"
                )
            if "compensation_receipt_json" not in columns:
                conn.execute(
                    "ALTER TABLE effect_transactions ADD COLUMN "
                    "compensation_receipt_json TEXT NOT NULL DEFAULT ''"
                )
            if "result_digest" not in columns:
                conn.execute(
                    "ALTER TABLE effect_transactions ADD COLUMN "
                    "result_digest TEXT NOT NULL DEFAULT ''"
                )
            if "compensation_result_digest" not in columns:
                conn.execute(
                    "ALTER TABLE effect_transactions ADD COLUMN "
                    "compensation_result_digest TEXT NOT NULL DEFAULT ''"
                )
            self._migrate_payload_policy(conn)
            yield conn
        finally:
            conn.close()

    def _migrate_payload_policy(self, conn: sqlite3.Connection) -> None:
        """Scrub legacy raw results and seal plaintext strict-store payloads.

        Early development versions stored the raw provider result.  Adding the
        digest columns is not sufficient because those bytes would remain on
        disk.  Opening the store opportunistically replaces them with the same
        fixed summary used for new transitions.  Strict mode also seals safe
        summaries and sanitized receipts in place, so reconciliation survives
        the posture upgrade without accepting plaintext payload columns.
        """

        from .crypto_at_rest import is_sealed_str

        rows = conn.execute(
            "SELECT logical_key, tenant, state, result, result_digest, receipt_json, "
            "compensation_state, compensation_result, "
            "compensation_result_digest, compensation_receipt_json "
            "FROM effect_transactions WHERE result <> '' OR receipt_json <> '' "
            "OR compensation_result <> '' OR compensation_receipt_json <> ''"
        ).fetchall()
        for row in rows:
            result = str(row["result"] or "")
            result_digest = str(row["result_digest"] or "")
            compensation_result = str(row["compensation_result"] or "")
            compensation_result_digest = str(
                row["compensation_result_digest"] or ""
            )
            receipt_json = str(row["receipt_json"] or "")
            compensation_receipt_json = str(
                row["compensation_receipt_json"] or ""
            )
            changed = False

            if result and not result_digest:
                legacy_result = _storage_decode(result)
                result, result_digest = _result_evidence(
                    legacy_result, outcome=str(row["state"]),
                )
                result = self._encode(result)
                changed = True
            elif result and self._require_encryption and not is_sealed_str(result):
                result = self._encode(result)
                changed = True

            if compensation_result and not compensation_result_digest:
                legacy_compensation = _storage_decode(compensation_result)
                compensation_result, compensation_result_digest = _result_evidence(
                    legacy_compensation,
                    outcome=(
                        "compensated"
                        if str(row["compensation_state"]) == "COMPENSATED"
                        else "compensation-indeterminate"
                    ),
                )
                compensation_result = self._encode(compensation_result)
                changed = True
            elif (
                compensation_result
                and self._require_encryption
                and not is_sealed_str(compensation_result)
            ):
                compensation_result = self._encode(compensation_result)
                changed = True

            if receipt_json and self._require_encryption and not is_sealed_str(
                receipt_json
            ):
                receipt_json = self._encode(
                    ProviderReceipt.from_json(receipt_json).to_json()
                )
                changed = True
            if (
                compensation_receipt_json
                and self._require_encryption
                and not is_sealed_str(compensation_receipt_json)
            ):
                compensation_receipt_json = self._encode(
                    ProviderReceipt.from_json(compensation_receipt_json).to_json()
                )
                changed = True

            if changed:
                conn.execute(
                    "UPDATE effect_transactions SET result = ?, result_digest = ?, "
                    "receipt_json = ?, compensation_result = ?, "
                    "compensation_result_digest = ?, "
                    "compensation_receipt_json = ? "
                    "WHERE logical_key = ? AND tenant = ?",
                    (
                        result,
                        result_digest,
                        receipt_json,
                        compensation_result,
                        compensation_result_digest,
                        compensation_receipt_json,
                        str(row["logical_key"]),
                        str(row["tenant"]),
                    ),
                )

    def _decode(self, value: object) -> str:
        raw = str(value or "")
        if not raw:
            return ""
        if self._require_encryption:
            from .crypto_at_rest import is_sealed_str

            if not is_sealed_str(raw):
                raise EffectProtocolError(
                    "strict effect storage contains an unencrypted payload"
                )
        return _storage_decode(raw)

    def _record(self, row: sqlite3.Row) -> EffectRecord:
        identity = EffectIdentity(
            tenant=str(row["tenant"]),
            principal=str(row["principal"]),
            run_id=str(row["run_id"]),
            node_id=str(row["node_id"]),
            connector=str(row["connector"]),
            operation=str(row["operation"]),
        )
        receipt_raw = self._decode(row["receipt_json"])
        compensation_receipt_raw = self._decode(row["compensation_receipt_json"])
        result_digest = str(row["result_digest"] or "")
        compensation_result_digest = str(
            row["compensation_result_digest"] or ""
        )
        if result_digest and not re.fullmatch(r"[0-9a-f]{64}", result_digest):
            raise EffectProtocolError("effect result digest is invalid")
        if compensation_result_digest and not re.fullmatch(
            r"[0-9a-f]{64}", compensation_result_digest
        ):
            raise EffectProtocolError("compensation result digest is invalid")
        return EffectRecord(
            identity=identity,
            request_digest=str(row["request_digest"]),
            state=EffectState(str(row["state"])),
            result=self._decode(row["result"]),
            result_digest=result_digest,
            receipt=ProviderReceipt.from_json(receipt_raw) if receipt_raw else None,
            compensation_state=CompensationState(str(row["compensation_state"])),
            compensation_result=self._decode(row["compensation_result"]),
            compensation_result_digest=compensation_result_digest,
            compensation_receipt=(
                ProviderReceipt.from_json(compensation_receipt_raw)
                if compensation_receipt_raw
                else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _select(
        conn: sqlite3.Connection,
        key: str,
        tenant: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM effect_transactions "
            "WHERE logical_key = ? AND tenant = ?",
            (key, tenant),
        ).fetchone()

    def get(self, identity: EffectIdentity) -> EffectRecord | None:
        with self._connect(identity.tenant) as conn:
            row = self._select(conn, identity.logical_key, identity.tenant)
        return self._record(row) if row is not None else None

    def claim(self, identity: EffectIdentity, digest: str) -> EffectClaim:
        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("request digest must be lowercase SHA-256")
        key = identity.logical_key
        token = secrets.token_hex(32)
        now = time.time()
        with self._connect(identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._select(conn, key, identity.tenant)
                if row is None:
                    conn.execute(
                        "INSERT INTO effect_transactions("
                        "logical_key, tenant, principal, run_id, node_id, connector, "
                        "operation, request_digest, state, claim_token_hash, created_at, "
                        "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?)",
                        (
                            key, identity.tenant, identity.principal, identity.run_id,
                            identity.node_id, identity.connector, identity.operation,
                            digest, _token_digest(token), now, now,
                        ),
                    )
                    row = self._select(conn, key, identity.tenant)
                    if row is None:  # pragma: no cover -- SQLite insert invariant
                        raise EffectProtocolError("effect claim disappeared after insert")
                    conn.execute("COMMIT")
                    return EffectClaim(ClaimOutcome.OWNER, self._record(row), token)

                record = self._record(row)
                if not hmac.compare_digest(record.request_digest, digest):
                    outcome = ClaimOutcome.CONFLICT
                elif record.state in {
                    EffectState.COMMITTED, EffectState.ABORTED, EffectState.COMPENSATED,
                }:
                    outcome = ClaimOutcome.CACHED
                else:
                    outcome = ClaimOutcome.IN_PROGRESS
                conn.execute("COMMIT")
                return EffectClaim(outcome, record)
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise

    def _owner_transition(
        self,
        claim: EffectClaim,
        *,
        allowed: tuple[EffectState, ...],
        target: EffectState,
        result: str = "",
        receipt: ProviderReceipt | None = None,
    ) -> EffectRecord:
        if claim.outcome is not ClaimOutcome.OWNER or not claim.claim_token:
            raise InvalidTransition("only the durable claim owner may dispatch an effect")
        if target is EffectState.SENT:
            result, result_digest = "", ""
        else:
            result, result_digest = _result_evidence(result, outcome=target.value)
            result = self._encode(result)
        receipt_json = self._encode(
            receipt.to_json() if receipt is not None else ""
        )
        allowed_values = tuple(state.value for state in allowed)
        placeholders = ",".join("?" for _ in allowed_values)
        key = claim.record.logical_key
        with self._connect(claim.record.identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions SET state = ?, result = ?, "
                    "result_digest = ?, receipt_json = ?, updated_at = ? "
                    "WHERE logical_key = ? AND tenant = ? "
                    f"AND state IN ({placeholders}) AND claim_token_hash = ?",
                    (
                        target.value, result, result_digest, receipt_json, now, key,
                        claim.record.identity.tenant,
                        *allowed_values, _token_digest(claim.claim_token),
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidTransition(
                        f"effect cannot transition to {target.value} from its current state"
                    )
                row = self._select(conn, key, claim.record.identity.tenant)
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after transition")
        return self._record(row)

    def mark_sent(self, claim: EffectClaim) -> EffectRecord:
        return self._owner_transition(
            claim, allowed=(EffectState.PREPARED,), target=EffectState.SENT,
        )

    def commit(
        self,
        claim: EffectClaim,
        result: str,
        receipt: ProviderReceipt | None = None,
    ) -> EffectRecord:
        return self._owner_transition(
            claim,
            allowed=(EffectState.SENT,),
            target=EffectState.COMMITTED,
            result=result,
            receipt=receipt,
        )

    def abort(self, claim: EffectClaim, result: str = "") -> EffectRecord:
        return self._owner_transition(
            claim,
            allowed=(EffectState.PREPARED, EffectState.SENT),
            target=EffectState.ABORTED,
            result=result,
        )

    def mark_indeterminate(self, claim: EffectClaim, result: str = "") -> EffectRecord:
        return self._owner_transition(
            claim,
            allowed=(EffectState.SENT,),
            target=EffectState.INDETERMINATE,
            result=result,
        )

    def resolve(
        self,
        identity: EffectIdentity,
        digest: str,
        status: ProviderStatus,
    ) -> EffectRecord:
        if status.state is EffectState.INDETERMINATE:
            record = self.get(identity)
            if record is None:
                raise InvalidTransition("cannot reconcile an unknown effect")
            return record
        result, result_digest = _result_evidence(
            status.result, outcome=status.state.value,
        )
        result = self._encode(result)
        receipt_json = self._encode(
            status.receipt.to_json() if status.receipt is not None else ""
        )
        with self._connect(identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions SET state = ?, result = ?, "
                    "result_digest = ?, receipt_json = ?, updated_at = ? "
                    "WHERE logical_key = ? AND tenant = ? "
                    "AND request_digest = ? AND state IN ('SENT', 'INDETERMINATE')",
                    (
                        status.state.value, result, result_digest, receipt_json, now,
                        identity.logical_key, identity.tenant, digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidTransition("effect is not eligible for reconciliation")
                row = self._select(conn, identity.logical_key, identity.tenant)
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after reconciliation")
        return self._record(row)

    def _claim_compensation(self, record: EffectRecord) -> tuple[str, EffectRecord]:
        token = secrets.token_hex(32)
        with self._connect(record.identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions SET compensation_claim_hash = ?, "
                    "compensation_state = 'COMPENSATING', compensation_result = '', "
                    "compensation_result_digest = '', "
                    "compensation_receipt_json = '', updated_at = ? "
                    "WHERE logical_key = ? AND tenant = ? AND request_digest = ? "
                    "AND state = 'COMMITTED' "
                    "AND compensation_state = 'NOT_STARTED' "
                    "AND compensation_claim_hash IS NULL",
                    (
                        _token_digest(token), now, record.logical_key,
                        record.identity.tenant,
                        record.request_digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CompensationUnavailable(
                        "effect is not committed or compensation is not ready to start"
                    )
                row = self._select(
                    conn, record.logical_key, record.identity.tenant,
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after compensation claim")
        return token, self._record(row)

    def mark_compensation_indeterminate(
        self,
        record: EffectRecord,
        compensation_token: str,
        result: str = "",
    ) -> EffectRecord:
        """Quarantine a compensation whose provider outcome is not proven."""

        compensation_result, compensation_result_digest = _result_evidence(
            result, outcome="compensation-indeterminate",
        )
        compensation_result = self._encode(compensation_result)
        with self._connect(record.identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions "
                    "SET compensation_state = 'INDETERMINATE', "
                    "compensation_result = ?, compensation_result_digest = ?, "
                    "updated_at = ? "
                    "WHERE logical_key = ? AND tenant = ? AND request_digest = ? "
                    "AND state = 'COMMITTED' "
                    "AND compensation_state = 'COMPENSATING' "
                    "AND compensation_claim_hash = ?",
                    (
                        compensation_result,
                        compensation_result_digest,
                        now,
                        record.logical_key,
                        record.identity.tenant,
                        record.request_digest,
                        _token_digest(compensation_token),
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidTransition(
                        "compensation claim no longer owns the effect"
                    )
                row = self._select(
                    conn, record.logical_key, record.identity.tenant,
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after compensation failure")
        return self._record(row)

    def mark_compensated(
        self,
        record: EffectRecord,
        compensation_token: str,
        receipt: ProviderReceipt,
    ) -> EffectRecord:
        receipt_json = self._encode(receipt.to_json())
        with self._connect(record.identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions SET state = 'COMPENSATED', "
                    "compensation_state = 'COMPENSATED', "
                    "compensation_receipt_json = ?, updated_at = ? "
                    "WHERE logical_key = ? AND tenant = ? "
                    "AND request_digest = ? AND state = 'COMMITTED' "
                    "AND compensation_state = 'COMPENSATING' "
                    "AND compensation_claim_hash = ?",
                    (
                        receipt_json, now, record.logical_key,
                        record.identity.tenant, record.request_digest,
                        _token_digest(compensation_token),
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidTransition("compensation claim no longer owns the effect")
                row = self._select(
                    conn, record.logical_key, record.identity.tenant,
                )
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after compensation")
        return self._record(row)

    def resolve_compensation(
        self,
        identity: EffectIdentity,
        digest: str,
        status: ProviderCompensationStatus,
    ) -> EffectRecord:
        """Settle a crashed or ambiguous compensation from provider evidence."""

        if status.state is CompensationState.NOT_STARTED:
            # A negative provider lookup is not terminal proof that an in-flight
            # callback did not apply the undo just before, during, or after the
            # lookup.  Clearing the token here would let another worker issue a
            # duplicate compensation while the original owner is still alive.
            raise CompensationUnavailable(
                "provider NOT_STARTED status is not terminal proof; "
                "compensation remains quarantined"
            )
        if status.state is CompensationState.COMPENSATING:
            raise InvalidTransition("provider cannot resolve to an in-flight state")
        compensation_result, compensation_result_digest = _result_evidence(
            status.result,
            outcome=(
                "compensated"
                if status.state is CompensationState.COMPENSATED
                else "compensation-not-started"
            ),
        )
        compensation_result = self._encode(compensation_result)
        compensation_receipt_json = self._encode(
            status.receipt.to_json() if status.receipt is not None else ""
        )
        target_effect_state = (
            EffectState.COMPENSATED
            if status.state is CompensationState.COMPENSATED
            else EffectState.COMMITTED
        )
        with self._connect(identity.tenant) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = self._transaction_time(conn)
                cursor = conn.execute(
                    "UPDATE effect_transactions SET state = ?, "
                    "compensation_state = ?, compensation_result = ?, "
                    "compensation_result_digest = ?, "
                    "compensation_receipt_json = ?, compensation_claim_hash = ?, "
                    "updated_at = ? WHERE logical_key = ? AND tenant = ? "
                    "AND request_digest = ? "
                    "AND state = 'COMMITTED' AND compensation_state IN "
                    "('COMPENSATING', 'INDETERMINATE')",
                    (
                        target_effect_state.value,
                        status.state.value,
                        compensation_result,
                        compensation_result_digest,
                        compensation_receipt_json,
                        self._compensation_claim_hash(
                            conn, identity.logical_key, identity.tenant,
                        ),
                        now,
                        identity.logical_key,
                        identity.tenant,
                        digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise InvalidTransition(
                        "compensation is not eligible for reconciliation"
                    )
                row = self._select(conn, identity.logical_key, identity.tenant)
                conn.execute("COMMIT")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        if row is None:  # pragma: no cover -- update invariant
            raise EffectProtocolError("effect disappeared after compensation reconciliation")
        return self._record(row)

    @staticmethod
    def _compensation_claim_hash(
        conn: sqlite3.Connection,
        key: str,
        tenant: str,
    ) -> str | None:
        row = conn.execute(
            "SELECT compensation_claim_hash FROM effect_transactions "
            "WHERE logical_key = ? AND tenant = ?",
            (key, tenant),
        ).fetchone()
        return None if row is None else row["compensation_claim_hash"]


class PostgresEffectStore(EffectStore):
    """Fleet-shared Postgres effect ledger with tenant RLS and CAS writes.

    Connections are short-lived by default so callers do not share a psycopg
    transaction across threads.  Tests and embedders may inject a connection
    factory; production connection and schema errors are converted to fixed
    messages so credential-bearing DSNs never reach tool results or logs.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        require_encryption: bool = False,
        require_shared: bool | None = None,
        _connection_factory: Callable[[], Any] | None = None,
        _initialize_schema: bool = True,
    ):
        del require_encryption
        if path is not None:
            raise ValueError("a Postgres effect store does not accept a file path")
        _selected, deployment_requires_shared = _effect_backend_posture()
        self._strict_tenant_scope = bool(
            deployment_requires_shared or require_shared is True
        )
        # Shared rows must be readable by a different replica without ever
        # persisting summaries or receipt metadata in plaintext.
        self._require_encryption = True
        self._encryption_validated = False
        self._schema_ready = not _initialize_schema
        self._schema_lock = threading.Lock()
        self._legacy_checked: set[str] = set()
        self._legacy_lock = threading.Lock()

        if _connection_factory is not None:
            self._connection_factory = _connection_factory
        else:
            dsn = _postgres_effect_dsn()
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError:
                raise EffectProtocolError(
                    "shared Postgres effect storage requires the postgres extra"
                ) from None

            def _connect() -> Any:
                return psycopg.connect(
                    dsn,
                    autocommit=False,
                    row_factory=dict_row,
                )

            self._connection_factory = _connect
        self.assert_storage_policy()

    @property
    def backend(self) -> str:
        return "postgres"

    @property
    def shared(self) -> bool:
        return True

    @staticmethod
    def _transaction_time(connection: Any) -> float:
        row = connection.execute(
            "SELECT EXTRACT(EPOCH FROM clock_timestamp()) AS authority_time"
        ).fetchone()
        if row is None:
            raise EffectProtocolError(
                "shared Postgres effect authority clock is unavailable"
            )
        try:
            value = float(_row_item(row, "authority_time", 0))
        except (TypeError, ValueError, OverflowError) as exc:
            raise EffectProtocolError(
                "shared Postgres effect authority clock is invalid"
            ) from exc
        if not math.isfinite(value) or value <= 0:
            raise EffectProtocolError(
                "shared Postgres effect authority clock is invalid"
            )
        return value

    def _assert_tenant_scope(self, tenant: str) -> None:
        try:
            from .paths import current_tenant_id_strict

            active_tenant = current_tenant_id_strict()
        except Exception:
            raise EffectProtocolError(
                "shared effect storage could not resolve the tenant boundary"
            ) from None
        if active_tenant is not None and active_tenant != tenant:
            raise EffectProtocolError(
                "shared effect storage tenant does not match the active tenant"
            )
        if active_tenant is None:
            if self._strict_tenant_scope:
                raise EffectProtocolError(
                    "shared effect storage requires an explicitly bound tenant"
                )
            if tenant != "<shared>":
                raise EffectProtocolError(
                    "unbound shared effect storage accepts only the internal "
                    "shared tenant"
                )

    def _assert_no_legacy_claims(self, tenant: str) -> None:
        """Refuse a cutover that would forget local in-flight effects."""

        if tenant in self._legacy_checked:
            return
        with self._legacy_lock:
            if tenant in self._legacy_checked:
                return
            from .paths import data_dir

            storage_tenant = None if tenant == "<shared>" else tenant
            path = data_dir(
                "effects",
                "transactions.sqlite3",
                tenant=storage_tenant,
            )
            if path.exists():
                connection: sqlite3.Connection | None = None
                try:
                    # ``mode=ro`` prevents a check/delete race from silently
                    # creating a fresh empty database and declaring cutover safe.
                    uri = path.resolve().as_uri() + "?mode=ro"
                    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
                    table = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'effect_transactions'"
                    ).fetchone()
                    claim = (
                        connection.execute(
                            "SELECT 1 FROM effect_transactions LIMIT 1"
                        ).fetchone()
                        if table is not None
                        else None
                    )
                except (OSError, sqlite3.Error):
                    raise EffectProtocolError(
                        "legacy effect ledger could not be inspected before "
                        "Postgres cutover"
                    ) from None
                finally:
                    if connection is not None:
                        connection.close()
                if claim is not None:
                    raise EffectProtocolError(
                        "legacy SQLite effect ledger contains claims; reconcile "
                        "and migrate it before Postgres cutover"
                    )
            self._legacy_checked.add(tenant)

    def _ensure_schema(self, connection: Any) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            _initialize_postgres_effect_schema(connection)
            self._schema_ready = True

    @contextlib.contextmanager
    def _connect(self, tenant: str) -> Iterator[Any]:
        self.assert_storage_policy()
        self._assert_tenant_scope(tenant)
        self._assert_no_legacy_claims(tenant)
        try:
            raw_connection = self._connection_factory()
        except Exception:
            raise EffectProtocolError(
                "shared Postgres effect storage is unavailable"
            ) from None
        connection = _PostgresConnectionAdapter(raw_connection)
        try:
            self._ensure_schema(raw_connection)
            connection.begin()
            # Recheck the complete database-enforced boundary for every
            # authority transaction.  In particular, an added permissive RLS
            # policy would be OR-combined by Postgres and must not remain an
            # undetected cross-tenant bypass after startup.
            _verify_postgres_effect_schema(connection)
            connection.execute(
                "SELECT set_config('maverick.tenant', ?, true)",
                (tenant,),
            )
            yield connection
            connection.commit_if_active()
        except (EffectProtocolError, ValueError):
            connection.rollback_if_active()
            raise
        except Exception:
            connection.rollback_if_active()
            raise EffectProtocolError(
                "shared Postgres effect storage transaction failed"
            ) from None
        finally:
            try:
                raw_connection.close()
            except Exception:
                pass

    def claim(self, identity: EffectIdentity, digest: str) -> EffectClaim:
        """Atomically insert one tenant/key owner or classify the winner."""

        if not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            raise ValueError("request digest must be lowercase SHA-256")
        key = identity.logical_key
        token = secrets.token_hex(32)
        with self._connect(identity.tenant) as conn:
            now = self._transaction_time(conn)
            cursor = conn.execute(
                "INSERT INTO effect_transactions("
                "logical_key, tenant, principal, run_id, node_id, connector, "
                "operation, request_digest, state, claim_token_hash, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARED', ?, ?, ?) "
                "ON CONFLICT (tenant, logical_key) DO NOTHING RETURNING *",
                (
                    key,
                    identity.tenant,
                    identity.principal,
                    identity.run_id,
                    identity.node_id,
                    identity.connector,
                    identity.operation,
                    digest,
                    _token_digest(token),
                    now,
                    now,
                ),
            )
            row = cursor.fetchone()
            if row is not None:
                conn.execute("COMMIT")
                return EffectClaim(ClaimOutcome.OWNER, self._record(row), token)

            row = conn.execute(
                "SELECT * FROM effect_transactions "
                "WHERE logical_key = ? AND tenant = ? FOR UPDATE",
                (key, identity.tenant),
            ).fetchone()
            if row is None:
                raise EffectProtocolError(
                    "effect claim conflict winner was not visible"
                )
            record = self._record(row)
            if not hmac.compare_digest(record.request_digest, digest):
                outcome = ClaimOutcome.CONFLICT
            elif record.state in {
                EffectState.COMMITTED,
                EffectState.ABORTED,
                EffectState.COMPENSATED,
            }:
                outcome = ClaimOutcome.CACHED
            elif (
                record.state is EffectState.PREPARED
                and now - record.updated_at >= _PREPARED_CLAIM_TTL_SECONDS
            ):
                prior_token_hash = str(row["claim_token_hash"])
                cursor = conn.execute(
                    "UPDATE effect_transactions SET claim_token_hash = ?, "
                    "updated_at = ? WHERE logical_key = ? AND tenant = ? "
                    "AND request_digest = ? AND state = 'PREPARED' "
                    "AND claim_token_hash = ?",
                    (
                        _token_digest(token),
                        now,
                        key,
                        identity.tenant,
                        digest,
                        prior_token_hash,
                    ),
                )
                if cursor.rowcount != 1:
                    raise EffectProtocolError(
                        "stale prepared effect claim changed during recovery"
                    )
                row = self._select(conn, key, identity.tenant)
                if row is None:  # pragma: no cover -- update invariant
                    raise EffectProtocolError(
                        "recovered effect claim disappeared after update"
                    )
                conn.execute("COMMIT")
                return EffectClaim(ClaimOutcome.OWNER, self._record(row), token)
            else:
                outcome = ClaimOutcome.IN_PROGRESS
            conn.execute("COMMIT")
            return EffectClaim(outcome, record)


def reconcile_effect(
    store: EffectStore,
    identity: EffectIdentity,
    spec: EffectSpec,
) -> EffectRecord:
    """Ask a typed read-only resolver to settle an ambiguous transaction."""

    record = store.get(identity)
    if record is None:
        raise InvalidTransition("cannot reconcile an unknown effect")
    if record.state not in {EffectState.SENT, EffectState.INDETERMINATE}:
        return record
    if spec.status_resolver is None:
        raise EffectProtocolError("effect has no provider status resolver")
    status = spec.status_resolver(record)
    if not isinstance(status, ProviderStatus):
        raise TypeError("provider status resolver returned an incompatible value")
    return store.resolve(identity, record.request_digest, status)


def compensate_effect(
    store: EffectStore,
    identity: EffectIdentity,
    spec: EffectSpec,
) -> EffectRecord:
    """Run one explicitly declared compensation and persist its receipt."""

    if spec.compensator is None:
        raise CompensationUnavailable("effect has no provider compensator")
    record = store.get(identity)
    if record is None:
        raise CompensationUnavailable("cannot compensate an unknown effect")
    token, claimed_record = store._claim_compensation(record)
    try:
        receipt = spec.compensator(claimed_record)
        if not isinstance(receipt, ProviderReceipt):
            raise TypeError("provider compensator returned an incompatible value")
    except Exception as exc:
        # The provider may have applied the undo before raising.  Persist the
        # ambiguity before surfacing the callback failure; automatic replay is
        # forbidden until a typed resolver proves the provider outcome.
        try:
            store.mark_compensation_indeterminate(
                claimed_record,
                token,
                type(exc).__name__[:120],
            )
        except Exception as state_exc:
            raise EffectProtocolError(
                "compensation failed and its indeterminate state could not be persisted"
            ) from state_exc
        raise
    return store.mark_compensated(record, token, receipt)


def reconcile_compensation(
    store: EffectStore,
    identity: EffectIdentity,
    spec: EffectSpec,
    *,
    stale_after_seconds: float = 300.0,
    now: float | None = None,
) -> EffectRecord:
    """Settle a crashed or ambiguous compensation through a typed resolver.

    A fresh ``COMPENSATING`` record may still have a live owner, so it cannot be
    reconciled until the bounded staleness window has elapsed. Shared stores use
    their database clock for this decision. An explicitly ``INDETERMINATE``
    record is immediately eligible, but only terminal provider proof may settle
    either state; a ``NOT_STARTED`` observation remains quarantined.
    """

    record = store.get(identity)
    if record is None:
        raise CompensationUnavailable("cannot reconcile an unknown effect")
    if record.compensation_state not in {
        CompensationState.COMPENSATING,
        CompensationState.INDETERMINATE,
    }:
        return record
    timeout = float(stale_after_seconds)
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError("compensation reconciliation timing is invalid")
    if store.shared:
        if now is not None:
            raise ValueError(
                "shared compensation reconciliation does not accept a caller clock"
            )
        current = float(store.authoritative_time(identity.tenant))
    else:
        current = float(time.time() if now is None else now)
    if not math.isfinite(current) or current <= 0:
        raise ValueError("compensation reconciliation timing is invalid")
    if (
        record.compensation_state is CompensationState.COMPENSATING
        and current - record.updated_at < timeout
    ):
        raise CompensationUnavailable("compensation claim may still have a live owner")
    resolver = spec.compensation_status_resolver
    if resolver is None:
        raise EffectProtocolError("effect has no provider compensation status resolver")
    status = resolver(record)
    if not isinstance(status, ProviderCompensationStatus):
        raise TypeError(
            "provider compensation status resolver returned an incompatible value"
        )
    return store.resolve_compensation(identity, record.request_digest, status)


__all__ = [
    "ClaimOutcome",
    "CompensationState",
    "CompensationUnavailable",
    "EffectClaim",
    "EffectConflict",
    "EffectContext",
    "EffectIdentity",
    "EffectKind",
    "EffectProtocolError",
    "EffectRecord",
    "EffectSpec",
    "EffectState",
    "EffectStore",
    "IdempotencyMode",
    "InvalidTransition",
    "PostgresEffectStore",
    "ProviderReceipt",
    "ProviderCompensationStatus",
    "ProviderStatus",
    "bind_effect_context",
    "bind_idempotency_key",
    "compensate_effect",
    "current_effect_context",
    "current_idempotency_header",
    "current_idempotency_key",
    "reconcile_compensation",
    "reconcile_effect",
    "request_digest",
]
