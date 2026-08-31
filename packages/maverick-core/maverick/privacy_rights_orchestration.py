"""Provider-neutral privacy-rights request orchestration authority.

This module coordinates local control-plane state only.  It performs no
network I/O, provider writes, response delivery, identity verification, or
legal-compliance determination.  Provider work is represented by a durable
callback outbox and must be performed by separately governed adapters.

The capability is closed unless both gates are explicitly enabled::

    [privacy_automation]
    enable = true
    [privacy_automation.rights_orchestration]
    enable = true

Raw subject identifiers and external request references are accepted only long
enough to derive tenant-keyed HMAC digests.  They are never persisted or
returned.  Sources contain opaque adapter and credential *references*, never
credentials, URLs, origins, or provider payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .file_lock import ensure_private_directory, ensure_private_file
from .paths import canonical_tenant_id

SCHEMA_VERSION = 1
AUDIT_SCHEMA = "lightwork.privacy-rights-orchestration-audit.v1"
MANIFEST_SCHEMA = "lightwork.privacy-rights-completeness-manifest.v1"
EXPORT_SCHEMA = "lightwork.privacy-rights-export.v1"
LIVE_PROVIDER_OPERATIONS = False
LIVE_PROVIDER_ACCEPTANCE_VERIFIED = False
RESPONSE_DELIVERY_IMPLEMENTED = False

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_OP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")
_CREDENTIAL_REF_RE = re.compile(
    r"(?:env|vault|secret):[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}\Z"
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_RE = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_LIST = 1_000
_MAX_SOURCES = 1_000
_MAX_TASKS = 10_000
_MAX_LEASE_SECONDS = 3_600
_MAX_BACKOFF_SECONDS = 7 * 24 * 3_600
_MIN_RETENTION_SECONDS = 60
_MAX_RETENTION_SECONDS = 10 * 365 * 24 * 3_600
_MAX_PACKAGE_SECONDS = 90 * 24 * 3_600

RIGHTS = frozenset(
    {
        "access",
        "portability",
        "erasure",
        "rectification",
        "restriction",
        "objection",
        "opt_out",
    }
)
TASK_PHASES = frozenset(
    {
        "discover",
        "retrieve",
        "redact",
        "delete",
        "verify_delete",
        "rectify",
        "restrict",
        "opt_out",
    }
)
CALLBACK_PHASES = frozenset(TASK_PHASES - {"redact"})
DESTRUCTIVE_PHASES = frozenset({"delete"})
IDENTITY_STATUSES = frozenset(
    {"unverified", "pending", "verified", "rejected", "expired"}
)
EVIDENCE_STATUSES = frozenset({"complete", "not_found", "partial", "failed"})
CALLBACK_STATES = frozenset({"pending", "leased", "acked", "dead_letter"})

_RIGHT_PHASES = {
    "access": ("discover", "retrieve", "redact"),
    "portability": ("discover", "retrieve", "redact"),
    "erasure": ("discover", "delete", "verify_delete"),
    "rectification": ("discover", "rectify"),
    "restriction": ("discover", "restrict"),
    "objection": ("discover", "opt_out"),
    "opt_out": ("discover", "opt_out"),
}


class PrivacyRightsOrchestrationError(RuntimeError):
    """Base privacy-rights orchestration error."""


class PrivacyRightsConfigError(PrivacyRightsOrchestrationError):
    """The two-level feature gate is not enabled."""


class PrivacyRightsValidationError(PrivacyRightsOrchestrationError, ValueError):
    """Input is outside a closed and bounded schema."""


class PrivacyRightsConflict(PrivacyRightsOrchestrationError):
    """A CAS, idempotency, lease, or state precondition failed."""


class PrivacyRightsNotFound(PrivacyRightsOrchestrationError):
    """A tenant-scoped record is absent or deliberately undisclosed."""


class PrivacyRightsPermissionDenied(PrivacyRightsOrchestrationError):
    """Authenticated authority or four-eyes separation is insufficient."""


class PrivacyRightsIntegrityError(PrivacyRightsOrchestrationError):
    """Persisted content or the audit chain failed integrity validation."""


def enabled() -> bool:
    """Return true only when both privacy-rights gates are explicitly true."""

    try:
        from .config import config_source_errors, load_config

        if config_source_errors():
            return False
        config = load_config()
    except Exception:
        return False
    if not isinstance(config, Mapping):
        return False
    privacy = config.get("privacy_automation")
    rights = (
        privacy.get("rights_orchestration")
        if isinstance(privacy, Mapping)
        else None
    )
    return (
        isinstance(privacy, Mapping)
        and privacy.get("enable") is True
        and isinstance(rights, Mapping)
        and rights.get("enable") is True
    )


def require_enabled() -> None:
    """Fail closed unless both configuration gates are explicitly enabled."""

    if not enabled():
        raise PrivacyRightsConfigError(
            "privacy rights orchestration is disabled; explicitly enable "
            "privacy_automation and privacy_automation.rights_orchestration"
        )


def _runtime_principal() -> str | None:
    try:
        from .connections import current_principal

        return current_principal()
    except Exception:
        return None


def _default_digest_key_resolver(tenant_id: str) -> bytes:
    del tenant_id
    value = os.environ.get("MAVERICK_PRIVACY_RIGHTS_DIGEST_KEY")
    if value is None or len(value.encode("utf-8")) < 32:
        raise PrivacyRightsConfigError(
            "MAVERICK_PRIVACY_RIGHTS_DIGEST_KEY is absent or shorter than 32 bytes"
        )
    return value.encode("utf-8")


def _canonical(value: object, *, maximum: int = _MAX_JSON_BYTES) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PrivacyRightsValidationError(
            "privacy-rights state must be finite canonical JSON"
        ) from exc
    if not 1 <= len(encoded) <= maximum:
        raise PrivacyRightsValidationError(
            "privacy-rights state exceeds its canonical byte bound"
        )
    return encoded


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _tenant(value: object) -> str:
    try:
        return canonical_tenant_id(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise PrivacyRightsValidationError("tenant_id is invalid") from exc


def _id(
    value: object,
    label: str,
    pattern: re.Pattern[str] = _ID_RE,
) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PrivacyRightsValidationError(
            f"{label} must be a bounded opaque identifier"
        )
    return value


def _credential_ref(value: object) -> str:
    if not isinstance(value, str) or not _CREDENTIAL_REF_RE.fullmatch(value):
        raise PrivacyRightsValidationError(
            "credential_ref must be an env:, vault:, or secret: opaque reference"
        )
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_RE.fullmatch(value):
        raise PrivacyRightsValidationError(
            f"{label} must be a lowercase SHA-256 digest"
        )
    return value


def _integer(value: object, label: str, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise PrivacyRightsValidationError(
            f"{label} must be an integer between {minimum} and {maximum}"
        )
    return value


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) < 100_000_000_000.0
    ):
        raise PrivacyRightsValidationError(
            f"{label} must be a positive finite timestamp"
        )
    return float(value)


def _raw_reference(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise PrivacyRightsValidationError(f"{label} must be text")
    encoded = value.encode("utf-8")
    if not 1 <= len(encoded) <= 1_024 or value.strip() != value:
        raise PrivacyRightsValidationError(f"{label} is invalid or exceeds 1024 bytes")
    return encoded


def _hmac_reference(key: bytes, tenant_id: str, label: str, value: object) -> str:
    if not isinstance(key, bytes) or len(key) < 32:
        raise PrivacyRightsConfigError("tenant digest key must contain at least 32 bytes")
    raw = _raw_reference(value, label)
    scoped = tenant_id.encode("utf-8") + b"\x00" + label.encode("ascii") + b"\x00" + raw
    return hmac.new(key, scoped, hashlib.sha256).hexdigest()


def _task_id(case_id: str, source_id: str, phase: str) -> str:
    digest = hashlib.sha256(
        _canonical({"case_id": case_id, "source_id": source_id, "phase": phase})
    ).hexdigest()[:40]
    return f"task:{digest}"


def _callback_id(task_id: str) -> str:
    return f"callback:{hashlib.sha256(task_id.encode()).hexdigest()[:40]}"


def _choice_sequence(
    value: object,
    label: str,
    allowed: frozenset[str],
    *,
    maximum: int,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PrivacyRightsValidationError(f"{label} must be a sequence")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or item not in allowed:
            raise PrivacyRightsValidationError(f"{label} contains an unsupported value")
        if item not in items:
            items.append(item)
    if not 1 <= len(items) <= maximum:
        raise PrivacyRightsValidationError(f"{label} has an invalid item count")
    return tuple(sorted(items))


def _retained_until(value: object, now: float) -> float:
    retained = _timestamp(value, "retained_until")
    delta = retained - now
    if not _MIN_RETENTION_SECONDS <= delta <= _MAX_RETENTION_SECONDS:
        raise PrivacyRightsValidationError(
            "retained_until must be between 60 seconds and 10 years from now"
        )
    return retained


@dataclass(frozen=True)
class SourceEnrollment:
    tenant_id: str
    source_id: str
    adapter_ref: str
    credential_ref: str
    capabilities: tuple[str, ...]
    state: str
    revision: int
    enrolled_by: str
    created_at: float
    updated_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class RightsCase:
    tenant_id: str
    case_id: str
    request_version: int
    external_request_sha256: str
    subject_sha256: str
    rights: tuple[str, ...]
    identity_status: str
    identity_evidence_sha256: str | None
    identity_evidence_ref: str | None
    legal_hold: bool
    legal_hold_evidence_sha256: str | None
    legal_hold_evidence_ref: str | None
    retention_blocks_deletion: bool
    retention_rule_ref: str | None
    retention_evidence_sha256: str | None
    status: str
    retained_until: float
    opened_by: str
    closure_approved_by: str | None
    closed_by: str | None
    revision: int
    created_at: float
    updated_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class SourceTask:
    tenant_id: str
    task_id: str
    case_id: str
    source_id: str
    phase: str
    destructive: bool
    state: str
    evidence_id: str | None
    approved_by: str | None
    planned_by: str
    revision: int
    created_at: float
    updated_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class CallbackItem:
    tenant_id: str
    callback_id: str
    task_id: str
    case_id: str
    source_id: str
    phase: str
    state: str
    attempts: int
    max_attempts: int
    recovery_count: int
    next_attempt_at: float
    lease_owner: str | None
    lease_token: str | None
    lease_until: float | None
    last_error_code: str | None
    acknowledgement_sha256: str | None
    acknowledgement_ref: str | None
    revision: int
    created_at: float
    updated_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class SourceEvidenceReceipt:
    tenant_id: str
    evidence_id: str
    case_id: str
    source_id: str
    phase: str
    status: str
    provider_evidence_sha256: str
    record_count: int
    opaque_ref: str
    recorded_by: str
    created_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class CompletenessManifest:
    tenant_id: str
    manifest_id: str
    case_id: str
    schema: str
    requested_rights: tuple[str, ...]
    enrolled_source_ids: tuple[str, ...]
    covered_source_ids: tuple[str, ...]
    uncovered_sources: tuple[Mapping[str, str], ...]
    estate_completeness_claimed: bool
    limitation: str
    case_revision: int
    created_by: str
    created_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class ResponsePackage:
    tenant_id: str
    package_id: str
    case_id: str
    object_ref: str
    package_sha256: str
    encryption_key_ref: str
    expires_at: float
    delivery_performed: bool
    created_by: str
    created_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class CaseExport:
    tenant_id: str
    export_id: str
    case_id: str
    schema: str
    body: Mapping[str, object]
    retained_until: float
    created_by: str
    created_at: float
    integrity_sha256: str


@dataclass(frozen=True)
class IntegrityVerification:
    tenant_id: str
    verified_records: int
    audit_events: int
    terminal_audit_sha256: str


class PrivacyRightsOrchestrationStore:
    """Durable, tenant-isolated privacy-rights control-plane authority."""

    def __init__(
        self,
        path: str | Path,
        *,
        config_gate: Callable[[], None] = require_enabled,
        principal_resolver: Callable[[], str | None] = _runtime_principal,
        digest_key_resolver: Callable[[str], bytes] = _default_digest_key_resolver,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: f"lease:{secrets.token_urlsafe(32)}",
    ) -> None:
        config_gate()
        self.path = Path(path)
        self._principal_resolver = principal_resolver
        self._digest_key_resolver = digest_key_resolver
        self._clock = clock
        self._token_factory = token_factory
        ensure_private_directory(self.path.parent)
        self._initialize()

    def _actor(self) -> str:
        actor = self._principal_resolver()
        if actor is None or actor == "":
            raise PrivacyRightsPermissionDenied(
                "an authenticated runtime principal is required"
            )
        return _id(actor, "authenticated principal")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA secure_delete=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rights_sources(
                    tenant_id TEXT NOT NULL,source_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,source_id)
                );
                CREATE TABLE IF NOT EXISTS rights_cases(
                    tenant_id TEXT NOT NULL,case_id TEXT NOT NULL,status TEXT NOT NULL,
                    retained_until REAL NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,case_id)
                );
                CREATE INDEX IF NOT EXISTS rights_case_retention
                    ON rights_cases(tenant_id,status,retained_until);
                CREATE TABLE IF NOT EXISTS rights_tasks(
                    tenant_id TEXT NOT NULL,task_id TEXT NOT NULL,case_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,phase TEXT NOT NULL,state TEXT NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,task_id),
                    UNIQUE(tenant_id,case_id,source_id,phase),
                    FOREIGN KEY(tenant_id,case_id)
                        REFERENCES rights_cases(tenant_id,case_id) ON DELETE CASCADE,
                    FOREIGN KEY(tenant_id,source_id)
                        REFERENCES rights_sources(tenant_id,source_id)
                );
                CREATE TABLE IF NOT EXISTS rights_callbacks(
                    tenant_id TEXT NOT NULL,callback_id TEXT NOT NULL,task_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,source_id TEXT NOT NULL,phase TEXT NOT NULL,
                    state TEXT NOT NULL,next_attempt_at REAL NOT NULL,lease_until REAL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,callback_id),
                    UNIQUE(tenant_id,task_id),
                    FOREIGN KEY(tenant_id,task_id)
                        REFERENCES rights_tasks(tenant_id,task_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS rights_callback_claim
                    ON rights_callbacks(tenant_id,state,next_attempt_at,lease_until);
                CREATE TABLE IF NOT EXISTS rights_evidence(
                    tenant_id TEXT NOT NULL,evidence_id TEXT NOT NULL,case_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,phase TEXT NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,evidence_id),
                    FOREIGN KEY(tenant_id,case_id)
                        REFERENCES rights_cases(tenant_id,case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS rights_manifests(
                    tenant_id TEXT NOT NULL,manifest_id TEXT NOT NULL,case_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,manifest_id),
                    FOREIGN KEY(tenant_id,case_id)
                        REFERENCES rights_cases(tenant_id,case_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS rights_packages(
                    tenant_id TEXT NOT NULL,package_id TEXT NOT NULL,case_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,package_id),
                    FOREIGN KEY(tenant_id,case_id)
                        REFERENCES rights_cases(tenant_id,case_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS rights_package_expiry
                    ON rights_packages(tenant_id,expires_at);
                CREATE TABLE IF NOT EXISTS rights_exports(
                    tenant_id TEXT NOT NULL,export_id TEXT NOT NULL,case_id TEXT NOT NULL,
                    retained_until REAL NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,export_id)
                );
                CREATE INDEX IF NOT EXISTS rights_export_retention
                    ON rights_exports(tenant_id,retained_until);
                CREATE TABLE IF NOT EXISTS rights_claim_batches(
                    tenant_id TEXT NOT NULL,batch_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,batch_id)
                );
                CREATE TABLE IF NOT EXISTS rights_operations(
                    tenant_id TEXT NOT NULL,operation_sha256 TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,result_kind TEXT NOT NULL,
                    result_id TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,operation_sha256)
                );
                CREATE TABLE IF NOT EXISTS rights_audit_events(
                    tenant_id TEXT NOT NULL,sequence INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,integrity_sha256 TEXT NOT NULL,
                    PRIMARY KEY(tenant_id,sequence)
                );
                CREATE TRIGGER IF NOT EXISTS rights_audit_no_update
                    BEFORE UPDATE ON rights_audit_events
                    BEGIN SELECT RAISE(ABORT, 'immutable privacy-rights audit'); END;
                CREATE TRIGGER IF NOT EXISTS rights_audit_no_delete
                    BEFORE DELETE ON rights_audit_events
                    BEGIN SELECT RAISE(ABORT, 'immutable privacy-rights audit'); END;
                CREATE TRIGGER IF NOT EXISTS rights_evidence_no_update
                    BEFORE UPDATE ON rights_evidence
                    BEGIN SELECT RAISE(ABORT, 'immutable privacy-rights evidence'); END;
                CREATE TRIGGER IF NOT EXISTS rights_evidence_no_delete
                    BEFORE DELETE ON rights_evidence
                    WHEN EXISTS(
                        SELECT 1 FROM rights_cases
                        WHERE tenant_id=OLD.tenant_id AND case_id=OLD.case_id
                    )
                    BEGIN SELECT RAISE(ABORT, 'immutable privacy-rights evidence'); END;
                INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version','1');
                COMMIT;
                """
            )
            version = connection.execute(
                "SELECT value FROM metadata WHERE key='schema_version'"
            ).fetchone()
            if version is None or version["value"] != str(SCHEMA_VERSION):
                raise PrivacyRightsIntegrityError(
                    "privacy-rights database schema is unsupported"
                )
        ensure_private_file(self.path)

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _payload(row: sqlite3.Row, label: str) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PrivacyRightsIntegrityError(
                f"persisted {label} is not canonical JSON"
            ) from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["integrity_sha256"]:
            raise PrivacyRightsIntegrityError(
                f"persisted {label} failed integrity verification"
            )
        if payload.get("tenant_id") != row["tenant_id"]:
            raise PrivacyRightsIntegrityError(
                f"persisted {label} tenant binding failed integrity verification"
            )
        return payload

    @staticmethod
    def _insert(
        connection: sqlite3.Connection,
        table: str,
        columns: tuple[str, ...],
        indexes: tuple[object, ...],
        payload: Mapping[str, object],
    ) -> str:
        digest = _sha256(dict(payload))
        column_sql = ",".join(columns + ("payload_json", "integrity_sha256"))
        placeholders = ",".join("?" for _ in range(len(columns) + 2))
        connection.execute(
            f"INSERT INTO {table}({column_sql}) VALUES({placeholders})",  # noqa: S608
            indexes + (_canonical(dict(payload)).decode("ascii"), digest),
        )
        return digest

    @staticmethod
    def _write(
        connection: sqlite3.Connection,
        table: str,
        tenant_id: str,
        id_column: str,
        object_id: str,
        payload: Mapping[str, object],
        *,
        index_updates: Mapping[str, object] | None = None,
    ) -> str:
        digest = _sha256(dict(payload))
        assignments = ["payload_json=?", "integrity_sha256=?"]
        values: list[object] = [_canonical(dict(payload)).decode("ascii"), digest]
        for column, value in (index_updates or {}).items():
            assignments.append(f"{column}=?")
            values.append(value)
        values.extend((tenant_id, object_id))
        result = connection.execute(
            f"UPDATE {table} SET {','.join(assignments)} "  # noqa: S608
            f"WHERE tenant_id=? AND {id_column}=?",
            tuple(values),
        )
        if result.rowcount != 1:
            raise PrivacyRightsNotFound(f"{table} record was not found")
        return digest

    @staticmethod
    def _get(
        connection: sqlite3.Connection,
        table: str,
        tenant_id: str,
        id_column: str,
        object_id: str,
        label: str,
    ) -> tuple[dict[str, Any], str]:
        row = connection.execute(
            f"SELECT * FROM {table} WHERE tenant_id=? AND {id_column}=?",  # noqa: S608
            (tenant_id, object_id),
        ).fetchone()
        if row is None:
            raise PrivacyRightsNotFound(f"{label} was not found")
        return PrivacyRightsOrchestrationStore._payload(row, label), row[
            "integrity_sha256"
        ]

    @staticmethod
    def _operation(
        connection: sqlite3.Connection,
        tenant_id: str,
        idempotency_key: str,
        request: Mapping[str, object],
    ) -> tuple[str, sqlite3.Row | None]:
        key = _id(idempotency_key, "idempotency_key", _OP_RE)
        operation_sha = hashlib.sha256(key.encode()).hexdigest()
        request_sha = _sha256(dict(request))
        row = connection.execute(
            "SELECT * FROM rights_operations WHERE tenant_id=? AND operation_sha256=?",
            (tenant_id, operation_sha),
        ).fetchone()
        if row is not None:
            binding = {
                "tenant_id": row["tenant_id"],
                "operation_sha256": row["operation_sha256"],
                "request_sha256": row["request_sha256"],
                "result_kind": row["result_kind"],
                "result_id": row["result_id"],
            }
            if _sha256(binding) != row["integrity_sha256"]:
                raise PrivacyRightsIntegrityError(
                    "persisted idempotency binding failed integrity verification"
                )
            if row["request_sha256"] != request_sha:
                raise PrivacyRightsConflict(
                    "idempotency key is bound to different canonical content"
                )
        return request_sha, row

    @staticmethod
    def _record_operation(
        connection: sqlite3.Connection,
        tenant_id: str,
        idempotency_key: str,
        request_sha: str,
        result_kind: str,
        result_id: str,
    ) -> None:
        binding = {
            "tenant_id": tenant_id,
            "operation_sha256": hashlib.sha256(idempotency_key.encode()).hexdigest(),
            "request_sha256": request_sha,
            "result_kind": _id(result_kind, "result_kind"),
            "result_id": _id(result_id, "result_id"),
        }
        connection.execute(
            "INSERT INTO rights_operations VALUES(?,?,?,?,?,?)",
            (
                tenant_id,
                binding["operation_sha256"],
                request_sha,
                binding["result_kind"],
                binding["result_id"],
                _sha256(binding),
            ),
        )

    def _audit(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        action: str,
        subject: str,
        actor_ref: str,
        details: Mapping[str, object],
        now: float,
    ) -> None:
        previous = connection.execute(
            "SELECT sequence,integrity_sha256 FROM rights_audit_events "
            "WHERE tenant_id=? ORDER BY sequence DESC LIMIT 1",
            (tenant_id,),
        ).fetchone()
        sequence = 1 if previous is None else int(previous["sequence"]) + 1
        previous_sha = "0" * 64 if previous is None else previous["integrity_sha256"]
        payload = {
            "schema": AUDIT_SCHEMA,
            "tenant_id": tenant_id,
            "sequence": sequence,
            "action": _id(action, "audit action"),
            "subject_sha256": hashlib.sha256(subject.encode()).hexdigest(),
            "actor_ref": _id(actor_ref, "authenticated principal"),
            "details": dict(details),
            "occurred_at": now,
            "previous_sha256": previous_sha,
        }
        digest = _sha256(payload)
        connection.execute(
            "INSERT INTO rights_audit_events VALUES(?,?,?,?)",
            (tenant_id, sequence, _canonical(payload).decode("ascii"), digest),
        )

    @staticmethod
    def _source(payload: Mapping[str, Any], digest: str) -> SourceEnrollment:
        values = dict(payload)
        values["capabilities"] = tuple(values["capabilities"])
        return SourceEnrollment(**values, integrity_sha256=digest)

    @staticmethod
    def _case(payload: Mapping[str, Any], digest: str) -> RightsCase:
        values = dict(payload)
        values["rights"] = tuple(values["rights"])
        return RightsCase(**values, integrity_sha256=digest)

    @staticmethod
    def _task(payload: Mapping[str, Any], digest: str) -> SourceTask:
        return SourceTask(**dict(payload), integrity_sha256=digest)

    @staticmethod
    def _callback(payload: Mapping[str, Any], digest: str) -> CallbackItem:
        return CallbackItem(**dict(payload), integrity_sha256=digest)

    @staticmethod
    def _evidence(
        payload: Mapping[str, Any], digest: str
    ) -> SourceEvidenceReceipt:
        return SourceEvidenceReceipt(**dict(payload), integrity_sha256=digest)

    @staticmethod
    def _manifest(
        payload: Mapping[str, Any], digest: str
    ) -> CompletenessManifest:
        values = dict(payload)
        values["requested_rights"] = tuple(values["requested_rights"])
        values["enrolled_source_ids"] = tuple(values["enrolled_source_ids"])
        values["covered_source_ids"] = tuple(values["covered_source_ids"])
        values["uncovered_sources"] = tuple(
            dict(item) for item in values["uncovered_sources"]
        )
        return CompletenessManifest(**values, integrity_sha256=digest)

    @staticmethod
    def _package(payload: Mapping[str, Any], digest: str) -> ResponsePackage:
        return ResponsePackage(**dict(payload), integrity_sha256=digest)

    @staticmethod
    def _export(payload: Mapping[str, Any], digest: str) -> CaseExport:
        values = dict(payload)
        values["body"] = dict(values["body"])
        return CaseExport(**values, integrity_sha256=digest)

    def _get_case_row(
        self, connection: sqlite3.Connection, tenant_id: str, case_id: str
    ) -> tuple[dict[str, Any], str]:
        return self._get(
            connection, "rights_cases", tenant_id, "case_id", case_id, "rights case"
        )

    def enroll_source(
        self,
        tenant_id: str,
        source_id: str,
        *,
        adapter_ref: str,
        credential_ref: str,
        capabilities: Sequence[str],
        expected_revision: int,
        idempotency_key: str,
    ) -> SourceEnrollment:
        """Create or CAS-update a source containing references and capabilities only."""

        tenant = _tenant(tenant_id)
        source = _id(source_id, "source_id")
        adapter = _id(adapter_ref, "adapter_ref")
        credential = _credential_ref(credential_ref)
        capability_set = _choice_sequence(
            capabilities, "capabilities", TASK_PHASES, maximum=len(TASK_PHASES)
        )
        expected = _integer(expected_revision, "expected_revision", 0, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "enroll_source",
            "source_id": source,
            "adapter_ref": adapter,
            "credential_ref": credential,
            "capabilities": list(capability_set),
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            row = connection.execute(
                "SELECT * FROM rights_sources WHERE tenant_id=? AND source_id=?",
                (tenant, source),
            ).fetchone()
            if operation is not None:
                if row is None:
                    raise PrivacyRightsIntegrityError(
                        "source idempotency result is missing"
                    )
                return self._source(self._payload(row, "source"), row["integrity_sha256"])
            current_revision = 0
            current_payload: dict[str, Any] | None = None
            if row is not None:
                current_payload = self._payload(row, "source")
                current_revision = int(current_payload["revision"])
            if current_revision != expected:
                raise PrivacyRightsConflict(
                    f"source revision CAS failed: expected {expected}, "
                    f"found {current_revision}"
                )
            payload = {
                "tenant_id": tenant,
                "source_id": source,
                "adapter_ref": adapter,
                "credential_ref": credential,
                "capabilities": list(capability_set),
                "state": "active",
                "revision": expected + 1,
                "enrolled_by": (
                    actor if current_payload is None else current_payload["enrolled_by"]
                ),
                "created_at": now if current_payload is None else current_payload["created_at"],
                "updated_at": now,
            }
            if row is None:
                digest_value = self._insert(
                    connection,
                    "rights_sources",
                    ("tenant_id", "source_id"),
                    (tenant, source),
                    payload,
                )
            else:
                digest_value = self._write(
                    connection,
                    "rights_sources",
                    tenant,
                    "source_id",
                    source,
                    payload,
                )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "source",
                source,
            )
            self._audit(
                connection,
                tenant,
                "source_enrolled",
                source,
                actor,
                {"revision": expected + 1, "capability_count": len(capability_set)},
                now,
            )
            return self._source(payload, digest_value)

    def get_source(self, tenant_id: str, source_id: str) -> SourceEnrollment:
        tenant = _tenant(tenant_id)
        source = _id(source_id, "source_id")
        self._actor()
        with self._connect() as connection:
            payload, digest_value = self._get(
                connection,
                "rights_sources",
                tenant,
                "source_id",
                source,
                "source",
            )
            return self._source(payload, digest_value)

    def list_sources(
        self, tenant_id: str, *, limit: int = 100
    ) -> tuple[SourceEnrollment, ...]:
        tenant = _tenant(tenant_id)
        bounded_limit = _integer(limit, "limit", 1, _MAX_LIST)
        self._actor()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rights_sources WHERE tenant_id=? "
                "ORDER BY source_id LIMIT ?",
                (tenant, bounded_limit),
            ).fetchall()
            return tuple(
                self._source(self._payload(row, "source"), row["integrity_sha256"])
                for row in rows
            )

    def open_case(
        self,
        tenant_id: str,
        case_id: str,
        *,
        request_version: int,
        external_request_ref: str,
        subject_reference: str,
        rights: Sequence[str],
        retained_until: float,
        idempotency_key: str,
    ) -> RightsCase:
        """Open a case while retaining only keyed subject and request digests."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        version = _integer(request_version, "request_version", 1, 1_000_000)
        requested_rights = _choice_sequence(
            rights, "rights", RIGHTS, maximum=len(RIGHTS)
        )
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        retained = _retained_until(retained_until, now)
        tenant_key = self._digest_key_resolver(tenant)
        request_sha256 = _hmac_reference(
            tenant_key, tenant, "external_request_ref", external_request_ref
        )
        subject_sha256 = _hmac_reference(
            tenant_key, tenant, "subject_reference", subject_reference
        )
        request = {
            "action": "open_case",
            "case_id": case,
            "request_version": version,
            "external_request_sha256": request_sha256,
            "subject_sha256": subject_sha256,
            "rights": list(requested_rights),
            "retained_until": retained,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            operation_request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                payload, digest_value = self._get_case_row(connection, tenant, case)
                return self._case(payload, digest_value)
            if connection.execute(
                "SELECT 1 FROM rights_cases WHERE tenant_id=? AND case_id=?",
                (tenant, case),
            ).fetchone():
                raise PrivacyRightsConflict("case_id is already enrolled")
            duplicate = connection.execute(
                "SELECT tenant_id,payload_json,integrity_sha256 "
                "FROM rights_cases WHERE tenant_id=?",
                (tenant,),
            ).fetchall()
            for row in duplicate:
                existing = self._payload(row, "rights case")
                if (
                    existing["external_request_sha256"] == request_sha256
                    and existing["request_version"] == version
                ):
                    raise PrivacyRightsConflict(
                        "external request digest and version already identify a case"
                    )
            payload = {
                "tenant_id": tenant,
                "case_id": case,
                "request_version": version,
                "external_request_sha256": request_sha256,
                "subject_sha256": subject_sha256,
                "rights": list(requested_rights),
                "identity_status": "unverified",
                "identity_evidence_sha256": None,
                "identity_evidence_ref": None,
                "legal_hold": False,
                "legal_hold_evidence_sha256": None,
                "legal_hold_evidence_ref": None,
                "retention_blocks_deletion": False,
                "retention_rule_ref": None,
                "retention_evidence_sha256": None,
                "status": "open",
                "retained_until": retained,
                "opened_by": actor,
                "closure_approved_by": None,
                "closed_by": None,
                "revision": 1,
                "created_at": now,
                "updated_at": now,
            }
            digest_value = self._insert(
                connection,
                "rights_cases",
                ("tenant_id", "case_id", "status", "retained_until"),
                (tenant, case, "open", retained),
                payload,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                operation_request_sha,
                "case",
                case,
            )
            self._audit(
                connection,
                tenant,
                "case_opened",
                case,
                actor,
                {"request_version": version, "rights": list(requested_rights)},
                now,
            )
            return self._case(payload, digest_value)

    def get_case(self, tenant_id: str, case_id: str) -> RightsCase:
        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        self._actor()
        with self._connect() as connection:
            payload, digest_value = self._get_case_row(connection, tenant, case)
            return self._case(payload, digest_value)

    def _update_case_gate(
        self,
        tenant_id: str,
        case_id: str,
        *,
        action: str,
        updates: Mapping[str, object],
        expected_revision: int,
        idempotency_key: str,
        actor: str,
        now: float,
    ) -> RightsCase:
        request = {
            "action": action,
            "case_id": case_id,
            "updates": dict(updates),
            "expected_revision": expected_revision,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant_id, idempotency_key, request
            )
            raw, stored_digest = self._get_case_row(connection, tenant_id, case_id)
            if operation is not None:
                return self._case(raw, stored_digest)
            current = self._case(raw, stored_digest)
            if current.revision != expected_revision:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected_revision}, "
                    f"found {current.revision}"
                )
            if current.status != "open":
                raise PrivacyRightsConflict("rights case is not open")
            payload = dict(raw)
            payload.update(updates)
            payload.update(
                {
                    "revision": current.revision + 1,
                    "updated_at": now,
                    "closure_approved_by": None,
                }
            )
            digest_value = self._write(
                connection,
                "rights_cases",
                tenant_id,
                "case_id",
                case_id,
                payload,
            )
            self._record_operation(
                connection,
                tenant_id,
                idempotency_key,
                request_sha,
                "case",
                case_id,
            )
            self._audit(
                connection,
                tenant_id,
                action,
                case_id,
                actor,
                {"revision": current.revision + 1},
                now,
            )
            return self._case(payload, digest_value)

    def set_identity_verification(
        self,
        tenant_id: str,
        case_id: str,
        *,
        status: str,
        evidence_sha256: str | None,
        evidence_ref: str | None,
        expected_revision: int,
        idempotency_key: str,
    ) -> RightsCase:
        """Record external verification evidence; this module does not verify identity."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        if status not in IDENTITY_STATUSES:
            raise PrivacyRightsValidationError("identity status is unsupported")
        evidence = None if evidence_sha256 is None else _digest(
            evidence_sha256, "evidence_sha256"
        )
        reference = None if evidence_ref is None else _id(evidence_ref, "evidence_ref")
        if status == "verified" and (evidence is None or reference is None):
            raise PrivacyRightsValidationError(
                "verified identity requires an external evidence digest and opaque ref"
            )
        if status != "verified" and (evidence is not None or reference is not None):
            raise PrivacyRightsValidationError(
                "only verified status accepts external evidence"
            )
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        return self._update_case_gate(
            tenant,
            case,
            action="identity_verification_set",
            updates={
                "identity_status": status,
                "identity_evidence_sha256": evidence,
                "identity_evidence_ref": reference,
            },
            expected_revision=expected,
            idempotency_key=idempotency_key,
            actor=actor,
            now=now,
        )

    def set_legal_hold(
        self,
        tenant_id: str,
        case_id: str,
        *,
        active: bool,
        evidence_sha256: str,
        evidence_ref: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> RightsCase:
        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        if not isinstance(active, bool):
            raise PrivacyRightsValidationError("active must be boolean")
        evidence = _digest(evidence_sha256, "evidence_sha256")
        reference = _id(evidence_ref, "evidence_ref")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        return self._update_case_gate(
            tenant,
            case,
            action="legal_hold_set",
            updates={
                "legal_hold": active,
                "legal_hold_evidence_sha256": evidence,
                "legal_hold_evidence_ref": reference,
            },
            expected_revision=expected,
            idempotency_key=idempotency_key,
            actor=actor,
            now=now,
        )

    def set_retention_gate(
        self,
        tenant_id: str,
        case_id: str,
        *,
        blocks_deletion: bool,
        rule_ref: str,
        evidence_sha256: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> RightsCase:
        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        if not isinstance(blocks_deletion, bool):
            raise PrivacyRightsValidationError("blocks_deletion must be boolean")
        rule = _id(rule_ref, "rule_ref")
        evidence = _digest(evidence_sha256, "evidence_sha256")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        return self._update_case_gate(
            tenant,
            case,
            action="retention_gate_set",
            updates={
                "retention_blocks_deletion": blocks_deletion,
                "retention_rule_ref": rule,
                "retention_evidence_sha256": evidence,
            },
            expected_revision=expected,
            idempotency_key=idempotency_key,
            actor=actor,
            now=now,
        )

    @staticmethod
    def _required_phases(rights: Sequence[str]) -> tuple[str, ...]:
        ordered: list[str] = []
        for right in rights:
            for phase in _RIGHT_PHASES[right]:
                if phase not in ordered:
                    ordered.append(phase)
        return tuple(ordered)

    def plan_source_tasks(
        self,
        tenant_id: str,
        case_id: str,
        source_id: str,
        *,
        phases: Sequence[str] | None = None,
        max_attempts: int = 3,
        expected_case_revision: int,
        idempotency_key: str,
    ) -> tuple[SourceTask, ...]:
        """Plan bounded work and local callback intent after identity verification."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        source = _id(source_id, "source_id")
        attempts_limit = _integer(max_attempts, "max_attempts", 1, 20)
        expected = _integer(
            expected_case_revision, "expected_case_revision", 1, 1_000_000
        )
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        with self._connect() as connection:
            self._begin(connection)
            case_raw, case_digest = self._get_case_row(connection, tenant, case)
            current_case = self._case(case_raw, case_digest)
            requested = self._required_phases(current_case.rights)
            selected = (
                requested
                if phases is None
                else _choice_sequence(
                    phases, "phases", TASK_PHASES, maximum=len(TASK_PHASES)
                )
            )
            if not set(selected).issubset(requested):
                raise PrivacyRightsValidationError(
                    "phases must be justified by the case's requested rights"
                )
            request = {
                "action": "plan_source_tasks",
                "case_id": case,
                "source_id": source,
                "phases": list(selected),
                "max_attempts": attempts_limit,
                "expected_case_revision": expected,
                "actor_ref": actor,
            }
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                rows = connection.execute(
                    "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=? "
                    "AND source_id=? ORDER BY task_id",
                    (tenant, case, source),
                ).fetchall()
                return tuple(
                    self._task(self._payload(row, "source task"), row["integrity_sha256"])
                    for row in rows
                    if self._payload(row, "source task")["phase"] in selected
                )
            if current_case.revision != expected:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected}, "
                    f"found {current_case.revision}"
                )
            if current_case.status != "open":
                raise PrivacyRightsConflict("rights case is not open")
            if current_case.identity_status != "verified":
                raise PrivacyRightsConflict(
                    "source work cannot be planned until external identity evidence is verified"
                )
            source_raw, source_digest = self._get(
                connection,
                "rights_sources",
                tenant,
                "source_id",
                source,
                "source",
            )
            current_source = self._source(source_raw, source_digest)
            unsupported = set(selected) - set(current_source.capabilities)
            if unsupported:
                raise PrivacyRightsConflict(
                    "source lacks required capabilities: " + ",".join(sorted(unsupported))
                )
            existing_count = connection.execute(
                "SELECT COUNT(*) AS total FROM rights_tasks WHERE tenant_id=?",
                (tenant,),
            ).fetchone()["total"]
            if int(existing_count) + len(selected) > _MAX_TASKS:
                raise PrivacyRightsConflict("tenant task bound would be exceeded")
            tasks: list[SourceTask] = []
            for phase in selected:
                task_id = _task_id(case, source, phase)
                if connection.execute(
                    "SELECT 1 FROM rights_tasks WHERE tenant_id=? AND task_id=?",
                    (tenant, task_id),
                ).fetchone():
                    raise PrivacyRightsConflict("source phase is already planned")
                task_payload = {
                    "tenant_id": tenant,
                    "task_id": task_id,
                    "case_id": case,
                    "source_id": source,
                    "phase": phase,
                    "destructive": phase in DESTRUCTIVE_PHASES,
                    "state": "planned",
                    "evidence_id": None,
                    "approved_by": None,
                    "planned_by": actor,
                    "revision": 1,
                    "created_at": now,
                    "updated_at": now,
                }
                task_digest = self._insert(
                    connection,
                    "rights_tasks",
                    ("tenant_id", "task_id", "case_id", "source_id", "phase", "state"),
                    (tenant, task_id, case, source, phase, "planned"),
                    task_payload,
                )
                tasks.append(self._task(task_payload, task_digest))
                if phase in CALLBACK_PHASES:
                    callback_id = _callback_id(task_id)
                    callback_payload = {
                        "tenant_id": tenant,
                        "callback_id": callback_id,
                        "task_id": task_id,
                        "case_id": case,
                        "source_id": source,
                        "phase": phase,
                        "state": "pending",
                        "attempts": 0,
                        "max_attempts": attempts_limit,
                        "recovery_count": 0,
                        "next_attempt_at": now,
                        "lease_owner": None,
                        "lease_token": None,
                        "lease_until": None,
                        "last_error_code": None,
                        "acknowledgement_sha256": None,
                        "acknowledgement_ref": None,
                        "revision": 1,
                        "created_at": now,
                        "updated_at": now,
                    }
                    self._insert(
                        connection,
                        "rights_callbacks",
                        (
                            "tenant_id",
                            "callback_id",
                            "task_id",
                            "case_id",
                            "source_id",
                            "phase",
                            "state",
                            "next_attempt_at",
                            "lease_until",
                        ),
                        (
                            tenant,
                            callback_id,
                            task_id,
                            case,
                            source,
                            phase,
                            "pending",
                            now,
                            None,
                        ),
                        callback_payload,
                    )
            updated_case = dict(case_raw)
            updated_case.update(
                {
                    "revision": current_case.revision + 1,
                    "updated_at": now,
                    "closure_approved_by": None,
                }
            )
            self._write(
                connection,
                "rights_cases",
                tenant,
                "case_id",
                case,
                updated_case,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "task_plan",
                case,
            )
            self._audit(
                connection,
                tenant,
                "source_tasks_planned",
                case,
                actor,
                {"source_id": source, "phases": list(selected)},
                now,
            )
            return tuple(tasks)

    def approve_destructive_work(
        self,
        tenant_id: str,
        task_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> SourceTask:
        """Approve deletion only when a distinct authenticated planner exists."""

        tenant = _tenant(tenant_id)
        task_key = _id(task_id, "task_id")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "approve_destructive_work",
            "task_id": task_key,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get(
                connection,
                "rights_tasks",
                tenant,
                "task_id",
                task_key,
                "source task",
            )
            if operation is not None:
                return self._task(raw, stored_digest)
            current = self._task(raw, stored_digest)
            if current.revision != expected:
                raise PrivacyRightsConflict(
                    f"task revision CAS failed: expected {expected}, found {current.revision}"
                )
            if not current.destructive or current.phase != "delete":
                raise PrivacyRightsConflict("only destructive deletion work is approvable")
            if current.state != "planned":
                raise PrivacyRightsConflict("destructive task is not planned")
            if current.planned_by == actor:
                raise PrivacyRightsPermissionDenied(
                    "destructive work requires an approver distinct from its planner"
                )
            case_raw, case_digest = self._get_case_row(
                connection, tenant, current.case_id
            )
            rights_case = self._case(case_raw, case_digest)
            if rights_case.legal_hold:
                raise PrivacyRightsConflict("legal hold blocks destructive approval")
            if rights_case.retention_blocks_deletion:
                raise PrivacyRightsConflict("retention rule blocks destructive approval")
            payload = dict(raw)
            payload.update(
                {"approved_by": actor, "revision": current.revision + 1, "updated_at": now}
            )
            digest_value = self._write(
                connection,
                "rights_tasks",
                tenant,
                "task_id",
                task_key,
                payload,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "task",
                task_key,
            )
            self._audit(
                connection,
                tenant,
                "destructive_work_approved",
                task_key,
                actor,
                {"planner_ref": current.planned_by},
                now,
            )
            return self._task(payload, digest_value)

    def _callback_ready(
        self,
        connection: sqlite3.Connection,
        tenant_id: str,
        callback: CallbackItem,
    ) -> bool:
        case_raw, case_digest = self._get_case_row(
            connection, tenant_id, callback.case_id
        )
        rights_case = self._case(case_raw, case_digest)
        if rights_case.status != "open" or rights_case.identity_status != "verified":
            return False
        task_raw, task_digest = self._get(
            connection,
            "rights_tasks",
            tenant_id,
            "task_id",
            callback.task_id,
            "source task",
        )
        task = self._task(task_raw, task_digest)
        if task.state != "planned":
            return False
        if task.destructive:
            if (
                task.approved_by is None
                or rights_case.legal_hold
                or rights_case.retention_blocks_deletion
            ):
                return False
        phases = self._required_phases(rights_case.rights)
        phase_index = phases.index(callback.phase)
        for predecessor in phases[:phase_index]:
            row = connection.execute(
                "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=? "
                "AND source_id=? AND phase=?",
                (tenant_id, callback.case_id, callback.source_id, predecessor),
            ).fetchone()
            if row is not None:
                predecessor_task = self._task(
                    self._payload(row, "source task"), row["integrity_sha256"]
                )
                if predecessor_task.state != "complete":
                    return False
        return True

    def claim_callbacks(
        self,
        tenant_id: str,
        *,
        limit: int,
        lease_seconds: int,
        idempotency_key: str,
    ) -> tuple[CallbackItem, ...]:
        """Lease ready callbacks, including recovery of expired leases."""

        tenant = _tenant(tenant_id)
        bounded_limit = _integer(limit, "limit", 1, 100)
        lease = _integer(lease_seconds, "lease_seconds", 1, _MAX_LEASE_SECONDS)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "claim_callbacks",
            "limit": bounded_limit,
            "lease_seconds": lease,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                if operation["result_kind"] != "claim_batch":
                    raise PrivacyRightsIntegrityError(
                        "claim idempotency result kind is inconsistent"
                    )
                batch_raw, _batch_digest = self._get(
                    connection,
                    "rights_claim_batches",
                    tenant,
                    "batch_id",
                    operation["result_id"],
                    "claim batch",
                )
                return tuple(
                    self._callback(dict(item["payload"]), item["integrity_sha256"])
                    for item in batch_raw["callbacks"]
                )
            rows = connection.execute(
                "SELECT * FROM rights_callbacks WHERE tenant_id=? AND "
                "((state='pending' AND next_attempt_at<=?) OR "
                "(state='leased' AND lease_until<=?)) "
                "ORDER BY next_attempt_at,callback_id LIMIT ?",
                (tenant, now, now, min(1_000, bounded_limit * 10)),
            ).fetchall()
            claimed: list[CallbackItem] = []
            snapshots: list[dict[str, object]] = []
            for row in rows:
                if len(claimed) >= bounded_limit:
                    break
                raw = self._payload(row, "callback")
                current = self._callback(raw, row["integrity_sha256"])
                if not self._callback_ready(connection, tenant, current):
                    continue
                token = _id(self._token_factory(), "lease token", _OP_RE)
                recovered = current.state == "leased"
                payload = dict(raw)
                payload.update(
                    {
                        "state": "leased",
                        "lease_owner": actor,
                        "lease_token": token,
                        "lease_until": now + lease,
                        "recovery_count": current.recovery_count + int(recovered),
                        "revision": current.revision + 1,
                        "updated_at": now,
                    }
                )
                digest_value = self._write(
                    connection,
                    "rights_callbacks",
                    tenant,
                    "callback_id",
                    current.callback_id,
                    payload,
                    index_updates={
                        "state": "leased",
                        "next_attempt_at": current.next_attempt_at,
                        "lease_until": now + lease,
                    },
                )
                callback = self._callback(payload, digest_value)
                claimed.append(callback)
                snapshots.append(
                    {"payload": payload, "integrity_sha256": digest_value}
                )
                self._audit(
                    connection,
                    tenant,
                    "callback_claimed",
                    current.callback_id,
                    actor,
                    {"recovered": recovered, "lease_until": now + lease},
                    now,
                )
            batch_id = _id(
                "batch:" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32],
                "batch_id",
            )
            batch_payload = {
                "tenant_id": tenant,
                "batch_id": batch_id,
                "callbacks": snapshots,
                "created_at": now,
            }
            self._insert(
                connection,
                "rights_claim_batches",
                ("tenant_id", "batch_id"),
                (tenant, batch_id),
                batch_payload,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "claim_batch",
                batch_id,
            )
            return tuple(claimed)

    def fail_callback(
        self,
        tenant_id: str,
        callback_id: str,
        *,
        lease_token: str,
        error_code: str,
        retry_delay_seconds: int,
        expected_revision: int,
        idempotency_key: str,
    ) -> CallbackItem:
        tenant = _tenant(tenant_id)
        callback_key = _id(callback_id, "callback_id")
        token = _id(lease_token, "lease_token", _OP_RE)
        if not isinstance(error_code, str) or not _ERROR_RE.fullmatch(error_code):
            raise PrivacyRightsValidationError("error_code is unsupported")
        delay = _integer(
            retry_delay_seconds,
            "retry_delay_seconds",
            0,
            _MAX_BACKOFF_SECONDS,
        )
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "fail_callback",
            "callback_id": callback_key,
            "lease_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "error_code": error_code,
            "retry_delay_seconds": delay,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                "callback",
            )
            if operation is not None:
                return self._callback(raw, stored_digest)
            current = self._callback(raw, stored_digest)
            self._assert_lease(current, actor, token, expected, now)
            attempts = current.attempts + 1
            state = "dead_letter" if attempts >= current.max_attempts else "pending"
            payload = dict(raw)
            payload.update(
                {
                    "state": state,
                    "attempts": attempts,
                    "next_attempt_at": now + delay,
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_until": None,
                    "last_error_code": error_code,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            digest_value = self._write(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                payload,
                index_updates={
                    "state": state,
                    "next_attempt_at": now + delay,
                    "lease_until": None,
                },
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "callback",
                callback_key,
            )
            self._audit(
                connection,
                tenant,
                "callback_failed",
                callback_key,
                actor,
                {"attempts": attempts, "state": state, "error_code": error_code},
                now,
            )
            return self._callback(payload, digest_value)

    @staticmethod
    def _assert_lease(
        callback: CallbackItem,
        actor: str,
        token: str,
        expected_revision: int,
        now: float,
    ) -> None:
        if callback.revision != expected_revision:
            raise PrivacyRightsConflict(
                f"callback revision CAS failed: expected {expected_revision}, "
                f"found {callback.revision}"
            )
        if (
            callback.state != "leased"
            or callback.lease_owner != actor
            or callback.lease_token != token
            or callback.lease_until is None
            or callback.lease_until <= now
        ):
            raise PrivacyRightsConflict("callback lease is absent, expired, or not owned")

    def ack_callback(
        self,
        tenant_id: str,
        callback_id: str,
        *,
        lease_token: str,
        acknowledgement_sha256: str,
        acknowledgement_ref: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> CallbackItem:
        """Acknowledge callback delivery; provider task evidence remains separate."""

        tenant = _tenant(tenant_id)
        callback_key = _id(callback_id, "callback_id")
        token = _id(lease_token, "lease_token", _OP_RE)
        receipt = _digest(acknowledgement_sha256, "acknowledgement_sha256")
        reference = _id(acknowledgement_ref, "acknowledgement_ref")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "ack_callback",
            "callback_id": callback_key,
            "lease_token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            "acknowledgement_sha256": receipt,
            "acknowledgement_ref": reference,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                "callback",
            )
            if operation is not None:
                return self._callback(raw, stored_digest)
            current = self._callback(raw, stored_digest)
            self._assert_lease(current, actor, token, expected, now)
            payload = dict(raw)
            payload.update(
                {
                    "state": "acked",
                    "lease_owner": None,
                    "lease_token": None,
                    "lease_until": None,
                    "acknowledgement_sha256": receipt,
                    "acknowledgement_ref": reference,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            digest_value = self._write(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                payload,
                index_updates={"state": "acked", "lease_until": None},
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "callback",
                callback_key,
            )
            self._audit(
                connection,
                tenant,
                "callback_acknowledged",
                callback_key,
                actor,
                {"acknowledgement_sha256": receipt},
                now,
            )
            return self._callback(payload, digest_value)

    def requeue_callback(
        self,
        tenant_id: str,
        callback_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> CallbackItem:
        """Manually recover a dead-letter callback with a fresh retry budget."""

        tenant = _tenant(tenant_id)
        callback_key = _id(callback_id, "callback_id")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "requeue_callback",
            "callback_id": callback_key,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                "callback",
            )
            if operation is not None:
                return self._callback(raw, stored_digest)
            current = self._callback(raw, stored_digest)
            if current.revision != expected:
                raise PrivacyRightsConflict(
                    f"callback revision CAS failed: expected {expected}, "
                    f"found {current.revision}"
                )
            if current.state != "dead_letter":
                raise PrivacyRightsConflict("only dead-letter callbacks can be requeued")
            payload = dict(raw)
            payload.update(
                {
                    "state": "pending",
                    "attempts": 0,
                    "next_attempt_at": now,
                    "last_error_code": None,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            digest_value = self._write(
                connection,
                "rights_callbacks",
                tenant,
                "callback_id",
                callback_key,
                payload,
                index_updates={
                    "state": "pending",
                    "next_attempt_at": now,
                    "lease_until": None,
                },
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "callback",
                callback_key,
            )
            self._audit(
                connection,
                tenant,
                "callback_requeued",
                callback_key,
                actor,
                {"previous_attempts": current.attempts},
                now,
            )
            return self._callback(payload, digest_value)

    def record_source_evidence(
        self,
        tenant_id: str,
        case_id: str,
        source_id: str,
        phase: str,
        *,
        evidence_id: str,
        status: str,
        provider_evidence_sha256: str,
        record_count: int,
        opaque_ref: str,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> SourceEvidenceReceipt:
        """Append a privacy-minimised provider receipt and update its task by CAS."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        source = _id(source_id, "source_id")
        if phase not in TASK_PHASES:
            raise PrivacyRightsValidationError("phase is unsupported")
        evidence_key = _id(evidence_id, "evidence_id")
        if status not in EVIDENCE_STATUSES:
            raise PrivacyRightsValidationError("evidence status is unsupported")
        provider_digest = _digest(
            provider_evidence_sha256, "provider_evidence_sha256"
        )
        count = _integer(record_count, "record_count", 0, 1_000_000_000)
        reference = _id(opaque_ref, "opaque_ref")
        expected = _integer(
            expected_task_revision, "expected_task_revision", 1, 1_000_000
        )
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        task_key = _task_id(case, source, phase)
        request = {
            "action": "record_source_evidence",
            "case_id": case,
            "source_id": source,
            "phase": phase,
            "evidence_id": evidence_key,
            "status": status,
            "provider_evidence_sha256": provider_digest,
            "record_count": count,
            "opaque_ref": reference,
            "expected_task_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                raw, digest_value = self._get(
                    connection,
                    "rights_evidence",
                    tenant,
                    "evidence_id",
                    evidence_key,
                    "source evidence",
                )
                return self._evidence(raw, digest_value)
            task_raw, task_digest = self._get(
                connection,
                "rights_tasks",
                tenant,
                "task_id",
                task_key,
                "source task",
            )
            task = self._task(task_raw, task_digest)
            if task.revision != expected:
                raise PrivacyRightsConflict(
                    f"task revision CAS failed: expected {expected}, found {task.revision}"
                )
            if task.state != "planned":
                raise PrivacyRightsConflict("source task is not awaiting evidence")
            if phase in CALLBACK_PHASES:
                callback_row = connection.execute(
                    "SELECT * FROM rights_callbacks WHERE tenant_id=? AND task_id=?",
                    (tenant, task_key),
                ).fetchone()
                if callback_row is None:
                    raise PrivacyRightsIntegrityError("source task callback is missing")
                callback = self._callback(
                    self._payload(callback_row, "callback"),
                    callback_row["integrity_sha256"],
                )
                if callback.state != "acked":
                    raise PrivacyRightsConflict(
                        "provider evidence requires an acknowledged callback"
                    )
            evidence_payload = {
                "tenant_id": tenant,
                "evidence_id": evidence_key,
                "case_id": case,
                "source_id": source,
                "phase": phase,
                "status": status,
                "provider_evidence_sha256": provider_digest,
                "record_count": count,
                "opaque_ref": reference,
                "recorded_by": actor,
                "created_at": now,
            }
            evidence_digest = self._insert(
                connection,
                "rights_evidence",
                ("tenant_id", "evidence_id", "case_id", "source_id", "phase"),
                (tenant, evidence_key, case, source, phase),
                evidence_payload,
            )
            task_payload = dict(task_raw)
            task_payload.update(
                {
                    "evidence_id": evidence_key,
                    "state": (
                        "complete" if status in {"complete", "not_found"} else "planned"
                    ),
                    "revision": task.revision + 1,
                    "updated_at": now,
                }
            )
            self._write(
                connection,
                "rights_tasks",
                tenant,
                "task_id",
                task_key,
                task_payload,
                index_updates={"state": task_payload["state"]},
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "evidence",
                evidence_key,
            )
            self._audit(
                connection,
                tenant,
                "source_evidence_recorded",
                evidence_key,
                actor,
                {
                    "case_id": case,
                    "source_id": source,
                    "phase": phase,
                    "status": status,
                    "record_count": count,
                },
                now,
            )
            return self._evidence(evidence_payload, evidence_digest)

    def build_completeness_manifest(
        self,
        tenant_id: str,
        case_id: str,
        manifest_id: str,
        *,
        expected_case_revision: int,
        idempotency_key: str,
    ) -> CompletenessManifest:
        """Describe enrolled coverage without inferring data-estate completeness."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        manifest_key = _id(manifest_id, "manifest_id")
        expected = _integer(
            expected_case_revision, "expected_case_revision", 1, 1_000_000
        )
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "build_completeness_manifest",
            "case_id": case,
            "manifest_id": manifest_key,
            "expected_case_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                raw, digest_value = self._get(
                    connection,
                    "rights_manifests",
                    tenant,
                    "manifest_id",
                    manifest_key,
                    "completeness manifest",
                )
                return self._manifest(raw, digest_value)
            case_raw, case_digest = self._get_case_row(connection, tenant, case)
            rights_case = self._case(case_raw, case_digest)
            if rights_case.revision != expected:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected}, "
                    f"found {rights_case.revision}"
                )
            sources = connection.execute(
                "SELECT source_id FROM rights_sources WHERE tenant_id=? ORDER BY source_id",
                (tenant,),
            ).fetchall()
            if len(sources) > _MAX_SOURCES:
                raise PrivacyRightsConflict("enrolled source bound is exceeded")
            enrolled = tuple(row["source_id"] for row in sources)
            covered: list[str] = []
            uncovered: list[dict[str, str]] = []
            for source in enrolled:
                task_rows = connection.execute(
                    "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=? "
                    "AND source_id=? ORDER BY phase",
                    (tenant, case, source),
                ).fetchall()
                if not task_rows:
                    uncovered.append({"source_id": source, "reason": "not_planned"})
                    continue
                tasks = tuple(
                    self._task(
                        self._payload(row, "source task"), row["integrity_sha256"]
                    )
                    for row in task_rows
                )
                if all(task.state == "complete" for task in tasks):
                    covered.append(source)
                else:
                    uncovered.append({"source_id": source, "reason": "incomplete_tasks"})
            payload = {
                "tenant_id": tenant,
                "manifest_id": manifest_key,
                "case_id": case,
                "schema": MANIFEST_SCHEMA,
                "requested_rights": list(rights_case.rights),
                "enrolled_source_ids": list(enrolled),
                "covered_source_ids": covered,
                "uncovered_sources": uncovered,
                "estate_completeness_claimed": False,
                "limitation": (
                    "Coverage is limited to explicitly enrolled sources. The authority "
                    "does not discover or infer the complete enterprise data estate."
                ),
                "case_revision": rights_case.revision,
                "created_by": actor,
                "created_at": now,
            }
            digest_value = self._insert(
                connection,
                "rights_manifests",
                ("tenant_id", "manifest_id", "case_id"),
                (tenant, manifest_key, case),
                payload,
            )
            updated_case = dict(case_raw)
            updated_case.update(
                {
                    "revision": rights_case.revision + 1,
                    "updated_at": now,
                    "closure_approved_by": None,
                }
            )
            self._write(
                connection,
                "rights_cases",
                tenant,
                "case_id",
                case,
                updated_case,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "manifest",
                manifest_key,
            )
            self._audit(
                connection,
                tenant,
                "completeness_manifest_built",
                manifest_key,
                actor,
                {
                    "enrolled_count": len(enrolled),
                    "covered_count": len(covered),
                    "uncovered_count": len(uncovered),
                    "estate_completeness_claimed": False,
                },
                now,
            )
            return self._manifest(payload, digest_value)

    def create_response_package(
        self,
        tenant_id: str,
        case_id: str,
        package_id: str,
        *,
        object_ref: str,
        package_sha256: str,
        encryption_key_ref: str,
        expires_at: float,
        expected_case_revision: int,
        idempotency_key: str,
    ) -> ResponsePackage:
        """Record encrypted response-package metadata; never deliver the package."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        package_key = _id(package_id, "package_id")
        object_key = _id(object_ref, "object_ref")
        package_digest = _digest(package_sha256, "package_sha256")
        encryption_ref = _credential_ref(encryption_key_ref)
        expected = _integer(
            expected_case_revision, "expected_case_revision", 1, 1_000_000
        )
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        expiry = _timestamp(expires_at, "expires_at")
        if not now < expiry <= now + _MAX_PACKAGE_SECONDS:
            raise PrivacyRightsValidationError(
                "expires_at must be in the future and no more than 90 days away"
            )
        request = {
            "action": "create_response_package",
            "case_id": case,
            "package_id": package_key,
            "object_ref": object_key,
            "package_sha256": package_digest,
            "encryption_key_ref": encryption_ref,
            "expires_at": expiry,
            "expected_case_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                raw, digest_value = self._get(
                    connection,
                    "rights_packages",
                    tenant,
                    "package_id",
                    package_key,
                    "response package",
                )
                return self._package(raw, digest_value)
            case_raw, case_digest = self._get_case_row(connection, tenant, case)
            rights_case = self._case(case_raw, case_digest)
            if rights_case.revision != expected:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected}, "
                    f"found {rights_case.revision}"
                )
            if rights_case.identity_status != "verified":
                raise PrivacyRightsConflict("verified identity evidence is required")
            if not set(rights_case.rights).intersection({"access", "portability"}):
                raise PrivacyRightsConflict(
                    "response packages are only valid for access or portability cases"
                )
            if expiry > rights_case.retained_until:
                raise PrivacyRightsValidationError(
                    "response package cannot outlive case retention"
                )
            retrieval = connection.execute(
                "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=? "
                "AND phase IN ('retrieve','redact')",
                (tenant, case),
            ).fetchall()
            if not retrieval or any(
                self._task(
                    self._payload(row, "source task"), row["integrity_sha256"]
                ).state
                != "complete"
                for row in retrieval
            ):
                raise PrivacyRightsConflict(
                    "retrieval and redaction tasks must be complete before packaging"
                )
            payload = {
                "tenant_id": tenant,
                "package_id": package_key,
                "case_id": case,
                "object_ref": object_key,
                "package_sha256": package_digest,
                "encryption_key_ref": encryption_ref,
                "expires_at": expiry,
                "delivery_performed": False,
                "created_by": actor,
                "created_at": now,
            }
            digest_value = self._insert(
                connection,
                "rights_packages",
                ("tenant_id", "package_id", "case_id", "expires_at"),
                (tenant, package_key, case, expiry),
                payload,
            )
            updated_case = dict(case_raw)
            updated_case.update(
                {
                    "revision": rights_case.revision + 1,
                    "updated_at": now,
                    "closure_approved_by": None,
                }
            )
            self._write(
                connection,
                "rights_cases",
                tenant,
                "case_id",
                case,
                updated_case,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "package",
                package_key,
            )
            self._audit(
                connection,
                tenant,
                "response_package_created",
                package_key,
                actor,
                {"expires_at": expiry, "delivery_performed": False},
                now,
            )
            return self._package(payload, digest_value)

    def get_response_package(
        self, tenant_id: str, package_id: str
    ) -> ResponsePackage:
        tenant = _tenant(tenant_id)
        package_key = _id(package_id, "package_id")
        self._actor()
        now = _timestamp(self._clock(), "current time")
        with self._connect() as connection:
            raw, digest_value = self._get(
                connection,
                "rights_packages",
                tenant,
                "package_id",
                package_key,
                "response package",
            )
            package = self._package(raw, digest_value)
            if package.expires_at <= now:
                raise PrivacyRightsConflict("response package metadata has expired")
            return package

    def _assert_closure_ready(
        self,
        connection: sqlite3.Connection,
        tenant: str,
        rights_case: RightsCase,
        now: float,
    ) -> None:
        if rights_case.status != "open" or rights_case.identity_status != "verified":
            raise PrivacyRightsConflict("case is not open with verified identity")
        tasks = connection.execute(
            "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=?",
            (tenant, rights_case.case_id),
        ).fetchall()
        if not tasks:
            raise PrivacyRightsConflict("case has no planned source tasks")
        if any(
            self._task(self._payload(row, "source task"), row["integrity_sha256"]).state
            != "complete"
            for row in tasks
        ):
            raise PrivacyRightsConflict("all planned source tasks must be complete")
        callbacks = connection.execute(
            "SELECT * FROM rights_callbacks WHERE tenant_id=? AND case_id=?",
            (tenant, rights_case.case_id),
        ).fetchall()
        if any(
            self._callback(self._payload(row, "callback"), row["integrity_sha256"]).state
            != "acked"
            for row in callbacks
        ):
            raise PrivacyRightsConflict("all provider callback intents must be acknowledged")
        if connection.execute(
            "SELECT 1 FROM rights_manifests WHERE tenant_id=? AND case_id=? LIMIT 1",
            (tenant, rights_case.case_id),
        ).fetchone() is None:
            raise PrivacyRightsConflict("a completeness manifest is required")
        if set(rights_case.rights).intersection({"access", "portability"}):
            package = connection.execute(
                "SELECT 1 FROM rights_packages WHERE tenant_id=? AND case_id=? "
                "AND expires_at>? LIMIT 1",
                (tenant, rights_case.case_id, now),
            ).fetchone()
            if package is None:
                raise PrivacyRightsConflict(
                    "an unexpired response package is required for access or portability"
                )

    def approve_case_closure(
        self,
        tenant_id: str,
        case_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> RightsCase:
        """Approve closure as a principal distinct from the case opener."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "approve_case_closure",
            "case_id": case,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get_case_row(connection, tenant, case)
            if operation is not None:
                return self._case(raw, stored_digest)
            current = self._case(raw, stored_digest)
            if current.revision != expected:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected}, "
                    f"found {current.revision}"
                )
            if actor == current.opened_by:
                raise PrivacyRightsPermissionDenied(
                    "case closure requires an approver distinct from the opener"
                )
            self._assert_closure_ready(connection, tenant, current, now)
            payload = dict(raw)
            payload.update(
                {
                    "closure_approved_by": actor,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            digest_value = self._write(
                connection,
                "rights_cases",
                tenant,
                "case_id",
                case,
                payload,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "case",
                case,
            )
            self._audit(
                connection,
                tenant,
                "case_closure_approved",
                case,
                actor,
                {"opener_ref": current.opened_by},
                now,
            )
            return self._case(payload, digest_value)

    def close_case(
        self,
        tenant_id: str,
        case_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> RightsCase:
        """Close an approved case as the original opener, completing four-eyes."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        expected = _integer(expected_revision, "expected_revision", 1, 1_000_000)
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        request = {
            "action": "close_case",
            "case_id": case,
            "expected_revision": expected,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            raw, stored_digest = self._get_case_row(connection, tenant, case)
            if operation is not None:
                return self._case(raw, stored_digest)
            current = self._case(raw, stored_digest)
            if current.revision != expected:
                raise PrivacyRightsConflict(
                    f"case revision CAS failed: expected {expected}, "
                    f"found {current.revision}"
                )
            if actor != current.opened_by:
                raise PrivacyRightsPermissionDenied(
                    "only the case opener can execute approved closure"
                )
            if current.closure_approved_by is None or current.closure_approved_by == actor:
                raise PrivacyRightsPermissionDenied(
                    "a distinct authenticated closure approval is required"
                )
            self._assert_closure_ready(connection, tenant, current, now)
            payload = dict(raw)
            payload.update(
                {
                    "status": "closed",
                    "closed_by": actor,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            digest_value = self._write(
                connection,
                "rights_cases",
                tenant,
                "case_id",
                case,
                payload,
                index_updates={"status": "closed"},
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "case",
                case,
            )
            self._audit(
                connection,
                tenant,
                "case_closed",
                case,
                actor,
                {"approved_by": current.closure_approved_by},
                now,
            )
            return self._case(payload, digest_value)

    def export_case(
        self,
        tenant_id: str,
        case_id: str,
        export_id: str,
        *,
        retained_until: float,
        idempotency_key: str,
    ) -> CaseExport:
        """Create a privacy-minimised local evidence export."""

        tenant = _tenant(tenant_id)
        case = _id(case_id, "case_id")
        export_key = _id(export_id, "export_id")
        actor = self._actor()
        now = _timestamp(self._clock(), "current time")
        retained = _retained_until(retained_until, now)
        request = {
            "action": "export_case",
            "case_id": case,
            "export_id": export_key,
            "retained_until": retained,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                raw, digest_value = self._get(
                    connection,
                    "rights_exports",
                    tenant,
                    "export_id",
                    export_key,
                    "case export",
                )
                return self._export(raw, digest_value)
            case_raw, case_digest = self._get_case_row(connection, tenant, case)
            rights_case = self._case(case_raw, case_digest)
            source_rows = connection.execute(
                "SELECT * FROM rights_sources WHERE tenant_id=? ORDER BY source_id",
                (tenant,),
            ).fetchall()
            task_rows = connection.execute(
                "SELECT * FROM rights_tasks WHERE tenant_id=? AND case_id=? ORDER BY task_id",
                (tenant, case),
            ).fetchall()
            evidence_rows = connection.execute(
                "SELECT * FROM rights_evidence WHERE tenant_id=? AND case_id=? "
                "ORDER BY evidence_id",
                (tenant, case),
            ).fetchall()
            body = {
                "case": dict(case_raw),
                "sources": [self._payload(row, "source") for row in source_rows],
                "tasks": [self._payload(row, "source task") for row in task_rows],
                "evidence": [
                    self._payload(row, "source evidence") for row in evidence_rows
                ],
                "limitations": {
                    "provider_operations_performed": False,
                    "response_delivery_performed": False,
                    "estate_completeness_claimed": False,
                    "live_provider_acceptance_verified": False,
                },
            }
            payload = {
                "tenant_id": tenant,
                "export_id": export_key,
                "case_id": case,
                "schema": EXPORT_SCHEMA,
                "body": body,
                "retained_until": min(retained, rights_case.retained_until),
                "created_by": actor,
                "created_at": now,
            }
            digest_value = self._insert(
                connection,
                "rights_exports",
                ("tenant_id", "export_id", "case_id", "retained_until"),
                (tenant, export_key, case, payload["retained_until"]),
                payload,
            )
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "export",
                export_key,
            )
            self._audit(
                connection,
                tenant,
                "case_export_created",
                export_key,
                actor,
                {"case_id": case, "retained_until": payload["retained_until"]},
                now,
            )
            return self._export(payload, digest_value)

    def purge_expired(
        self,
        tenant_id: str,
        *,
        cutoff: float,
        idempotency_key: str,
    ) -> Mapping[str, int]:
        """Purge expired packages, exports, and eligible closed case state."""

        tenant = _tenant(tenant_id)
        moment = _timestamp(cutoff, "cutoff")
        actor = self._actor()
        request = {
            "action": "purge_expired",
            "cutoff": moment,
            "actor_ref": actor,
        }
        with self._connect() as connection:
            self._begin(connection)
            request_sha, operation = self._operation(
                connection, tenant, idempotency_key, request
            )
            if operation is not None:
                raw = operation["result_id"].split(":")
                if len(raw) != 3 or any(not part.isdigit() for part in raw):
                    raise PrivacyRightsIntegrityError("purge result is invalid")
                return {
                    "packages": int(raw[0]),
                    "exports": int(raw[1]),
                    "cases": int(raw[2]),
                }
            package_count = connection.execute(
                "DELETE FROM rights_packages WHERE tenant_id=? AND expires_at<=?",
                (tenant, moment),
            ).rowcount
            export_count = connection.execute(
                "DELETE FROM rights_exports WHERE tenant_id=? AND retained_until<=?",
                (tenant, moment),
            ).rowcount
            candidates = connection.execute(
                "SELECT * FROM rights_cases WHERE tenant_id=? AND status='closed' "
                "AND retained_until<=?",
                (tenant, moment),
            ).fetchall()
            purge_cases: list[str] = []
            for row in candidates:
                rights_case = self._case(
                    self._payload(row, "rights case"), row["integrity_sha256"]
                )
                if not rights_case.legal_hold and not rights_case.retention_blocks_deletion:
                    purge_cases.append(rights_case.case_id)
            for case in purge_cases:
                connection.execute(
                    "DELETE FROM rights_cases WHERE tenant_id=? AND case_id=?",
                    (tenant, case),
                )
            result_id = f"{package_count}:{export_count}:{len(purge_cases)}"
            self._record_operation(
                connection,
                tenant,
                idempotency_key,
                request_sha,
                "purge_result",
                result_id,
            )
            self._audit(
                connection,
                tenant,
                "retention_purge_completed",
                idempotency_key,
                actor,
                {
                    "packages": package_count,
                    "exports": export_count,
                    "cases": len(purge_cases),
                },
                moment,
            )
            return {
                "packages": package_count,
                "exports": export_count,
                "cases": len(purge_cases),
            }

    def verify_integrity(self, tenant_id: str) -> IntegrityVerification:
        """Verify row digests, index bindings, and tenant audit chains."""

        tenant = _tenant(tenant_id)
        self._actor()
        verified = 0
        tables = (
            ("rights_sources", "source"),
            ("rights_cases", "rights case"),
            ("rights_tasks", "source task"),
            ("rights_callbacks", "callback"),
            ("rights_evidence", "source evidence"),
            ("rights_manifests", "completeness manifest"),
            ("rights_packages", "response package"),
            ("rights_exports", "case export"),
            ("rights_claim_batches", "claim batch"),
        )
        with self._connect() as connection:
            for table, label in tables:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE tenant_id=?",  # noqa: S608
                    (tenant,),
                ).fetchall()
                for row in rows:
                    payload = self._payload(row, label)
                    if payload.get("tenant_id") != tenant or row["tenant_id"] != tenant:
                        raise PrivacyRightsIntegrityError(
                            f"persisted {label} is outside the requested tenant"
                        )
                    for column in (
                        "source_id",
                        "case_id",
                        "task_id",
                        "callback_id",
                        "phase",
                        "state",
                        "retained_until",
                        "expires_at",
                    ):
                        if column in row.keys() and payload.get(column) != row[column]:
                            raise PrivacyRightsIntegrityError(
                                f"persisted {label} index binding is invalid"
                            )
                    verified += 1
            operation_rows = connection.execute(
                "SELECT * FROM rights_operations WHERE tenant_id=?",
                (tenant,),
            ).fetchall()
            for row in operation_rows:
                if row["tenant_id"] != tenant:
                    raise PrivacyRightsIntegrityError(
                        "persisted idempotency binding is outside the requested tenant"
                    )
                binding = {
                    "tenant_id": row["tenant_id"],
                    "operation_sha256": row["operation_sha256"],
                    "request_sha256": row["request_sha256"],
                    "result_kind": row["result_kind"],
                    "result_id": row["result_id"],
                }
                if _sha256(binding) != row["integrity_sha256"]:
                    raise PrivacyRightsIntegrityError(
                        "persisted idempotency binding failed integrity verification"
                    )
                verified += 1
            audit_rows = connection.execute(
                "SELECT * FROM rights_audit_events WHERE tenant_id=? ORDER BY sequence",
                (tenant,),
            ).fetchall()
            previous = "0" * 64
            expected_sequence = 0
            terminal = "0" * 64
            tenant_events = 0
            for row in audit_rows:
                payload = self._payload(row, "audit event")
                expected_sequence += 1
                if (
                    row["tenant_id"] != tenant
                    or payload.get("tenant_id") != tenant
                    or payload.get("sequence") != expected_sequence
                    or payload.get("previous_sha256") != previous
                    or row["sequence"] != expected_sequence
                ):
                    raise PrivacyRightsIntegrityError(
                        "privacy-rights audit chain is discontinuous"
                    )
                previous = row["integrity_sha256"]
                tenant_events += 1
                terminal = row["integrity_sha256"]
                verified += 1
            return IntegrityVerification(
                tenant_id=tenant,
                verified_records=verified,
                audit_events=tenant_events,
                terminal_audit_sha256=terminal,
            )

    @staticmethod
    def product_boundaries() -> Mapping[str, object]:
        return {
            "provider_neutral": True,
            "network_io": False,
            "provider_writes": False,
            "identity_verification_performed": False,
            "response_delivery_performed": False,
            "raw_subject_persisted": False,
            "raw_external_request_ref_persisted": False,
            "credential_values_persisted": False,
            "provider_origins_persisted": False,
            "estate_completeness_claimed": False,
            "live_provider_acceptance_verified": False,
        }


__all__ = [
    "CALLBACK_PHASES",
    "DESTRUCTIVE_PHASES",
    "IDENTITY_STATUSES",
    "LIVE_PROVIDER_ACCEPTANCE_VERIFIED",
    "LIVE_PROVIDER_OPERATIONS",
    "RESPONSE_DELIVERY_IMPLEMENTED",
    "RIGHTS",
    "TASK_PHASES",
    "CallbackItem",
    "CaseExport",
    "CompletenessManifest",
    "IntegrityVerification",
    "PrivacyRightsConfigError",
    "PrivacyRightsConflict",
    "PrivacyRightsIntegrityError",
    "PrivacyRightsNotFound",
    "PrivacyRightsOrchestrationError",
    "PrivacyRightsOrchestrationStore",
    "PrivacyRightsPermissionDenied",
    "PrivacyRightsValidationError",
    "ResponsePackage",
    "RightsCase",
    "SourceEnrollment",
    "SourceEvidenceReceipt",
    "SourceTask",
    "enabled",
    "require_enabled",
]
