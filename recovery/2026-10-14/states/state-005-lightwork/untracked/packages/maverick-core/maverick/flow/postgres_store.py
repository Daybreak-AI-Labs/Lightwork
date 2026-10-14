"""Transactionally shared Postgres authority for flow control-plane state.

The public API remains in :mod:`maverick.flow.store`.  That module delegates to
this backend only when the deployment-global world backend is Postgres.  A
single tenant-bound record table keeps definitions, immutable snapshots,
publication epochs, run rows, and idempotency claims in one transaction domain.

The filesystem implementation is intentionally retained for explicit
single-replica community compatibility.  Selecting Postgres while legacy local
state exists is a hard cutover error; silently starting with an empty shared
authority would make existing flows and queued work disappear.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_TABLE = "flow_control_records"
_POLICY = "mvk_flow_tenant_isolation"
_SCHEMA_LOCK = 0x6D766B666C6F77  # ``mvkflow``
_UNBOUND_TENANT = "_" * 201

_KIND_DEFINITION = "flow_definition"
_KIND_VERSION = "flow_version"
_KIND_SCHEMA = "flow_schema"
_KIND_OBJECT = "flow_object"
_KIND_PUBLICATION = "flow_publication"
_KIND_RUN = "flow_run"
_KIND_CLAIM = "flow_idempotency"
_KINDS = (
    _KIND_DEFINITION,
    _KIND_VERSION,
    _KIND_SCHEMA,
    _KIND_OBJECT,
    _KIND_PUBLICATION,
    _KIND_RUN,
    _KIND_CLAIM,
)

_MAX_LOGICAL_PAYLOAD_BYTES = 4 * 1024 * 1024
_MAX_STORED_PAYLOAD_BYTES = 8 * 1024 * 1024
_MAX_IDENTIFIER_BYTES = 500
_MAX_OWNER_BYTES = 2_000
_MAX_STATUS_BYTES = 80
_MAX_LEGACY_ENTRIES = 100_000
_MAX_LEGACY_DIRECTORIES = 10_000

_COLUMNS = {
    "tenant_id": ("text", "NO"),
    "record_kind": ("text", "NO"),
    "record_key": ("text", "NO"),
    "revision": ("bigint", "NO"),
    "payload_text": ("text", "NO"),
    "flow_key": ("text", "NO"),
    "owner_key": ("text", "NO"),
    "status": ("text", "NO"),
    "created_at": ("double precision", "NO"),
    "updated_at": ("double precision", "NO"),
}
_PRIMARY_KEY = ("tenant_id", "record_kind", "record_key")
_INDEXES = {
    "idx_flow_control_kind_updated": (
        "tenant_id",
        "record_kind",
        "updated_at",
        "record_key",
    ),
    "idx_flow_control_run_lookup": (
        "tenant_id",
        "record_kind",
        "flow_key",
        "owner_key",
        "updated_at",
        "record_key",
    ),
}
_CHECK_NAMES = frozenset({
    "mvk_flow_tenant_bounds",
    "mvk_flow_kind_bounds",
    "mvk_flow_record_key_format",
    "mvk_flow_revision_bounds",
    "mvk_flow_payload_sealed_bound",
    "mvk_flow_flow_key_format",
    "mvk_flow_owner_key_format",
    "mvk_flow_status_bound",
    "mvk_flow_timestamp_bound",
})

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    tenant_id TEXT NOT NULL,
    record_kind TEXT NOT NULL,
    record_key TEXT NOT NULL,
    revision BIGINT NOT NULL,
    payload_text TEXT NOT NULL,
    flow_key TEXT NOT NULL DEFAULT '',
    owner_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, record_kind, record_key),
    CONSTRAINT mvk_flow_tenant_bounds
        CHECK (char_length(tenant_id) BETWEEN 1 AND 201),
    CONSTRAINT mvk_flow_kind_bounds
        CHECK (record_kind IN ({', '.join(repr(kind) for kind in _KINDS)})),
    CONSTRAINT mvk_flow_record_key_format
        CHECK (record_key ~ '^[0-9a-f]{{64}}(:v[1-9][0-9]{{0,18}})?$'),
    CONSTRAINT mvk_flow_revision_bounds
        CHECK (revision BETWEEN 1 AND 9223372036854775807),
    CONSTRAINT mvk_flow_payload_sealed_bound
        CHECK (octet_length(payload_text) <= {_MAX_STORED_PAYLOAD_BYTES}
               AND payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={{0,2}}$'),
    CONSTRAINT mvk_flow_flow_key_format
        CHECK (flow_key = '' OR flow_key ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT mvk_flow_owner_key_format
        CHECK (owner_key = '' OR owner_key ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT mvk_flow_status_bound
        CHECK (octet_length(status) <= {_MAX_STATUS_BYTES}),
    CONSTRAINT mvk_flow_timestamp_bound
        CHECK (created_at BETWEEN 0 AND 32503680000
               AND updated_at BETWEEN 0 AND 32503680000)
)
"""


class PostgresFlowStoreUnavailable(RuntimeError):
    """The shared flow authority cannot safely serve the operation."""


class FlowLegacyMigrationRequired(PostgresFlowStoreUnavailable):
    """Node-local flow state must be explicitly imported before cutover."""


class _SchemaIncompatible(RuntimeError):
    pass


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("shared flow JSON contains a duplicate key")
        value[key] = item
    return value


def _charge_tree(value: Any, remaining: list[int], *, depth: int = 0) -> None:
    if depth > 40:
        raise ValueError("shared flow JSON nesting is too deep")
    remaining[0] -= 8
    if remaining[0] < 0:
        raise ValueError("shared flow JSON exceeds its storage limit")
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
        raise ValueError("shared flow JSON exceeds its storage limit")


def _canonical_json(value: Mapping[str, Any]) -> str:
    _charge_tree(value, [_MAX_LOGICAL_PAYLOAD_BYTES])
    raw = json.dumps(
        dict(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(raw.encode("utf-8")) > _MAX_LOGICAL_PAYLOAD_BYTES:
        raise ValueError("shared flow JSON exceeds its storage limit")
    return raw


def _encode_payload(value: Mapping[str, Any]) -> str:
    raw = _canonical_json(value)
    try:
        from ..crypto_at_rest import at_rest_enabled, is_sealed_str, seal_to_str

        if not at_rest_enabled():
            raise PostgresFlowStoreUnavailable(
                "shared flow authority requires application at-rest encryption"
            )
        stored = seal_to_str(raw)
        if not is_sealed_str(stored):
            raise PostgresFlowStoreUnavailable(
                "shared flow authority could not seal its payload"
            )
    except PostgresFlowStoreUnavailable:
        raise
    except Exception:
        raise PostgresFlowStoreUnavailable(
            "shared flow authority encryption is unavailable"
        ) from None
    if len(stored.encode("utf-8")) > _MAX_STORED_PAYLOAD_BYTES:
        raise ValueError("sealed shared flow JSON exceeds its storage limit")
    return stored


def _decode_payload(stored: object) -> dict[str, Any]:
    if not isinstance(stored, str):
        raise PostgresFlowStoreUnavailable("shared flow payload is not text")
    if len(stored.encode("utf-8")) > _MAX_STORED_PAYLOAD_BYTES:
        raise PostgresFlowStoreUnavailable("shared flow payload exceeds its bound")
    try:
        from ..crypto_at_rest import (
            at_rest_enabled,
            is_sealed_str,
            unseal_from_str,
        )

        if not at_rest_enabled() or not is_sealed_str(stored):
            raise PostgresFlowStoreUnavailable(
                "unsealed shared flow payload was withheld"
            )
        raw = unseal_from_str(stored)
    except PostgresFlowStoreUnavailable:
        raise
    except Exception:
        raise PostgresFlowStoreUnavailable(
            "shared flow payload could not be decrypted"
        ) from None
    if len(raw.encode("utf-8")) > _MAX_LOGICAL_PAYLOAD_BYTES:
        raise PostgresFlowStoreUnavailable("shared flow JSON exceeds its bound")
    try:
        value = json.loads(raw, object_pairs_hook=_json_object)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise PostgresFlowStoreUnavailable("shared flow JSON is invalid") from None
    if not isinstance(value, dict):
        raise PostgresFlowStoreUnavailable("shared flow JSON is not an object")
    return value


def _probe_encryption() -> None:
    marker = f"maverick-flow-probe:{secrets.token_hex(16)}"
    stored = _encode_payload({"probe": marker})
    decoded = _decode_payload(stored)
    if decoded != {"probe": marker}:
        raise PostgresFlowStoreUnavailable(
            "shared flow authority encryption probe failed"
        )


def _validate_identifier(value: object, label: str) -> str:
    text = str(value or "")
    size = len(text.encode("utf-8"))
    if not text or size > _MAX_IDENTIFIER_BYTES or "\x00" in text:
        raise ValueError(f"{label} is invalid or exceeds {_MAX_IDENTIFIER_BYTES} bytes")
    return text


def _validate_owner(value: object) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) > _MAX_OWNER_BYTES or "\x00" in text:
        raise ValueError(f"flow owner exceeds {_MAX_OWNER_BYTES} bytes")
    return text


def _validate_status(value: object) -> str:
    text = str(value or "")
    if len(text.encode("utf-8")) > _MAX_STATUS_BYTES or "\x00" in text:
        raise ValueError(f"flow run status exceeds {_MAX_STATUS_BYTES} bytes")
    return text


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _flow_key(flow_id: object) -> str:
    return _digest(_validate_identifier(flow_id, "flow id"))


def _run_key(run_id: object) -> str:
    return _digest(_validate_identifier(run_id, "flow run id"))


def _owner_key(owner: object) -> str:
    return _digest(_validate_owner(owner))


def _version_key(flow_key: str, version: int) -> str:
    parsed = int(version)
    if parsed < 1 or parsed > 9_223_372_036_854_775_807:
        raise ValueError("flow definition version is out of range")
    return f"{flow_key}:v{parsed}"


def _lock_id(*parts: str) -> int:
    raw = "\0".join(parts).encode("utf-8")
    value = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    return value - (1 << 64) if value >= (1 << 63) else value


def _pg_array(value: object) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(str(item) for item in value)
    return tuple(
        item.strip().strip("\"'")
        for item in str(value or "").strip("{}").split(",")
        if item.strip()
    )


def _normalized_sql(value: object) -> str:
    return " ".join(
        str(value or "")
        .lower()
        .replace('"', "")
        .replace("(", " ")
        .replace(")", " ")
        .split()
    )


def _canonical_check(value: object) -> str:
    text = str(value or "").strip().lower().replace('"', "")
    text = re.sub(
        r"::(?:text|bigint|integer|double\s+precision)",
        "",
        text,
    )
    text = re.sub(r"'([0-9]+)'", r"\1", text)
    text = "".join(text.split())
    text = text.removeprefix("check")
    return text.replace("(", "").replace(")", "")


def _expected_check_expressions() -> dict[str, frozenset[str]]:
    kinds = ",".join(f"'{kind}'" for kind in _KINDS)

    def variants(*values: str) -> frozenset[str]:
        return frozenset(_canonical_check(value) for value in values)

    return {
        "mvk_flow_tenant_bounds": variants(
            "char_length(tenant_id) BETWEEN 1 AND 201",
            "char_length(tenant_id) >= 1 AND char_length(tenant_id) <= 201",
        ),
        "mvk_flow_kind_bounds": variants(
            f"record_kind IN ({kinds})",
            f"record_kind = ANY (ARRAY[{kinds}])",
        ),
        "mvk_flow_record_key_format": variants(
            r"record_key ~ '^[0-9a-f]{64}(:v[1-9][0-9]{0,18})?$'",
        ),
        "mvk_flow_revision_bounds": variants(
            "revision BETWEEN 1 AND 9223372036854775807",
            "revision >= 1 AND revision <= 9223372036854775807",
        ),
        "mvk_flow_payload_sealed_bound": variants(
            "octet_length(payload_text) <= 8388608 AND "
            r"payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={0,2}$'",
        ),
        "mvk_flow_flow_key_format": variants(
            r"flow_key = '' OR flow_key ~ '^[0-9a-f]{64}$'",
        ),
        "mvk_flow_owner_key_format": variants(
            r"owner_key = '' OR owner_key ~ '^[0-9a-f]{64}$'",
        ),
        "mvk_flow_status_bound": variants(
            "octet_length(status) <= 80",
        ),
        "mvk_flow_timestamp_bound": variants(
            "created_at BETWEEN 0 AND 32503680000 AND "
            "updated_at BETWEEN 0 AND 32503680000",
            "created_at >= 0 AND created_at <= 32503680000 AND "
            "updated_at >= 0 AND updated_at <= 32503680000",
        ),
    }


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
        start = lowered.rfind("(")
        end = lowered.rfind(")")
        if start < 0 or end <= start:
            return False
        actual = "".join(lowered[start + 1:end].split())
        expected = ",".join(
            f"{column}desc" if column == "updated_at" else column
            for column in columns
        )
        if actual != expected:
            return False
    return True


def _policy_rows_are_exact(rows: object) -> bool:
    if not isinstance(rows, (tuple, list)) or len(rows) != 1:
        return False
    row = rows[0]
    if not isinstance(row, (tuple, list)) or len(row) != 6:
        return False
    name, permissive, roles, command, qual, with_check = row
    if str(name) != _POLICY:
        return False
    role_names = [name.lower() for name in _pg_array(roles)]
    if (
        str(permissive).strip().lower() != "permissive"
        or role_names != ["public"]
        or str(command).strip().lower() != "all"
    ):
        return False

    expected = "tenant_id=nullif(current_setting('maverick.tenant',true),'')"

    def exact(expression: object) -> bool:
        compact = "".join(str(expression or "").lower().split())
        compact = compact.replace("::text", "")
        return compact in {expected, f"({expected})"}

    return exact(qual) and exact(with_check)


def _unsafe_alias(path: Path, info: os.stat_result) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & 0x400)
        or bool(callable(is_junction) and is_junction())
    )


