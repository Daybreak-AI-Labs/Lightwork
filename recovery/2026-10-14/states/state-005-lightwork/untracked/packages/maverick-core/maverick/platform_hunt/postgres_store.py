"""Fleet-shared Postgres authority for platform and environment hunters.

The SQLite implementation in :mod:`maverick.platform_hunt.store` remains the
single-replica compatibility backend.  This module is selected deployment-wide
when the world-model authority is Postgres.  A single tenant-bound table keeps
the hunter projections, tamper-evident journal, audit outbox, response-effect
claims, and scheduler leases in one transaction domain.

Every authority transaction re-verifies the runtime role, complete column and
constraint shape, required indexes, forced RLS, and the sole tenant policy.
Payloads are always application sealed before they reach Postgres.  Schema,
tenant, encryption, connection, and legacy-cutover ambiguity all fail closed.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import canonical_digest

log = logging.getLogger(__name__)

_TABLE = "hunter_control_records"
_POLICY = "mvk_hunter_tenant_isolation"
_SCHEMA_LOCK = 0x6D766B68756E74  # ``mvkhunt``
_JOURNAL_LOCK_PREFIX = "maverick:hunter:journal:v1"

_HUNTER_PLATFORM = "platform"
_HUNTER_ENVIRONMENT = "environment"
_HUNTER_KINDS = (_HUNTER_PLATFORM, _HUNTER_ENVIRONMENT)

_KIND_FINDING = "finding"
_KIND_INVESTIGATION = "investigation"
_KIND_JOURNAL_HEAD = "journal_head"
_KIND_JOURNAL = "journal"
_KIND_OUTBOX = "outbox"
_KIND_RESPONSE = "response"
_KIND_LEASE = "lease"
_RECORD_KINDS = (
    _KIND_FINDING,
    _KIND_INVESTIGATION,
    _KIND_JOURNAL_HEAD,
    _KIND_JOURNAL,
    _KIND_OUTBOX,
    _KIND_RESPONSE,
    _KIND_LEASE,
)

_MAX_LOGICAL_PAYLOAD_BYTES = 4 * 1024 * 1024
_MAX_STORED_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_RECORD_KEY_BYTES = 512
_MAX_STATUS_BYTES = 120
_MAX_ERROR_BYTES = 120
_MAX_JOURNAL_SEQUENCE = 9_223_372_036_854_775_807
_MAX_TIMESTAMP = 32_503_680_000.0
_AUDIT_CUSTODY_EVENT_ID = "platform-hunt-audit-custody-v1"
_AUDIT_CUSTODY_EVENT_KIND = "platform_hunt_custody_initialized"

_COLUMNS = {
    "tenant_id": ("text", "NO"),
    "hunter_kind": ("text", "NO"),
    "record_kind": ("text", "NO"),
    "record_key": ("text", "NO"),
    "revision": ("bigint", "NO"),
    "status": ("text", "NO"),
    "payload_text": ("text", "NO"),
    "payload_sha256": ("text", "NO"),
    "prev_hash": ("text", "NO"),
    "created_at": ("double precision", "NO"),
    "updated_at": ("double precision", "NO"),
    "delivered_at": ("double precision", "NO"),
    "expires_at": ("double precision", "NO"),
}
_PRIMARY_KEY = ("tenant_id", "hunter_kind", "record_kind", "record_key")
_INDEXES = {
    "idx_hunter_records_lookup": (
        "tenant_id",
        "hunter_kind",
        "record_kind",
        "status",
        "updated_at",
        "record_key",
    ),
    "idx_hunter_outbox_pending": (
        "tenant_id",
        "hunter_kind",
        "record_kind",
        "delivered_at",
        "created_at",
        "record_key",
    ),
}
_CHECK_NAMES = frozenset({
    "mvk_hunter_tenant_bounds",
    "mvk_hunter_kind_bound",
    "mvk_hunter_record_kind_bound",
    "mvk_hunter_record_key_bound",
    "mvk_hunter_revision_bound",
    "mvk_hunter_status_bound",
    "mvk_hunter_payload_sealed_bound",
    "mvk_hunter_payload_digest_bound",
    "mvk_hunter_prev_hash_bound",
    "mvk_hunter_timestamps_bound",
})

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    tenant_id TEXT NOT NULL,
    hunter_kind TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_key TEXT NOT NULL,
    revision BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT '',
    payload_text TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL DEFAULT '',
    prev_hash TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    delivered_at DOUBLE PRECISION NOT NULL DEFAULT -1,
    expires_at DOUBLE PRECISION NOT NULL DEFAULT -1,
    PRIMARY KEY (tenant_id, hunter_kind, record_kind, record_key),
    CONSTRAINT mvk_hunter_tenant_bounds
        CHECK (char_length(tenant_id) BETWEEN 1 AND 201),
    CONSTRAINT mvk_hunter_kind_bound
        CHECK (hunter_kind IN ({', '.join(repr(item) for item in _HUNTER_KINDS)})),
    CONSTRAINT mvk_hunter_record_kind_bound
        CHECK (record_kind IN ({', '.join(repr(item) for item in _RECORD_KINDS)})),
    CONSTRAINT mvk_hunter_record_key_bound
        CHECK (octet_length(record_key) BETWEEN 1 AND {_MAX_RECORD_KEY_BYTES}),
    CONSTRAINT mvk_hunter_revision_bound
        CHECK (revision BETWEEN 0 AND {_MAX_JOURNAL_SEQUENCE}),
    CONSTRAINT mvk_hunter_status_bound
        CHECK (octet_length(status) <= {_MAX_STATUS_BYTES}),
    CONSTRAINT mvk_hunter_payload_sealed_bound
        CHECK (octet_length(payload_text) <= {_MAX_STORED_PAYLOAD_BYTES}
               AND payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={{0,2}}$'),
    CONSTRAINT mvk_hunter_payload_digest_bound
        CHECK (payload_sha256 = '' OR payload_sha256 ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT mvk_hunter_prev_hash_bound
        CHECK (prev_hash = '' OR prev_hash ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT mvk_hunter_timestamps_bound
        CHECK (created_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
               AND updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
               AND (delivered_at = -1
                    OR delivered_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})
               AND (expires_at = -1
                    OR expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}))
)
"""


class PostgresHuntStoreUnavailable(RuntimeError):
    """The shared hunter authority cannot safely serve an operation."""


class HuntLegacyMigrationRequired(PostgresHuntStoreUnavailable):
    """Node-local hunter state requires an explicit import before cutover."""


class _SchemaIncompatible(RuntimeError):
    pass


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("shared hunter JSON contains a duplicate key")
        value[key] = item
    return value


