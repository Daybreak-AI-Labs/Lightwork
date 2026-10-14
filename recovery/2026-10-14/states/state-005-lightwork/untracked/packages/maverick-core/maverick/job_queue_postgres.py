"""Fleet-shared Postgres authority for :mod:`maverick.job_queue`.

The SQLite queue remains the deliberately local backend.  This module owns the
HA backend used when the deployment selects Postgres: queue rows, recurring
occurrence identity, fenced worker leases, trigger-poll leases, and dispatch
replay claims all live in one tenant-scoped database authority.

The adapter is intentionally not a generic JSON/CAS document.  Its invariants
are database constraints and atomic SQL transitions.  A verify-only bootstrap
lets a least-privilege runtime use tables installed by a separate migration
owner.  The runtime never provisions or repairs schema.
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
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .file_lock import (
    atomic_create_text,
    atomic_read_text,
    cross_process_lock,
    private_path_is_restricted,
)

log = logging.getLogger(__name__)

_JOBS_TABLE = "job_queue_jobs"
_POLLS_TABLE = "job_queue_poll_leases"
_DISPATCH_TABLE = "job_queue_dispatch_claims"
_TABLES = (_JOBS_TABLE, _POLLS_TABLE, _DISPATCH_TABLE)
_POLICY = "mvk_job_queue_tenant_isolation"
_MAX_TIMESTAMP = 32_503_708_800.0
_MAX_PAYLOAD_BYTES = 4 * 1024 * 1024
_MAX_ERROR_BYTES = 64 * 1024
_QUEUE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_HEX_32_RE = re.compile(r"^[0-9a-f]{32}$")


class SharedJobQueueUnavailable(RuntimeError):
    """The selected shared queue cannot safely serve an operation."""


class JobQueueMigrationRequired(RuntimeError):
    """A live node-local authority must be migrated before Postgres cutover."""


@dataclass(frozen=True)
class PollLease:
    """A fenced claim on one trigger's polling cadence."""

    key: str
    epoch: int
    token: str


@dataclass(frozen=True)
class _Selection:
    postgres: bool
    shared_required: bool
    dsn: str = ""


@dataclass(frozen=True)
class _AuthorityToken:
    dsn_sha256: str
    shared_required: bool


@dataclass(frozen=True)
class _CutoverFence:
    legacy_db_path: Path
    marker_path: Path
    dsn_sha256: str
    poll_artifacts: tuple[Path, ...]


_ACTIVE_CUTOVER = threading.local()


def _active_cutover_fence() -> _CutoverFence | None:
    return getattr(_ACTIVE_CUTOVER, "fence", None)


def _replica_count() -> int:
    raw: object = os.environ.get("MAVERICK_REPLICA_COUNT", "1")
    if isinstance(raw, bool):
        raise SharedJobQueueUnavailable("replica count must be a positive integer")
    try:
        parsed = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        raise SharedJobQueueUnavailable(
            "replica count must be a positive integer"
        ) from None
    if parsed < 1 or str(raw).strip() != str(parsed):
        raise SharedJobQueueUnavailable("replica count must be a positive integer")
    return parsed


def _selection() -> _Selection:
    """Resolve deployment-global authority policy without tenant overlays."""
    try:
        from .config import config_source_errors, load_global_config

        config = load_global_config()
        source_errors = config_source_errors(include_tenant=False)
    except Exception:
        raise SharedJobQueueUnavailable(
            "job queue deployment policy is unavailable"
        ) from None
    if not isinstance(config, Mapping):
        raise SharedJobQueueUnavailable("job queue deployment policy is invalid")
    world = config.get("world_model") or {}
    if not isinstance(world, Mapping):
        raise SharedJobQueueUnavailable("world-model policy is invalid")
    backend = str(
        os.environ.get("MAVERICK_WORLD_BACKEND")
        or world.get("backend")
        or "sqlite"
    ).strip().lower()
    if backend not in {"sqlite", "postgres"}:
        raise SharedJobQueueUnavailable("world-model backend is invalid")
    try:
        from .enterprise import deployment_enterprise_enabled

        enterprise = deployment_enterprise_enabled(
            config=config,
            source_errors=source_errors,
        )
    except Exception:
        enterprise = True
    shared_required = bool(enterprise or _replica_count() > 1 or source_errors)
    if backend != "postgres":
        if shared_required:
            raise SharedJobQueueUnavailable(
                "enterprise and multi-replica job authority requires Postgres; "
                "local fallback is disabled"
            )
        return _Selection(False, False)
    dsn = str(os.environ.get("MAVERICK_PG_DSN") or world.get("dsn") or "").strip()
    if not dsn:
        raise SharedJobQueueUnavailable(
            "Postgres job authority is selected but no DSN is configured"
        )
    return _Selection(True, shared_required, dsn)


def _authority_token(selection: _Selection) -> _AuthorityToken:
    if not selection.postgres or not selection.dsn:
        raise SharedJobQueueUnavailable("shared job authority is not Postgres")
    return _AuthorityToken(
        hashlib.sha256(selection.dsn.encode("utf-8")).hexdigest(),
        selection.shared_required,
    )


def _authority_matches(token: _AuthorityToken, selection: _Selection) -> bool:
    if not selection.postgres or not selection.dsn:
        return False
    current = hashlib.sha256(selection.dsn.encode("utf-8")).hexdigest()
    return (
        hmac.compare_digest(token.dsn_sha256, current)
        and token.shared_required is selection.shared_required
    )


_CUTOVER_PROTOCOL = 1


def _canonical_legacy_path(path: str | Path) -> Path:
    return Path(os.path.abspath(os.path.expanduser(os.fspath(path))))


def _cutover_marker_path(path: str | Path) -> Path:
    legacy = _canonical_legacy_path(path)
    return legacy.with_name(f"{legacy.name}.postgres-authority")


def _cutover_payload(dsn_sha256: str) -> str:
    return json.dumps(
        {
            "backend": "postgres",
            "dsn_sha256": dsn_sha256,
            "protocol": _CUTOVER_PROTOCOL,
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def _validated_cutover_marker(path: Path, dsn_sha256: str) -> None:
    try:
        info = path.lstat()
        if not _stable_regular_file(path, info):
            raise ValueError("unstable marker")
        if not private_path_is_restricted(path, 0o600):
            raise ValueError("non-private marker")
        payload = json.loads(atomic_read_text(path))
    except Exception:
        raise SharedJobQueueUnavailable(
            "Postgres job-authority cutover marker is invalid"
        ) from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"backend", "dsn_sha256", "protocol"}
        or payload.get("backend") != "postgres"
        or payload.get("protocol") != _CUTOVER_PROTOCOL
        or not isinstance(payload.get("dsn_sha256"), str)
        or not hmac.compare_digest(str(payload["dsn_sha256"]), dsn_sha256)
    ):
        raise SharedJobQueueUnavailable(
            "Postgres job-authority cutover marker does not match the selected authority"
        )


@contextlib.contextmanager
def local_job_authority_guard(path: str | Path) -> Iterator[None]:
    """Serialize a complete SQLite operation against Postgres cutover.

    Every current local queue operation passes through this gate.  Deployment
    policy and the durable marker are checked while the strict OS-backed lock
    is held, so a cutover cannot inspect quiescence between a local check and
    its SQLite commit.
    """
    marker = _cutover_marker_path(path)
    with cross_process_lock(marker, strict=True):
        try:
            marker.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise SharedJobQueueUnavailable(
                "local job-authority cutover state cannot be inspected"
            ) from None
        else:
            raise SharedJobQueueUnavailable(
                "local job authority is fenced after Postgres cutover"
            )
        selection = _selection()
        if selection.postgres or selection.shared_required:
            raise SharedJobQueueUnavailable(
                "local job queue is no longer the selected deployment authority"
            )
        yield


@contextlib.contextmanager
def _postgres_cutover_guard(
    path: str | Path,
    *,
    poll_artifacts: Sequence[str | Path],
    dsn_sha256: str,
) -> Iterator[_CutoverFence]:
    """Hold the local authority gate until Postgres admission succeeds.

    The caller verifies its Postgres connection and schema inside this scope.
    On success a private, atomic marker is published before the gate releases;
    on failure no new marker is left behind and local single-replica service can
    continue.  An existing valid marker is idempotent and remains authoritative.
    """
    legacy = _canonical_legacy_path(path)
    marker = _cutover_marker_path(legacy)
    artifacts = tuple(_canonical_legacy_path(item) for item in poll_artifacts)
    fence = _CutoverFence(legacy, marker, dsn_sha256, artifacts)
    with cross_process_lock(marker, strict=True):
        marker_exists = False
        try:
            marker.lstat()
            marker_exists = True
        except FileNotFoundError:
            pass
        except OSError:
            raise SharedJobQueueUnavailable(
                "Postgres job-authority cutover state cannot be inspected"
            ) from None
        if marker_exists:
            _validated_cutover_marker(marker, dsn_sha256)
        _assert_no_live_legacy_db(legacy)
        _assert_no_poll_artifacts(artifacts)
        prior_fence = _active_cutover_fence()
        _ACTIVE_CUTOVER.fence = fence
        try:
            yield fence
            if not marker_exists:
                try:
                    atomic_create_text(
                        marker,
                        _cutover_payload(dsn_sha256),
                        mode=0o600,
                    )
                except FileExistsError:
                    _validated_cutover_marker(marker, dsn_sha256)
                except Exception:
                    raise SharedJobQueueUnavailable(
                        "Postgres job-authority cutover marker could not be published"
                    ) from None
        finally:
            _ACTIVE_CUTOVER.fence = prior_fence


def _assert_postgres_cutover_fence(fence: _CutoverFence) -> None:
    with cross_process_lock(fence.marker_path, strict=True):
        _validated_cutover_marker(fence.marker_path, fence.dsn_sha256)
        # Detect a mixed-version writer that does not understand the marker.
        # Current local writers are excluded by the same gate and policy check.
        _assert_no_live_legacy_db(fence.legacy_db_path)
        _assert_no_poll_artifacts(fence.poll_artifacts)