def _legacy_flow_roots(home: Path) -> list[Path]:
    roots = [home / "flows"]
    tenants = home / "tenants"
    try:
        info = tenants.lstat()
    except FileNotFoundError:
        return roots
    except OSError as exc:
        raise FlowLegacyMigrationRequired(
            "legacy flow tenant inventory cannot be inspected"
        ) from exc
    if _unsafe_alias(tenants, info) or not stat.S_ISDIR(info.st_mode):
        raise FlowLegacyMigrationRequired(
            "legacy flow tenant inventory is not a stable directory"
        )
    try:
        with os.scandir(tenants) as entries:
            for index, entry in enumerate(entries, 1):
                if index > _MAX_LEGACY_DIRECTORIES:
                    raise FlowLegacyMigrationRequired(
                        "legacy flow tenant inventory exceeds its scan bound"
                    )
                try:
                    entry_info = entry.stat(follow_symlinks=False)
                except OSError as exc:
                    raise FlowLegacyMigrationRequired(
                        "legacy flow tenant inventory cannot be inspected"
                    ) from exc
                path = Path(entry.path)
                if _unsafe_alias(path, entry_info):
                    raise FlowLegacyMigrationRequired(
                        "legacy flow tenant inventory contains an alias"
                    )
                if stat.S_ISDIR(entry_info.st_mode):
                    roots.append(path / "flows")
    except FlowLegacyMigrationRequired:
        raise
    except OSError as exc:
        raise FlowLegacyMigrationRequired(
            "legacy flow tenant inventory cannot be inspected"
        ) from exc
    return roots


def _legacy_flow_root_has_state(root: Path) -> bool:
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise FlowLegacyMigrationRequired(
            "legacy flow state cannot be inspected"
        ) from exc
    if _unsafe_alias(root, root_info) or not stat.S_ISDIR(root_info.st_mode):
        raise FlowLegacyMigrationRequired(
            "legacy flow state is not a stable directory"
        )
    pending = [root]
    files_seen = 0
    directories_seen = 0
    while pending:
        directory = pending.pop()
        directories_seen += 1
        if directories_seen > _MAX_LEGACY_DIRECTORIES:
            raise FlowLegacyMigrationRequired(
                "legacy flow state exceeds its directory scan bound"
            )
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        raise FlowLegacyMigrationRequired(
                            "legacy flow state cannot be inspected"
                        ) from exc
                    if _unsafe_alias(path, info):
                        raise FlowLegacyMigrationRequired(
                            "legacy flow state contains an alias"
                        )
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path)
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise FlowLegacyMigrationRequired(
                            "legacy flow state contains an unsupported entry"
                        )
                    files_seen += 1
                    if files_seen > _MAX_LEGACY_ENTRIES:
                        raise FlowLegacyMigrationRequired(
                            "legacy flow state exceeds its file scan bound"
                        )
                    # The only non-authority files created by the old store are
                    # the stable lock targets. Any other file, including an
                    # unreadable/empty JSON claim, is state that needs import.
                    name = entry.name
                    if name == ".definitions" or (
                        name.startswith(".") and name.endswith(".activation")
                    ):
                        continue
                    return True
        except FlowLegacyMigrationRequired:
            raise
        except OSError as exc:
            raise FlowLegacyMigrationRequired(
                "legacy flow state cannot be inspected"
            ) from exc
    return False