def _charge_tree(value: Any, remaining: list[int], *, depth: int = 0) -> None:
    if depth > 40:
        raise ValueError("shared hunter JSON nesting is too deep")
    remaining[0] -= 8
    if isinstance(value, str):
        remaining[0] -= len(value.encode("utf-8"))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _charge_tree(str(key), remaining, depth=depth + 1)
            _charge_tree(item, remaining, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _charge_tree(item, remaining, depth=depth + 1)
    if remaining[0] < 0:
        raise ValueError("shared hunter JSON exceeds its storage limit")


def _canonical_json(value: Mapping[str, Any]) -> str:
    _charge_tree(value, [_MAX_LOGICAL_PAYLOAD_BYTES])
    try:
        raw = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
    except (TypeError, ValueError):
        raise ValueError("shared hunter payload must contain finite JSON values") from None
    if len(raw.encode("utf-8")) > _MAX_LOGICAL_PAYLOAD_BYTES:
        raise ValueError("shared hunter JSON exceeds its storage limit")
    return raw


def _encode_payload(value: Mapping[str, Any]) -> tuple[str, str]:
    raw = _canonical_json(value)
    try:
        from ..crypto_at_rest import at_rest_enabled, is_sealed_str, seal_to_str

        if not at_rest_enabled():
            raise PostgresHuntStoreUnavailable(
                "shared hunter authority requires application at-rest encryption"
            )
        stored = seal_to_str(raw)
        if not is_sealed_str(stored):
            raise PostgresHuntStoreUnavailable(
                "shared hunter authority could not seal its payload"
            )
    except PostgresHuntStoreUnavailable:
        raise
    except Exception:
        raise PostgresHuntStoreUnavailable(
            "shared hunter authority encryption is unavailable"
        ) from None
    if len(stored.encode("utf-8")) > _MAX_STORED_PAYLOAD_BYTES:
        raise ValueError("sealed shared hunter JSON exceeds its storage limit")
    return stored, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _decode_payload(stored: object, expected_digest: object = "") -> dict[str, Any]:
    if not isinstance(stored, str):
        raise PostgresHuntStoreUnavailable("shared hunter payload is not text")
    if len(stored.encode("utf-8")) > _MAX_STORED_PAYLOAD_BYTES:
        raise PostgresHuntStoreUnavailable("shared hunter payload exceeds its bound")
    try:
        from ..crypto_at_rest import at_rest_enabled, is_sealed_str, unseal_from_str

        if not at_rest_enabled() or not is_sealed_str(stored):
            raise PostgresHuntStoreUnavailable(
                "unsealed shared hunter payload was withheld"
            )
        raw = unseal_from_str(stored)
    except PostgresHuntStoreUnavailable:
        raise
    except Exception:
        raise PostgresHuntStoreUnavailable(
            "shared hunter payload could not be unsealed"
        ) from None
    raw_bytes = raw.encode("utf-8")
    if len(raw_bytes) > _MAX_LOGICAL_PAYLOAD_BYTES:
        raise PostgresHuntStoreUnavailable("shared hunter payload exceeds its bound")
    digest = str(expected_digest or "")
    if digest and not hmac.compare_digest(hashlib.sha256(raw_bytes).hexdigest(), digest):
        raise PostgresHuntStoreUnavailable(
            "shared hunter payload failed its content commitment"
        )
    try:
        value = json.loads(raw, object_pairs_hook=_json_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise PostgresHuntStoreUnavailable("shared hunter payload is invalid") from None
    if not isinstance(value, dict):
        raise PostgresHuntStoreUnavailable("shared hunter payload is not an object")
    _charge_tree(value, [_MAX_LOGICAL_PAYLOAD_BYTES])
    return value


def _probe_encryption() -> None:
    probe = uuid.uuid4().hex
    stored, _digest = _encode_payload({"probe": probe})
    decoded = _decode_payload(stored)
    if decoded != {"probe": probe}:
        raise PostgresHuntStoreUnavailable(
            "shared hunter encryption round-trip failed"
        )


def _bounded_text(value: object, name: str, *, limit: int = _MAX_RECORD_KEY_BYTES) -> str:
    text = str(value).strip()
    if not text or "\x00" in text or len(text.encode("utf-8")) > limit:
        raise ValueError(f"{name} is missing or exceeds its storage bound")
    return text


def _status_text(value: object) -> str:
    text = str(value)
    if "\x00" in text or len(text.encode("utf-8")) > _MAX_STATUS_BYTES:
        raise ValueError("hunter record status exceeds its storage bound")
    return text


def _timestamp(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite timestamp")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite timestamp") from None
    if not math.isfinite(parsed) or not 0 <= parsed <= _MAX_TIMESTAMP:
        raise ValueError(f"{name} must be a finite timestamp")
    return parsed


def _lock_id(*parts: str) -> int:
    raw = "\x1f".join(parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big", signed=True)


def _canonical_check(value: object) -> str:
    text = str(value or "").strip().lower().replace('"', "")
    text = re.sub(
        r"::(?:text|bigint|integer|double\s+precision)",
        "",
        text,
    )
    text = re.sub(r"'(-?[0-9]+(?:\.[0-9]+)?)'", r"\1", text)
    text = "".join(text.split()).removeprefix("check")
    return text.replace("(", "").replace(")", "")


def _expected_check_expressions() -> dict[str, frozenset[str]]:
    hunter_kinds = ",".join(repr(item) for item in _HUNTER_KINDS)
    record_kinds = ",".join(repr(item) for item in _RECORD_KINDS)

    def variants(*values: str) -> frozenset[str]:
        return frozenset(_canonical_check(value) for value in values)

    return {
        "mvk_hunter_tenant_bounds": variants(
            "char_length(tenant_id) BETWEEN 1 AND 201",
            "char_length(tenant_id) >= 1 AND char_length(tenant_id) <= 201",
        ),
        "mvk_hunter_kind_bound": variants(
            f"hunter_kind IN ({hunter_kinds})",
            f"hunter_kind = ANY (ARRAY[{hunter_kinds}])",
        ),
        "mvk_hunter_record_kind_bound": variants(
            f"record_kind IN ({record_kinds})",
            f"record_kind = ANY (ARRAY[{record_kinds}])",
        ),
        "mvk_hunter_record_key_bound": variants(
            f"octet_length(record_key) BETWEEN 1 AND {_MAX_RECORD_KEY_BYTES}",
            f"octet_length(record_key) >= 1 AND "
            f"octet_length(record_key) <= {_MAX_RECORD_KEY_BYTES}",
        ),
        "mvk_hunter_revision_bound": variants(
            f"revision BETWEEN 0 AND {_MAX_JOURNAL_SEQUENCE}",
            f"revision >= 0 AND revision <= {_MAX_JOURNAL_SEQUENCE}",
        ),
        "mvk_hunter_status_bound": variants(
            f"octet_length(status) <= {_MAX_STATUS_BYTES}",
        ),
        "mvk_hunter_payload_sealed_bound": variants(
            f"octet_length(payload_text) <= {_MAX_STORED_PAYLOAD_BYTES} AND "
            "payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={0,2}$'",
        ),
        "mvk_hunter_payload_digest_bound": variants(
            "payload_sha256 = '' OR payload_sha256 ~ '^[0-9a-f]{64}$'",
        ),
        "mvk_hunter_prev_hash_bound": variants(
            "prev_hash = '' OR prev_hash ~ '^[0-9a-f]{64}$'",
        ),
        "mvk_hunter_timestamps_bound": variants(
            f"created_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"(delivered_at = -1 OR delivered_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}) "
            f"AND (expires_at = -1 OR expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})",
            f"created_at >= 0 AND created_at <= {_MAX_TIMESTAMP:g} AND "
            f"updated_at >= 0 AND updated_at <= {_MAX_TIMESTAMP:g} AND "
            f"(delivered_at = -1 OR (delivered_at >= 0 AND "
            f"delivered_at <= {_MAX_TIMESTAMP:g})) AND "
            f"(expires_at = -1 OR (expires_at >= 0 AND "
            f"expires_at <= {_MAX_TIMESTAMP:g}))",
        ),
    }


def _pg_array(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    text = str(value or "").strip("{}")
    return tuple(part.strip().strip('"') for part in text.split(",") if part.strip())


def _index_columns(indexdef: object) -> tuple[str, ...]:
    text = str(indexdef)
    match = re.search(r"\((.+)\)(?:\s+where\s+.+)?$", text, re.IGNORECASE)
    if not match:
        return ()
    columns = []
    for raw in match.group(1).split(","):
        token = raw.strip().split()[0].strip('"').lower()
        columns.append(token)
    return tuple(columns)


def _required_indexes_are_exact(rows: object) -> bool:
    if not isinstance(rows, (tuple, list)):
        return False
    definitions = {str(row[0]): str(row[1]) for row in rows if len(row) == 2}
    for name, columns in _INDEXES.items():
        raw = definitions.get(name)
        if raw is None:
            return False
        lowered = " ".join(raw.lower().replace('"', "").split())
        if not lowered.startswith("create index ") or " unique " in lowered:
            return False
        if _TABLE not in lowered or " where " in lowered:
            return False
        if _index_columns(lowered) != columns:
            return False
        if "updated_at" in columns and "updated_at desc" not in lowered:
            return False
    return True


def _policy_rows_are_exact(rows: list[tuple[Any, ...]]) -> bool:
    if len(rows) != 1:
        return False
    name, permissive, roles, command, qual, with_check = rows[0]
    expression = (
        "tenant_id = nullif(current_setting('maverick.tenant', true), '')"
    )
    expected = "".join(expression.lower().split()).replace("::text", "")

    def exact(value: object) -> bool:
        compact = "".join(str(value or "").lower().split()).replace("::text", "")
        return compact in {expected, f"({expected})"}

    return (
        str(name) == _POLICY
        and str(permissive).upper() == "PERMISSIVE"
        and tuple(item.lower() for item in _pg_array(roles)) == ("public",)
        and str(command).upper() == "ALL"
        and exact(qual)
        and exact(with_check)
    )


def _validate_legacy_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise HuntLegacyMigrationRequired(
            "legacy hunter state cannot be inspected"
        ) from None
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", None)
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or bool(getattr(info, "st_file_attributes", 0) & reparse)
        or bool(callable(is_junction) and is_junction())
    ):
        raise HuntLegacyMigrationRequired(
            "legacy hunter state path is not a stable private file"
        )


def assert_no_legacy_hunt_state(path: str | Path) -> None:
    """Refuse a shared cutover while any local authority state remains."""
    db_path = Path(path)
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{db_path}{suffix}")
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise HuntLegacyMigrationRequired(
                "legacy hunter state cannot be inspected"
            ) from None
        try:
            _validate_legacy_path(sidecar)
            if info.st_size:
                raise HuntLegacyMigrationRequired(
                    "legacy hunter state requires an explicit Postgres import"
                )
        except HuntLegacyMigrationRequired:
            raise
        except OSError:
            raise HuntLegacyMigrationRequired(
                "legacy hunter state cannot be inspected"
            ) from None
    try:
        info = db_path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise HuntLegacyMigrationRequired(
            "legacy hunter state cannot be inspected"
        ) from None
    _validate_legacy_path(db_path)
    if info.st_size == 0:
        return
    expected = {
        "hunt_findings",
        "hunt_investigations",
        "hunt_journal",
        "hunt_audit_outbox",
        "hunt_response_executions",
        "hunt_scheduler_leases",
    }
    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            tables = {str(row[0]) for row in rows}
            if tables - expected:
                raise HuntLegacyMigrationRequired(
                    "legacy hunter state has an unrecognized schema"
                )
            for table in sorted(tables):
                row = conn.execute(
                    f"SELECT 1 FROM {table} LIMIT 1"  # noqa: S608 - catalog allowlist
                ).fetchone()
                if row is not None:
                    raise HuntLegacyMigrationRequired(
                        "legacy hunter state requires an explicit Postgres import"
                    )
    except HuntLegacyMigrationRequired:
        raise
    except (OSError, sqlite3.Error):
        raise HuntLegacyMigrationRequired(
            "legacy hunter state cannot be inspected"
        ) from None


@dataclass(frozen=True)
class _Selection:
    postgres: bool
    shared_required: bool
    dsn: str = ""


@dataclass(frozen=True)
class _AuthorityToken:
    """Non-secret deployment binding for one Postgres authority."""

    dsn_sha256: str
    shared_required: bool


def _authority_token(selection: _Selection) -> _AuthorityToken:
    if not selection.postgres or not selection.dsn:
        raise PostgresHuntStoreUnavailable(
            "shared hunter deployment authority is not Postgres"
        )
    return _AuthorityToken(
        dsn_sha256=hashlib.sha256(selection.dsn.encode("utf-8")).hexdigest(),
        shared_required=selection.shared_required,
    )


def _authority_matches(
    token: _AuthorityToken,
    selection: _Selection,
) -> bool:
    if not selection.postgres or not selection.dsn:
        return False
    current = hashlib.sha256(selection.dsn.encode("utf-8")).hexdigest()
    return (
        hmac.compare_digest(token.dsn_sha256, current)
        and token.shared_required is selection.shared_required
    )


_MUTATING_SQL = re.compile(
    r"^\s*(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY|CREATE|ALTER|DROP|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


class _AuthorityBoundCursor:
    """Revalidate a production binding before each authority mutation."""

    def __init__(self, cursor: Any, validate: Callable[[], None]) -> None:
        self._cursor = cursor
        self._validate = validate
        self._failed = False

    def execute(self, sql: str, params: Any = ()) -> Any:
        if self._failed:
            raise PostgresHuntStoreUnavailable(
                "shared hunter authority transaction was marked for rollback"
            )
        if _MUTATING_SQL.match(str(sql)):
            try:
                self._validate()
            except BaseException:
                self._failed = True
                raise
        result = self._cursor.execute(sql, params)
        return self if result is self._cursor else result

    def assert_healthy(self) -> None:
        if self._failed:
            raise PostgresHuntStoreUnavailable(
                "shared hunter authority transaction was marked for rollback"
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def _replica_count() -> int:
    raw: object = os.environ.get("MAVERICK_REPLICA_COUNT", "1")
    if isinstance(raw, bool):
        raise PostgresHuntStoreUnavailable("replica count must be a positive integer")
    try:
        parsed = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        raise PostgresHuntStoreUnavailable(
            "replica count must be a positive integer"
        ) from None
    if parsed < 1 or str(raw).strip() != str(parsed):
        raise PostgresHuntStoreUnavailable("replica count must be a positive integer")
    return parsed


def _selection() -> _Selection:
    try:
        from ..config import config_source_errors, load_global_config

        config = load_global_config()
        source_errors = config_source_errors(include_tenant=False)
    except Exception:
        raise PostgresHuntStoreUnavailable(
            "hunter authority deployment policy is unavailable"
        ) from None
    if not isinstance(config, Mapping):
        raise PostgresHuntStoreUnavailable("hunter authority policy is invalid")
    world = config.get("world_model") or {}
    if not isinstance(world, Mapping):
        raise PostgresHuntStoreUnavailable("world-model policy is invalid")
    backend = str(
        os.environ.get("MAVERICK_WORLD_BACKEND")
        or world.get("backend")
        or "sqlite"
    ).strip().lower()
    if backend not in {"sqlite", "postgres"}:
        raise PostgresHuntStoreUnavailable("world-model backend is invalid")
    try:
        from ..enterprise import deployment_enterprise_enabled

        enterprise = deployment_enterprise_enabled(
            config=config,
            source_errors=source_errors,
        )
    except Exception:
        enterprise = True
    shared_required = bool(enterprise or _replica_count() > 1 or source_errors)
    if backend != "postgres":
        if shared_required:
            raise PostgresHuntStoreUnavailable(
                "enterprise and multi-replica hunter authority requires Postgres; "
                "local fallback is disabled"
            )
        return _Selection(False, False)
    dsn = str(os.environ.get("MAVERICK_PG_DSN") or world.get("dsn") or "").strip()
    if not dsn:
        raise PostgresHuntStoreUnavailable(
            "Postgres hunter authority is selected but no DSN is configured"
        )
    return _Selection(True, shared_required, dsn)


def _hunter_kind(record_change_event_kind: str) -> str:
    return (
        _HUNTER_ENVIRONMENT
        if str(record_change_event_kind) == "env_hunt_record_changed"
        else _HUNTER_PLATFORM
    )


class PostgresHuntStore:
    """Tenant-scoped shared implementation of the stable ``HuntStore`` API."""

    def __init__(
        self,
        path: str | Path,
        audit_recorder: Callable[[str, dict[str, Any]], bool] | None = None,
        *,
        record_change_event_kind: str = "threat_hunt_record_changed",
        dsn: str = "",
        transaction_factory: Callable[[], Any] | None = None,
        connect: Callable[..., Any] | None = None,
        check_legacy: bool = True,
        ensure_schema: bool = True,
        _allow_test_time: bool = False,
        _authority_token: _AuthorityToken | None = None,
    ) -> None:
        # Only ``selected_shared_hunt_store`` supplies this token. Directly
        # injected transaction factories remain deterministic test doubles and
        # do not unexpectedly consult deployment configuration.
        self._authority_token = _authority_token
        self.path = Path(path)
        self._audit_recorder = audit_recorder
        self._record_change_event_kind = str(record_change_event_kind)
        self._hunter_kind = _hunter_kind(record_change_event_kind)
        self._check_legacy = bool(check_legacy)
        self._allow_test_time = bool(_allow_test_time)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        if self._check_legacy:
            assert_no_legacy_hunt_state(self.path)
        _probe_encryption()
        if transaction_factory is None:
            if not dsn:
                raise PostgresHuntStoreUnavailable(
                    "Postgres hunter authority has no configured connection"
                )
            if connect is None:
                try:
                    import psycopg

                    connect = psycopg.connect
                except ImportError:
                    raise PostgresHuntStoreUnavailable(
                        "Postgres hunter authority requires maverick-core[postgres]"
                    ) from None

            @contextlib.contextmanager
            def direct_transaction():
                connection = None
                cursor = None
                try:
                    connection = connect(dsn, autocommit=False)
                    cursor = connection.cursor()
                    yield cursor
                    connection.commit()
                except BaseException:
                    if connection is not None:
                        try:
                            connection.rollback()
                        except Exception:
                            pass
                    raise
                finally:
                    if cursor is not None:
                        try:
                            cursor.close()
                        except Exception:
                            pass
                    if connection is not None:
                        try:
                            connection.close()
                        except Exception:
                            pass

            transaction_factory = direct_transaction
        self._transaction_factory = transaction_factory
        if ensure_schema:
            self._ensure_schema()

    def _assert_authority_current(self) -> None:
        token = self._authority_token
        if token is None:
            return
        try:
            selection = _selection()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise PostgresHuntStoreUnavailable(
                "shared hunter deployment authority cannot be revalidated"
            ) from None
        if not _authority_matches(token, selection):
            raise PostgresHuntStoreUnavailable(
                "shared hunter deployment authority changed after backend construction"
            )

    def _assert_transaction_binding(self, tenant: str) -> None:
        if self._authority_token is None:
            return
        self._assert_authority_current()
        if not hmac.compare_digest(self._tenant_key(), tenant):
            raise PostgresHuntStoreUnavailable(
                "shared hunter tenant changed during an authority transaction"
            )

    def _authority_cursor(
        self,
        cursor: Any,
        *,
        tenant: str | None = None,
    ) -> Any:
        if self._authority_token is None:
            return cursor
        validate = (
            self._assert_authority_current
            if tenant is None
            else lambda: self._assert_transaction_binding(tenant)
        )
        return _AuthorityBoundCursor(cursor, validate)

    @staticmethod
    def _policy_sql() -> str:
        return (
            f"CREATE POLICY {_POLICY} ON {_TABLE} "
            "USING (tenant_id = nullif(current_setting('maverick.tenant', true), '')) "
            "WITH CHECK (tenant_id = nullif(current_setting('maverick.tenant', true), ''))"
        )

    def _schema_status_on(self, cur: Any) -> str:
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        role = cur.fetchone()
        if not role or bool(role[0]) or bool(role[1]):
            return "incompatible"
        cur.execute(f"SELECT to_regclass(current_schema() || '.{_TABLE}')")
        row = cur.fetchone()
        if not row or row[0] is None:
            return "missing"
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=%s "
            "ORDER BY ordinal_position",
            (_TABLE,),
        )
        columns = {
            str(item[0]): (str(item[1]), str(item[2]))
            for item in cur.fetchall()
        }
        if set(columns) != set(_COLUMNS):
            return "missing" if set(columns) < set(_COLUMNS) else "incompatible"
        if columns != _COLUMNS:
            return "incompatible"
        cur.execute(
            "SELECT array_agg(a.attname ORDER BY keys.ordinality) "
            "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN unnest(i.indkey) WITH ORDINALITY AS keys(attnum, ordinality) "
            "ON true JOIN pg_attribute a ON a.attrelid=c.oid "
            "AND a.attnum=keys.attnum "
            f"WHERE c.oid='{_TABLE}'::regclass AND i.indisprimary "
            "GROUP BY i.indexrelid"
        )
        primary = cur.fetchone()
        if not primary:
            return "missing"
        if _pg_array(primary[0]) != _PRIMARY_KEY:
            return "incompatible"
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            f"WHERE conrelid='{_TABLE}'::regclass AND contype='c'"
        )
        checks = {str(item[0]): _canonical_check(item[1]) for item in cur.fetchall()}
        if set(checks) != _CHECK_NAMES:
            return "missing" if set(checks) < _CHECK_NAMES else "incompatible"
        expected = _expected_check_expressions()
        if any(checks[name] not in expected[name] for name in _CHECK_NAMES):
            return "incompatible"
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname=current_schema() AND tablename=%s",
            (_TABLE,),
        )
        index_rows = list(cur.fetchall())
        index_names = {str(item[0]) for item in index_rows}
        if not set(_INDEXES) <= index_names:
            return "missing"
        if not _required_indexes_are_exact(index_rows):
            return "incompatible"
        cur.execute(
            f"SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            f"WHERE oid='{_TABLE}'::regclass"
        )
        flags = cur.fetchone()
        if not flags or not bool(flags[0]) or not bool(flags[1]):
            return "missing"
        cur.execute(
            "SELECT policyname, permissive, roles, cmd, qual, with_check "
            "FROM pg_policies WHERE schemaname=current_schema() "
            "AND tablename=%s ORDER BY policyname",
            (_TABLE,),
        )
        policies = list(cur.fetchall())
        if not policies:
            return "missing"
        if not _policy_rows_are_exact(policies):
            return "incompatible"
        return "ready"

    def _schema_status(self) -> str:
        try:
            self._assert_authority_current()
            with self._transaction_factory() as cur:
                self._assert_authority_current()
                return self._schema_status_on(cur)
        except Exception:
            return "unavailable"

    def _ensure_schema(self) -> None:
        self._assert_authority_current()
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            initial = self._schema_status()
            if initial == "ready":
                self._schema_ready = True
                return
            if initial == "incompatible":
                raise PostgresHuntStoreUnavailable(
                    "shared hunter schema or database role is incompatible"
                )
            if initial == "unavailable":
                raise PostgresHuntStoreUnavailable(
                    "shared hunter schema cannot be verified"
                )
            try:
                with self._transaction_factory() as cur:
                    self._assert_authority_current()
                    cur = self._authority_cursor(cur)
                    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
                    waited = self._schema_status_on(cur)
                    if waited == "ready":
                        self._schema_ready = True
                        return
                    if waited != "missing":
                        raise _SchemaIncompatible
                    cur.execute(_CREATE_TABLE_SQL)
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hunter_records_lookup "
                        f"ON {_TABLE}(tenant_id, hunter_kind, record_kind, status, "
                        "updated_at DESC, record_key)"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_hunter_outbox_pending "
                        f"ON {_TABLE}(tenant_id, hunter_kind, record_kind, "
                        "delivered_at, created_at, record_key)"
                    )
                    cur.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
                    cur.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
                    cur.execute(
                        "SELECT policyname, permissive, roles, cmd, qual, with_check "
                        "FROM pg_policies WHERE schemaname=current_schema() "
                        "AND tablename=%s ORDER BY policyname",
                        (_TABLE,),
                    )
                    policies = list(cur.fetchall())
                    if not policies:
                        cur.execute(self._policy_sql())
                    elif not _policy_rows_are_exact(policies):
                        raise _SchemaIncompatible
                    if self._schema_status_on(cur) != "ready":
                        raise _SchemaIncompatible
            except Exception as exc:
                if self._schema_status() != "ready":
                    log.error(
                        "Postgres hunter schema unavailable (%s)",
                        type(exc).__name__,
                    )
                    raise PostgresHuntStoreUnavailable(
                        "shared hunter schema is unavailable or ambiguous"
                    ) from None
            if self._schema_status() != "ready":
                raise PostgresHuntStoreUnavailable(
                    "shared hunter schema is unavailable or ambiguous"
                )
            self._schema_ready = True

    def _tenant_key(self) -> str:
        try:
            from ..paths import canonical_tenant_id, current_tenant_id_strict

            tenant = current_tenant_id_strict()
            if tenant is None:
                raise PostgresHuntStoreUnavailable(
                    "shared hunter authority requires an active tenant"
                )
            return canonical_tenant_id(tenant)
        except PostgresHuntStoreUnavailable:
            raise
        except Exception:
            raise PostgresHuntStoreUnavailable(
                "shared hunter tenant binding is unavailable"
            ) from None

    def _assert_tenant(self, expected: str) -> None:
        if not hmac.compare_digest(self._tenant_key(), expected):
            raise PostgresHuntStoreUnavailable(
                "shared hunter tenant changed during an authority operation"
            )

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[tuple[Any, str]]:
        from .store import RecordNotFound, RevisionConflict

        self._assert_authority_current()
        tenant = self._tenant_key()
        if self._check_legacy:
            assert_no_legacy_hunt_state(self.path)
        _probe_encryption()
        self._ensure_schema()
        self._assert_authority_current()
        try:
            with self._transaction_factory() as cur:
                self._assert_authority_current()
                if self._schema_status_on(cur) != "ready":
                    raise PostgresHuntStoreUnavailable(
                        "shared hunter schema or database role is unavailable"
                    )
                cur.execute(
                    "SELECT set_config('maverick.tenant', %s, true)",
                    (tenant,),
                )
                guarded = self._authority_cursor(cur, tenant=tenant)
                yield guarded, tenant
                if isinstance(guarded, _AuthorityBoundCursor):
                    guarded.assert_healthy()
                self._assert_transaction_binding(tenant)
        except (
            HuntLegacyMigrationRequired,
            PostgresHuntStoreUnavailable,
            RecordNotFound,
            RevisionConflict,
            ValueError,
        ):
            raise
        except Exception as exc:
            log.error(
                "Postgres hunter authority transaction failed (%s)",
                type(exc).__name__,
            )
            raise PostgresHuntStoreUnavailable(
                "Postgres hunter authority is unavailable"
            ) from None

    @staticmethod
    def _expected_revision(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError("expected_revision must be a positive integer")
        return value

    @staticmethod
    def _plain(value: object) -> dict[str, Any]:
        from .store import _plain, _reject_raw_telemetry

        payload = _plain(value)
        _reject_raw_telemetry(payload)
        return payload

    @staticmethod
    def _actor_label(actor: str) -> str:
        from .store import _actor_label

        return _actor_label(actor)

    def _append_journal(
        self,
        cur: Any,
        tenant: str,
        *,
        record_type: str,
        record_id: str,
        revision: int,
        action: str,
        actor_label: str,
        payload_sha256: str,
        timestamp: float,
    ) -> None:
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_lock_id(_JOURNAL_LOCK_PREFIX, tenant, self._hunter_kind),),
        )
        cur.execute(
            f"SELECT revision, payload_text, payload_sha256, prev_hash, created_at "
            f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
            "AND record_kind=%s AND record_key='head' FOR UPDATE",
            (tenant, self._hunter_kind, _KIND_JOURNAL_HEAD),
        )
        head = cur.fetchone()
        cur.execute(
            f"SELECT revision, payload_text, payload_sha256, prev_hash FROM {_TABLE} "
            "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
            "ORDER BY revision DESC LIMIT 1 FOR UPDATE",
            (tenant, self._hunter_kind, _KIND_JOURNAL),
        )
        tail = cur.fetchone()
        if head is None:
            if tail is not None:
                raise PostgresHuntStoreUnavailable(
                    "shared hunter journal head is missing"
                )
            sequence = 1
            previous = ""
            head_created_at = timestamp
        else:
            head_payload = _decode_payload(head[1], head[2])
            head_hash = str(head_payload.get("entry_hash", ""))
            try:
                tail_payload = _decode_payload(tail[1]) if tail is not None else {}
                tail_hash = canonical_digest(tail_payload)
                tail_previous = str(tail_payload.get("prev_hash", ""))
            except PostgresHuntStoreUnavailable:
                tail_hash = ""
                tail_previous = ""
            if (
                len(head_hash) != 64
                or any(char not in "0123456789abcdef" for char in head_hash)
                or not hmac.compare_digest(str(head[3]), head_hash)
                or tail is None
                or int(tail[0]) != int(head[0])
                or not hmac.compare_digest(str(tail[2]), head_hash)
                or not hmac.compare_digest(tail_hash, head_hash)
                or not hmac.compare_digest(str(tail[3]), tail_previous)
            ):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter journal head failed its tail binding"
                )
            sequence = int(head[0]) + 1
            previous = head_hash
            head_created_at = float(head[4])
        if sequence > _MAX_JOURNAL_SEQUENCE:
            raise PostgresHuntStoreUnavailable("shared hunter journal is exhausted")
        body = {
            "ts": timestamp,
            "record_type": record_type,
            "record_id": record_id,
            "revision": revision,
            "action": action,
            "actor_label": actor_label,
            "payload_sha256": payload_sha256,
            "prev_hash": previous,
        }
        entry_hash = canonical_digest(body)
        stored, _stored_digest = _encode_payload(body)
        cur.execute(
            f"INSERT INTO {_TABLE} "
            "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
            "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
            "delivered_at, expires_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,-1,-1) "
            "ON CONFLICT DO NOTHING",
            (
                tenant,
                self._hunter_kind,
                _KIND_JOURNAL,
                f"{sequence:020d}",
                sequence,
                _bounded_text(action, "journal action", limit=_MAX_STATUS_BYTES),
                stored,
                entry_hash,
                previous,
                timestamp,
                timestamp,
            ),
        )
        if cur.rowcount != 1:
            raise PostgresHuntStoreUnavailable(
                "shared hunter journal append raced unexpectedly"
            )
        head_stored, head_digest = _encode_payload({"entry_hash": entry_hash})
        if head is None:
            cur.execute(
                f"INSERT INTO {_TABLE} "
                "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
                "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
                "delivered_at, expires_at) "
                "VALUES (%s,%s,%s,'head',%s,'active',%s,%s,%s,%s,%s,-1,-1) "
                "ON CONFLICT DO NOTHING",
                (
                    tenant,
                    self._hunter_kind,
                    _KIND_JOURNAL_HEAD,
                    sequence,
                    head_stored,
                    head_digest,
                    entry_hash,
                    head_created_at,
                    timestamp,
                ),
            )
        else:
            cur.execute(
                f"UPDATE {_TABLE} SET revision=%s, payload_text=%s, "
                "payload_sha256=%s, prev_hash=%s, updated_at=%s "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key='head' AND revision=%s",
                (
                    sequence,
                    head_stored,
                    head_digest,
                    entry_hash,
                    timestamp,
                    tenant,
                    self._hunter_kind,
                    _KIND_JOURNAL_HEAD,
                    int(head[0]),
                ),
            )
        if cur.rowcount != 1:
            raise PostgresHuntStoreUnavailable(
                "shared hunter journal head update raced unexpectedly"
            )

    def _queue_event(
        self,
        cur: Any,
        tenant: str,
        event_kind: str,
        payload: dict[str, Any],
        *,
        timestamp: float,
        allow_existing: bool = False,
    ) -> None:
        event_id = _bounded_text(payload.get("event_id") or uuid.uuid4().hex, "event id")
        event_payload = {**payload, "event_id": event_id}
        stored, digest = _encode_payload({
            "event_kind": _bounded_text(event_kind, "event kind", limit=120),
            "payload": event_payload,
        })
        cur.execute(
            f"INSERT INTO {_TABLE} "
            "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
            "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
            "delivered_at, expires_at) "
            "VALUES (%s,%s,%s,%s,1,'pending',%s,%s,'',%s,%s,-1,-1) "
            "ON CONFLICT DO NOTHING",
            (
                tenant,
                self._hunter_kind,
                _KIND_OUTBOX,
                event_id,
                stored,
                digest,
                timestamp,
                timestamp,
            ),
        )
        if cur.rowcount != 1 and not allow_existing:
            raise PostgresHuntStoreUnavailable("shared hunter audit event id collided")
        if cur.rowcount != 1:
            # The only insert-once event is the custody witness.  Never accept
            # an attacker-planted row under that well-known id as proof that a
            # prior signed audit chain existed.
            if (
                event_id != _AUDIT_CUSTODY_EVENT_ID
                or event_kind != _AUDIT_CUSTODY_EVENT_KIND
            ):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter insert-once audit event is invalid"
                )
            cur.execute(
                f"SELECT payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, _KIND_OUTBOX, event_id),
            )
            existing = cur.fetchone()
            if existing is None:
                raise PostgresHuntStoreUnavailable(
                    "shared hunter custody witness disappeared"
                )
            decoded = _decode_payload(existing[0], existing[1])
            durable_payload = decoded.get("payload")
            if (
                str(decoded.get("event_kind", "")) != _AUDIT_CUSTODY_EVENT_KIND
                or not isinstance(durable_payload, dict)
                or durable_payload.get("event_id") != _AUDIT_CUSTODY_EVENT_ID
                or durable_payload.get("version") != 1
                or durable_payload.get("purpose") != "platform_hunt_audit_custody"
            ):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter custody witness failed its binding"
                )
            _timestamp(durable_payload.get("occurred_at"), "custody timestamp")

    def _queue_audit(
        self,
        cur: Any,
        tenant: str,
        *,
        record_type: str,
        record_id: str,
        revision: int,
        action: str,
        actor_label: str,
        payload_sha256: str,
        status: str,
        timestamp: float,
    ) -> None:
        event_id = uuid.uuid4().hex
        self._queue_event(
            cur,
            tenant,
            self._record_change_event_kind,
            {
                "event_id": event_id,
                "record_type": record_type,
                "record_id": record_id,
                "revision": revision,
                "action": action,
                "actor": actor_label,
                "record_sha256": payload_sha256,
                "status": status,
                "occurred_at": timestamp,
            },
            timestamp=timestamp,
        )

    def _create(self, record_type: str, record: object, *, actor: str) -> dict[str, Any]:
        from .store import RevisionConflict

        if record_type not in {_KIND_FINDING, _KIND_INVESTIGATION}:
            raise ValueError("unknown hunter record type")
        payload = self._plain(record)
        id_key = f"{record_type}_id"
        record_id = _bounded_text(payload.get(id_key, ""), id_key)
        payload.pop("revision", None)
        revision = 1
        payload["revision"] = revision
        stored, digest = _encode_payload(payload)
        status = _status_text(payload.get("status", "open"))
        timestamp = _timestamp(time.time(), "record timestamp")
        label = self._actor_label(actor)
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"INSERT INTO {_TABLE} "
                "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
                "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
                "delivered_at, expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'',%s,%s,-1,-1) "
                "ON CONFLICT DO NOTHING",
                (
                    tenant,
                    self._hunter_kind,
                    record_type,
                    record_id,
                    revision,
                    status,
                    stored,
                    digest,
                    timestamp,
                    timestamp,
                ),
            )
            if cur.rowcount != 1:
                raise RevisionConflict(f"{record_type} {record_id} already exists")
            self._append_journal(
                cur,
                tenant,
                record_type=record_type,
                record_id=record_id,
                revision=revision,
                action="create",
                actor_label=label,
                payload_sha256=digest,
                timestamp=timestamp,
            )
            self._queue_audit(
                cur,
                tenant,
                record_type=record_type,
                record_id=record_id,
                revision=revision,
                action="create",
                actor_label=label,
                payload_sha256=digest,
                status=status,
                timestamp=timestamp,
            )
        self.flush_audit_outbox(_expected_tenant=tenant)
        return payload

    def _update(
        self,
        record_type: str,
        record_id: str,
        changes: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
        action: str = "update",
    ) -> dict[str, Any]:
        from .store import RecordNotFound, RevisionConflict, _reject_raw_telemetry

        if record_type not in {_KIND_FINDING, _KIND_INVESTIGATION}:
            raise ValueError("unknown hunter record type")
        key = _bounded_text(record_id, f"{record_type} id")
        expected = self._expected_revision(expected_revision)
        forbidden = {"revision", f"{record_type}_id"}
        if forbidden.intersection(changes):
            raise ValueError("record identity and revision cannot be replaced")
        _reject_raw_telemetry(changes)
        timestamp = _timestamp(time.time(), "record timestamp")
        label = self._actor_label(actor)
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT revision, payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, record_type, key),
            )
            row = cur.fetchone()
            if row is None:
                raise RecordNotFound(key)
            current = int(row[0])
            if current != expected:
                raise RevisionConflict(
                    f"expected revision {expected}, found {current} for "
                    f"{record_type} {key}"
                )
            payload = _decode_payload(row[1], row[2])
            payload.update(dict(changes))
            revision = current + 1
            payload["revision"] = revision
            stored, digest = _encode_payload(payload)
            status = _status_text(payload.get("status", "open"))
            cur.execute(
                f"UPDATE {_TABLE} SET revision=%s, status=%s, payload_text=%s, "
                "payload_sha256=%s, updated_at=%s "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s AND revision=%s",
                (
                    revision,
                    status,
                    stored,
                    digest,
                    timestamp,
                    tenant,
                    self._hunter_kind,
                    record_type,
                    key,
                    expected,
                ),
            )
            if cur.rowcount != 1:
                raise RevisionConflict(f"concurrent update to {record_type} {key}")
            self._append_journal(
                cur,
                tenant,
                record_type=record_type,
                record_id=key,
                revision=revision,
                action=action,
                actor_label=label,
                payload_sha256=digest,
                timestamp=timestamp,
            )
            self._queue_audit(
                cur,
                tenant,
                record_type=record_type,
                record_id=key,
                revision=revision,
                action=action,
                actor_label=label,
                payload_sha256=digest,
                status=status,
                timestamp=timestamp,
            )
        self.flush_audit_outbox(_expected_tenant=tenant)
        return payload

    def create_finding(self, finding: object, *, actor: str) -> dict[str, Any]:
        return self._create(_KIND_FINDING, finding, actor=actor)

    def update_finding(
        self,
        finding_id: str,
        changes: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        return self._update(
            _KIND_FINDING,
            finding_id,
            changes,
            expected_revision=expected_revision,
            actor=actor,
        )

    def create_investigation(self, investigation: object, *, actor: str) -> dict[str, Any]:
        return self._create(_KIND_INVESTIGATION, investigation, actor=actor)

    def update_investigation(
        self,
        investigation_id: str,
        changes: dict[str, Any],
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        return self._update(
            _KIND_INVESTIGATION,
            investigation_id,
            changes,
            expected_revision=expected_revision,
            actor=actor,
        )

    def propose_containment(
        self,
        investigation_id: str,
        proposal: object,
        *,
        expected_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        return self._update(
            _KIND_INVESTIGATION,
            investigation_id,
            {"containment": self._plain(proposal)},
            expected_revision=expected_revision,
            actor=actor,
            action="propose_containment",
        )

    def _get(self, record_type: str, record_id: str) -> dict[str, Any] | None:
        key = _bounded_text(record_id, f"{record_type} id")
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s",
                (tenant, self._hunter_kind, record_type, key),
            )
            row = cur.fetchone()
            return None if row is None else _decode_payload(row[0], row[1])

    def get_finding(self, finding_id: str) -> dict[str, Any] | None:
        return self._get(_KIND_FINDING, finding_id)

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        return self._get(_KIND_INVESTIGATION, investigation_id)

    def _list(self, record_type: str, *, status: str | None, limit: int) -> list[dict]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10_000:
            raise ValueError("limit must be between 1 and 10000")
        params: list[Any] = [self._hunter_kind, record_type]
        status_sql = ""
        if status is not None:
            status_value = _status_text(status)
            status_sql = " AND status=%s"
            params.append(status_value)
        params.append(limit)
        with self._transaction() as (cur, tenant):
            params.insert(0, tenant)
            cur.execute(
                f"SELECT payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s"
                f"{status_sql} ORDER BY updated_at DESC, record_key LIMIT %s",
                tuple(params),
            )
            return [_decode_payload(row[0], row[1]) for row in cur.fetchall()]

    def list_findings(self, *, status: str | None = None, limit: int = 500) -> list[dict]:
        return self._list(_KIND_FINDING, status=status, limit=limit)

    def list_investigations(
        self,
        *,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        return self._list(_KIND_INVESTIGATION, status=status, limit=limit)

    @staticmethod
    def _execution_binding(
        *, proposal_sha256: str, approval_id: str, executor: str,
    ) -> tuple[str, str, str]:
        digest = str(proposal_sha256).strip().lower()
        approval = _bounded_text(approval_id, "approval id")
        normalized_executor = _bounded_text(executor, "executor", limit=120).lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("response proposal digest must be hexadecimal SHA-256")
        return digest, approval, normalized_executor

    @staticmethod
    def _response_result(
        proposal: str,
        payload: dict[str, Any],
        status: str,
        created_at: float,
        updated_at: float,
        *,
        new_claim: bool | None = None,
    ) -> dict[str, Any]:
        result = {
            "proposal_id": proposal,
            "proposal_sha256": str(payload.get("proposal_sha256", "")),
            "approval_id": str(payload.get("approval_id", "")),
            "executor": str(payload.get("executor", "")),
            "status": status,
            "error_kind": str(payload.get("error_kind", "")),
            "created_at": created_at,
            "updated_at": updated_at,
        }
        receipt = payload.get("receipt")
        if isinstance(receipt, dict):
            result["receipt"] = receipt
        if new_claim is not None:
            result["new_claim"] = new_claim
        return result

    def claim_response_execution(
        self,
        *,
        proposal_id: str,
        proposal_sha256: str,
        approval_id: str,
        executor: str,
        actor: str,
    ) -> dict[str, Any]:
        from .store import RevisionConflict

        proposal = _bounded_text(proposal_id, "proposal id")
        digest, approval, normalized_executor = self._execution_binding(
            proposal_sha256=proposal_sha256,
            approval_id=approval_id,
            executor=executor,
        )
        timestamp = _timestamp(time.time(), "response timestamp")
        payload = {
            "proposal_sha256": digest,
            "approval_id": approval,
            "executor": normalized_executor,
            "error_kind": "",
        }
        stored, payload_digest = _encode_payload(payload)
        created = False
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"INSERT INTO {_TABLE} "
                "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
                "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
                "delivered_at, expires_at) "
                "VALUES (%s,%s,%s,%s,1,'claimed',%s,%s,'',%s,%s,-1,-1) "
                "ON CONFLICT DO NOTHING",
                (
                    tenant,
                    self._hunter_kind,
                    _KIND_RESPONSE,
                    proposal,
                    stored,
                    payload_digest,
                    timestamp,
                    timestamp,
                ),
            )
            created = cur.rowcount == 1
            cur.execute(
                f"SELECT status, payload_text, payload_sha256, created_at, updated_at "
                f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind=%s AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, _KIND_RESPONSE, proposal),
            )
            row = cur.fetchone()
            if row is None:
                raise PostgresHuntStoreUnavailable(
                    "shared response execution claim disappeared"
                )
            durable = _decode_payload(row[1], row[2])
            if any(
                not hmac.compare_digest(str(durable.get(name, "")), expected)
                for name, expected in (
                    ("proposal_sha256", digest),
                    ("approval_id", approval),
                    ("executor", normalized_executor),
                )
            ):
                raise RevisionConflict(
                    "response proposal was already claimed under a different binding"
                )
            if created:
                self._queue_event(
                    cur,
                    tenant,
                    "env_hunt_response_execution_claimed",
                    {
                        "proposal_id": proposal,
                        "proposal_sha256": digest,
                        "approval_id": approval,
                        "executor": normalized_executor,
                        "actor": self._actor_label(actor),
                        "phase": "claimed",
                        "occurred_at": timestamp,
                    },
                    timestamp=timestamp,
                )
            result = self._response_result(
                proposal,
                durable,
                str(row[0]),
                float(row[3]),
                float(row[4]),
                new_claim=created,
            )
        self.flush_audit_outbox(_expected_tenant=tenant)
        return result

    def complete_response_execution(
        self,
        *,
        proposal_id: str,
        proposal_sha256: str,
        approval_id: str,
        executor: str,
        receipt: dict[str, Any],
        actor: str,
    ) -> dict[str, Any]:
        from .store import RevisionConflict, _reject_raw_telemetry

        proposal = _bounded_text(proposal_id, "proposal id")
        digest, approval, normalized_executor = self._execution_binding(
            proposal_sha256=proposal_sha256,
            approval_id=approval_id,
            executor=executor,
        )
        receipt_payload = dict(receipt)
        _reject_raw_telemetry(receipt_payload)
        try:
            encoded_receipt = json.dumps(
                receipt_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise ValueError("response receipt must contain finite JSON values") from None
        if len(encoded_receipt.encode("utf-8")) > 64 * 1024:
            raise ValueError("response receipt exceeds the 64 KiB persistence limit")
        timestamp = _timestamp(time.time(), "response timestamp")
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT revision, status, payload_text, payload_sha256, created_at "
                f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind=%s AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, _KIND_RESPONSE, proposal),
            )
            row = cur.fetchone()
            if row is None:
                raise RevisionConflict("response proposal has no durable execution claim")
            payload = _decode_payload(row[2], row[3])
            if any(
                not hmac.compare_digest(str(payload.get(name, "")), expected)
                for name, expected in (
                    ("proposal_sha256", digest),
                    ("approval_id", approval),
                    ("executor", normalized_executor),
                )
            ):
                raise RevisionConflict("response execution binding changed after claim")
            status = str(row[1])
            if status == "completed":
                prior = payload.get("receipt")
                if not isinstance(prior, dict):
                    raise PostgresHuntStoreUnavailable(
                        "completed response execution has no durable receipt"
                    )
                return prior
            if status != "claimed":
                raise RevisionConflict("response execution outcome requires reconciliation")
            payload["receipt"] = receipt_payload
            payload["error_kind"] = ""
            stored, payload_digest = _encode_payload(payload)
            cur.execute(
                f"UPDATE {_TABLE} SET revision=%s, status='completed', payload_text=%s, "
                "payload_sha256=%s, updated_at=%s WHERE tenant_id=%s "
                "AND hunter_kind=%s AND record_kind=%s AND record_key=%s "
                "AND revision=%s AND status='claimed'",
                (
                    int(row[0]) + 1,
                    stored,
                    payload_digest,
                    timestamp,
                    tenant,
                    self._hunter_kind,
                    _KIND_RESPONSE,
                    proposal,
                    int(row[0]),
                ),
            )
            if cur.rowcount != 1:
                raise RevisionConflict("concurrent response execution completion")
            self._queue_event(
                cur,
                tenant,
                "env_hunt_response_executed",
                {
                    "proposal_id": proposal,
                    "proposal_sha256": digest,
                    "approval_id": approval,
                    "executor": normalized_executor,
                    "outcome": str(receipt_payload.get("outcome", ""))[:120],
                    "actor": self._actor_label(actor),
                    "phase": "completed",
                    "occurred_at": timestamp,
                },
                timestamp=timestamp,
            )
        self.flush_audit_outbox(_expected_tenant=tenant)
        return receipt_payload

    def mark_response_execution_ambiguous(
        self,
        *,
        proposal_id: str,
        proposal_sha256: str,
        approval_id: str,
        executor: str,
        error_kind: str,
        actor: str,
    ) -> None:
        from .store import RevisionConflict

        proposal = _bounded_text(proposal_id, "proposal id")
        digest, approval, normalized_executor = self._execution_binding(
            proposal_sha256=proposal_sha256,
            approval_id=approval_id,
            executor=executor,
        )
        safe_error = str(error_kind).strip()[:_MAX_ERROR_BYTES] or "execution_error"
        timestamp = _timestamp(time.time(), "response timestamp")
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT revision, status, payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, _KIND_RESPONSE, proposal),
            )
            row = cur.fetchone()
            if row is None:
                raise RevisionConflict("response proposal has no durable execution claim")
            payload = _decode_payload(row[2], row[3])
            if any(
                not hmac.compare_digest(str(payload.get(name, "")), expected)
                for name, expected in (
                    ("proposal_sha256", digest),
                    ("approval_id", approval),
                    ("executor", normalized_executor),
                )
            ):
                raise RevisionConflict("response execution binding changed after claim")
            status = str(row[1])
            if status in {"completed", "ambiguous"}:
                return
            if status != "claimed":
                raise RevisionConflict("response execution outcome requires reconciliation")
            payload["error_kind"] = safe_error
            stored, payload_digest = _encode_payload(payload)
            cur.execute(
                f"UPDATE {_TABLE} SET revision=%s, status='ambiguous', payload_text=%s, "
                "payload_sha256=%s, updated_at=%s WHERE tenant_id=%s "
                "AND hunter_kind=%s AND record_kind=%s AND record_key=%s "
                "AND revision=%s AND status='claimed'",
                (
                    int(row[0]) + 1,
                    stored,
                    payload_digest,
                    timestamp,
                    tenant,
                    self._hunter_kind,
                    _KIND_RESPONSE,
                    proposal,
                    int(row[0]),
                ),
            )
            if cur.rowcount != 1:
                raise RevisionConflict("concurrent response execution ambiguity")
            self._queue_event(
                cur,
                tenant,
                "env_hunt_response_ambiguous",
                {
                    "proposal_id": proposal,
                    "proposal_sha256": digest,
                    "approval_id": approval,
                    "executor": normalized_executor,
                    "error_kind": safe_error,
                    "actor": self._actor_label(actor),
                    "occurred_at": timestamp,
                },
                timestamp=timestamp,
            )
        self.flush_audit_outbox(_expected_tenant=tenant)

    def get_response_execution(self, proposal_id: str) -> dict[str, Any] | None:
        proposal = _bounded_text(proposal_id, "proposal id")
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT status, payload_text, payload_sha256, created_at, updated_at "
                f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind=%s AND record_key=%s",
                (tenant, self._hunter_kind, _KIND_RESPONSE, proposal),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return self._response_result(
                proposal,
                _decode_payload(row[1], row[2]),
                str(row[0]),
                float(row[3]),
                float(row[4]),
            )

    def try_acquire_scheduler_lease(
        self,
        name: str,
        *,
        owner: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> bool:
        lease_name = _bounded_text(name, "scheduler lease", limit=120).lower()
        if isinstance(lease_seconds, bool):
            raise ValueError("scheduler lease must be between 30 seconds and one day")
        try:
            duration = float(lease_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(
                "scheduler lease must be between 30 seconds and one day"
            ) from None
        if not math.isfinite(duration) or not 30 <= duration <= 86_400:
            raise ValueError("scheduler lease must be between 30 seconds and one day")
        payload = {"owner_label": self._actor_label(owner)}
        stored, digest = _encode_payload(payload)
        with self._transaction() as (cur, tenant):
            if now is not None:
                if not self._allow_test_time:
                    raise ValueError(
                        "shared scheduler lease time is database-authoritative"
                    )
                timestamp = _timestamp(now, "lease timestamp")
            else:
                cur.execute("SELECT EXTRACT(EPOCH FROM clock_timestamp())")
                clock = cur.fetchone()
                if clock is None:
                    raise PostgresHuntStoreUnavailable(
                        "shared scheduler lease database time is unavailable"
                    )
                timestamp = _timestamp(clock[0], "lease timestamp")
            expires = _timestamp(timestamp + duration, "lease expiry")
            cur.execute(
                f"SELECT revision, expires_at FROM {_TABLE} WHERE tenant_id=%s "
                "AND hunter_kind=%s AND record_kind=%s AND record_key=%s FOR UPDATE",
                (tenant, self._hunter_kind, _KIND_LEASE, lease_name),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    f"INSERT INTO {_TABLE} "
                    "(tenant_id, hunter_kind, record_kind, record_key, revision, status, "
                    "payload_text, payload_sha256, prev_hash, created_at, updated_at, "
                    "delivered_at, expires_at) "
                    "VALUES (%s,%s,%s,%s,1,'active',%s,%s,'',%s,%s,-1,%s) "
                    "ON CONFLICT DO NOTHING",
                    (
                        tenant,
                        self._hunter_kind,
                        _KIND_LEASE,
                        lease_name,
                        stored,
                        digest,
                        timestamp,
                        timestamp,
                        expires,
                    ),
                )
                return cur.rowcount == 1
            if float(row[1]) > timestamp:
                return False
            cur.execute(
                f"UPDATE {_TABLE} SET revision=%s, payload_text=%s, "
                "payload_sha256=%s, updated_at=%s, expires_at=%s "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s AND revision=%s AND expires_at<=%s",
                (
                    int(row[0]) + 1,
                    stored,
                    digest,
                    timestamp,
                    expires,
                    tenant,
                    self._hunter_kind,
                    _KIND_LEASE,
                    lease_name,
                    int(row[0]),
                    timestamp,
                ),
            )
            return cur.rowcount == 1

    def _record_audit(self, kind: str, payload: dict[str, Any]) -> bool:
        if self._audit_recorder is not None:
            try:
                return bool(self._audit_recorder(kind, payload))
            except Exception:
                return False
        try:
            from ..audit import record

            return bool(record(kind, **payload))
        except Exception:
            return False

    def _undelivered_event(
        self,
        event_id: str,
    ) -> tuple[str, str, dict[str, Any], float] | None:
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT payload_text, payload_sha256, delivered_at FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND record_key=%s",
                (tenant, self._hunter_kind, _KIND_OUTBOX, event_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            decoded = _decode_payload(row[0], row[1])
            payload = decoded.get("payload")
            if not isinstance(payload, dict):
                raise PostgresHuntStoreUnavailable("shared hunter outbox is invalid")
            return tenant, str(decoded.get("event_kind", "")), payload, float(row[2])

    def _deliver_audit_outbox_event(
        self,
        event_id: str,
        *,
        _expected_tenant: str | None = None,
    ) -> bool:
        event = self._undelivered_event(_bounded_text(event_id, "event id"))
        if event is None:
            return False
        bound_tenant, event_kind, payload, delivered_at = event
        if _expected_tenant is not None and not hmac.compare_digest(
            bound_tenant,
            _expected_tenant,
        ):
            raise PostgresHuntStoreUnavailable(
                "shared hunter tenant changed before audit delivery"
            )
        if delivered_at >= 0:
            return True
        if not self._record_audit(event_kind, payload):
            return False
        self._assert_tenant(bound_tenant)
        timestamp = _timestamp(time.time(), "audit delivery timestamp")
        with self._transaction() as (cur, tenant):
            if not hmac.compare_digest(tenant, bound_tenant):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter tenant changed during audit delivery"
                )
            cur.execute(
                f"UPDATE {_TABLE} SET status='delivered', delivered_at=%s, "
                "updated_at=%s WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind=%s AND record_key=%s AND delivered_at=-1",
                (
                    timestamp,
                    timestamp,
                    tenant,
                    self._hunter_kind,
                    _KIND_OUTBOX,
                    event_id,
                ),
            )
            if cur.rowcount == 1:
                return True
        event = self._undelivered_event(event_id)
        if event is None:
            return False
        if not hmac.compare_digest(event[0], bound_tenant):
            raise PostgresHuntStoreUnavailable(
                "shared hunter tenant changed after audit delivery"
            )
        return event[3] >= 0

    def ensure_audit_custody_witness(self) -> bool:
        timestamp = _timestamp(time.time(), "custody timestamp")
        with self._transaction() as (cur, tenant):
            self._queue_event(
                cur,
                tenant,
                _AUDIT_CUSTODY_EVENT_KIND,
                {
                    "event_id": _AUDIT_CUSTODY_EVENT_ID,
                    "version": 1,
                    "purpose": "platform_hunt_audit_custody",
                    "occurred_at": timestamp,
                },
                timestamp=timestamp,
                allow_existing=True,
            )
        return self._deliver_audit_outbox_event(
            _AUDIT_CUSTODY_EVENT_ID,
            _expected_tenant=tenant,
        )

    def flush_audit_outbox(
        self,
        *,
        limit: int = 32,
        _expected_tenant: str | None = None,
    ) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
            raise ValueError("audit flush limit must be between 1 and 256")
        with self._transaction() as (cur, tenant):
            if _expected_tenant is not None and not hmac.compare_digest(
                tenant,
                _expected_tenant,
            ):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter tenant changed before audit flush"
                )
            bound_tenant = tenant
            cur.execute(
                f"SELECT record_key, payload_text, payload_sha256 FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "AND delivered_at=-1 ORDER BY created_at, record_key LIMIT %s",
                (tenant, self._hunter_kind, _KIND_OUTBOX, limit),
            )
            rows = list(cur.fetchall())
        delivered = 0
        for row in rows:
            decoded = _decode_payload(row[1], row[2])
            payload = decoded.get("payload")
            if not isinstance(payload, dict):
                raise PostgresHuntStoreUnavailable("shared hunter outbox is invalid")
            if not self._record_audit(str(decoded.get("event_kind", "")), payload):
                continue
            self._assert_tenant(bound_tenant)
            timestamp = _timestamp(time.time(), "audit delivery timestamp")
            with self._transaction() as (cur, tenant):
                if not hmac.compare_digest(tenant, bound_tenant):
                    raise PostgresHuntStoreUnavailable(
                        "shared hunter tenant changed during audit flush"
                    )
                cur.execute(
                    f"UPDATE {_TABLE} SET status='delivered', delivered_at=%s, "
                    "updated_at=%s WHERE tenant_id=%s AND hunter_kind=%s "
                    "AND record_kind=%s AND record_key=%s AND delivered_at=-1",
                    (
                        timestamp,
                        timestamp,
                        tenant,
                        self._hunter_kind,
                        _KIND_OUTBOX,
                        str(row[0]),
                    ),
                )
                delivered += int(cur.rowcount == 1)
        return delivered

    def pending_audit_count(self) -> int:
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT COUNT(*) FROM {_TABLE} WHERE tenant_id=%s "
                "AND hunter_kind=%s AND record_kind=%s AND delivered_at=-1",
                (tenant, self._hunter_kind, _KIND_OUTBOX),
            )
            row = cur.fetchone()
            if row is None:
                raise PostgresHuntStoreUnavailable("shared hunter outbox count failed")
            return int(row[0])

    def verify_journal(self) -> list[dict[str, Any]]:
        breaks: list[dict[str, Any]] = []
        previous = ""
        latest: dict[tuple[str, str], tuple[int, str]] = {}
        with self._transaction() as (cur, tenant):
            cur.execute(
                f"SELECT revision, payload_text, payload_sha256, prev_hash "
                f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind=%s AND record_key='head'",
                (tenant, self._hunter_kind, _KIND_JOURNAL_HEAD),
            )
            head = cur.fetchone()
            cur.execute(
                f"SELECT revision, payload_text, payload_sha256, prev_hash FROM {_TABLE} "
                "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                "ORDER BY revision",
                (tenant, self._hunter_kind, _KIND_JOURNAL),
            )
            rows = list(cur.fetchall())
            expected_sequence = 1
            for row in rows:
                sequence = int(row[0])
                try:
                    body = _decode_payload(row[1])
                except PostgresHuntStoreUnavailable:
                    breaks.append({"seq": sequence, "reason": "bad_hash"})
                    previous = str(row[2])
                    expected_sequence = sequence + 1
                    continue
                expected = canonical_digest(body)
                if sequence != expected_sequence or str(row[3]) != previous:
                    breaks.append({"seq": sequence, "reason": "chain_mismatch"})
                if not hmac.compare_digest(str(row[2]), expected):
                    breaks.append({"seq": sequence, "reason": "bad_hash"})
                previous = str(row[2])
                expected_sequence = sequence + 1
                record_type = str(body.get("record_type", ""))
                record_id = str(body.get("record_id", ""))
                try:
                    revision = int(body.get("revision", 0))
                except (TypeError, ValueError, OverflowError):
                    revision = 0
                latest[(record_type, record_id)] = (
                    revision,
                    str(body.get("payload_sha256", "")),
                )
            if head is None:
                if rows:
                    breaks.append({"reason": "head_mismatch"})
            else:
                try:
                    head_payload = _decode_payload(head[1], head[2])
                    head_hash = str(head_payload.get("entry_hash", ""))
                except PostgresHuntStoreUnavailable:
                    head_hash = ""
                if (
                    int(head[0]) != len(rows)
                    or not hmac.compare_digest(str(head[3]), head_hash)
                    or not hmac.compare_digest(previous, head_hash)
                ):
                    breaks.append({"reason": "head_mismatch"})
            for (record_type, record_id), (revision, digest) in latest.items():
                if record_type not in {_KIND_FINDING, _KIND_INVESTIGATION}:
                    breaks.append({
                        "record_id": record_id,
                        "reason": "unknown_record_type",
                    })
                    continue
                cur.execute(
                    f"SELECT revision, payload_sha256, payload_text FROM {_TABLE} "
                    "WHERE tenant_id=%s AND hunter_kind=%s AND record_kind=%s "
                    "AND record_key=%s",
                    (tenant, self._hunter_kind, record_type, record_id),
                )
                record = cur.fetchone()
                mismatch = record is None
                if record is not None:
                    mismatch = (
                        int(record[0]) != revision
                        or not hmac.compare_digest(str(record[1]), digest)
                    )
                    if not mismatch:
                        try:
                            _decode_payload(record[2], record[1])
                        except PostgresHuntStoreUnavailable:
                            mismatch = True
                if mismatch:
                    breaks.append({"record_id": record_id, "reason": "record_mismatch"})
            cur.execute(
                f"SELECT record_kind, record_key, revision, payload_sha256, payload_text "
                f"FROM {_TABLE} WHERE tenant_id=%s AND hunter_kind=%s "
                "AND record_kind IN (%s,%s)",
                (
                    tenant,
                    self._hunter_kind,
                    _KIND_FINDING,
                    _KIND_INVESTIGATION,
                ),
            )
            for row in cur.fetchall():
                identity = (str(row[0]), str(row[1]))
                if identity in latest:
                    continue
                try:
                    _decode_payload(row[4], row[3])
                except PostgresHuntStoreUnavailable:
                    pass
                breaks.append({"record_id": str(row[1]), "reason": "record_mismatch"})
        return breaks