def _stable_regular_file(path: Path, info: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    is_junction = getattr(path, "is_junction", None)
    return bool(
        stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and info.st_nlink == 1
        and not bool(getattr(info, "st_file_attributes", 0) & reparse)
        and not bool(callable(is_junction) and is_junction())
    )


def _assert_no_live_legacy_db(path: Path) -> None:
    """Inspect a legacy SQLite queue read-only, never creating or following it."""
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        try:
            info = sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise JobQueueMigrationRequired(
                "legacy job queue sidecar cannot be inspected"
            ) from None
        if not _stable_regular_file(sidecar, info) or info.st_size:
            raise JobQueueMigrationRequired(
                "legacy job queue sidecar requires an explicit Postgres cutover"
            )

    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        raise JobQueueMigrationRequired(
            "legacy job queue cannot be inspected"
        ) from None
    if not _stable_regular_file(path, info):
        raise JobQueueMigrationRequired(
            "legacy job queue is not a stable private file"
        )
    if info.st_size == 0:
        return
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            expected = {"jobs", "dispatch_envelope_claims"}
            if tables - expected:
                raise JobQueueMigrationRequired(
                    "legacy job queue has an unrecognized authority schema"
                )
            objects = dict(connection.execute(
                "SELECT name, type FROM sqlite_master WHERE name IN "
                "('jobs', 'dispatch_envelope_claims')"
            ).fetchall())
            if any(str(kind) != "table" for kind in objects.values()):
                raise JobQueueMigrationRequired(
                    "legacy job queue has an invalid authority schema"
                )
            if "jobs" in objects and connection.execute(
                "SELECT 1 FROM jobs WHERE status IN ('pending','running') LIMIT 1"
            ).fetchone() is not None:
                raise JobQueueMigrationRequired(
                    "live legacy jobs require an explicit Postgres import"
                )
            # Cutover has no shared clock yet, so it cannot safely classify a
            # local replay claim as expired. Any retained claim is authority
            # that must be explicitly retired/imported while the old side is
            # offline; a node wall clock must never waive it.
            if "dispatch_envelope_claims" in objects and connection.execute(
                "SELECT 1 FROM dispatch_envelope_claims LIMIT 1"
            ).fetchone() is not None:
                raise JobQueueMigrationRequired(
                    "live legacy dispatch claims require an explicit Postgres import"
                )
    except JobQueueMigrationRequired:
        raise
    except (OSError, sqlite3.Error):
        raise JobQueueMigrationRequired(
            "legacy job queue cannot be proven quiescent"
        ) from None


def _assert_no_poll_artifacts(paths: Sequence[Path]) -> None:
    for path in paths:
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise JobQueueMigrationRequired(
                "legacy automation poll state cannot be inspected"
            ) from None
        reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        is_junction = getattr(path, "is_junction", None)
        unsafe = bool(
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & reparse
            or bool(callable(is_junction) and is_junction())
        )
        if not stat.S_ISDIR(info.st_mode):
            unsafe = unsafe or getattr(info, "st_nlink", 1) != 1
        if unsafe:
            raise JobQueueMigrationRequired(
                "legacy automation poll state is not a stable local object"
            )
        if stat.S_ISDIR(info.st_mode):
            try:
                if next(path.iterdir(), None) is not None:
                    raise JobQueueMigrationRequired(
                        "legacy automation poll markers require explicit cutover"
                    )
            except JobQueueMigrationRequired:
                raise
            except OSError:
                raise JobQueueMigrationRequired(
                    "legacy automation poll markers cannot be inspected"
                ) from None
        else:
            # A lock file can remain after a clean process exit.  Its existence
            # is still ambiguous at cutover: another local scheduler may own it.
            raise JobQueueMigrationRequired(
                "legacy automation poll lock requires explicit cutover"
            )


def assert_no_legacy_job_authority(
    db_path: str | Path,
    *,
    poll_artifacts: Sequence[str | Path] = (),
) -> None:
    legacy = _canonical_legacy_path(db_path)
    marker = _cutover_marker_path(legacy)
    with cross_process_lock(marker, strict=True):
        _assert_no_live_legacy_db(legacy)
        _assert_no_poll_artifacts(
            tuple(_canonical_legacy_path(item) for item in poll_artifacts)
        )


_CREATE_JOBS_SQL = f"""
CREATE TABLE IF NOT EXISTS {_JOBS_TABLE} (
    id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_text TEXT NOT NULL,
    run_at DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_epoch BIGINT NOT NULL DEFAULT 0,
    last_error_text TEXT NOT NULL DEFAULT '',
    recurrence_key TEXT,
    occurrence_at DOUBLE PRECISION,
    lease_token TEXT NOT NULL DEFAULT '',
    lease_expires_at DOUBLE PRECISION NOT NULL DEFAULT -1,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    CONSTRAINT mvk_job_tenant_bound CHECK (char_length(tenant_id) BETWEEN 1 AND 201),
    CONSTRAINT mvk_job_queue_bound CHECK (queue_name ~ '^[a-z0-9][a-z0-9_.-]{{0,127}}$'),
    CONSTRAINT mvk_job_kind_bound CHECK (octet_length(kind) BETWEEN 1 AND 200),
    CONSTRAINT mvk_job_payload_bound CHECK (
        octet_length(payload_text) BETWEEN 1 AND {_MAX_PAYLOAD_BYTES}
        AND payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={{0,2}}$'
    ),
    CONSTRAINT mvk_job_status_bound CHECK (status IN ('pending','running','done','failed')),
    CONSTRAINT mvk_job_attempt_bound CHECK (attempt_epoch BETWEEN 0 AND 9223372036854775807),
    CONSTRAINT mvk_job_error_bound CHECK (
        last_error_text = '' OR (
            octet_length(last_error_text) <= {_MAX_ERROR_BYTES}
            AND last_error_text ~ '^MVKAR1:[A-Za-z0-9+/]+={{0,2}}$'
        )
    ),
    CONSTRAINT mvk_job_recurrence_bound CHECK (
        (recurrence_key IS NULL AND occurrence_at IS NULL)
        OR (recurrence_key ~ '^[0-9a-f]{{64}}$'
            AND occurrence_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})
    ),
    CONSTRAINT mvk_job_lease_bound CHECK (
        (status = 'running' AND lease_token ~ '^[0-9a-f]{{32}}$'
         AND lease_expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})
        OR (status <> 'running' AND lease_token = '' AND lease_expires_at = -1)
    ),
    CONSTRAINT mvk_job_time_bound CHECK (
        run_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
        AND created_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
        AND updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
    )
)
"""

_CREATE_POLLS_SQL = f"""
CREATE TABLE IF NOT EXISTS {_POLLS_TABLE} (
    tenant_id TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    poll_key TEXT NOT NULL,
    next_due_at DOUBLE PRECISION NOT NULL,
    lease_epoch BIGINT NOT NULL,
    lease_token TEXT NOT NULL,
    lease_expires_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, queue_name, poll_key),
    CONSTRAINT mvk_poll_tenant_bound CHECK (char_length(tenant_id) BETWEEN 1 AND 201),
    CONSTRAINT mvk_poll_queue_bound CHECK (queue_name ~ '^[a-z0-9][a-z0-9_.-]{{0,127}}$'),
    CONSTRAINT mvk_poll_key_bound CHECK (octet_length(poll_key) BETWEEN 1 AND 512),
    CONSTRAINT mvk_poll_epoch_bound CHECK (lease_epoch BETWEEN 1 AND 9223372036854775807),
    CONSTRAINT mvk_poll_lease_bound CHECK (
        (lease_token = '' AND lease_expires_at = -1)
        OR (lease_token ~ '^[0-9a-f]{{32}}$'
            AND lease_expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})
    ),
    CONSTRAINT mvk_poll_time_bound CHECK (
        next_due_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
        AND updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
    )
)
"""

_CREATE_DISPATCH_SQL = f"""
CREATE TABLE IF NOT EXISTS {_DISPATCH_TABLE} (
    tenant_id TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    message_id TEXT NOT NULL,
    nonce_hash TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    claimed_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (tenant_id, queue_name, message_id),
    CONSTRAINT mvk_dispatch_tenant_bound CHECK (char_length(tenant_id) BETWEEN 1 AND 201),
    CONSTRAINT mvk_dispatch_queue_bound CHECK (queue_name ~ '^[a-z0-9][a-z0-9_.-]{{0,127}}$'),
    CONSTRAINT mvk_dispatch_message_bound CHECK (octet_length(message_id) BETWEEN 1 AND 512),
    CONSTRAINT mvk_dispatch_nonce_bound CHECK (nonce_hash ~ '^[0-9a-f]{{64}}$'),
    CONSTRAINT mvk_dispatch_time_bound CHECK (
        expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
        AND claimed_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}
    )
)
"""


def _policy_sql(table: str) -> str:
    return (
        "DO $mvk$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_policies "
        "WHERE schemaname=current_schema() "
        f"AND tablename='{table}' AND policyname='{_POLICY}') THEN "
        "BEGIN EXECUTE $policy$CREATE POLICY "
        f"{_POLICY} ON {table} "
        "USING (tenant_id = nullif(current_setting('maverick.tenant', true), '')) "
        "WITH CHECK (tenant_id = nullif(current_setting('maverick.tenant', true), ''))"
        "$policy$; EXCEPTION WHEN duplicate_object THEN NULL; END; "
        "END IF; END $mvk$"
    )


POSTGRES_JOB_QUEUE_MIGRATION: tuple[str, ...] = (
    _CREATE_JOBS_SQL,
    "CREATE INDEX IF NOT EXISTS idx_mvk_jobs_ready ON "
    f"{_JOBS_TABLE}(tenant_id, queue_name, status, run_at, id)",
    "CREATE INDEX IF NOT EXISTS idx_mvk_jobs_terminal ON "
    f"{_JOBS_TABLE}(tenant_id, queue_name, status, updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_mvk_jobs_occurrence ON "
    f"{_JOBS_TABLE}(tenant_id, queue_name, recurrence_key, occurrence_at) "
    "WHERE recurrence_key IS NOT NULL",
    _CREATE_POLLS_SQL,
    "CREATE INDEX IF NOT EXISTS idx_mvk_polls_due ON "
    f"{_POLLS_TABLE}(tenant_id, queue_name, next_due_at, lease_expires_at)",
    _CREATE_DISPATCH_SQL,
    "CREATE INDEX IF NOT EXISTS idx_mvk_dispatch_expiry ON "
    f"{_DISPATCH_TABLE}(tenant_id, queue_name, expires_at)",
    *(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" for table in _TABLES),
    *(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" for table in _TABLES),
    *(_policy_sql(table) for table in _TABLES),
    *(f"REVOKE ALL ON TABLE {table} FROM PUBLIC" for table in _TABLES),
    "REVOKE ALL ON SEQUENCE job_queue_jobs_id_seq FROM PUBLIC",
)

_COLUMN_SHAPES = {
    _JOBS_TABLE: {
        "id": ("bigint", "NO"),
        "tenant_id": ("text", "NO"),
        "queue_name": ("text", "NO"),
        "kind": ("text", "NO"),
        "payload_text": ("text", "NO"),
        "run_at": ("double precision", "NO"),
        "status": ("text", "NO"),
        "attempt_epoch": ("bigint", "NO"),
        "last_error_text": ("text", "NO"),
        "recurrence_key": ("text", "YES"),
        "occurrence_at": ("double precision", "YES"),
        "lease_token": ("text", "NO"),
        "lease_expires_at": ("double precision", "NO"),
        "created_at": ("double precision", "NO"),
        "updated_at": ("double precision", "NO"),
    },
    _POLLS_TABLE: {
        "tenant_id": ("text", "NO"),
        "queue_name": ("text", "NO"),
        "poll_key": ("text", "NO"),
        "next_due_at": ("double precision", "NO"),
        "lease_epoch": ("bigint", "NO"),
        "lease_token": ("text", "NO"),
        "lease_expires_at": ("double precision", "NO"),
        "updated_at": ("double precision", "NO"),
    },
    _DISPATCH_TABLE: {
        "tenant_id": ("text", "NO"),
        "queue_name": ("text", "NO"),
        "message_id": ("text", "NO"),
        "nonce_hash": ("text", "NO"),
        "expires_at": ("double precision", "NO"),
        "claimed_at": ("double precision", "NO"),
    },
}
_COLUMN_DEFAULTS = {
    _JOBS_TABLE: {
        "status": "'pending'",
        "attempt_epoch": "0",
        "last_error_text": "''",
        "lease_token": "''",
        "lease_expires_at": "-1",
    },
    _POLLS_TABLE: {},
    _DISPATCH_TABLE: {},
}
_IDENTITY_COLUMNS = {_JOBS_TABLE: {"id": "BY DEFAULT"}}
_BUILTIN_UDT_NAMES = {
    "bigint": "int8",
    "double precision": "float8",
    "text": "text",
}
_PRIMARY_KEYS = {
    _JOBS_TABLE: ("id",),
    _POLLS_TABLE: ("tenant_id", "queue_name", "poll_key"),
    _DISPATCH_TABLE: ("tenant_id", "queue_name", "message_id"),
}
_CHECK_EXPRESSIONS = {
    _JOBS_TABLE: {
        "mvk_job_tenant_bound": "char_length(tenant_id) BETWEEN 1 AND 201",
        "mvk_job_queue_bound": "queue_name ~ '^[a-z0-9][a-z0-9_.-]{0,127}$'",
        "mvk_job_kind_bound": "octet_length(kind) BETWEEN 1 AND 200",
        "mvk_job_payload_bound": (
            f"octet_length(payload_text) BETWEEN 1 AND {_MAX_PAYLOAD_BYTES} "
            "AND payload_text ~ '^MVKAR1:[A-Za-z0-9+/]+={0,2}$'"
        ),
        "mvk_job_status_bound": (
            "status IN ('pending','running','done','failed')"
        ),
        "mvk_job_attempt_bound": (
            "attempt_epoch BETWEEN 0 AND 9223372036854775807"
        ),
        "mvk_job_error_bound": (
            "last_error_text = '' OR ("
            f"octet_length(last_error_text) <= {_MAX_ERROR_BYTES} AND "
            "last_error_text ~ '^MVKAR1:[A-Za-z0-9+/]+={0,2}$')"
        ),
        "mvk_job_recurrence_bound": (
            "(recurrence_key IS NULL AND occurrence_at IS NULL) OR ("
            "recurrence_key ~ '^[0-9a-f]{64}$' AND "
            f"occurrence_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})"
        ),
        "mvk_job_lease_bound": (
            "(status = 'running' AND lease_token ~ '^[0-9a-f]{32}$' AND "
            f"lease_expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}) OR ("
            "status <> 'running' AND lease_token = '' AND lease_expires_at = -1)"
        ),
        "mvk_job_time_bound": (
            f"run_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"created_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}"
        ),
    },
    _POLLS_TABLE: {
        "mvk_poll_tenant_bound": "char_length(tenant_id) BETWEEN 1 AND 201",
        "mvk_poll_queue_bound": "queue_name ~ '^[a-z0-9][a-z0-9_.-]{0,127}$'",
        "mvk_poll_key_bound": "octet_length(poll_key) BETWEEN 1 AND 512",
        "mvk_poll_epoch_bound": (
            "lease_epoch BETWEEN 1 AND 9223372036854775807"
        ),
        "mvk_poll_lease_bound": (
            "(lease_token = '' AND lease_expires_at = -1) OR ("
            "lease_token ~ '^[0-9a-f]{32}$' AND "
            f"lease_expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g})"
        ),
        "mvk_poll_time_bound": (
            f"next_due_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"updated_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}"
        ),
    },
    _DISPATCH_TABLE: {
        "mvk_dispatch_tenant_bound": "char_length(tenant_id) BETWEEN 1 AND 201",
        "mvk_dispatch_queue_bound": (
            "queue_name ~ '^[a-z0-9][a-z0-9_.-]{0,127}$'"
        ),
        "mvk_dispatch_message_bound": (
            "octet_length(message_id) BETWEEN 1 AND 512"
        ),
        "mvk_dispatch_nonce_bound": "nonce_hash ~ '^[0-9a-f]{64}$'",
        "mvk_dispatch_time_bound": (
            f"expires_at BETWEEN 0 AND {_MAX_TIMESTAMP:g} AND "
            f"claimed_at BETWEEN 0 AND {_MAX_TIMESTAMP:g}"
        ),
    },
}
_ALL_TABLE_PRIVILEGES = (
    "SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER",
)
_TABLE_PRIVILEGES = {
    _JOBS_TABLE: {"SELECT", "INSERT", "UPDATE", "DELETE"},
    _POLLS_TABLE: {"SELECT", "INSERT", "UPDATE"},
    # DELETE predicates and ON CONFLICT arbitration read row columns, so
    # Postgres also requires SELECT even though the API exposes no row listing.
    _DISPATCH_TABLE: {"SELECT", "INSERT", "DELETE"},
}
_ALL_SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
_JOBS_SEQUENCE = "job_queue_jobs_id_seq"
_INDEX_SPECS = {
    "idx_mvk_jobs_ready": (
        _JOBS_TABLE,
        ("tenant_id", "queue_name", "status", "run_at", "id"),
        False,
        "",
    ),
    "idx_mvk_jobs_terminal": (
        _JOBS_TABLE,
        ("tenant_id", "queue_name", "status", "updated_at"),
        False,
        "",
    ),
    "uq_mvk_jobs_occurrence": (
        _JOBS_TABLE,
        ("tenant_id", "queue_name", "recurrence_key", "occurrence_at"),
        True,
        "recurrence_keyisnotnull",
    ),
    "idx_mvk_polls_due": (
        _POLLS_TABLE,
        ("tenant_id", "queue_name", "next_due_at", "lease_expires_at"),
        False,
        "",
    ),
    "idx_mvk_dispatch_expiry": (
        _DISPATCH_TABLE,
        ("tenant_id", "queue_name", "expires_at"),
        False,
        "",
    ),
}


def _pg_array(value: object) -> tuple[str, ...]:
    if isinstance(value, (tuple, list)):
        return tuple(str(item).lower() for item in value)
    return tuple(
        item.strip().strip('"').lower()
        for item in str(value or "").strip("{}").split(",")
        if item.strip()
    )


def _strip_outer_parentheses(value: str) -> str:
    text = value.strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        quoted = False
        encloses_all = True
        index = 0
        while index < len(text):
            char = text[index]
            if char == "'":
                if quoted and index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                quoted = not quoted
            elif not quoted:
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0 and index != len(text) - 1:
                        encloses_all = False
                        break
            index += 1
        if not encloses_all or depth != 0 or quoted:
            break
        text = text[1:-1].strip()
    return text


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace('"', "")
    text = re.sub(
        r"::\s*(?:text|bigint|integer|double\s+precision)",
        "",
        text,
    )
    text = re.sub(r"'(-?\d+(?:\.\d+)?)'", r"\1", text)
    while True:
        simplified = re.sub(r"\(\s*(-?\d+(?:\.\d+)?)\s*\)", r"\1", text)
        if simplified == text:
            break
        text = simplified
    text = _strip_outer_parentheses(text)
    compact = "".join(text.split())
    numeric = re.fullmatch(r"'(-?\d+(?:\.\d+)?)'", compact)
    return numeric.group(1) if numeric else compact


def _columns_are_exact(rows: list[tuple[Any, ...]], table: str) -> bool:
    expected_shape = _COLUMN_SHAPES[table]
    if len(rows) != len(expected_shape):
        return False
    expected_defaults = _COLUMN_DEFAULTS[table]
    expected_identity = _IDENTITY_COLUMNS.get(table, {})
    for row, (name, shape) in zip(rows, expected_shape.items(), strict=True):
        if len(row) != 14:
            return False
        (
            actual_name,
            data_type,
            nullable,
            default,
            is_identity,
            identity_generation,
            is_generated,
            generation_expression,
            udt_schema,
            udt_name,
            domain_schema,
            domain_name,
            collation_schema,
            collation_name,
        ) = row
        generation = expected_identity.get(name)
        expected_type = shape[0]
        if (
            str(actual_name) != name
            or (str(data_type).lower(), str(nullable).upper()) != shape
            or _normalize_default(default)
            != _normalize_default(expected_defaults.get(name))
            or str(is_identity).upper() != ("YES" if generation else "NO")
            or (
                str(identity_generation).upper()
                if identity_generation is not None
                else None
            )
            != generation
            or str(is_generated).upper() != "NEVER"
            or generation_expression is not None
            # ``information_schema.data_type`` reports the underlying type for
            # a domain, so it is not sufficient to prove the actual column
            # type. A domain or explicit collation can inject constraints or
            # change text equality while preserving the old high-level shape;
            # on tenant_id that can weaken the RLS equality boundary.
            or str(udt_schema) != "pg_catalog"
            or str(udt_name) != _BUILTIN_UDT_NAMES[expected_type]
            or domain_schema is not None
            or domain_name is not None
            # PostgreSQL reports NULL here for the database-default collation.
            # Any named column collation is schema drift, even if its visible
            # name happens to be ``default``.
            or collation_schema is not None
            or collation_name is not None
        ):
            return False
    return True


def _replace_between(value: str) -> str:
    operand = r"(?:[a-z_][a-z0-9_]*|[a-z_][a-z0-9_]*\([^()]*\))"
    number = r"-?\d+(?:\.\d+)?"
    pattern = re.compile(
        rf"(?P<left>{operand})\s+between\s+(?P<low>{number})\s+and\s+"
        rf"(?P<high>{number})",
        re.IGNORECASE,
    )
    return pattern.sub(
        lambda match: (
            f"(({match.group('left')} >= {match.group('low')}) and "
            f"({match.group('left')} <= {match.group('high')}))"
        ),
        value,
    )


def _split_top_level(value: str, operator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quoted = False
    start = 0
    index = 0
    needle = operator.lower()
    while index < len(value):
        char = value[index]
        if char == "'":
            if quoted and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
            index += 1
            continue
        if not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0 and value[index:index + len(needle)].lower() == needle:
                before = value[index - 1] if index else " "
                after_index = index + len(needle)
                after = value[after_index] if after_index < len(value) else " "
                if not (before.isalnum() or before == "_") and not (
                    after.isalnum() or after == "_"
                ):
                    parts.append(value[start:index])
                    start = after_index
                    index = after_index
                    continue
        index += 1
    if parts:
        parts.append(value[start:])
    return parts


def _boolean_fingerprint(value: object) -> tuple[Any, ...] | str:
    text = str(value or "").strip().lower().replace('"', "")
    if text.startswith("check"):
        text = text[5:].strip()
    text = re.sub(
        r"::\s*(?:text|bigint|integer|double\s+precision)",
        "",
        text,
    )
    text = re.sub(r"'(-?\d+(?:\.\d+)?)'", r"\1", text)
    while True:
        simplified = re.sub(r"\(\s*(-?\d+(?:\.\d+)?)\s*\)", r"\1", text)
        if simplified == text:
            break
        text = simplified
    text = re.sub(
        r"\b([a-z_][a-z0-9_]*)\s*=\s*any\s*"
        r"\(\s*array\s*\[(.*?)\]\s*\)",
        r"\1 in (\2)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = _strip_outer_parentheses(_replace_between(text))

    def parse(expression: str) -> tuple[Any, ...] | str:
        expression = _strip_outer_parentheses(expression)
        for operator in ("or", "and"):
            parts = _split_top_level(expression, operator)
            if parts:
                children: list[tuple[Any, ...] | str] = []
                for part in parts:
                    child = parse(part)
                    if isinstance(child, tuple) and child and child[0] == operator:
                        children.extend(child[1:])
                    else:
                        children.append(child)
                return (operator, *children)
        return "".join(expression.split())

    return parse(text)


def _check_constraints_are_exact(
    rows: list[tuple[Any, ...]],
    table: str,
) -> bool:
    expected = _CHECK_EXPRESSIONS[table]
    if len(rows) != len(expected):
        return False
    actual: dict[str, tuple[Any, ...] | str] = {}
    for row in rows:
        if len(row) != 7:
            return False
        (
            name,
            definition,
            validated,
            enforced,
            no_inherit,
            deferrable,
            deferred,
        ) = row
        if (
            not bool(validated)
            or not bool(enforced)
            or bool(no_inherit)
            or bool(deferrable)
            or bool(deferred)
        ):
            return False
        actual[str(name)] = _boolean_fingerprint(definition)
    return set(actual) == set(expected) and all(
        actual[name] == _boolean_fingerprint(definition)
        for name, definition in expected.items()
    )


def _not_null_constraints_are_safe(
    rows: list[tuple[Any, ...]],
    table: str,
) -> bool:
    """Accept version-specific pg_constraint rows only when semantically exact.

    PostgreSQL 18 began storing column NOT NULL specifications in
    ``pg_constraint``; older supported releases normally return no table rows
    of type ``n``. Column nullability is already pinned through
    ``information_schema``. Here we additionally reject an unvalidated,
    unenforced, NO-INHERIT/deferrable, multi-column, duplicate, or nullable-
    column entry without making one catalog representation mandatory.
    """
    non_nullable = {
        name for name, (_data_type, nullable) in _COLUMN_SHAPES[table].items()
        if nullable == "NO"
    }
    seen: set[str] = set()
    for row in rows:
        if len(row) != 7:
            return False
        _, columns, validated, enforced, no_inherit, deferrable, deferred = row
        names = _pg_array(columns)
        if (
            len(names) != 1
            or names[0] not in non_nullable
            or names[0] in seen
            or not bool(validated)
            or not bool(enforced)
            or bool(no_inherit)
            or bool(deferrable)
            or bool(deferred)
        ):
            return False
        seen.add(names[0])
    return True


def _table_access_owner(cur: Any, table: str) -> str | None:
    privilege_columns = ", ".join(
        "has_table_privilege(current_user, c.oid, "
        f"'{privilege}')"
        for privilege in _ALL_TABLE_PRIVILEGES
    )
    grant_columns = ", ".join(
        "has_table_privilege(current_user, c.oid, "
        f"'{privilege} WITH GRANT OPTION')"
        for privilege in _ALL_TABLE_PRIVILEGES
    )
    cur.execute(
        "SELECT c.relowner::regrole::text, "
        "c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
        "pg_has_role(current_user, c.relowner, 'MEMBER'), c.relkind, "
        "c.relpersistence, "
        "NOT c.relhassubclass, "
        "NOT EXISTS (SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid), "
        "NOT EXISTS (SELECT 1 FROM pg_rewrite rw WHERE rw.ev_class=c.oid), "
        "NOT EXISTS (SELECT 1 FROM pg_inherits inh "
        "WHERE inh.inhrelid=c.oid OR inh.inhparent=c.oid), "
        + privilege_columns
        + ", "
        + grant_columns
        + " FROM pg_class c WHERE c.oid="
        "to_regclass(current_schema() || '.' || %s)",
        (table,),
    )
    row = cur.fetchone()
    if (
        row is None
        or len(row) != 9 + 2 * len(_ALL_TABLE_PRIVILEGES)
        or not isinstance(row[0], str)
        or not row[0]
        or bool(row[1])
        or bool(row[2])
        or str(row[3]) != "r"
        or str(row[4]) != "p"
        # Queue relations have no legitimate subclass flag, trigger,
        # rewrite-rule, parent, or child behavior. Any of those can change the
        # rows seen or effects produced by otherwise-correct DML while every
        # column/index/ACL below still looks exact. The catalog checks are
        # available on every supported PostgreSQL release (14-18).
        or not all(bool(row[index]) for index in range(5, 9))
    ):
        return None
    offset = 9
    granted = {
        privilege
        for index, privilege in enumerate(_ALL_TABLE_PRIVILEGES)
        if bool(row[offset + index])
    }
    if granted != _TABLE_PRIVILEGES[table] or any(
        bool(row[offset + len(_ALL_TABLE_PRIVILEGES) + index])
        for index in range(len(_ALL_TABLE_PRIVILEGES))
    ):
        return None
    cur.execute(
        "SELECT privilege_type FROM pg_class c, LATERAL "
        "aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) acl "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s) "
        "AND acl.grantee=0",
        (table,),
    )
    if cur.fetchall():
        return None
    cur.execute(
        "SELECT 1 FROM pg_attribute WHERE attrelid="
        "to_regclass(current_schema() || '.' || %s) AND attnum > 0 "
        "AND NOT attisdropped AND attacl IS NOT NULL LIMIT 1",
        (table,),
    )
    if cur.fetchone() is not None:
        return None
    return str(row[0])


def _sequence_access_is_exact(cur: Any, owner: str) -> bool:
    privilege_columns = ", ".join(
        "has_sequence_privilege(current_user, c.oid, "
        f"'{privilege}')"
        for privilege in _ALL_SEQUENCE_PRIVILEGES
    )
    grant_columns = ", ".join(
        "has_sequence_privilege(current_user, c.oid, "
        f"'{privilege} WITH GRANT OPTION')"
        for privilege in _ALL_SEQUENCE_PRIVILEGES
    )
    cur.execute(
        "SELECT c.relowner::regrole::text, "
        "c.relowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
        "pg_has_role(current_user, c.relowner, 'MEMBER'), c.relkind, "
        "c.relpersistence, n.nspname=current_schema(), "
        + privilege_columns
        + ", "
        + grant_columns
        + " FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s)",
        (_JOBS_SEQUENCE,),
    )
    row = cur.fetchone()
    offset = 6
    if (
        row is None
        or len(row) != offset + 2 * len(_ALL_SEQUENCE_PRIVILEGES)
        or str(row[0]) != owner
        or bool(row[1])
        or bool(row[2])
        or str(row[3]) != "S"
        or str(row[4]) != "p"
        or not bool(row[5])
    ):
        return False
    granted = {
        privilege
        for index, privilege in enumerate(_ALL_SEQUENCE_PRIVILEGES)
        if bool(row[offset + index])
    }
    if granted != {"USAGE"} or any(
        bool(row[offset + len(_ALL_SEQUENCE_PRIVILEGES) + index])
        for index in range(len(_ALL_SEQUENCE_PRIVILEGES))
    ):
        return False
    cur.execute(
        "SELECT privilege_type FROM pg_class c, LATERAL "
        "aclexplode(COALESCE(c.relacl, acldefault('S', c.relowner))) acl "
        "WHERE c.oid=to_regclass(current_schema() || '.' || %s) "
        "AND acl.grantee=0",
        (_JOBS_SEQUENCE,),
    )
    if cur.fetchall():
        return False
    cur.execute(
        "SELECT data_type, start_value, min_value, max_value, increment_by, "
        "cycle, cache_size FROM pg_sequences WHERE schemaname=current_schema() "
        "AND sequencename=%s",
        (_JOBS_SEQUENCE,),
    )
    properties = cur.fetchone()
    return bool(
        properties is not None
        and tuple(properties[:5])
        == ("bigint", 1, 1, 9223372036854775807, 1)
        and not bool(properties[5])
        and int(properties[6]) == 1
    )


def _policy_rows_are_exact(rows: list[tuple[Any, ...]]) -> bool:
    if len(rows) != 1:
        return False
    name, permissive, roles, command, qual, with_check = rows[0]
    expected = "".join(
        "tenant_id = nullif(current_setting('maverick.tenant', true), '')"
        .lower().split()
    ).replace("::text", "")

    def exact(value: object) -> bool:
        compact = "".join(str(value or "").lower().split()).replace("::text", "")
        return compact in {expected, f"({expected})"}

    return (
        str(name) == _POLICY
        and str(permissive).upper() == "PERMISSIVE"
        and _pg_array(roles) == ("public",)
        and str(command).upper() == "ALL"
        and exact(qual)
        and exact(with_check)
    )


def _indexes_are_exact(
    rows: list[tuple[Any, ...]],
    *,
    owner: str | None = None,
) -> bool:
    if len(rows) != len(_INDEX_SPECS) or any(len(row) != 9 for row in rows):
        return False
    definitions: dict[str, str] = {}
    for row in rows:
        (
            name,
            definition,
            valid,
            ready,
            live,
            immediate,
            relkind,
            persistence,
            index_owner,
        ) = row
        if (
            not bool(valid)
            or not bool(ready)
            or not bool(live)
            or not bool(immediate)
            or str(relkind) != "i"
            or str(persistence) != "p"
            or not isinstance(index_owner, str)
            or not index_owner
            or (owner is not None and index_owner != owner)
        ):
            return False
        definitions[str(name)] = str(definition)
    if set(definitions) != set(_INDEX_SPECS):
        return False
    for name, (table, expected_columns, unique, predicate) in _INDEX_SPECS.items():
        definition = " ".join(definitions[name].lower().replace('"', "").split())
        prefix = (
            f"create unique index {name} " if unique else f"create index {name} "
        )
        if (
            not definition.startswith(prefix)
            or (
                f" {table} " not in definition
                and f".{table} " not in definition
            )
        ):
            return False
        marker = " using btree ("
        start = definition.find(marker)
        if start < 0:
            # Some catalog renderers omit ``USING btree`` for the default.
            marker = " ("
            start = definition.find(marker)
        if start < 0:
            return False
        relation = definition[len(prefix):start].strip()
        if not relation.startswith("on "):
            return False
        relation = relation[3:].strip()
        if not (
            relation == table
            or (relation.endswith(f".{table}") and " " not in relation)
        ):
            return False
        start += len(marker)
        end = definition.find(")", start)
        if end < 0:
            return False
        columns = tuple(
            part.strip()
            for part in definition[start:end].split(",")
        )
        if columns != expected_columns:
            return False
        tail = "".join(definition[end + 1:].split()).replace("(", "").replace(")", "")
        if tail and not tail.startswith("where"):
            return False
        actual_predicate = tail.removeprefix("where")
        if actual_predicate != predicate:
            return False
    return True


_MUTATING_SQL = re.compile(
    r"^\s*(?:INSERT|UPDATE|DELETE|MERGE|TRUNCATE|COPY|CREATE|ALTER|DROP|GRANT|REVOKE|WITH)\b",
    re.IGNORECASE,
)


class _AuthorityBoundCursor:
    def __init__(self, cursor: Any, validate: Callable[[], None]) -> None:
        self._cursor = cursor
        self._validate = validate
        self._failed = False

    def execute(self, sql: str, params: Any = ()) -> Any:
        if self._failed:
            raise SharedJobQueueUnavailable(
                "shared job authority transaction was marked for rollback"
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
            raise SharedJobQueueUnavailable(
                "shared job authority transaction was marked for rollback"
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


def _is_injected_test_queue(
    *,
    dsn: object,
    transaction_factory: Callable[[], Any] | None,
    legacy_db_path: str | Path | None,
    legacy_poll_artifacts: Sequence[str | Path],
    check_legacy: bool,
    ensure_schema: bool,
    verify_schema: bool,
    require_sealing: bool,
    cutover_fence: _CutoverFence | None,
) -> bool:
    """Recognize the deliberately guard-free in-memory SQL model seam.

    A transaction factory is not a production connection selector: its backing
    database cannot be bound to the configured DSN. Keep it available only to
    deterministic unit tests that explicitly disable every production guard
    and supply no legacy/cutover state.
    """
    return bool(
        transaction_factory is not None
        and not dsn
        and legacy_db_path is None
        and not legacy_poll_artifacts
        and not check_legacy
        and not ensure_schema
        and not verify_schema
        and not require_sealing
        and cutover_fence is None
    )


def _validate_direct_dsn_admission(
    *,
    dsn: object,
    transaction_factory: Callable[[], Any] | None,
    legacy_db_path: str | Path | None,
    legacy_poll_artifacts: Sequence[str | Path],
    check_legacy: bool,
    ensure_schema: bool,
    verify_schema: bool,
    require_sealing: bool,
    authority_token: _AuthorityToken | None,
    cutover_fence: _CutoverFence | None,
) -> None:
    """Bind a real connection to the selector and an active cutover gate."""
    if not isinstance(dsn, str) or not dsn:
        raise SharedJobQueueUnavailable(
            "Postgres job authority has no configured connection"
        )
    if transaction_factory is not None:
        raise SharedJobQueueUnavailable(
            "a real Postgres job DSN cannot use an injected transaction factory"
        )
    if not isinstance(authority_token, _AuthorityToken):
        raise SharedJobQueueUnavailable(
            "direct Postgres job construction requires a selector authority token"
        )
    try:
        selection = _selection()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        raise SharedJobQueueUnavailable(
            "Postgres job deployment authority cannot be validated"
        ) from None
    if not _authority_matches(authority_token, selection):
        raise SharedJobQueueUnavailable(
            "Postgres job authority token does not match deployment policy"
        )
    dsn_sha256 = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(authority_token.dsn_sha256, dsn_sha256):
        raise SharedJobQueueUnavailable(
            "Postgres job DSN does not match the selected deployment authority"
        )
    if check_legacy or not (ensure_schema and verify_schema and require_sealing):
        raise SharedJobQueueUnavailable(
            "a real Postgres job authority requires all production admission guards"
        )
    active_fence = _active_cutover_fence()
    if cutover_fence is None or active_fence is not cutover_fence:
        raise SharedJobQueueUnavailable(
            "direct Postgres job construction requires the active cutover protocol"
        )
    try:
        legacy = (
            _canonical_legacy_path(legacy_db_path)
            if legacy_db_path is not None
            else None
        )
        artifacts = tuple(
            _canonical_legacy_path(item) for item in legacy_poll_artifacts
        )
    except Exception:
        raise SharedJobQueueUnavailable(
            "Postgres job cutover authority paths are invalid"
        ) from None
    if (
        legacy is None
        or cutover_fence.legacy_db_path != legacy
        or cutover_fence.marker_path != _cutover_marker_path(legacy)
        or cutover_fence.poll_artifacts != artifacts
        or not hmac.compare_digest(cutover_fence.dsn_sha256, dsn_sha256)
    ):
        raise SharedJobQueueUnavailable(
            "Postgres job construction does not match the active cutover fence"
        )


class PostgresJobQueue:
    """Tenant-bound, fleet-shared implementation of the ``JobQueue`` API."""

    is_shared = True

    def __init__(
        self,
        *,
        namespace: str,
        dsn: str = "",
        transaction_factory: Callable[[], Any] | None = None,
        legacy_db_path: str | Path | None = None,
        legacy_poll_artifacts: Sequence[str | Path] = (),
        check_legacy: bool = True,
        ensure_schema: bool = True,
        verify_schema: bool = True,
        require_sealing: bool = True,
        authority_token: _AuthorityToken | None = None,
        cutover_fence: _CutoverFence | None = None,
        authority_tenant: str | None = None,
        allow_tenant_switch: bool = False,
    ) -> None:
        if not isinstance(namespace, str) or not _QUEUE_RE.fullmatch(namespace):
            raise ValueError("queue namespace is invalid")
        injected_test_queue = _is_injected_test_queue(
            dsn=dsn,
            transaction_factory=transaction_factory,
            legacy_db_path=legacy_db_path,
            legacy_poll_artifacts=legacy_poll_artifacts,
            check_legacy=check_legacy,
            ensure_schema=ensure_schema,
            verify_schema=verify_schema,
            require_sealing=require_sealing,
            cutover_fence=cutover_fence,
        )
        if dsn:
            _validate_direct_dsn_admission(
                dsn=dsn,
                transaction_factory=transaction_factory,
                legacy_db_path=legacy_db_path,
                legacy_poll_artifacts=legacy_poll_artifacts,
                check_legacy=check_legacy,
                ensure_schema=ensure_schema,
                verify_schema=verify_schema,
                require_sealing=require_sealing,
                authority_token=authority_token,
                cutover_fence=cutover_fence,
            )
        elif transaction_factory is not None and not injected_test_queue:
            raise SharedJobQueueUnavailable(
                "injected Postgres job transactions are available only to the "
                "all-guards-off unit-test seam"
            )
        self.namespace = namespace
        self.db_path = f"postgres:{namespace}"
        self._legacy_db_path = Path(legacy_db_path) if legacy_db_path else None
        self._legacy_poll_artifacts = tuple(Path(p) for p in legacy_poll_artifacts)
        self._check_legacy = bool(check_legacy)
        self._verify_schema = bool(verify_schema)
        self._require_sealing = bool(require_sealing)
        self._authority_token = authority_token
        self._cutover_fence = cutover_fence
        self._allow_tenant_switch = bool(allow_tenant_switch)
        # Explicit wall-clock values make deterministic transition tests useful,
        # but they must never become an escape hatch in a real shared queue.
        # Only the deliberately stripped-down injected-cursor test shape may
        # supply ``now=``; production obtains every lease decision from the
        # Postgres transaction clock shared by all replicas.
        self._test_time_override = bool(
            injected_test_queue and authority_token is None
        )
        self._schema_ready = False
        self._schema_lock = threading.Lock()
        self._identity_lock = threading.Lock()
        self._database_identity: tuple[str, str, str] | None = None
        self._bound_tenant = self._tenant_key(authority_tenant)
        if self._check_legacy:
            self._assert_legacy_clear()
        self._probe_sealing()
        if transaction_factory is None:
            if not dsn:
                raise SharedJobQueueUnavailable(
                    "Postgres job authority has no configured connection"
                )
            try:
                import psycopg
            except ImportError:
                raise SharedJobQueueUnavailable(
                    "Postgres job authority requires maverick-core[postgres]"
                ) from None

            @contextlib.contextmanager
            def direct_transaction() -> Iterator[Any]:
                connection = cursor = None
                try:
                    connection = psycopg.connect(dsn, autocommit=False)
                    cursor = connection.cursor()
                    yield cursor
                    connection.commit()
                except BaseException:
                    if connection is not None:
                        with contextlib.suppress(Exception):
                            connection.rollback()
                    raise
                finally:
                    if cursor is not None:
                        with contextlib.suppress(Exception):
                            cursor.close()
                    if connection is not None:
                        with contextlib.suppress(Exception):
                            connection.close()

            transaction_factory = direct_transaction
        self._transaction_factory = transaction_factory
        if ensure_schema:
            self._ensure_schema()

    @staticmethod
    def _tenant_key(explicit: str | None = None) -> str:
        try:
            from .paths import canonical_tenant_id, current_tenant_id_strict

            tenant = explicit if explicit is not None else current_tenant_id_strict()
            if tenant is None:
                raise SharedJobQueueUnavailable(
                    "shared job authority requires an active tenant"
                )
            if not isinstance(tenant, str) or not tenant:
                raise SharedJobQueueUnavailable(
                    "shared job authority requires a deployment tenant"
                )
            return canonical_tenant_id(tenant)
        except SharedJobQueueUnavailable:
            raise
        except Exception:
            raise SharedJobQueueUnavailable(
                "shared job tenant binding is unavailable"
            ) from None

    def _assert_tenant(self) -> None:
        if self._allow_tenant_switch:
            # The dashboard automation namespace is a deployment-scoped
            # service queue. Its handlers deliberately pin each payload's
            # tenant while the queue row itself stays under the immutable
            # deployment authority tenant supplied at construction.
            return
        if not hmac.compare_digest(self._bound_tenant, self._tenant_key()):
            raise SharedJobQueueUnavailable(
                "shared job tenant changed during an authority operation"
            )

    def assert_authority_current(self) -> None:
        token = self._authority_token
        if token is None:
            self._assert_tenant()
            return
        try:
            selection = _selection()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            raise SharedJobQueueUnavailable(
                "shared job deployment authority cannot be revalidated"
            ) from None
        if not _authority_matches(token, selection):
            raise SharedJobQueueUnavailable(
                "shared job deployment authority changed after construction"
            )
        self._assert_tenant()

    def _assert_legacy_clear(self) -> None:
        if self._cutover_fence is not None:
            _assert_postgres_cutover_fence(self._cutover_fence)
        elif self._legacy_db_path is not None:
            assert_no_legacy_job_authority(
                self._legacy_db_path,
                poll_artifacts=self._legacy_poll_artifacts,
            )
        elif self._legacy_poll_artifacts:
            _assert_no_poll_artifacts(self._legacy_poll_artifacts)

    def _probe_sealing(self) -> None:
        if not self._require_sealing:
            return
        try:
            from .crypto_at_rest import at_rest_enabled

            if not at_rest_enabled():
                raise ValueError("at-rest encryption is disabled")
        except Exception:
            raise SharedJobQueueUnavailable(
                "shared job authority requires application at-rest encryption"
            ) from None

    def _seal(self, text: str, *, limit: int) -> str:
        raw = text.encode("utf-8")
        if len(raw) > limit:
            raise ValueError("job authority value exceeds its byte limit")
        if not self._require_sealing:
            return text
        from .paths import reset_tenant, set_tenant

        tenant_token = set_tenant(self._bound_tenant)
        try:
            from .crypto_at_rest import is_sealed_str, seal_to_str

            stored = seal_to_str(text)
            if not is_sealed_str(stored) or len(stored.encode("utf-8")) > limit:
                raise ValueError("sealed job authority value is invalid")
            return stored
        except Exception:
            raise SharedJobQueueUnavailable(
                "shared job authority could not seal its payload"
            ) from None
        finally:
            reset_tenant(tenant_token)

    def _unseal(self, stored: str) -> str:
        if not self._require_sealing:
            return stored
        from .paths import reset_tenant, set_tenant

        tenant_token = set_tenant(self._bound_tenant)
        try:
            from .crypto_at_rest import is_sealed_str, unseal_from_str

            if not is_sealed_str(stored):
                raise ValueError("unsealed shared job payload")
            return unseal_from_str(stored)
        except Exception:
            raise SharedJobQueueUnavailable(
                "shared job authority could not open its payload"
            ) from None
        finally:
            reset_tenant(tenant_token)

    def _encode_payload(self, payload: dict | None) -> str:
        raw = json.dumps(
            payload or {},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
        return self._seal(raw, limit=_MAX_PAYLOAD_BYTES)

    def _decode_payload(self, stored: str) -> dict:
        try:
            payload = json.loads(self._unseal(stored))
        except SharedJobQueueUnavailable:
            raise
        except (TypeError, ValueError):
            raise SharedJobQueueUnavailable(
                "shared job payload is malformed"
            ) from None
        return payload if isinstance(payload, dict) else {"_": payload}

    @staticmethod
    def _finite_time(value: float | int, name: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed < 0 or parsed > _MAX_TIMESTAMP:
            raise ValueError(f"{name} must be a finite supported timestamp")
        return parsed

    @staticmethod
    def _duration(value: float | int, name: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed) or parsed <= 0:
            raise ValueError(f"{name} must be positive and finite")
        return parsed

    def _transaction_time(self, cur: Any, explicit: float | None = None) -> float:
        """Return the database transaction's epoch, with one isolated test seam.

        Lease acquisition, renewal, expiry, and due eligibility must share one
        clock.  A node's wall clock can jump or differ from a peer's, while
        ``transaction_timestamp()`` is authoritative and stable for the whole
        Postgres transaction.  Deterministic model tests may inject ``now=``
        only when the queue was constructed with every production guard off.
        """
        if explicit is not None:
            if not self._test_time_override:
                raise ValueError(
                    "explicit shared job time is available only to isolated tests"
                )
            return self._finite_time(explicit, "test transaction time")
        cur.execute("SELECT EXTRACT(EPOCH FROM transaction_timestamp())")
        row = cur.fetchone()
        if not row:
            raise SharedJobQueueUnavailable(
                "Postgres job authority returned no transaction time"
            )
        try:
            value = row[0]
        except (KeyError, IndexError, TypeError):
            try:
                value = row["transaction_time"]
            except (KeyError, TypeError):
                raise SharedJobQueueUnavailable(
                    "Postgres job authority returned an invalid transaction time"
                ) from None
        try:
            return self._finite_time(value, "database transaction time")
        except (TypeError, ValueError, OverflowError):
            raise SharedJobQueueUnavailable(
                "Postgres job authority returned an invalid transaction time"
            ) from None

    def authority_time(self) -> float:
        """Snapshot the shared clock for backend-neutral one-shot drains."""
        with self._transaction() as cur:
            return self._transaction_time(cur)

    def _schema_status_on(self, cur: Any) -> str:  # noqa: C901 - catalog gate
        if not self._verify_schema:
            return "ready"
        cur.execute(
            "SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, "
            "r.rolcreatedb, r.rolreplication, "
            "has_database_privilege(current_user, current_database(), 'CREATE'), "
            "has_database_privilege(current_user, current_database(), 'TEMP') "
            "FROM pg_roles r WHERE r.rolname=current_user"
        )
        role = cur.fetchone()
        if not role or len(role) != 7 or any(bool(item) for item in role):
            return "incompatible"
        cur.execute("SELECT current_database(), current_schema(), current_user")
        identity_row = cur.fetchone()
        if (
            identity_row is None
            or len(identity_row) != 3
            or not all(isinstance(item, str) and item for item in identity_row)
        ):
            return "incompatible"
        identity = tuple(str(item) for item in identity_row)
        with self._identity_lock:
            if self._database_identity is None:
                self._database_identity = identity
            elif self._database_identity != identity:
                return "incompatible"
        cur.execute(
            "SELECT n.nspowner=(SELECT oid FROM pg_roles WHERE rolname=current_user), "
            "pg_has_role(current_user, n.nspowner, 'MEMBER'), "
            "has_schema_privilege(current_user, n.oid, 'USAGE'), "
            "has_schema_privilege(current_user, current_schema(), 'CREATE') "
            "FROM pg_namespace n WHERE n.nspname=current_schema()"
        )
        schema_access = cur.fetchone()
        if not (
            schema_access is not None
            and len(schema_access) == 4
            and not bool(schema_access[0])
            and not bool(schema_access[1])
            and bool(schema_access[2])
            and not bool(schema_access[3])
        ):
            return "incompatible"
        owners: set[str] = set()
        for table in _TABLES:
            cur.execute("SELECT to_regclass(current_schema() || '.' || %s)", (table,))
            row = cur.fetchone()
            if not row or row[0] is None:
                return "missing"
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default, "
                "is_identity, identity_generation, is_generated, "
                "generation_expression, udt_schema, udt_name, domain_schema, "
                "domain_name, collation_schema, collation_name "
                "FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=%s "
                "ORDER BY ordinal_position",
                (table,),
            )
            if not _columns_are_exact(list(cur.fetchall()), table):
                return "incompatible"
            owner = _table_access_owner(cur, table)
            if owner is None:
                return "incompatible"
            owners.add(owner)
            cur.execute(
                "SELECT array_agg(a.attname ORDER BY keys.ordinality), "
                "i.indisvalid, i.indisready, i.indislive, i.indimmediate, "
                "ic.relkind, ic.relpersistence, ic.relowner::regrole::text "
                "FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid "
                "JOIN pg_class ic ON ic.oid=i.indexrelid "
                "JOIN unnest(i.indkey) WITH ORDINALITY AS keys(attnum, ordinality) "
                "ON true JOIN pg_attribute a ON a.attrelid=c.oid "
                "AND a.attnum=keys.attnum WHERE c.oid=%s::regclass "
                "AND i.indisprimary GROUP BY i.indexrelid, i.indisvalid, "
                "i.indisready, i.indislive, i.indimmediate, ic.relkind, "
                "ic.relpersistence, ic.relowner",
                (table,),
            )
            primary = cur.fetchone()
            if not (
                primary is not None
                and len(primary) == 8
                and _pg_array(primary[0]) == _PRIMARY_KEYS[table]
                and all(bool(primary[index]) for index in range(1, 5))
                and str(primary[5]) == "i"
                and str(primary[6]) == "p"
                and str(primary[7]) == owner
            ):
                return "incompatible"
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid), convalidated, "
                "COALESCE((to_jsonb(pg_constraint)->>'conenforced')::boolean, true), "
                "condeferrable, condeferred FROM pg_constraint "
                "WHERE conrelid=%s::regclass AND contype='p'",
                (table,),
            )
            primary_constraints = list(cur.fetchall())
            compact_primary = (
                "primarykey(" + ",".join(_PRIMARY_KEYS[table]) + ")"
            )
            if not (
                len(primary_constraints) == 1
                and len(primary_constraints[0]) == 6
                and str(primary_constraints[0][0]) == f"{table}_pkey"
                and "".join(
                    str(primary_constraints[0][1]).lower().replace('"', "").split()
                ) == compact_primary
                and bool(primary_constraints[0][2])
                and bool(primary_constraints[0][3])
                and not bool(primary_constraints[0][4])
                and not bool(primary_constraints[0][5])
            ):
                return "incompatible"
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid), convalidated, "
                "COALESCE((to_jsonb(pg_constraint)->>'conenforced')::boolean, true), "
                "connoinherit, condeferrable, condeferred FROM pg_constraint "
                "WHERE conrelid=%s::regclass AND contype='c' ORDER BY conname",
                (table,),
            )
            if not _check_constraints_are_exact(list(cur.fetchall()), table):
                return "incompatible"
            cur.execute(
                "SELECT con.conname, (SELECT array_agg(a.attname "
                "ORDER BY keys.ordinality) FROM unnest(con.conkey) WITH ORDINALITY "
                "AS keys(attnum, ordinality) JOIN pg_attribute a "
                "ON a.attrelid=con.conrelid AND a.attnum=keys.attnum), "
                "con.convalidated, COALESCE((to_jsonb(con)->>'conenforced')::boolean, "
                "true), con.connoinherit, con.condeferrable, con.condeferred "
                "FROM pg_constraint con WHERE con.conrelid=%s::regclass "
                "AND con.contype='n' ORDER BY con.conname",
                (table,),
            )
            if not _not_null_constraints_are_safe(list(cur.fetchall()), table):
                return "incompatible"
            cur.execute(
                "SELECT 1 FROM pg_constraint WHERE conrelid=%s::regclass "
                "AND contype NOT IN ('c','n','p') LIMIT 1",
                (table,),
            )
            if cur.fetchone() is not None:
                return "incompatible"
            cur.execute(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid=%s::regclass",
                (table,),
            )
            flags = cur.fetchone()
            if not flags or not bool(flags[0]) or not bool(flags[1]):
                return "incompatible"
            cur.execute(
                "SELECT policyname, permissive, roles, cmd, qual, with_check "
                "FROM pg_policies WHERE schemaname=current_schema() "
                "AND tablename=%s ORDER BY policyname",
                (table,),
            )
            if not _policy_rows_are_exact(list(cur.fetchall())):
                return "incompatible"
        if len(owners) != 1:
            return "incompatible"
        owner = next(iter(owners))
        cur.execute(
            "SELECT ic.relname, pg_get_indexdef(i.indexrelid), i.indisvalid, "
            "i.indisready, i.indislive, i.indimmediate, ic.relkind, "
            "ic.relpersistence, ic.relowner::regrole::text FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_class ic ON ic.oid=i.indexrelid "
            "WHERE n.nspname=current_schema() AND c.relname=ANY(%s) "
            "AND NOT i.indisprimary ORDER BY ic.relname",
            (list(_TABLES),),
        )
        if not _indexes_are_exact(list(cur.fetchall()), owner=owner):
            return "incompatible"
        cur.execute(
            "SELECT to_regclass(pg_get_serial_sequence("
            "current_schema() || '.' || %s, 'id')) = "
            "to_regclass(current_schema() || '.' || %s)",
            (_JOBS_TABLE, _JOBS_SEQUENCE),
        )
        sequence_binding = cur.fetchone()
        if not sequence_binding or not bool(sequence_binding[0]):
            return "incompatible"
        if not _sequence_access_is_exact(cur, owner):
            return "incompatible"
        return "ready"

    def _schema_status(self) -> str:
        try:
            self.assert_authority_current()
            with self._transaction_factory() as cur:
                self.assert_authority_current()
                return self._schema_status_on(cur)
        except Exception:
            return "unavailable"

    def _ensure_schema(self) -> None:
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
                raise SharedJobQueueUnavailable(
                    "shared job schema or database role is incompatible"
                )
            if initial == "unavailable":
                raise SharedJobQueueUnavailable(
                    "shared job schema cannot be verified"
                )
            # Missing schema is a deployment migration error.  A runtime role
            # admitted by the exact ACL checks above must be incapable of DDL,
            # and must never acquire an owner advisory lock to repair itself.
            raise SharedJobQueueUnavailable(
                "shared job schema is missing; run migration v37 as the migration owner"
            )

    def _assert_transaction_binding(self) -> None:
        self.assert_authority_current()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[Any]:
        self.assert_authority_current()
        if self._check_legacy:
            self._assert_legacy_clear()
        self._probe_sealing()
        self._ensure_schema()
        try:
            with self._transaction_factory() as cur:
                self.assert_authority_current()
                if self._verify_schema and self._schema_status_on(cur) != "ready":
                    raise SharedJobQueueUnavailable(
                        "shared job schema drift was detected"
                    )
                cur.execute(
                    "SELECT set_config('maverick.tenant', %s, true)",
                    (self._bound_tenant,),
                )
                guarded: Any = cur
                if self._authority_token is not None:
                    guarded = _AuthorityBoundCursor(cur, self._assert_transaction_binding)
                yield guarded
                if isinstance(guarded, _AuthorityBoundCursor):
                    guarded.assert_healthy()
                self._assert_transaction_binding()
        except (
            JobQueueMigrationRequired,
            SharedJobQueueUnavailable,
            ValueError,
        ):
            raise
        except Exception as exc:
            log.error(
                "Postgres job authority transaction failed (%s)",
                type(exc).__name__,
            )
            raise SharedJobQueueUnavailable(
                "Postgres job authority is unavailable"
            ) from None

    @staticmethod
    def _validate_kind(kind: str) -> str:
        if not isinstance(kind, str) or not kind or len(kind.encode("utf-8")) > 200:
            raise ValueError("job kind must be a non-empty bounded string")
        return kind

    @staticmethod
    def _validate_job_id(job_id: int) -> int:
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
            raise ValueError("job id must be a positive integer")
        return job_id

    def enqueue(
        self,
        kind: str,
        payload: dict | None = None,
        *,
        run_at: float | None = None,
    ) -> int:
        requested_due = None if run_at is None else self._finite_time(run_at, "run_at")
        stored = self._encode_payload(payload)
        with self._transaction() as cur:
            now = self._transaction_time(cur)
            due = now if requested_due is None else requested_due
            cur.execute(
                f"INSERT INTO {_JOBS_TABLE} "
                "(tenant_id, queue_name, kind, payload_text, run_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (self._bound_tenant, self.namespace, self._validate_kind(kind),
                 stored, due, now, now),
            )
            row = cur.fetchone()
        if not row:
            raise SharedJobQueueUnavailable("shared job enqueue returned no identity")
        return int(row[0])

    def enqueue_recurring(
        self,
        kind: str,
        payload: dict,
        *,
        run_at: float,
    ) -> int:
        """Materialize one cron occurrence exactly once across all replicas."""
        due = self._finite_time(run_at, "run_at")
        identity = {
            key: value
            for key, value in (payload or {}).items()
            if key != "__rearmed__"
        }
        canonical = json.dumps(
            [self._validate_kind(kind), identity],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        )
        recurrence_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        stored = self._encode_payload(payload)
        with self._transaction() as cur:
            now = self._transaction_time(cur)
            cur.execute(
                f"INSERT INTO {_JOBS_TABLE} "
                "(tenant_id, queue_name, kind, payload_text, run_at, recurrence_key, "
                "occurrence_at, created_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, queue_name, recurrence_key, occurrence_at) "
                "WHERE recurrence_key IS NOT NULL DO NOTHING RETURNING id",
                (self._bound_tenant, self.namespace, kind, stored, due,
                 recurrence_key, due, now, now),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    f"SELECT id FROM {_JOBS_TABLE} WHERE tenant_id=%s AND queue_name=%s "
                    "AND recurrence_key=%s AND occurrence_at=%s",
                    (self._bound_tenant, self.namespace, recurrence_key, due),
                )
                row = cur.fetchone()
        if not row:
            raise SharedJobQueueUnavailable(
                "shared recurring occurrence could not be materialized"
            )
        return int(row[0])

    _RETURNING = (
        "id, kind, payload_text, run_at, status, attempt_epoch, "
        "last_error_text, created_at, updated_at, lease_token, lease_expires_at"
    )

    def _row_to_job(self, row: Any):
        from .job_queue import Job

        error = self._unseal(str(row[6])) if str(row[6] or "") else ""
        return Job(
            id=int(row[0]),
            kind=str(row[1]),
            payload=self._decode_payload(str(row[2])),
            run_at=float(row[3]),
            status=str(row[4]),
            attempts=int(row[5]),
            last_error=error,
            created_at=float(row[7]),
            updated_at=float(row[8]),
            lease_token=str(row[9] or ""),
            lease_expires_at=float(row[10]),
        )

    def claim(
        self,
        *,
        now: float | None = None,
        ready_at: float | None = None,
        lease_seconds: float = 3600.0,
    ):
        lease = self._duration(lease_seconds, "lease_seconds")
        token = uuid.uuid4().hex
        with self._transaction() as cur:
            claimed_at = self._transaction_time(cur, now)
            requested_cutoff = (
                claimed_at
                if ready_at is None
                else self._finite_time(ready_at, "ready_at")
            )
            # A caller-supplied drain snapshot may delay eligibility, but it can
            # never move the database's due boundary into the future.  Worker
            # obtains that snapshot through ``authority_time()`` in production.
            cutoff = min(requested_cutoff, claimed_at)
            expires = self._finite_time(claimed_at + lease, "lease_expires_at")
            cur.execute(
                f"WITH candidate AS (SELECT id FROM {_JOBS_TABLE} "
                "WHERE tenant_id=%s AND queue_name=%s AND status='pending' "
                "AND run_at <= %s ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1) "
                f"UPDATE {_JOBS_TABLE} AS jobs SET status='running', "
                "attempt_epoch=jobs.attempt_epoch+1, lease_token=%s, "
                "lease_expires_at=%s, updated_at=%s FROM candidate "
                "WHERE jobs.id=candidate.id AND jobs.tenant_id=%s "
                "AND jobs.queue_name=%s AND jobs.status='pending' "
                f"RETURNING jobs.{self._RETURNING.replace(', ', ', jobs.')}",
                (self._bound_tenant, self.namespace, cutoff, token, expires,
                 claimed_at, self._bound_tenant, self.namespace),
            )
            row = cur.fetchone()
        return self._row_to_job(row) if row else None

    @staticmethod
    def _fence(expected_attempts: int | None, expected_lease_token: str | None) -> tuple[int, str]:
        if (
            isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
            or expected_attempts < 1
            or not isinstance(expected_lease_token, str)
            or not _HEX_32_RE.fullmatch(expected_lease_token)
        ):
            raise ValueError("a valid attempt epoch and lease token are required")
        return expected_attempts, expected_lease_token

    def heartbeat(
        self,
        job_id: int,
        *,
        now: float | None = None,
        expected_attempts: int | None = None,
        expected_lease_token: str | None = None,
        lease_seconds: float = 3600.0,
    ) -> bool:
        epoch, token = self._fence(expected_attempts, expected_lease_token)
        lease = self._duration(lease_seconds, "lease_seconds")
        with self._transaction() as cur:
            heartbeat_at = self._transaction_time(cur, now)
            expires = self._finite_time(
                heartbeat_at + lease,
                "lease_expires_at",
            )
            cur.execute(
                f"UPDATE {_JOBS_TABLE} SET updated_at=%s, lease_expires_at=%s "
                "WHERE tenant_id=%s AND queue_name=%s AND id=%s "
                "AND status='running' AND attempt_epoch=%s AND lease_token=%s",
                (heartbeat_at, expires, self._bound_tenant, self.namespace,
                 self._validate_job_id(job_id), epoch, token),
            )
            return int(cur.rowcount) == 1

    def complete(
        self,
        job_id: int,
        *,
        expected_attempts: int | None = None,
        expected_lease_token: str | None = None,
    ) -> bool:
        epoch, token = self._fence(expected_attempts, expected_lease_token)
        with self._transaction() as cur:
            now = self._transaction_time(cur)
            cur.execute(
                f"UPDATE {_JOBS_TABLE} SET status='done', lease_token='', "
                "lease_expires_at=-1, updated_at=%s WHERE tenant_id=%s "
                "AND queue_name=%s AND id=%s AND status='running' "
                "AND attempt_epoch=%s AND lease_token=%s",
                (now, self._bound_tenant, self.namespace,
                 self._validate_job_id(job_id), epoch, token),
            )
            return int(cur.rowcount) == 1

    def set_payload(
        self,
        job_id: int,
        payload: dict,
        *,
        expected_attempts: int | None = None,
        expected_lease_token: str | None = None,
    ) -> bool:
        epoch, token = self._fence(expected_attempts, expected_lease_token)
        stored = self._encode_payload(payload)
        with self._transaction() as cur:
            now = self._transaction_time(cur)
            cur.execute(
                f"UPDATE {_JOBS_TABLE} SET payload_text=%s, updated_at=%s "
                "WHERE tenant_id=%s AND queue_name=%s AND id=%s "
                "AND status='running' AND attempt_epoch=%s AND lease_token=%s",
                (stored, now, self._bound_tenant, self.namespace,
                 self._validate_job_id(job_id), epoch, token),
            )
            return int(cur.rowcount) == 1

    def fail(
        self,
        job_id: int,
        error: str,
        *,
        retry_after: float | None = 60.0,
        max_attempts: int = 5,
        expected_attempts: int | None = None,
        expected_lease_token: str | None = None,
    ) -> bool:
        epoch, token = self._fence(expected_attempts, expected_lease_token)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 0:
            raise ValueError("max_attempts must be a non-negative integer")
        stored_error = self._seal(str(error)[:16_000], limit=_MAX_ERROR_BYTES)
        retrying = retry_after is not None and epoch < max_attempts
        delay: float | None = None
        if retrying:
            delay = float(retry_after)
            if not math.isfinite(delay) or delay < 0:
                raise ValueError("retry_after must be non-negative and finite")
        with self._transaction() as cur:
            now = self._transaction_time(cur)
            next_run = (
                self._finite_time(now + delay, "retry run_at")
                if delay is not None
                else None
            )
            status = "pending" if next_run is not None else "failed"
            if next_run is None:
                cur.execute(
                    f"UPDATE {_JOBS_TABLE} SET status=%s, last_error_text=%s, "
                    "lease_token='', lease_expires_at=-1, updated_at=%s "
                    "WHERE tenant_id=%s AND queue_name=%s AND id=%s "
                    "AND status='running' AND attempt_epoch=%s AND lease_token=%s",
                    (status, stored_error, now, self._bound_tenant, self.namespace,
                     self._validate_job_id(job_id), epoch, token),
                )
            else:
                cur.execute(
                    f"UPDATE {_JOBS_TABLE} SET status=%s, run_at=%s, "
                    "last_error_text=%s, lease_token='', lease_expires_at=-1, "
                    "updated_at=%s WHERE tenant_id=%s AND queue_name=%s AND id=%s "
                    "AND status='running' AND attempt_epoch=%s AND lease_token=%s",
                    (status, next_run, stored_error, now, self._bound_tenant,
                     self.namespace, self._validate_job_id(job_id), epoch, token),
                )
            return int(cur.rowcount) == 1

    def reclaim_stale(
        self,
        lease_seconds: float,
        *,
        now: float | None = None,
        max_attempts: int = 5,
    ) -> int:
        # ``lease_seconds`` remains in the interface for local parity. Shared
        # claims carry their exact expiry, so reclamation never recomputes it
        # from a possibly different worker configuration.
        compatibility_lease = float(lease_seconds)
        if not math.isfinite(compatibility_lease) or compatibility_lease < 0:
            raise ValueError("lease_seconds must be non-negative and finite")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts < 0:
            raise ValueError("max_attempts must be a non-negative integer")
        error = self._seal(
            "lease expired (worker presumed crashed)",
            limit=_MAX_ERROR_BYTES,
        )
        with self._transaction() as cur:
            n = self._transaction_time(cur, now)
            cur.execute(
                f"UPDATE {_JOBS_TABLE} SET status='failed', last_error_text=%s, "
                "lease_token='', lease_expires_at=-1, updated_at=%s "
                "WHERE tenant_id=%s AND queue_name=%s AND status='running' "
                "AND lease_expires_at <= %s AND attempt_epoch >= %s",
                (error, n, self._bound_tenant, self.namespace, n, max_attempts),
            )
            failed = int(cur.rowcount)
            cur.execute(
                f"UPDATE {_JOBS_TABLE} SET status='pending', run_at=%s, "
                "lease_token='', lease_expires_at=-1, updated_at=%s "
                "WHERE tenant_id=%s AND queue_name=%s AND status='running' "
                "AND lease_expires_at <= %s AND attempt_epoch < %s",
                (n, n, self._bound_tenant, self.namespace, n, max_attempts),
            )
            return failed + int(cur.rowcount)

    def cancel(self, job_id: int) -> bool:
        with self._transaction() as cur:
            cur.execute(
                f"DELETE FROM {_JOBS_TABLE} WHERE tenant_id=%s AND queue_name=%s "
                "AND id=%s AND status='pending'",
                (self._bound_tenant, self.namespace, self._validate_job_id(job_id)),
            )
            return int(cur.rowcount) == 1

    def get(self, job_id: int):
        with self._transaction() as cur:
            cur.execute(
                f"SELECT {self._RETURNING} FROM {_JOBS_TABLE} WHERE tenant_id=%s "
                "AND queue_name=%s AND id=%s",
                (self._bound_tenant, self.namespace, self._validate_job_id(job_id)),
            )
            row = cur.fetchone()
        return self._row_to_job(row) if row else None

    def list(self, *, status: str = "", limit: int = 100) -> list:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100_000:
            raise ValueError("limit must be between 1 and 100000")
        if status and status not in {"pending", "running", "done", "failed"}:
            raise ValueError("job status is invalid")
        with self._transaction() as cur:
            if status:
                cur.execute(
                    f"SELECT {self._RETURNING} FROM {_JOBS_TABLE} WHERE tenant_id=%s "
                    "AND queue_name=%s AND status=%s ORDER BY id DESC LIMIT %s",
                    (self._bound_tenant, self.namespace, status, limit),
                )
            else:
                cur.execute(
                    f"SELECT {self._RETURNING} FROM {_JOBS_TABLE} WHERE tenant_id=%s "
                    "AND queue_name=%s ORDER BY id DESC LIMIT %s",
                    (self._bound_tenant, self.namespace, limit),
                )
            rows = list(cur.fetchall())
        return [self._row_to_job(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self._transaction() as cur:
            cur.execute(
                f"SELECT status, COUNT(*) FROM {_JOBS_TABLE} WHERE tenant_id=%s "
                "AND queue_name=%s GROUP BY status",
                (self._bound_tenant, self.namespace),
            )
            rows = list(cur.fetchall())
        return {str(row[0]): int(row[1]) for row in rows}

    def purge(self, *, older_than_days: float = 7.0) -> int:
        days = float(older_than_days)
        if not math.isfinite(days) or days < 0:
            raise ValueError("older_than_days must be non-negative and finite")
        with self._transaction() as cur:
            authoritative_now = self._transaction_time(cur)
            cutoff = self._finite_time(
                max(0.0, authoritative_now - days * 86400),
                "cutoff",
            )
            cur.execute(
                f"DELETE FROM {_JOBS_TABLE} WHERE tenant_id=%s AND queue_name=%s "
                "AND status IN ('done','failed') AND updated_at < %s",
                (self._bound_tenant, self.namespace, cutoff),
            )
            return int(cur.rowcount)

    def claim_dispatch_envelope(
        self,
        message_id: str,
        nonce: str,
        *,
        expires_at: float,
        now: float | None = None,
    ) -> bool:
        if not isinstance(message_id, str) or not message_id or len(message_id.encode()) > 512:
            raise ValueError("message_id must be a non-empty bounded string")
        if not isinstance(nonce, str) or not nonce:
            raise ValueError("nonce must be a non-empty string")
        expiry = self._finite_time(expires_at, "expires_at")
        digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        with self._transaction() as cur:
            claimed = self._transaction_time(cur, now)
            cur.execute(
                f"DELETE FROM {_DISPATCH_TABLE} WHERE tenant_id=%s AND queue_name=%s "
                "AND expires_at <= %s",
                (self._bound_tenant, self.namespace, claimed),
            )
            if expiry <= claimed:
                return False
            cur.execute(
                f"INSERT INTO {_DISPATCH_TABLE} "
                "(tenant_id, queue_name, message_id, nonce_hash, expires_at, claimed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (self._bound_tenant, self.namespace, message_id, digest, expiry, claimed),
            )
            return int(cur.rowcount) == 1

    def prune_dispatch_envelope_claims(self, *, now: float | None = None) -> int:
        with self._transaction() as cur:
            cutoff = self._transaction_time(cur, now)
            cur.execute(
                f"DELETE FROM {_DISPATCH_TABLE} WHERE tenant_id=%s AND queue_name=%s "
                "AND expires_at <= %s",
                (self._bound_tenant, self.namespace, cutoff),
            )
            return int(cur.rowcount)

    def claim_poll(
        self,
        key: str,
        *,
        now: float | None = None,
        lease_seconds: float = 300.0,
    ) -> PollLease | None:
        if not isinstance(key, str) or not key or len(key.encode("utf-8")) > 512:
            raise ValueError("poll key must be a non-empty bounded string")
        lease = self._duration(lease_seconds, "lease_seconds")
        token = uuid.uuid4().hex
        with self._transaction() as cur:
            n = self._transaction_time(cur, now)
            expires = self._finite_time(
                n + lease,
                "lease_expires_at",
            )
            cur.execute(
                f"INSERT INTO {_POLLS_TABLE} "
                "(tenant_id, queue_name, poll_key, next_due_at, lease_epoch, "
                "lease_token, lease_expires_at, updated_at) "
                "VALUES (%s,%s,%s,%s,1,%s,%s,%s) "
                "ON CONFLICT (tenant_id, queue_name, poll_key) DO UPDATE SET "
                f"lease_epoch={_POLLS_TABLE}.lease_epoch+1, lease_token=EXCLUDED.lease_token, "
                "lease_expires_at=EXCLUDED.lease_expires_at, updated_at=EXCLUDED.updated_at "
                f"WHERE {_POLLS_TABLE}.next_due_at <= %s AND "
                f"({_POLLS_TABLE}.lease_token='' OR {_POLLS_TABLE}.lease_expires_at <= %s) "
                "RETURNING lease_epoch, lease_token",
                (self._bound_tenant, self.namespace, key, n, token, expires, n, n, n),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return PollLease(key, int(row[0]), str(row[1]))

    def finish_poll(
        self,
        lease: PollLease,
        *,
        interval_seconds: float,
        success: bool,
        now: float | None = None,
    ) -> bool:
        if not isinstance(lease, PollLease) or not _HEX_32_RE.fullmatch(lease.token):
            raise ValueError("poll lease is invalid")
        interval = self._duration(interval_seconds, "interval_seconds")
        with self._transaction() as cur:
            n = self._transaction_time(cur, now)
            next_due = self._finite_time(
                n + interval if success else n,
                "next_due_at",
            )
            cur.execute(
                f"UPDATE {_POLLS_TABLE} SET next_due_at=%s, lease_token='', "
                "lease_expires_at=-1, updated_at=%s WHERE tenant_id=%s "
                "AND queue_name=%s AND poll_key=%s AND lease_epoch=%s "
                "AND lease_token=%s",
                (next_due, n, self._bound_tenant, self.namespace, lease.key,
                 lease.epoch, lease.token),
            )
            return int(cur.rowcount) == 1


def selected_postgres_job_queue(
    *,
    namespace: str,
    legacy_db_path: str | Path,
    legacy_poll_artifacts: Sequence[str | Path] = (),
    authority_tenant: str | None = None,
    allow_tenant_switch: bool = False,
) -> PostgresJobQueue | None:
    """Return the selected Postgres queue, ``None`` only for valid local mode."""
    selection = _selection()
    if not selection.postgres:
        return None
    token = _authority_token(selection)
    try:
        # Hold the same strict gate as every current SQLite operation while the
        # new backend and its least-privilege schema are admitted.  Only then is
        # the durable marker published and local authority released.
        with _postgres_cutover_guard(
            legacy_db_path,
            poll_artifacts=legacy_poll_artifacts,
            dsn_sha256=token.dsn_sha256,
        ) as fence:
            queue = PostgresJobQueue(
                namespace=namespace,
                dsn=selection.dsn,
                legacy_db_path=legacy_db_path,
                legacy_poll_artifacts=legacy_poll_artifacts,
                check_legacy=False,
                authority_token=token,
                cutover_fence=fence,
                authority_tenant=authority_tenant,
                allow_tenant_switch=allow_tenant_switch,
            )
            # Construction can perform network and catalog probes. Re-read
            # deployment authority at the end of that window while the local
            # cutover gate is still held; selector/DSN drift must roll back
            # without publishing a stale durable marker.
            queue.assert_authority_current()
        queue._check_legacy = True
        return queue
    except JobQueueMigrationRequired:
        raise
    except Exception as exc:
        log.error(
            "Postgres job authority initialization failed (%s)",
            type(exc).__name__,
        )
        if isinstance(exc, SharedJobQueueUnavailable):
            raise
        raise SharedJobQueueUnavailable(
            "Postgres job authority is unavailable; local fallback was not used"
        ) from None


def assert_queue_backend_current(queue: Any) -> None:
    """Fail a cached dashboard queue if deployment authority changed in place."""
    if getattr(queue, "is_shared", False):
        queue.assert_authority_current()
        return
    selection = _selection()
    if selection.postgres or selection.shared_required:
        raise SharedJobQueueUnavailable(
            "local job queue is no longer the selected deployment authority"
        )


__all__ = [
    "JobQueueMigrationRequired",
    "PollLease",
    "PostgresJobQueue",
    "POSTGRES_JOB_QUEUE_MIGRATION",
    "SharedJobQueueUnavailable",
    "assert_no_legacy_job_authority",
    "assert_queue_backend_current",
    "selected_postgres_job_queue",
]