def assert_no_legacy_flow_state(home_override: Path | None = None) -> None:
    if home_override is None:
        from ..paths import maverick_home

        home = maverick_home()
    else:
        home = Path(home_override)
    for root in _legacy_flow_roots(home):
        if _legacy_flow_root_has_state(root):
            raise FlowLegacyMigrationRequired(
                "legacy filesystem flow state requires an explicit Postgres "
                "import before shared authority can serve"
            )


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
        raise PostgresFlowStoreUnavailable(
            "shared flow deployment authority is not Postgres"
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
            raise PostgresFlowStoreUnavailable(
                "shared flow authority transaction was marked for rollback"
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
            raise PostgresFlowStoreUnavailable(
                "shared flow authority transaction was marked for rollback"
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def _replica_count() -> int:
    raw: object = os.environ.get("MAVERICK_REPLICA_COUNT", "1")
    if isinstance(raw, bool):
        raise PostgresFlowStoreUnavailable("replica count must be a positive integer")
    try:
        count = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        raise PostgresFlowStoreUnavailable(
            "replica count must be a positive integer"
        ) from None
    if count < 1 or str(raw).strip() != str(count):
        raise PostgresFlowStoreUnavailable("replica count must be a positive integer")
    return count


def _selection() -> _Selection:
    try:
        from ..config import config_source_errors, load_global_config

        config = load_global_config()
        source_errors = config_source_errors(include_tenant=False)
    except Exception:
        raise PostgresFlowStoreUnavailable(
            "flow authority deployment policy is unavailable"
        ) from None
    if not isinstance(config, Mapping):
        raise PostgresFlowStoreUnavailable("flow authority policy is invalid")
    world = config.get("world_model") or {}
    if not isinstance(world, Mapping):
        raise PostgresFlowStoreUnavailable("world-model policy is invalid")
    backend = str(
        os.environ.get("MAVERICK_WORLD_BACKEND")
        or world.get("backend")
        or "sqlite"
    ).strip().lower()
    if backend not in {"sqlite", "postgres"}:
        raise PostgresFlowStoreUnavailable("world-model backend is invalid")
    try:
        from ..enterprise import deployment_enterprise_enabled

        enterprise = deployment_enterprise_enabled(
            config=config,
            source_errors=source_errors,
        )
    except Exception:
        enterprise = True
    replicas = _replica_count()
    shared_required = bool(enterprise or replicas > 1 or source_errors)
    if backend != "postgres":
        if shared_required:
            raise PostgresFlowStoreUnavailable(
                "enterprise and multi-replica flow authority requires Postgres; "
                "local fallback is disabled"
            )
        return _Selection(False, False)
    dsn = str(os.environ.get("MAVERICK_PG_DSN") or world.get("dsn") or "").strip()
    if not dsn:
        raise PostgresFlowStoreUnavailable(
            "Postgres flow authority is selected but no DSN is configured"
        )
    return _Selection(True, shared_required, dsn)


@dataclass
class _TransactionState:
    tenant: str
    cursor: Any
    run_revisions: dict[str, int] = field(default_factory=dict)


class PostgresFlowStore:
    """One tenant-scoped, transactionally shared flow authority."""

    def __init__(
        self,
        *,
        dsn: str = "",
        transaction_factory: Callable[[], Any] | None = None,
        session_connection_factory: Callable[[], Any] | None = None,
        connect: Callable[..., Any] | None = None,
        require_bound_tenant: bool = False,
        legacy_home_override: Path | None = None,
        check_legacy: bool = True,
        ensure_schema: bool = True,
        _authority_token: _AuthorityToken | None = None,
    ) -> None:
        # Only ``selected_shared_flow_store`` supplies this token. Directly
        # injected transaction factories remain useful for offline tests and
        # do not unexpectedly consult deployment configuration.
        self._authority_token = _authority_token
        self._require_bound_tenant = bool(require_bound_tenant)
        self._legacy_home_override = legacy_home_override
        self._check_legacy = bool(check_legacy)
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._world: Any | None = None
        self._tx_state: contextvars.ContextVar[_TransactionState | None] = (
            contextvars.ContextVar(
                f"maverick_flow_pg_tx_{id(self)}",
                default=None,
            )
        )
        self._session_tenant: contextvars.ContextVar[str | None] = (
            contextvars.ContextVar(
                f"maverick_flow_pg_session_tenant_{id(self)}",
                default=None,
            )
        )
        self._held_session_locks: contextvars.ContextVar[frozenset[int]] = (
            contextvars.ContextVar(
                f"maverick_flow_pg_session_locks_{id(self)}",
                default=frozenset(),
            )
        )
        self._held_run_revisions: contextvars.ContextVar[dict[str, int] | None] = (
            contextvars.ContextVar(
                f"maverick_flow_pg_run_revisions_{id(self)}",
                default=None,
            )
        )
        if self._check_legacy:
            assert_no_legacy_flow_state(self._legacy_home_override)
        _probe_encryption()
        if transaction_factory is None:
            if not dsn:
                raise PostgresFlowStoreUnavailable(
                    "Postgres flow authority has no configured connection"
                )
            if connect is None:
                try:
                    import psycopg

                    connect = psycopg.connect
                except ImportError:
                    raise PostgresFlowStoreUnavailable(
                        "Postgres flow authority requires maverick-core[postgres]"
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
        if session_connection_factory is None and connect is not None and dsn:

            def direct_session_connection():
                return connect(dsn, autocommit=True)

            session_connection_factory = direct_session_connection
        if transaction_factory is None:  # pragma: no cover - guarded above
            try:
                raise RuntimeError("transaction factory was not initialized")
            except Exception as exc:
                log.error(
                    "Postgres flow authority connection failed (%s)",
                    type(exc).__name__,
                )
                raise PostgresFlowStoreUnavailable(
                    "Postgres flow authority is unavailable"
                ) from None
        self._transaction_factory = transaction_factory
        self._session_connection_factory = session_connection_factory
        if ensure_schema:
            self._ensure_schema()

    def close(self) -> None:
        if self._world is not None:
            try:
                self._world.close()
            except Exception:
                pass

    @contextlib.contextmanager
    def _operation_errors(self, action: str):
        from .store import FlowSnapshotError, FlowVersionConflict

        try:
            yield
        except (
            FlowLegacyMigrationRequired,
            FlowSnapshotError,
            FlowVersionConflict,
            PostgresFlowStoreUnavailable,
            ValueError,
        ):
            raise
        except Exception as exc:
            log.error(
                "Postgres flow authority %s failed (%s)",
                action,
                type(exc).__name__,
            )
            raise PostgresFlowStoreUnavailable(
                f"Postgres flow authority {action} is unavailable"
            ) from None

    def _assert_authority_current(self) -> None:
        token = self._authority_token
        if token is None:
            return
        try:
            selection = _selection()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise PostgresFlowStoreUnavailable(
                "shared flow deployment authority cannot be revalidated"
            ) from None
        if not _authority_matches(token, selection):
            raise PostgresFlowStoreUnavailable(
                "shared flow deployment authority changed after backend construction"
            )

    def _assert_transaction_binding(self, tenant: str) -> None:
        if self._authority_token is None:
            return
        self._assert_authority_current()
        if not hmac.compare_digest(self._tenant_key(), tenant):
            raise PostgresFlowStoreUnavailable(
                "flow tenant changed during a shared authority transaction"
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
        """Return ``missing``, ``ready``, or ``incompatible`` without DDL."""
        cur.execute(
            "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        role = cur.fetchone()
        if not role or bool(role[0]) or bool(role[1]):
            return "incompatible"
        cur.execute(f"SELECT to_regclass(current_schema() || '.{_TABLE}')")
        table = cur.fetchone()
        if not table or table[0] is None:
            return "missing"
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=%s "
            "ORDER BY ordinal_position",
            (_TABLE,),
        )
        columns = {
            str(row[0]): (str(row[1]), str(row[2]))
            for row in cur.fetchall()
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
        checks = {str(row[0]): _canonical_check(row[1]) for row in cur.fetchall()}
        if set(checks) != _CHECK_NAMES:
            return "missing" if set(checks) < _CHECK_NAMES else "incompatible"
        expected_checks = _expected_check_expressions()
        if any(
            checks[name] not in expected_checks[name]
            for name in _CHECK_NAMES
        ):
            return "incompatible"
        cur.execute(
            "SELECT indexname, indexdef FROM pg_indexes "
            "WHERE schemaname=current_schema() AND tablename=%s",
            (_TABLE,),
        )
        index_rows = list(cur.fetchall())
        index_names = {str(row[0]) for row in index_rows}
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
                raise PostgresFlowStoreUnavailable(
                    "shared flow schema or database role is incompatible"
                )
            if initial == "unavailable":
                raise PostgresFlowStoreUnavailable(
                    "shared flow schema cannot be verified"
                )
            try:
                with self._transaction_factory() as cur:
                    self._assert_authority_current()
                    cur = self._authority_cursor(cur)
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (_SCHEMA_LOCK,),
                    )
                    waited = self._schema_status_on(cur)
                    if waited == "ready":
                        self._schema_ready = True
                        return
                    if waited not in {"missing"}:
                        raise _SchemaIncompatible
                    cur.execute(_CREATE_TABLE_SQL)
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_flow_control_kind_updated "
                        f"ON {_TABLE}(tenant_id, record_kind, updated_at DESC, record_key)"
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS idx_flow_control_run_lookup "
                        f"ON {_TABLE}(tenant_id, record_kind, flow_key, owner_key, "
                        "updated_at DESC, record_key)"
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
                # A least-privilege runtime role may not run DDL. It succeeds
                # when a migration owner provisioned the exact schema while we
                # waited; otherwise no local or permissive fallback is allowed.
                if self._schema_status() != "ready":
                    log.error(
                        "Postgres flow schema unavailable (%s)",
                        type(exc).__name__,
                    )
                    raise PostgresFlowStoreUnavailable(
                        "shared flow schema is unavailable or ambiguous"
                    ) from None
            if self._schema_status() != "ready":
                raise PostgresFlowStoreUnavailable(
                    "shared flow schema is unavailable or ambiguous"
                )
            self._schema_ready = True

    def _tenant_key(self) -> str:
        try:
            from ..paths import canonical_tenant_id, current_tenant_id_strict

            tenant = current_tenant_id_strict()
            if tenant is None:
                if self._require_bound_tenant:
                    raise PostgresFlowStoreUnavailable(
                        "shared flow authority requires an active tenant"
                    )
                return _UNBOUND_TENANT
            return canonical_tenant_id(tenant)
        except PostgresFlowStoreUnavailable:
            raise
        except Exception:
            raise PostgresFlowStoreUnavailable(
                "shared flow tenant binding is unavailable"
            ) from None

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[tuple[Any, _TransactionState]]:
        self._assert_authority_current()
        tenant = self._tenant_key()
        session_tenant = self._session_tenant.get()
        if session_tenant is not None and not hmac.compare_digest(
            session_tenant,
            tenant,
        ):
            raise PostgresFlowStoreUnavailable(
                "flow tenant changed while a shared authority lock was held"
            )
        if self._check_legacy:
            assert_no_legacy_flow_state(self._legacy_home_override)
        try:
            from ..crypto_at_rest import at_rest_enabled

            if not at_rest_enabled():
                raise PostgresFlowStoreUnavailable(
                    "shared flow authority requires application at-rest encryption"
                )
        except PostgresFlowStoreUnavailable:
            raise
        except Exception:
            raise PostgresFlowStoreUnavailable(
                "shared flow authority encryption is unavailable"
            ) from None
        active = self._tx_state.get()
        if active is not None:
            if not hmac.compare_digest(active.tenant, tenant):
                raise PostgresFlowStoreUnavailable(
                    "flow tenant changed during a shared transaction"
                )
            self._assert_transaction_binding(tenant)
            yield active.cursor, active
            return
        self._ensure_schema()
        self._assert_authority_current()
        with self._transaction_factory() as cur:
            self._assert_authority_current()
            # Re-prove the role, PK, bounds, indexes, forced RLS, and sole
            # policy for every authority transaction. Added permissive policies
            # are OR-combined by Postgres and must fail closed immediately.
            if self._schema_status_on(cur) != "ready":
                raise PostgresFlowStoreUnavailable(
                    "shared flow schema or database role is unavailable"
                )
            cur.execute(
                "SELECT set_config('maverick.tenant', %s, true)",
                (tenant,),
            )
            guarded = self._authority_cursor(cur, tenant=tenant)
            state = _TransactionState(tenant=tenant, cursor=guarded)
            token = self._tx_state.set(state)
            try:
                yield guarded, state
                if isinstance(guarded, _AuthorityBoundCursor):
                    guarded.assert_healthy()
                self._assert_transaction_binding(tenant)
            finally:
                self._tx_state.reset(token)

    @contextlib.contextmanager
    def _session_lock(self, *parts: str):
        """Hold a Postgres session advisory lock across committed operations.

        Run execution and queue publication span multiple durable writes. A
        transaction-level lock would keep those writes uncommitted until after
        the external effect/queue boundary, so a crash could roll them back and
        authorize replay. A dedicated autocommit connection holds only the
        advisory mutex; nested row writes use short transactions and commit at
        their normal durability boundaries.
        """
        self._assert_authority_current()
        tenant = self._tenant_key()
        # Session locks must share the exact namespace used by transaction
        # writers.  Omitting the tenant here made a publication/run guard and
        # its competing writer take different advisory locks, so the guard did
        # not actually linearize the durable mutation it was meant to fence.
        lock_id = _lock_id(tenant, *parts)
        held = self._held_session_locks.get()
        if lock_id in held:
            bound = self._session_tenant.get()
            if bound is None or not hmac.compare_digest(bound, tenant):
                raise PostgresFlowStoreUnavailable(
                    "nested shared flow lock has an invalid tenant binding"
                )
            self._assert_transaction_binding(tenant)
            yield
            self._assert_transaction_binding(tenant)
            return
        if self._session_connection_factory is None:
            raise PostgresFlowStoreUnavailable(
                "Postgres session locks are unavailable for flow authority"
            )
        # Verify role/schema/encryption/cutover posture immediately before the
        # lock connection is opened. The normal transaction also independently
        # resolves the tenant and never trusts operation payload fields.
        with self._transaction():
            pass
        self._assert_transaction_binding(tenant)
        connection = None
        cursor = None
        acquired = False
        try:
            connection = self._session_connection_factory()
            cursor = connection.cursor()
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname=current_user"
            )
            role = cursor.fetchone()
            if not role or bool(role[0]) or bool(role[1]):
                raise PostgresFlowStoreUnavailable(
                    "shared flow lock connection has an unsafe database role"
                )
            cursor.execute("SELECT pg_advisory_lock(%s)", (lock_id,))
            acquired = True
            self._assert_transaction_binding(tenant)
            tenant_token = self._session_tenant.set(tenant)
            lock_token = self._held_session_locks.set(held | {lock_id})
            try:
                yield
                self._assert_transaction_binding(tenant)
            finally:
                self._held_session_locks.reset(lock_token)
                self._session_tenant.reset(tenant_token)
        finally:
            if acquired and cursor is not None:
                try:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", (lock_id,))
                except Exception:
                    # Closing the session releases every advisory lock even if
                    # the explicit unlock cannot be confirmed.
                    pass
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

    @staticmethod
    def _advisory_lock(cur: Any, *parts: str) -> None:
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_lock_id(*parts),),
        )

    @staticmethod
    def _select_record(
        cur: Any,
        tenant: str,
        kind: str,
        key: str,
        *,
        for_update: bool = False,
    ) -> Any:
        sql = (
            "SELECT revision, payload_text, flow_key, owner_key, status, "
            f"created_at, updated_at FROM {_TABLE} "
            "WHERE tenant_id=%s AND record_kind=%s AND record_key=%s"
        )
        if for_update:
            sql += " FOR UPDATE"
        cur.execute(sql, (tenant, kind, key))
        return cur.fetchone()

    @staticmethod
    def _insert_record(
        cur: Any,
        tenant: str,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
        *,
        flow_key: str = "",
        owner_key: str = "",
        status: str = "",
        created_at: float | None = None,
        updated_at: float | None = None,
    ) -> bool:
        now = time.time()
        created = now if created_at is None else float(created_at)
        updated = now if updated_at is None else float(updated_at)
        if not all(math.isfinite(value) and 0 <= value <= 32_503_680_000 for value in (
            created,
            updated,
        )):
            raise ValueError("shared flow timestamp is invalid")
        stored = _encode_payload(payload)
        cur.execute(
            f"INSERT INTO {_TABLE}(tenant_id, record_kind, record_key, revision, "
            "payload_text, flow_key, owner_key, status, created_at, updated_at) "
            "VALUES(%s, %s, %s, 1, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, record_kind, record_key) DO NOTHING "
            "RETURNING revision",
            (
                tenant,
                kind,
                key,
                stored,
                flow_key,
                owner_key,
                status,
                created,
                updated,
            ),
        )
        return cur.fetchone() is not None

    @staticmethod
    def _update_record(
        cur: Any,
        tenant: str,
        kind: str,
        key: str,
        expected_revision: int,
        payload: Mapping[str, Any],
        *,
        flow_key: str = "",
        owner_key: str = "",
        status: str = "",
        updated_at: float | None = None,
    ) -> int | None:
        updated = time.time() if updated_at is None else float(updated_at)
        if not math.isfinite(updated) or not 0 <= updated <= 32_503_680_000:
            raise ValueError("shared flow timestamp is invalid")
        stored = _encode_payload(payload)
        cur.execute(
            f"UPDATE {_TABLE} SET revision=revision+1, payload_text=%s, "
            "flow_key=%s, owner_key=%s, status=%s, updated_at=%s "
            "WHERE tenant_id=%s AND record_kind=%s AND record_key=%s "
            "AND revision=%s RETURNING revision",
            (
                stored,
                flow_key,
                owner_key,
                status,
                updated,
                tenant,
                kind,
                key,
                int(expected_revision),
            ),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    @staticmethod
    def _record_payload(row: Any) -> dict[str, Any]:
        if not isinstance(row, (tuple, list)) or len(row) != 7:
            raise PostgresFlowStoreUnavailable("shared flow row has an invalid shape")
        revision = row[0]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise PostgresFlowStoreUnavailable("shared flow row revision is invalid")
        return _decode_payload(row[1])

    @staticmethod
    def _flow_from_row(row: Any, *, expected_id: str | None = None):
        from .ir import Flow
        from .store import FlowSnapshotError

        payload = PostgresFlowStore._record_payload(row)
        try:
            flow = Flow.from_dict(payload)
        except Exception:
            raise FlowSnapshotError("shared flow definition is invalid") from None
        try:
            validated_id = _validate_identifier(flow.id, "flow id")
            validated_owner = _validate_owner(flow.owner)
        except ValueError as exc:
            raise FlowSnapshotError("shared flow identity is invalid") from exc
        if expected_id is not None and validated_id != expected_id:
            raise FlowSnapshotError("shared flow identity does not match its key")
        if str(row[2]) != _flow_key(validated_id):
            raise FlowSnapshotError("shared flow key failed integrity verification")
        if str(row[3]) != _owner_key(validated_owner):
            raise FlowSnapshotError("shared flow owner key failed integrity verification")
        if str(row[4]) != "":
            raise FlowSnapshotError("shared flow definition metadata is invalid")
        return flow

    def _load_flow_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow_id: str,
        *,
        for_update: bool = False,
    ):
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_DEFINITION,
            key,
            for_update=for_update,
        )
        return self._flow_from_row(row, expected_id=validated) if row else None

    def _insert_immutable(
        self,
        cur: Any,
        state: _TransactionState,
        kind: str,
        key: str,
        payload: Mapping[str, Any],
        *,
        flow_key: str = "",
        owner_key: str = "",
        created_at: float | None = None,
    ) -> None:
        if self._insert_record(
            cur,
            state.tenant,
            kind,
            key,
            payload,
            flow_key=flow_key,
            owner_key=owner_key,
            created_at=created_at,
            updated_at=created_at,
        ):
            return
        existing = self._select_record(cur, state.tenant, kind, key)
        if existing is None or self._record_payload(existing) != dict(payload):
            from .store import FlowSnapshotError

            raise FlowSnapshotError("immutable shared flow record collided or changed")

    def _save_flow_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow: Any,
        *,
        expected_version: int | None = None,
        expected_revision: str | None = None,
    ):
        from .store import FlowVersionConflict, _assert_safe_definition, _new_revision

        _assert_safe_definition(flow.to_dict())
        flow_id = _validate_identifier(flow.id, "flow id")
        owner = _validate_owner(flow.owner)
        key = _flow_key(flow_id)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_DEFINITION,
            key,
            for_update=True,
        )
        prior = self._flow_from_row(row, expected_id=flow_id) if row else None
        actual_version = int(prior.version) if prior is not None else 0
        if expected_version is not None and actual_version != int(expected_version):
            raise FlowVersionConflict(
                f"flow {flow_id!r} changed (expected version {expected_version}, "
                f"current {actual_version})"
            )
        actual_generation = str(getattr(prior, "revision", "") or "")
        if (
            expected_revision is not None
            and actual_generation != str(expected_revision)
        ):
            raise FlowVersionConflict(
                f"flow {flow_id!r} was replaced or changed generation"
            )
        if prior is None:
            flow.version = 1
            flow.revision = _new_revision()
        else:
            flow.version = int(prior.version) + 1
            flow.revision = actual_generation or _new_revision()
        payload = flow.to_dict()
        _assert_safe_definition(payload)
        now = time.time()
        if prior is None:
            inserted = self._insert_record(
                cur,
                state.tenant,
                _KIND_DEFINITION,
                key,
                payload,
                flow_key=key,
                owner_key=_owner_key(owner),
                created_at=now,
                updated_at=now,
            )
            if not inserted:
                raise FlowVersionConflict(
                    f"flow {flow_id!r} changed during creation"
                )
            return flow
        self._insert_immutable(
            cur,
            state,
            _KIND_VERSION,
            _version_key(key, int(prior.version)),
            prior.to_dict(),
            flow_key=key,
            owner_key=_owner_key(prior.owner),
            created_at=now,
        )
        updated = self._update_record(
            cur,
            state.tenant,
            _KIND_DEFINITION,
            key,
            int(row[0]),
            payload,
            flow_key=key,
            owner_key=_owner_key(owner),
            updated_at=now,
        )
        if updated is None:
            raise FlowVersionConflict(f"flow {flow_id!r} changed during save")
        return flow

    def save_flow(
        self,
        flow: Any,
        *,
        expected_version: int | None = None,
        expected_revision: str | None = None,
    ):
        from .store import _assert_safe_definition

        _assert_safe_definition(flow.to_dict())
        with self._operation_errors("save definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                return self._save_flow_locked(
                    cur,
                    state,
                    flow,
                    expected_version=expected_version,
                    expected_revision=expected_revision,
                )

    def load_flow(self, flow_id: str):
        with self._operation_errors("load definition"):
            with self._transaction() as (cur, state):
                return self._load_flow_locked(cur, state, flow_id)

    def list_flows(self) -> list[Any]:
        with self._operation_errors("list definitions"):
            with self._transaction() as (cur, state):
                cur.execute(
                    "SELECT revision, payload_text, flow_key, owner_key, status, "
                    f"created_at, updated_at FROM {_TABLE} "
                    "WHERE tenant_id=%s AND record_kind=%s ORDER BY record_key",
                    (state.tenant, _KIND_DEFINITION),
                )
                return [self._flow_from_row(row) for row in cur.fetchall()]

    def list_flow_summaries(self) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for flow in self.list_flows():
            raw = flow.to_dict()
            nodes = [
                node for node in (raw.get("nodes") or []) if isinstance(node, dict)
            ]
            summaries.append({
                "id": str(raw["id"]),
                "name": str(raw.get("name") or ""),
                "nodes": len(nodes),
                "single_agent": (
                    len(nodes) == 1
                    and nodes[0].get("kind") == "agent"
                    and not nodes[0].get("next")
                ),
            })
        return summaries

    def list_versions(self, flow_id: str) -> list[Any]:
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("list definition versions"):
            with self._transaction() as (cur, state):
                cur.execute(
                    "SELECT revision, payload_text, flow_key, owner_key, status, "
                    f"created_at, updated_at FROM {_TABLE} "
                    "WHERE tenant_id=%s AND record_kind=%s AND flow_key=%s "
                    "ORDER BY record_key",
                    (state.tenant, _KIND_VERSION, key),
                )
                out = [
                    self._flow_from_row(row, expected_id=validated)
                    for row in cur.fetchall()
                ]
                current = self._load_flow_locked(cur, state, validated)
                if current is not None:
                    out.append(current)
                versions = [int(item.version) for item in out]
                if len(versions) != len(set(versions)):
                    from .store import FlowSnapshotError

                    raise FlowSnapshotError("shared flow history has duplicate versions")
                out.sort(key=lambda item: int(item.version))
                return out

    def _load_version_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow_id: str,
        version: int,
    ):
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        parsed = int(version)
        if parsed < 1 or parsed > 9_223_372_036_854_775_807:
            return None
        current = self._load_flow_locked(cur, state, validated)
        if current is not None and int(current.version) == parsed:
            return current
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_VERSION,
            _version_key(key, parsed),
        )
        if row is None:
            return None
        flow = self._flow_from_row(row, expected_id=validated)
        if int(flow.version) != parsed:
            from .store import FlowSnapshotError

            raise FlowSnapshotError("shared flow history key failed integrity checks")
        return flow

    def load_version(self, flow_id: str, version: int):
        with self._operation_errors("load definition version"):
            with self._transaction() as (cur, state):
                return self._load_version_locked(cur, state, flow_id, version)

    def rollback_flow(
        self,
        flow_id: str,
        version: int | None = None,
        *,
        expected_version: int | None = None,
        expected_revision: str | None = None,
    ):
        with self._operation_errors("rollback definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                current = self._load_flow_locked(
                    cur,
                    state,
                    flow_id,
                    for_update=True,
                )
                if current is None:
                    return None
                target_version = (
                    int(version) if version is not None else int(current.version) - 1
                )
                if target_version < 1:
                    return None
                target = self._load_version_locked(
                    cur,
                    state,
                    flow_id,
                    target_version,
                )
                if target is None:
                    return None
                return self._save_flow_locked(
                    cur,
                    state,
                    target.copy(),
                    expected_version=expected_version,
                    expected_revision=expected_revision,
                )

    def load_flow_schema(self, flow_id: str) -> dict[str, Any]:
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("load learned schema"):
            with self._transaction() as (cur, state):
                row = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_SCHEMA,
                    key,
                )
                if row is None:
                    return {}
                payload = self._record_payload(row)
                if str(row[2]) != key:
                    raise PostgresFlowStoreUnavailable(
                        "shared learned-schema key failed integrity checks"
                    )
                return payload

    def record_flow_schema(
        self,
        flow_id: str,
        shapes: dict[str, Any],
        *,
        revision: str | None = None,
    ) -> None:
        if not shapes:
            return
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("record learned schema"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                current = self._load_flow_locked(cur, state, validated)
                if (
                    revision is not None
                    and (
                        current is None
                        or str(current.revision) != str(revision)
                    )
                ):
                    return
                row = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_SCHEMA,
                    key,
                    for_update=True,
                )
                prior = self._record_payload(row) if row else {}
                from .schema_infer import merge_shapes

                merged = merge_shapes(prior, shapes)
                _canonical_json(merged)
                if row is None:
                    if not self._insert_record(
                        cur,
                        state.tenant,
                        _KIND_SCHEMA,
                        key,
                        merged,
                        flow_key=key,
                    ):
                        raise PostgresFlowStoreUnavailable(
                            "shared learned-schema creation raced"
                        )
                elif self._update_record(
                    cur,
                    state.tenant,
                    _KIND_SCHEMA,
                    key,
                    int(row[0]),
                    merged,
                    flow_key=key,
                ) is None:
                    raise PostgresFlowStoreUnavailable(
                        "shared learned-schema update raced"
                    )

    def _delete_flow_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow_id: str,
        *,
        expected_version: int | None,
        expected_revision: str | None,
    ) -> bool:
        from .store import FlowVersionConflict

        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_DEFINITION,
            key,
            for_update=True,
        )
        if row is None:
            return False
        current = self._flow_from_row(row, expected_id=validated)
        if expected_version is not None and int(current.version) != int(
            expected_version
        ):
            raise FlowVersionConflict(
                f"flow {validated!r} changed (expected version {expected_version}, "
                f"current {current.version})"
            )
        if (
            expected_revision is not None
            and str(current.revision or "") != str(expected_revision)
        ):
            raise FlowVersionConflict(
                f"flow {validated!r} was replaced or changed generation"
            )
        # Publication authority disappears in the same transaction as the
        # draft/history. Immutable objects and old runs remain as evidence.
        cur.execute(
            f"DELETE FROM {_TABLE} WHERE tenant_id=%s AND record_kind=%s "
            "AND record_key=%s",
            (state.tenant, _KIND_PUBLICATION, key),
        )
        cur.execute(
            f"DELETE FROM {_TABLE} WHERE tenant_id=%s AND record_kind=%s "
            "AND record_key=%s AND revision=%s",
            (state.tenant, _KIND_DEFINITION, key, int(row[0])),
        )
        if cur.rowcount != 1:
            raise FlowVersionConflict(f"flow {validated!r} changed during deletion")
        cur.execute(
            f"DELETE FROM {_TABLE} WHERE tenant_id=%s "
            "AND record_kind IN (%s, %s) AND flow_key=%s",
            (state.tenant, _KIND_VERSION, _KIND_SCHEMA, key),
        )
        return True

    def delete_flow(
        self,
        flow_id: str,
        *,
        expected_version: int | None = None,
        expected_revision: str | None = None,
    ) -> bool:
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("delete definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "activation", key)
                self._advisory_lock(cur, state.tenant, "definitions")
                return self._delete_flow_locked(
                    cur,
                    state,
                    validated,
                    expected_version=expected_version,
                    expected_revision=expected_revision,
                )

    def _store_flow_object_locked(
        self,
        cur: Any,
        state: _TransactionState,
        raw: dict[str, Any],
    ) -> str:
        from .store import FlowSnapshotError, _assert_safe_definition, _canonical_flow_bytes

        _assert_safe_definition(raw)
        digest = hashlib.sha256(_canonical_flow_bytes(raw)).hexdigest()
        try:
            flow_id = _validate_identifier(raw.get("id"), "flow id")
            owner = _validate_owner(raw.get("owner"))
        except ValueError as exc:
            raise FlowSnapshotError("flow snapshot identity is invalid") from exc
        self._insert_immutable(
            cur,
            state,
            _KIND_OBJECT,
            digest,
            raw,
            flow_key=_flow_key(flow_id),
            owner_key=_owner_key(owner),
            created_at=time.time(),
        )
        row = self._select_record(cur, state.tenant, _KIND_OBJECT, digest)
        if row is None:
            raise FlowSnapshotError(f"flow snapshot {digest} is missing after write")
        existing = self._record_payload(row)
        actual = hashlib.sha256(_canonical_flow_bytes(existing)).hexdigest()
        if actual != digest:
            raise FlowSnapshotError(f"flow snapshot {digest} is corrupt")
        return digest

    def store_flow_object(self, raw: dict[str, Any]) -> str:
        with self._operation_errors("store immutable object"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                return self._store_flow_object_locked(cur, state, raw)

    def _load_flow_snapshot_locked(
        self,
        cur: Any,
        state: _TransactionState,
        digest: str,
    ):
        from .ir import Flow
        from .store import FlowSnapshotError, _canonical_flow_bytes

        normalized = str(digest or "").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", normalized):
            raise FlowSnapshotError("invalid flow snapshot digest")
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_OBJECT,
            normalized,
        )
        if row is None:
            raise FlowSnapshotError(f"flow snapshot {normalized} is missing")
        raw = self._record_payload(row)
        actual = hashlib.sha256(_canonical_flow_bytes(raw)).hexdigest()
        if actual != normalized:
            raise FlowSnapshotError(
                f"flow snapshot {normalized} failed integrity verification"
            )
        try:
            flow = Flow.from_dict(raw)
            flow_id = _validate_identifier(flow.id, "flow id")
            owner = _validate_owner(flow.owner)
        except Exception as exc:
            raise FlowSnapshotError(f"flow snapshot {normalized} is invalid") from exc
        if str(row[2]) != _flow_key(flow_id) or str(row[3]) != _owner_key(owner):
            raise FlowSnapshotError(
                f"flow snapshot {normalized} metadata failed integrity verification"
            )
        if str(row[4]) != "":
            raise FlowSnapshotError(
                f"flow snapshot {normalized} metadata is invalid"
            )
        errors = flow.validate()
        if errors:
            raise FlowSnapshotError(
                "flow snapshot is structurally invalid: " + "; ".join(errors[:3])
            )
        return flow

    def load_flow_snapshot(self, digest: str):
        with self._operation_errors("load immutable object"):
            with self._transaction() as (cur, state):
                return self._load_flow_snapshot_locked(cur, state, digest)

    def _snapshot_flow_bundle_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow: Any,
    ) -> tuple[str, int, dict[str, str | None]]:
        from .store import FlowSnapshotError, _assert_safe_definition

        _assert_safe_definition(flow.to_dict())
        root_owner = _validate_owner(flow.owner)
        manifest: dict[str, str | None] = {}

        def walk(raw: dict[str, Any], *, depth: int = 0) -> None:
            if depth > 40:
                raise FlowSnapshotError("subflow graph nesting is too deep")
            for node in raw.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                body = node.get("body")
                if isinstance(body, dict):
                    walk(body, depth=depth + 1)
                for branch in node.get("branches") or []:
                    if isinstance(branch, dict):
                        walk(branch, depth=depth + 1)
                if str(node.get("kind")) != "subflow":
                    continue
                ref = str(node.get("flow_ref") or "").strip()
                if not ref or ref in manifest:
                    continue
                _validate_identifier(ref, "subflow id")
                if len(manifest) >= 20_000:
                    raise FlowSnapshotError("subflow manifest exceeds its bound")
                child = self._load_flow_locked(cur, state, ref)
                if child is None:
                    manifest[ref] = None
                    continue
                if _validate_owner(child.owner) != root_owner:
                    raise FlowSnapshotError(
                        f"subflow {ref!r} is unavailable to this flow owner"
                    )
                child_raw = child.to_dict()
                manifest[ref] = self._store_flow_object_locked(
                    cur,
                    state,
                    child_raw,
                )
                walk(child_raw, depth=depth + 1)

        root = flow.to_dict()
        root_digest = self._store_flow_object_locked(cur, state, root)
        walk(root)
        _canonical_json({"subflow_digests": manifest})
        return root_digest, int(flow.version), manifest

    def snapshot_flow_bundle(self, flow: Any) -> tuple[str, int, dict[str, str | None]]:
        with self._operation_errors("snapshot bundle"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                return self._snapshot_flow_bundle_locked(cur, state, flow)

    def snapshot_current_flow_bundle(
        self,
        flow_id: str,
    ) -> tuple[str, int, dict[str, str | None]]:
        from .store import FlowSnapshotError

        with self._operation_errors("snapshot current bundle"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "definitions")
                flow = self._load_flow_locked(cur, state, flow_id)
                if flow is None:
                    raise FlowSnapshotError(f"flow {flow_id!r} is unavailable")
                return self._snapshot_flow_bundle_locked(cur, state, flow)

    def _validate_snapshot_bundle_owners_locked(  # noqa: C901
        self,
        cur: Any,
        state: _TransactionState,
        root: Any,
        manifest: dict[str, str | None],
    ) -> None:
        from .store import FlowSnapshotError

        if not isinstance(manifest, dict):
            raise FlowSnapshotError("published subflow manifest is invalid")
        root_owner = _validate_owner(root.owner)
        normalized = {
            str(ref): (str(digest) if digest is not None else None)
            for ref, digest in manifest.items()
        }
        if len(normalized) > 20_000:
            raise FlowSnapshotError("published subflow manifest exceeds its bound")
        required: set[str] = set()
        visited: set[str] = set()

        def references(flow: Any, *, depth: int = 0) -> None:
            if depth > 40:
                raise FlowSnapshotError("published subflow graph nesting is too deep")

            def walk_nodes(nodes: Any, node_depth: int) -> None:
                if node_depth > 40:
                    raise FlowSnapshotError(
                        "published subflow graph nesting is too deep"
                    )
                for node in nodes or []:
                    if not isinstance(node, dict):
                        continue
                    body = node.get("body")
                    if isinstance(body, dict):
                        walk_nodes(body.get("nodes"), node_depth + 1)
                    for branch in node.get("branches") or []:
                        if isinstance(branch, dict):
                            walk_nodes(branch.get("nodes"), node_depth + 1)
                    if str(node.get("kind") or "") == "subflow":
                        ref = str(node.get("flow_ref") or "").strip()
                        if ref:
                            _validate_identifier(ref, "subflow id")
                            required.add(ref)

            walk_nodes(flow.to_dict().get("nodes"), depth)

        pending = [root]
        while pending:
            current = pending.pop()
            references(current)
            for ref in sorted(required - visited):
                visited.add(ref)
                if len(visited) > 20_000:
                    raise FlowSnapshotError("published subflow graph exceeds its bound")
                if ref not in normalized:
                    raise FlowSnapshotError(
                        f"subflow snapshot manifest is missing {ref!r}"
                    )
                digest = normalized[ref]
                if digest is None:
                    continue
                child = self._load_flow_snapshot_locked(cur, state, digest)
                if child.id != ref:
                    raise FlowSnapshotError(
                        f"subflow snapshot identity mismatch for {ref!r}"
                    )
                if _validate_owner(child.owner) != root_owner:
                    raise FlowSnapshotError(
                        f"subflow {ref!r} is unavailable to this flow owner"
                    )
                pending.append(child)
        extras = set(normalized) - required
        if extras:
            raise FlowSnapshotError(
                "subflow snapshot manifest has unreferenced entry "
                f"{sorted(extras)[0]!r}"
            )

    def _load_published_bundle_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow_id: str,
        *,
        for_update: bool = False,
    ):
        from .store import (
            FlowSnapshotError,
            _valid_release_token,
            release_digest_for,
        )

        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_PUBLICATION,
            key,
            for_update=for_update,
        )
        if row is None:
            return None
        raw = self._record_payload(row)
        if str(raw.get("flow_id") or "") != validated:
            raise FlowSnapshotError("published flow identity does not match its key")
        flow = self._load_flow_snapshot_locked(
            cur,
            state,
            str(raw.get("definition_digest") or ""),
        )
        if (
            flow.id != validated
            or int(flow.version) != int(raw.get("definition_version") or 0)
            or str(flow.revision or "")
            != str(raw.get("definition_revision") or "")
        ):
            raise FlowSnapshotError(
                "published flow metadata does not match its snapshot"
            )
        if str(row[2]) != key or str(row[3]) != _owner_key(flow.owner):
            raise FlowSnapshotError("published flow row metadata is invalid")
        if str(row[4]) != "active":
            raise FlowSnapshotError("published flow row status is invalid")
        try:
            published_at = float(raw.get("published_at"))
            row_updated_at = float(row[6])
        except (TypeError, ValueError) as exc:
            raise FlowSnapshotError("published flow timestamp is invalid") from exc
        if (
            not math.isfinite(published_at)
            or published_at != row_updated_at
            or published_at < 0
            or published_at > 32_503_680_000
        ):
            raise FlowSnapshotError(
                "published flow timestamp does not match its row"
            )
        manifest_raw = raw.get("subflow_digests") or {}
        if not isinstance(manifest_raw, dict):
            raise FlowSnapshotError("published subflow manifest is invalid")
        manifest = {
            str(ref): (str(digest) if digest is not None else None)
            for ref, digest in manifest_raw.items()
        }
        self._validate_snapshot_bundle_owners_locked(cur, state, flow, manifest)
        expected_digest = release_digest_for(
            str(raw.get("definition_digest") or ""),
            manifest,
        )
        stored_digest = str(raw.get("release_digest") or "")
        stored_id = str(raw.get("release_id") or "")
        if stored_digest:
            if stored_digest != expected_digest:
                raise FlowSnapshotError(
                    "published release content failed integrity verification"
                )
            if not _valid_release_token(stored_id):
                raise FlowSnapshotError("published activation identity is invalid")
            activation_id = stored_id
        else:
            if stored_id and stored_id != expected_digest:
                raise FlowSnapshotError(
                    "legacy published release identity failed integrity verification"
                )
            activation_id = expected_digest
        release = dict(raw)
        release["subflow_digests"] = manifest
        release["release_digest"] = expected_digest
        release["release_id"] = activation_id
        return flow, release

    def load_published_bundle(self, flow_id: str):
        with self._operation_errors("load publication"):
            with self._transaction() as (cur, state):
                return self._load_published_bundle_locked(cur, state, flow_id)

    def _write_publication_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow: Any,
        release: dict[str, Any],
    ) -> None:
        key = _flow_key(flow.id)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_PUBLICATION,
            key,
            for_update=True,
        )
        if row is None:
            if not self._insert_record(
                cur,
                state.tenant,
                _KIND_PUBLICATION,
                key,
                release,
                flow_key=key,
                owner_key=_owner_key(flow.owner),
                status="active",
                created_at=float(release["published_at"]),
                updated_at=float(release["published_at"]),
            ):
                from .store import FlowSnapshotError

                raise FlowSnapshotError("published flow activation raced")
            return
        if self._update_record(
            cur,
            state.tenant,
            _KIND_PUBLICATION,
            key,
            int(row[0]),
            release,
            flow_key=key,
            owner_key=_owner_key(flow.owner),
            status="active",
            updated_at=float(release["published_at"]),
        ) is None:
            from .store import FlowSnapshotError

            raise FlowSnapshotError("published flow activation changed")

    def _publish_flow_locked(
        self,
        cur: Any,
        state: _TransactionState,
        flow_id: str,
        *,
        expected_version: int,
        expected_revision: str,
    ) -> dict[str, Any]:
        from .store import FlowSnapshotError, FlowVersionConflict, release_digest_for

        flow = self._load_flow_locked(cur, state, flow_id, for_update=True)
        if flow is None:
            raise FlowSnapshotError(f"flow {flow_id!r} is unavailable")
        if int(flow.version) != int(expected_version):
            raise FlowVersionConflict(
                f"flow {flow_id!r} changed (expected version {expected_version}, "
                f"current {flow.version})"
            )
        if str(flow.revision or "") != str(expected_revision):
            raise FlowVersionConflict(
                f"flow {flow_id!r} was replaced or changed generation"
            )
        errors = flow.validate()
        if errors:
            raise FlowSnapshotError(
                "flow cannot be published: " + "; ".join(errors[:3])
            )
        digest, version, subflows = self._snapshot_flow_bundle_locked(
            cur,
            state,
            flow,
        )
        release_digest = release_digest_for(digest, subflows)
        release = {
            "flow_id": flow.id,
            "definition_digest": digest,
            "definition_version": version,
            "definition_revision": flow.revision,
            "subflow_digests": subflows,
            "release_digest": release_digest,
            "release_id": secrets.token_hex(32),
            "published_at": time.time(),
        }
        _canonical_json(release)
        self._write_publication_locked(cur, state, flow, release)
        return release

    def publish_flow(
        self,
        flow_id: str,
        *,
        expected_version: int,
        expected_revision: str,
    ) -> dict[str, Any]:
        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("publish definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "activation", key)
                self._advisory_lock(cur, state.tenant, "definitions")
                return self._publish_flow_locked(
                    cur,
                    state,
                    validated,
                    expected_version=expected_version,
                    expected_revision=expected_revision,
                )

    def save_and_publish_flow(
        self,
        flow: Any,
        *,
        expected_version: int,
        expected_revision: str,
        expected_release_id: str,
    ) -> tuple[Any, dict[str, Any]]:
        from .store import FlowSnapshotError, FlowVersionConflict, release_digest_for

        flow_id = _validate_identifier(flow.id, "flow id")
        key = _flow_key(flow_id)
        with self._operation_errors("save and publish definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "activation", key)
                self._advisory_lock(cur, state.tenant, "definitions")
                current = self._load_flow_locked(
                    cur,
                    state,
                    flow_id,
                    for_update=True,
                )
                if current is None:
                    raise FlowSnapshotError(f"flow {flow_id!r} is unavailable")
                if int(current.version) != int(expected_version):
                    raise FlowVersionConflict(
                        f"flow {flow_id!r} changed (expected version "
                        f"{expected_version}, current {current.version})"
                    )
                if str(current.revision or "") != str(expected_revision):
                    raise FlowVersionConflict(
                        f"flow {flow_id!r} was replaced or changed generation"
                    )
                published = self._load_published_bundle_locked(
                    cur,
                    state,
                    flow_id,
                    for_update=True,
                )
                if published is None:
                    raise FlowSnapshotError(f"flow {flow_id!r} is not published")
                _active_flow, active_release = published
                if str(active_release.get("release_id") or "") != str(
                    expected_release_id
                ):
                    raise FlowSnapshotError(
                        f"published flow {flow_id!r} no longer matches the "
                        "bound activation"
                    )
                digest, _version, subflows = self._snapshot_flow_bundle_locked(
                    cur,
                    state,
                    current,
                )
                if release_digest_for(digest, subflows) != str(
                    active_release.get("release_digest") or ""
                ):
                    raise FlowSnapshotError(
                        "current draft or subflow graph diverged from the active release"
                    )
                saved = self._save_flow_locked(
                    cur,
                    state,
                    flow,
                    expected_version=expected_version,
                    expected_revision=expected_revision,
                )
                release = self._publish_flow_locked(
                    cur,
                    state,
                    saved.id,
                    expected_version=saved.version,
                    expected_revision=saved.revision,
                )
                return saved, release

    @contextlib.contextmanager
    def published_flow_guard(
        self,
        flow_id: str,
        *,
        expected_release_id: str = "",
        expected_digest: str = "",
    ):
        from .store import FlowSnapshotError

        validated = _validate_identifier(flow_id, "flow id")
        with self._operation_errors("reserve publication"):
            with self._session_lock("definitions"):
                published = self.load_published_bundle(validated)
                if published is None:
                    raise FlowSnapshotError(f"flow {validated!r} is not published")
                _flow, release = published
                expected = str(expected_release_id or expected_digest or "")
                if expected and str(release.get("release_id") or "") != expected:
                    raise FlowSnapshotError(
                        f"published flow {validated!r} no longer matches the "
                        "bound revision"
                    )
                yield published

    @contextlib.contextmanager
    def active_release_guard(self, flow_id: str, *, expected_release_id: str):
        from .store import FlowSnapshotError

        validated = _validate_identifier(flow_id, "flow id")
        expected = str(expected_release_id or "")
        if not expected:
            raise FlowSnapshotError("flow run has no immutable release identity")
        key = _flow_key(validated)
        with self._operation_errors("authorize dispatch"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "activation", key)
                published = self._load_published_bundle_locked(
                    cur,
                    state,
                    validated,
                )
                if published is None:
                    raise FlowSnapshotError(f"flow {validated!r} is not published")
                _flow, release = published
                if str(release.get("release_id") or "") != expected:
                    raise FlowSnapshotError(
                        f"published flow {validated!r} no longer matches the "
                        "bound revision"
                    )
                yield published

    def list_published_bundles(self) -> list[tuple[Any, dict[str, Any]]]:
        from .store import FlowSnapshotError

        with self._operation_errors("list publications"):
            with self._transaction() as (cur, state):
                cur.execute(
                    "SELECT revision, payload_text, flow_key, owner_key, status, "
                    f"created_at, updated_at FROM {_TABLE} "
                    "WHERE tenant_id=%s AND record_kind=%s ORDER BY record_key",
                    (state.tenant, _KIND_PUBLICATION),
                )
                flow_ids: list[str] = []
                for row in cur.fetchall():
                    raw = self._record_payload(row)
                    flow_id = str(raw.get("flow_id") or "")
                    if not flow_id:
                        raise FlowSnapshotError(
                            "published flow pointer has no flow identity"
                        )
                    flow_ids.append(flow_id)
                out: list[tuple[Any, dict[str, Any]]] = []
                for flow_id in flow_ids:
                    bundle = self._load_published_bundle_locked(
                        cur,
                        state,
                        flow_id,
                    )
                    if bundle is not None:
                        out.append(bundle)
                return out

    def unpublish_flow(
        self,
        flow_id: str,
        *,
        expected_version: int,
        expected_revision: str,
    ) -> bool:
        from .store import FlowVersionConflict

        validated = _validate_identifier(flow_id, "flow id")
        key = _flow_key(validated)
        with self._operation_errors("unpublish definition"):
            with self._transaction() as (cur, state):
                self._advisory_lock(cur, state.tenant, "activation", key)
                self._advisory_lock(cur, state.tenant, "definitions")
                current = self._load_flow_locked(
                    cur,
                    state,
                    validated,
                    for_update=True,
                )
                if current is None:
                    return False
                if int(current.version) != int(expected_version):
                    raise FlowVersionConflict(
                        f"flow {validated!r} changed (expected version "
                        f"{expected_version}, current {current.version})"
                    )
                if str(current.revision or "") != str(expected_revision):
                    raise FlowVersionConflict(
                        f"flow {validated!r} was replaced or changed generation"
                    )
                publication = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_PUBLICATION,
                    key,
                    for_update=True,
                )
                if publication is None:
                    return False
                cur.execute(
                    f"DELETE FROM {_TABLE} WHERE tenant_id=%s "
                    "AND record_kind=%s AND record_key=%s AND revision=%s",
                    (state.tenant, _KIND_PUBLICATION, key, int(publication[0])),
                )
                if cur.rowcount != 1:
                    from .store import FlowSnapshotError

                    raise FlowSnapshotError("published flow changed during revocation")
                return True

    @staticmethod
    def _run_from_row(row: Any, *, expected_id: str | None = None):
        from .store import FlowRun, FlowSnapshotError

        raw = PostgresFlowStore._record_payload(row)
        try:
            run = FlowRun.from_dict(raw)
            run_id = _validate_identifier(run.run_id, "flow run id")
            flow_id = _validate_identifier(run.flow_id, "flow id")
            owner = _validate_owner(run.owner)
            status = _validate_status(run.status)
        except Exception as exc:
            raise FlowSnapshotError("shared flow run is invalid") from exc
        if expected_id is not None and run_id != expected_id:
            raise FlowSnapshotError("shared flow run identity does not match its key")
        if str(row[2]) != _flow_key(flow_id):
            raise FlowSnapshotError("shared flow run definition key is invalid")
        if str(row[3]) != _owner_key(owner) or str(row[4]) != status:
            raise FlowSnapshotError("shared flow run metadata is invalid")
        try:
            body_created = float(run.created)
            body_updated = float(run.updated)
            row_created = float(row[5])
            row_updated = float(row[6])
        except (TypeError, ValueError) as exc:
            raise FlowSnapshotError("shared flow run timestamp is invalid") from exc
        if (
            not all(math.isfinite(value) for value in (
                body_created,
                body_updated,
                row_created,
                row_updated,
            ))
            or body_created != row_created
            or body_updated != row_updated
        ):
            raise FlowSnapshotError("shared flow run timestamp does not match its row")
        run._storage_revision = int(row[0])
        return run

    def _load_run_locked(
        self,
        cur: Any,
        state: _TransactionState,
        run_id: str,
        *,
        for_update: bool = False,
    ):
        validated = _validate_identifier(run_id, "flow run id")
        key = _run_key(validated)
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_RUN,
            key,
            for_update=for_update,
        )
        return self._run_from_row(row, expected_id=validated) if row else None

    def save_run(self, run: Any, payload: dict[str, Any]) -> None:
        from .store import FlowVersionConflict

        run_id = _validate_identifier(run.run_id, "flow run id")
        flow_id = _validate_identifier(run.flow_id, "flow id")
        owner = _validate_owner(run.owner)
        status = _validate_status(run.status)
        if str(payload.get("run_id") or "") != run_id:
            raise ValueError("flow run payload identity is invalid")
        if str(payload.get("flow_id") or "") != flow_id:
            raise ValueError("flow run payload definition identity is invalid")
        _canonical_json(payload)
        key = _run_key(run_id)
        with self._operation_errors("save run"):
            with self._transaction() as (cur, state):
                row = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_RUN,
                    key,
                    for_update=True,
                )
                object_revision = int(getattr(run, "_storage_revision", 0) or 0)
                held_revisions = self._held_run_revisions.get() or {}
                locked_revision = held_revisions.get(key)
                if row is None:
                    if object_revision or (
                        locked_revision is not None and locked_revision != 0
                    ):
                        raise FlowVersionConflict(
                            f"flow run {run_id!r} was deleted or replaced"
                        )
                    inserted = self._insert_record(
                        cur,
                        state.tenant,
                        _KIND_RUN,
                        key,
                        payload,
                        flow_key=_flow_key(flow_id),
                        owner_key=_owner_key(owner),
                        status=status,
                        created_at=float(run.created),
                        updated_at=float(run.updated),
                    )
                    if not inserted:
                        raise FlowVersionConflict(
                            f"flow run {run_id!r} changed during creation"
                        )
                    # The durable run reservation and its first-writer claim
                    # are one transaction. A process crash can therefore never
                    # leave an unclaimed queued row that falls outside the
                    # bounded compatibility scan and permits a duplicate run.
                    if run.idem_key and not self.save_idempotency_claim(
                        flow_id,
                        str(run.idem_key),
                        run_id,
                        definition_digest=str(run.definition_digest or ""),
                        release_id=str(run.release_id or ""),
                        owner=owner,
                        overwrite=False,
                    ):
                        raise FlowVersionConflict(
                            "idempotency key is already reserved by another run"
                        )
                    run._storage_revision = 1
                    if locked_revision is not None:
                        held_revisions[key] = 1
                    return
                current = self._run_from_row(row, expected_id=run_id)
                current_revision = int(row[0])
                if locked_revision is not None:
                    expected = int(locked_revision)
                else:
                    expected = object_revision
                if expected < 1 or current_revision != expected:
                    raise FlowVersionConflict(
                        f"flow run {run_id!r} changed during state transition"
                    )
                # The immutable run/flow identity may not be changed by a CAS
                # update even if a caller constructs a new dataclass instance.
                if current.run_id != run_id or current.flow_id != flow_id:
                    raise FlowVersionConflict(
                        f"flow run {run_id!r} identity changed"
                    )
                updated_revision = self._update_record(
                    cur,
                    state.tenant,
                    _KIND_RUN,
                    key,
                    expected,
                    payload,
                    flow_key=_flow_key(flow_id),
                    owner_key=_owner_key(owner),
                    status=status,
                    updated_at=float(run.updated),
                )
                if updated_revision is None:
                    raise FlowVersionConflict(
                        f"flow run {run_id!r} changed during state transition"
                    )
                run._storage_revision = updated_revision
                if locked_revision is not None:
                    held_revisions[key] = updated_revision

    def load_run(self, run_id: str):
        with self._operation_errors("load run"):
            with self._transaction() as (cur, state):
                return self._load_run_locked(cur, state, run_id)

    def delete_run(self, run_id: str) -> bool:
        from .store import FlowVersionConflict

        validated = _validate_identifier(run_id, "flow run id")
        key = _run_key(validated)
        with self._operation_errors("delete run"):
            with self._transaction() as (cur, state):
                if key not in (self._held_run_revisions.get() or {}):
                    self._advisory_lock(cur, state.tenant, "run", key)
                row = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_RUN,
                    key,
                    for_update=True,
                )
                if row is None:
                    return False
                self._run_from_row(row, expected_id=validated)
                cur.execute(
                    f"DELETE FROM {_TABLE} WHERE tenant_id=%s "
                    "AND record_kind=%s AND record_key=%s AND revision=%s",
                    (state.tenant, _KIND_RUN, key, int(row[0])),
                )
                if cur.rowcount != 1:
                    raise FlowVersionConflict(
                        f"flow run {validated!r} changed during deletion"
                    )
                held = self._held_run_revisions.get() or {}
                if key in held:
                    held.pop(key, None)
                return True

    @contextlib.contextmanager
    def run_lock(self, run_id: str):
        validated = _validate_identifier(run_id, "flow run id")
        key = _run_key(validated)
        held = self._held_run_revisions.get() or {}
        if key in held:
            yield
            return
        with self._operation_errors("lock run"):
            with self._session_lock("run", key):
                current = self.load_run(validated)
                revision = int(
                    getattr(current, "_storage_revision", 0) or 0
                ) if current is not None else 0
                token = self._held_run_revisions.set({**held, key: revision})
                try:
                    yield
                finally:
                    self._held_run_revisions.reset(token)

    def _list_runs_locked(
        self,
        cur: Any,
        state: _TransactionState,
        *,
        flow_id: str | None = None,
        owner: str | None = None,
        limit: int | None = 50,
    ) -> list[Any]:
        conditions = ["tenant_id=%s", "record_kind=%s"]
        params: list[Any] = [state.tenant, _KIND_RUN]
        validated_flow: str | None = None
        validated_owner: str | None = None
        if flow_id is not None:
            validated_flow = _validate_identifier(flow_id, "flow id")
            conditions.append("flow_key=%s")
            params.append(_flow_key(validated_flow))
        if owner is not None:
            validated_owner = _validate_owner(owner)
            conditions.append("owner_key=%s")
            params.append(_owner_key(validated_owner))
        sql = (
            "SELECT revision, payload_text, flow_key, owner_key, status, "
            f"created_at, updated_at FROM {_TABLE} WHERE "
            + " AND ".join(conditions)
            + " ORDER BY updated_at DESC, record_key"
        )
        if limit is not None:
            bounded = max(0, min(100_000, int(limit)))
            if bounded == 0:
                return []
            sql += " LIMIT %s"
            params.append(bounded)
        cur.execute(sql, tuple(params))
        runs = [self._run_from_row(row) for row in cur.fetchall()]
        for run in runs:
            if validated_flow is not None and run.flow_id != validated_flow:
                from .store import FlowSnapshotError

                raise FlowSnapshotError("shared flow run lookup key collided")
            if validated_owner is not None and run.owner != validated_owner:
                from .store import FlowSnapshotError

                raise FlowSnapshotError("shared flow run owner key collided")
        return runs

    def list_runs(
        self,
        *,
        flow_id: str | None = None,
        owner: str | None = None,
        limit: int | None = 50,
    ) -> list[Any]:
        with self._operation_errors("list runs"):
            with self._transaction() as (cur, state):
                return self._list_runs_locked(
                    cur,
                    state,
                    flow_id=flow_id,
                    owner=owner,
                    limit=limit,
                )

    @staticmethod
    def _claim_key(
        flow_id: str,
        idem_key: str,
        definition_digest: str = "",
        owner: str | None = None,
        *,
        release_id: str = "",
    ) -> str:
        material = f"{flow_id}\0{idem_key}".encode()
        release_scope = str(release_id or definition_digest or "")
        if release_scope or owner is not None:
            owner_scope = "<any-owner>" if owner is None else f"owner:{owner}"
            scope_version = "v2" if release_id else "v1"
            material += (
                f"\0exact-release-{scope_version}\0{release_scope}\0{owner_scope}"
            ).encode()
        return hashlib.sha256(material).hexdigest()

    @staticmethod
    def _legacy_claim_key(
        flow_id: str,
        idem_key: str,
        definition_digest: str,
        owner: str | None,
    ) -> str:
        material = f"{flow_id}\0{idem_key}".encode()
        owner_scope = "<any-owner>" if owner is None else f"owner:{owner}"
        material += (
            f"\0exact-release-v1\0{definition_digest}\0{owner_scope}"
        ).encode()
        return hashlib.sha256(material).hexdigest()

    @contextlib.contextmanager
    def idempotency_lock(
        self,
        flow_id: str,
        idem_key: str,
        definition_digest: str = "",
        owner: str | None = None,
        *,
        release_id: str = "",
    ):
        validated_flow = _validate_identifier(flow_id, "flow id")
        _validate_owner("" if owner is None else owner)
        claim_key = self._claim_key(
            validated_flow,
            str(idem_key),
            str(definition_digest),
            owner,
            release_id=str(release_id),
        )
        with self._operation_errors("lock idempotency claim"):
            with self._session_lock("idempotency", claim_key):
                yield

    def _claimed_run_locked(
        self,
        cur: Any,
        state: _TransactionState,
        claim_key: str,
        *,
        flow_id: str,
        idem_key: str,
        revision: str | None,
        definition_digest: str | None,
        release_id: str | None,
        owner: str | None,
    ):
        row = self._select_record(
            cur,
            state.tenant,
            _KIND_CLAIM,
            claim_key,
        )
        if row is None:
            return None
        claim = self._record_payload(row)
        if str(row[2]) != _flow_key(flow_id):
            from .store import FlowSnapshotError

            raise FlowSnapshotError("shared idempotency claim key is invalid")
        run = self._load_run_locked(
            cur,
            state,
            str(claim.get("run_id") or ""),
        )
        if (
            run is not None
            and run.flow_id == flow_id
            and run.idem_key == idem_key
            and (revision is None or run.definition_revision == revision)
            and (
                definition_digest is None
                or run.definition_digest == definition_digest
            )
            and (release_id is None or run.release_id == release_id)
            and (owner is None or run.owner == owner)
        ):
            return run
        return None

    def find_run_by_idem(
        self,
        flow_id: str,
        idem_key: str,
        *,
        within: int | None = 500,
        revision: str | None = None,
        definition_digest: str | None = None,
        release_id: str | None = None,
        owner: str | None = None,
    ):
        if not idem_key:
            return None
        validated_flow = _validate_identifier(flow_id, "flow id")
        expected_digest = (
            None if definition_digest is None else str(definition_digest)
        )
        expected_release = None if release_id is None else str(release_id)
        claim_key = self._claim_key(
            validated_flow,
            str(idem_key),
            expected_digest or "",
            owner,
            release_id=expected_release or "",
        )
        with self._operation_errors("find idempotent run"):
            with self._transaction() as (cur, state):
                claimed = self._claimed_run_locked(
                    cur,
                    state,
                    claim_key,
                    flow_id=validated_flow,
                    idem_key=str(idem_key),
                    revision=revision,
                    definition_digest=expected_digest,
                    release_id=expected_release,
                    owner=owner,
                )
                if claimed is not None:
                    return claimed
                if expected_digest and expected_release:
                    legacy_key = self._legacy_claim_key(
                        validated_flow,
                        str(idem_key),
                        expected_digest,
                        owner,
                    )
                    claimed = self._claimed_run_locked(
                        cur,
                        state,
                        legacy_key,
                        flow_id=validated_flow,
                        idem_key=str(idem_key),
                        revision=revision,
                        definition_digest=expected_digest,
                        release_id=expected_release,
                        owner=owner,
                    )
                    if claimed is not None:
                        return claimed
                for run in self._list_runs_locked(
                    cur,
                    state,
                    flow_id=validated_flow,
                    limit=within,
                ):
                    if (
                        run.idem_key == idem_key
                        and (
                            revision is None
                            or run.definition_revision == revision
                        )
                        and (
                            expected_digest is None
                            or run.definition_digest == expected_digest
                        )
                        and (
                            expected_release is None
                            or run.release_id == expected_release
                        )
                        and (owner is None or run.owner == owner)
                    ):
                        return run
                return None

    def save_idempotency_claim(
        self,
        flow_id: str,
        idem_key: str,
        run_id: str,
        *,
        definition_digest: str = "",
        release_id: str = "",
        owner: str | None = None,
        overwrite: bool = True,
    ) -> bool:
        if not idem_key:
            return False
        validated_flow = _validate_identifier(flow_id, "flow id")
        validated_run = _validate_identifier(run_id, "flow run id")
        validated_owner = "" if owner is None else _validate_owner(owner)
        claim_key = self._claim_key(
            validated_flow,
            str(idem_key),
            str(definition_digest),
            owner,
            release_id=str(release_id),
        )
        payload = {
            "flow_id": validated_flow,
            "run_id": validated_run,
            "definition_digest": str(definition_digest),
            "release_id": str(release_id or definition_digest),
            "owner": validated_owner,
        }
        with self._operation_errors("save idempotency claim"):
            with self._transaction() as (cur, state):
                run = self._load_run_locked(cur, state, validated_run)
                if (
                    run is None
                    or run.flow_id != validated_flow
                    or run.idem_key != str(idem_key)
                    or (
                        definition_digest
                        and run.definition_digest != str(definition_digest)
                    )
                    or (release_id and run.release_id != str(release_id))
                    or (owner is not None and run.owner != validated_owner)
                ):
                    raise ValueError(
                        "idempotency claim does not match its durable flow run"
                    )
                row = self._select_record(
                    cur,
                    state.tenant,
                    _KIND_CLAIM,
                    claim_key,
                    for_update=True,
                )
                if row is None:
                    if not self._insert_record(
                        cur,
                        state.tenant,
                        _KIND_CLAIM,
                        claim_key,
                        payload,
                        flow_key=_flow_key(validated_flow),
                        owner_key=_owner_key(validated_owner),
                    ):
                        return False
                    return True
                if not overwrite:
                    return False
                if self._update_record(
                    cur,
                    state.tenant,
                    _KIND_CLAIM,
                    claim_key,
                    int(row[0]),
                    payload,
                    flow_key=_flow_key(validated_flow),
                    owner_key=_owner_key(validated_owner),
                ) is None:
                    from .store import FlowVersionConflict

                    raise FlowVersionConflict("idempotency claim changed during save")
                return True