_SELECTION_LOCK = threading.Lock()
_BOUND_AUTHORITY_TOKEN: _AuthorityToken | None = None


def reset_shared_hunt_store_for_tests() -> None:
    global _BOUND_AUTHORITY_TOKEN

    with _SELECTION_LOCK:
        _BOUND_AUTHORITY_TOKEN = None


def selected_shared_hunt_store(
    path: str | Path,
    audit_recorder: Callable[[str, dict[str, Any]], bool] | None,
    *,
    record_change_event_kind: str,
) -> PostgresHuntStore | None:
    selection = _selection()
    token = _authority_token(selection) if selection.postgres else None
    global _BOUND_AUTHORITY_TOKEN

    with _SELECTION_LOCK:
        if _BOUND_AUTHORITY_TOKEN is not None:
            if token is None or not _authority_matches(
                _BOUND_AUTHORITY_TOKEN,
                selection,
            ):
                raise PostgresHuntStoreUnavailable(
                    "shared hunter deployment authority changed after process binding"
                )
        elif token is None:
            return None
        if token is None:  # pragma: no cover - narrowed by binding checks above
            raise PostgresHuntStoreUnavailable(
                "shared hunter deployment authority binding is unavailable"
            )
        store = PostgresHuntStore(
            path,
            audit_recorder=audit_recorder,
            record_change_event_kind=record_change_event_kind,
            dsn=selection.dsn,
            _authority_token=token,
        )
        if _BOUND_AUTHORITY_TOKEN is None:
            _BOUND_AUTHORITY_TOKEN = token
        return store


__all__ = [
    "HuntLegacyMigrationRequired",
    "PostgresHuntStore",
    "PostgresHuntStoreUnavailable",
    "assert_no_legacy_hunt_state",
    "reset_shared_hunt_store_for_tests",
    "selected_shared_hunt_store",
]