_CACHE_LOCK = threading.Lock()
_CACHED_STORE: PostgresFlowStore | None = None
_BOUND_AUTHORITY_TOKEN: _AuthorityToken | None = None


def reset_shared_flow_store_for_tests() -> None:
    global _BOUND_AUTHORITY_TOKEN, _CACHED_STORE

    with _CACHE_LOCK:
        prior = _CACHED_STORE
        _CACHED_STORE = None
        _BOUND_AUTHORITY_TOKEN = None
    if prior is not None:
        prior.close()


def selected_shared_flow_store() -> PostgresFlowStore | None:
    """Resolve the deployment-global flow authority without local fallback."""
    selection = _selection()
    token = _authority_token(selection) if selection.postgres else None
    global _BOUND_AUTHORITY_TOKEN, _CACHED_STORE

    with _CACHE_LOCK:
        if _BOUND_AUTHORITY_TOKEN is not None:
            if token is None or not _authority_matches(
                _BOUND_AUTHORITY_TOKEN,
                selection,
            ):
                raise PostgresFlowStoreUnavailable(
                    "shared flow deployment authority changed after process binding"
                )
            if _CACHED_STORE is None:  # pragma: no cover - defensive invariant
                raise PostgresFlowStoreUnavailable(
                    "shared flow deployment authority binding is unavailable"
                )
            return _CACHED_STORE
        if token is None:
            return None
        # Cutover refusal precedes opening or mutating the shared authority.
        assert_no_legacy_flow_state()
        store = PostgresFlowStore(
            dsn=selection.dsn,
            require_bound_tenant=selection.shared_required,
            _authority_token=token,
        )
        _CACHED_STORE = store
        _BOUND_AUTHORITY_TOKEN = token
        return _CACHED_STORE


__all__ = [
    "FlowLegacyMigrationRequired",
    "PostgresFlowStore",
    "PostgresFlowStoreUnavailable",
    "assert_no_legacy_flow_state",
    "reset_shared_flow_store_for_tests",
    "selected_shared_flow_store",
]
