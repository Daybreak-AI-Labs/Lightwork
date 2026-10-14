"""Fail-closed Postgres schema migration runner for the first production profile.

The serving runtime never owns schema objects and never migrates a production
database at startup.  This module is the separate, drained migration-owner
path.  It binds one short-lived operator authorization to the exact release
image, canonical migration lock, source schema state, managed-backup marker,
and recovery-drill marker before it opens the database.

Only three database states are admitted:

* an empty dedicated ``public`` schema for an explicit bootstrap;
* the exact immediately preceding migration prefix for an adjacent upgrade;
* the exact current ladder for a read/repair verification or an idempotent
  retry after a commit/report ambiguity.

Every other state -- including a missing middle version, a newer version, a
duplicate version, an unexpected table, or a table owned by another role -- is
refused.  Schema DDL is one transaction.  RLS installation is a second
idempotent transaction because the existing reviewed RLS installer owns its
transaction boundary; a failure between the two leaves serving startup
fail-closed and the same authorization can safely reconcile the current schema.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROFILE_ID = "enterprise-single-control-plane-v1"
AUTHORIZATION_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
MAX_AUTHORIZATION_BYTES = 16 * 1024
MAX_JSON_DEPTH = 8
MAX_AUTHORIZATION_LIFETIME = timedelta(hours=24)
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_DRAIN_AGE = timedelta(hours=1)
MAX_BACKUP_AGE = timedelta(hours=24)
MAX_RECOVERY_DRILL_AGE = timedelta(days=90)
FAILURE_POLICY = "rollback-transaction-then-forward-fix-or-full-restore"
_RECEIPT_PREFIX = "lightwork-production-migration-authorization-sha256:"
_REPOSITORY_SOURCE_SHA256 = {
    "deploy/helm/maverick/templates/_helpers.tpl": (
        "09582a4df2abae8c58a642ced3a5b761100e0036633837a6fe8dde8c3504bbfa"
    ),
    "deploy/helm/maverick/templates/migration-job.yaml": (
        "146ad71a9227a8043c8318829ccd3fe744e64932121101c907b0a58aee4da7d7"
    ),
    "deploy/helm/maverick/values-production.yaml": (
        "62e2bdd0ec8674dcfcaa343d9e7dd1f3564289aa09feae0faa3588614ea55119"
    ),
    "deploy/helm/maverick/values.yaml": (
        "967e42a65705491675dc71807e3b6601ec4f8bc71da448501f96ee8ca641e9cf"
    ),
}

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{2,127}")
_OPERATION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}")
_RELEASE_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z")
_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
    re.IGNORECASE,
)

_AUTHORIZATION_KEYS = {
    "schema_version",
    "profile_id",
    "operation_id",
    "mode",
    "source_head",
    "target_head",
    "migration_lock_sha256",
    "release_version",
    "image_digest",
    "issued_at",
    "expires_at",
    "approvals",
    "drain",
    "backup",
    "recovery",
    "failure_policy",
}
_DRAIN_KEYS = {"confirmed", "completed_at", "evidence_id", "evidence_sha256"}
_BACKUP_KEYS = {
    "status",
    "kind",
    "source_head",
    "completed_at",
    "evidence_id",
    "evidence_sha256",
}
_RECOVERY_KEYS = {
    "completed_at",
    "evidence_id",
    "evidence_sha256",
}


class ProductionMigrationError(RuntimeError):
    """A production migration admission or execution invariant was refused."""


@dataclass(frozen=True)
class MigrationState:
    name: str
    applied_versions: tuple[int, ...]
    source_head: int
    mutation_required: bool


@dataclass(frozen=True)
class MigrationAuthorization:
    document: dict[str, Any]
    raw_sha256: str
    mode: str
    operation_id: str
    source_head: int
    target_head: int
    migration_lock_sha256: str
    release_version: str
    image_digest: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProductionMigrationError(
                f"migration authorization contains duplicate key {key!r}"
            )
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ProductionMigrationError(
        f"migration authorization contains non-finite JSON value {value!r}"
    )


def _json_depth(value: object, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        raise ProductionMigrationError("migration authorization is too deeply nested")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ProductionMigrationError(
                    "migration authorization object keys must be strings"
                )
            _json_depth(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _json_depth(child, depth + 1)
    return depth


def parse_authorization_bytes(raw: bytes) -> dict[str, Any]:
    """Parse one bounded, duplicate-free, finite JSON authorization object."""
    if not raw or len(raw) > MAX_AUTHORIZATION_BYTES:
        raise ProductionMigrationError(
            "migration authorization has an invalid byte length"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProductionMigrationError(
            "migration authorization is not UTF-8"
        ) from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except ProductionMigrationError:
        raise
    except (RecursionError, ValueError, TypeError) as exc:
        raise ProductionMigrationError(
            "migration authorization is not strict JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProductionMigrationError("migration authorization must be an object")
    _json_depth(value)
    return value


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProductionMigrationError(f"{label} must be an object")
    keys = set(value)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ProductionMigrationError(
            f"{label} has an unexpected schema (missing={missing}, extra={extra})"
        )
    return value


def _exact_int(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ProductionMigrationError(f"{label} must be an integer >= {minimum}")
    return value


def _exact_string(value: object, label: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ProductionMigrationError(f"{label} has an invalid value")
    return value


def _parse_utc(value: object, label: str) -> datetime:
    text = _exact_string(value, label, _UTC_RE)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00")
    except ValueError as exc:  # pragma: no cover - regex already narrows the shape
        raise ProductionMigrationError(f"{label} is not a valid UTC timestamp") from exc
    if parsed.tzinfo != timezone.utc:
        raise ProductionMigrationError(f"{label} must use UTC")
    return parsed


def migration_versions() -> tuple[int, ...]:
    from .world_model_backends.postgres import MIGRATIONS

    versions = tuple(version for version, _statements in MIGRATIONS)
    if (
        not versions
        or any(type(version) is not int or version <= 0 for version in versions)
        or tuple(sorted(set(versions))) != versions
    ):
        raise ProductionMigrationError(
            "the packaged Postgres migration ladder is not strictly ordered and unique"
        )
    return versions


def canonical_migration_lock() -> tuple[dict[str, Any], str]:
    """Return the strict packaged lock and its platform-stable SHA-256."""
    from . import migration_governance

    try:
        raw = migration_governance.lock_path().read_bytes()
    except OSError as exc:
        raise ProductionMigrationError(
            "the packaged migration lock is unavailable"
        ) from exc
    lock = parse_authorization_bytes(raw)
    problems = migration_governance.validate()
    if problems:
        raise ProductionMigrationError(
            "the packaged migration ladder does not match its reviewed lock"
        )
    canonical = json.dumps(
        lock,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return lock, hashlib.sha256(canonical).hexdigest()


def expected_public_tables(
    applied_versions: Iterable[int] | None = None,
) -> tuple[str, ...]:
    """Derive the exact ordinary-table inventory from the reviewed SQL prefix."""
    from .world_model_backends.postgres import MIGRATIONS

    included = set(migration_versions() if applied_versions is None else applied_versions)
    names = {"schema_migrations"}
    for version, statements in MIGRATIONS:
        if version not in included:
            continue
        for statement in statements:
            names.update(match.group(1).lower() for match in _CREATE_TABLE_RE.finditer(statement))
    return tuple(sorted(names))


def classify_applied_versions(
    applied_versions: Sequence[object],
    *,
    expected_versions: Sequence[int] | None = None,
) -> MigrationState:
    """Classify only empty, adjacent-predecessor, or exact-current states.

    The comparison is tuple-exact.  A MAX(version) check is intentionally not
    used because it accepts a database with a missing middle migration.
    """
    expected = tuple(expected_versions or migration_versions())
    if not expected:
        raise ProductionMigrationError("the expected migration ladder is empty")
    if tuple(sorted(set(expected))) != expected or any(
        type(version) is not int or version <= 0 for version in expected
    ):
        raise ProductionMigrationError("the expected migration ladder is invalid")
    if any(type(version) is not int for version in applied_versions):
        raise ProductionMigrationError(
            "database migration versions must be exact integers"
        )
    applied = tuple(applied_versions)  # type: ignore[arg-type]
    if applied == ():
        return MigrationState("empty", applied, 0, True)
    if applied == expected[:-1]:
        return MigrationState("adjacent-predecessor", applied, applied[-1], True)
    if applied == expected:
        return MigrationState("current", applied, applied[-1], False)
    raise ProductionMigrationError(
        "database migration set is newer, gapped, duplicated, out of order, "
        "or not the supported adjacent predecessor"
    )


def validate_authorization(  # noqa: C901 - exact nested contract is intentionally explicit
    document: Mapping[str, Any],
    *,
    raw_sha256: str,
    expected_raw_sha256: str,
    expected_lock_sha256: str,
    expected_image_digest: str,
    expected_release_version: str,
    now: datetime | None = None,
) -> MigrationAuthorization:
    """Validate the exact short-lived operator authorization contract."""
    doc = _exact_keys(dict(document), _AUTHORIZATION_KEYS, "authorization")
    if (
        _exact_int(doc["schema_version"], "schema_version", minimum=1)
        != AUTHORIZATION_SCHEMA_VERSION
    ):
        raise ProductionMigrationError("unsupported authorization schema_version")
    if doc["profile_id"] != PROFILE_ID:
        raise ProductionMigrationError("authorization profile_id is not admitted")
    if not _SHA256_RE.fullmatch(raw_sha256):
        raise ProductionMigrationError("authorization raw digest is invalid")
    if raw_sha256 != expected_raw_sha256:
        raise ProductionMigrationError(
            "authorization bytes do not match the Helm-bound SHA-256"
        )

    operation_id = _exact_string(doc["operation_id"], "operation_id", _OPERATION_ID_RE)
    mode = doc["mode"]
    if mode not in {"bootstrap", "upgrade", "verify"}:
        raise ProductionMigrationError("authorization mode is not supported")
    source_head = _exact_int(doc["source_head"], "source_head")
    target_head = _exact_int(doc["target_head"], "target_head", minimum=1)
    expected_versions = migration_versions()
    predecessor = expected_versions[-2] if len(expected_versions) > 1 else 0
    required_source = {"bootstrap": 0, "upgrade": predecessor, "verify": target_head}[mode]
    if source_head != required_source or target_head != expected_versions[-1]:
        raise ProductionMigrationError(
            "authorization source/target is not the exact supported transition"
        )

    lock_sha = _exact_string(
        doc["migration_lock_sha256"], "migration_lock_sha256", _SHA256_RE
    )
    if lock_sha != expected_lock_sha256:
        raise ProductionMigrationError(
            "authorization does not bind the packaged migration lock"
        )
    image_digest = _exact_string(doc["image_digest"], "image_digest", _IMAGE_DIGEST_RE)
    if image_digest != expected_image_digest:
        raise ProductionMigrationError(
            "authorization image digest differs from the running Helm release"
        )
    release_version = _exact_string(
        doc["release_version"], "release_version", _RELEASE_RE
    )
    if release_version != expected_release_version:
        raise ProductionMigrationError(
            "authorization release version differs from the chart appVersion"
        )

    current_time = (now or _utc_now()).astimezone(timezone.utc)
    issued = _parse_utc(doc["issued_at"], "issued_at")
    expires = _parse_utc(doc["expires_at"], "expires_at")
    if issued > current_time + MAX_CLOCK_SKEW:
        raise ProductionMigrationError("authorization is not yet valid")
    if expires <= current_time or expires <= issued:
        raise ProductionMigrationError("authorization is expired or has no valid window")
    if expires - issued > MAX_AUTHORIZATION_LIFETIME:
        raise ProductionMigrationError("authorization lifetime exceeds 24 hours")

    approvals = doc["approvals"]
    if not isinstance(approvals, list) or len(approvals) != 2:
        raise ProductionMigrationError("authorization requires exactly two approvals")
    approval_ids = [
        _exact_string(value, f"approvals[{index}]", _ID_RE)
        for index, value in enumerate(approvals)
    ]
    if len(set(approval_ids)) != 2:
        raise ProductionMigrationError("authorization approvals must be distinct")

    drain = _exact_keys(doc["drain"], _DRAIN_KEYS, "drain")
    if drain["confirmed"] is not True:
        raise ProductionMigrationError("all serving admissions and writers must be drained")
    drain_time = _parse_utc(drain["completed_at"], "drain.completed_at")
    _exact_string(drain["evidence_id"], "drain.evidence_id", _ID_RE)
    _exact_string(drain["evidence_sha256"], "drain.evidence_sha256", _SHA256_RE)
    if (
        drain_time > issued + MAX_CLOCK_SKEW
        or drain_time > current_time + MAX_CLOCK_SKEW
        or current_time - drain_time > MAX_DRAIN_AGE
    ):
        raise ProductionMigrationError("drain evidence is stale or later than authorization")

    backup = _exact_keys(doc["backup"], _BACKUP_KEYS, "backup")
    backup_source = _exact_int(backup["source_head"], "backup.source_head")
    if backup_source != source_head:
        raise ProductionMigrationError("backup marker is not bound to the source head")
    if mode == "upgrade":
        if backup["status"] != "completed" or backup["kind"] not in {
            "managed-snapshot",
            "continuous-pitr",
        }:
            raise ProductionMigrationError(
                "adjacent upgrade requires a completed managed snapshot or PITR marker"
            )
        backup_time = _parse_utc(backup["completed_at"], "backup.completed_at")
        _exact_string(backup["evidence_id"], "backup.evidence_id", _ID_RE)
        _exact_string(backup["evidence_sha256"], "backup.evidence_sha256", _SHA256_RE)
        if backup_time <= drain_time:
            raise ProductionMigrationError(
                "backup evidence does not postdate the completed writer drain"
            )
        if (
            backup_time > issued + MAX_CLOCK_SKEW
            or backup_time > current_time + MAX_CLOCK_SKEW
            or current_time - backup_time > MAX_BACKUP_AGE
        ):
            raise ProductionMigrationError(
                "backup evidence is stale or later than authorization"
            )
    else:
        expected_status = (
            "not-applicable-empty-database"
            if mode == "bootstrap"
            else "not-required-schema-current"
        )
        expected_kind = "none-empty-database" if mode == "bootstrap" else "none-schema-current"
        if (
            backup["status"] != expected_status
            or backup["kind"] != expected_kind
            or backup["completed_at"] is not None
            or backup["evidence_id"] is not None
            or backup["evidence_sha256"] is not None
        ):
            raise ProductionMigrationError(
                "non-upgrade backup marker does not match the exact mode contract"
            )

    recovery = _exact_keys(doc["recovery"], _RECOVERY_KEYS, "recovery")
    recovery_time = _parse_utc(recovery["completed_at"], "recovery.completed_at")
    _exact_string(recovery["evidence_id"], "recovery.evidence_id", _ID_RE)
    _exact_string(recovery["evidence_sha256"], "recovery.evidence_sha256", _SHA256_RE)
    if (
        recovery_time > issued + MAX_CLOCK_SKEW
        or recovery_time > current_time + MAX_CLOCK_SKEW
        or current_time - recovery_time > MAX_RECOVERY_DRILL_AGE
    ):
        raise ProductionMigrationError(
            "restore-drill evidence is stale or later than authorization"
        )
    if doc["failure_policy"] != FAILURE_POLICY:
        raise ProductionMigrationError("authorization failure policy is not admitted")

    return MigrationAuthorization(
        document=doc,
        raw_sha256=raw_sha256,
        mode=mode,
        operation_id=operation_id,
        source_head=source_head,
        target_head=target_head,
        migration_lock_sha256=lock_sha,
        release_version=release_version,
        image_digest=image_digest,
    )


def read_and_validate_authorization(
    path: str | Path,
    *,
    expected_raw_sha256: str,
    expected_lock_sha256: str,
    expected_image_digest: str,
    expected_release_version: str,
    now: datetime | None = None,
) -> MigrationAuthorization:
    from .file_lock import read_custodied_bytes

    authorization_path = Path(path)
    try:
        raw = read_custodied_bytes(
            authorization_path,
            custody_root=authorization_path.parent,
            max_bytes=MAX_AUTHORIZATION_BYTES,
        )
    except Exception as exc:
        raise ProductionMigrationError(
            "migration authorization failed private custody validation"
        ) from exc
    digest = hashlib.sha256(raw).hexdigest()
    document = parse_authorization_bytes(raw)
    return validate_authorization(
        document,
        raw_sha256=digest,
        expected_raw_sha256=expected_raw_sha256,
        expected_lock_sha256=expected_lock_sha256,
        expected_image_digest=expected_image_digest,
        expected_release_version=expected_release_version,
        now=now,
    )


def _role_and_schema(cur: Any) -> str:
    cur.execute("SELECT current_schema()")
    schema = cur.fetchone()
    if not schema or schema[0] != "public":
        raise ProductionMigrationError(
            "production migration requires current_schema()=public"
        )
    # Remove every DSN/session-controlled schema from name resolution before
    # any DDL. pg_catalog remains implicitly first when it is not explicitly
    # listed, and application objects resolve only in the dedicated public
    # schema verified below.
    cur.execute("SET LOCAL search_path = public")
    cur.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname = current_user"
    )
    row = cur.fetchone()
    if not row:
        raise ProductionMigrationError("Postgres migration-owner role is unavailable")
    if bool(row[1]) or bool(row[2]):
        raise ProductionMigrationError(
            "Postgres migration owner must be NOSUPERUSER NOBYPASSRLS"
        )
    role = str(row[0])
    cur.execute(
        "SELECT pg_catalog.pg_get_userbyid(n.nspowner), EXISTS ("
        "SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
        "n.nspacl, pg_catalog.acldefault('n', n.nspowner))) AS acl "
        "WHERE acl.privilege_type = 'CREATE' "
        "AND acl.grantee <> n.nspowner) "
        "FROM pg_catalog.pg_namespace AS n "
        "WHERE n.nspname = current_schema()"
    )
    schema_row = cur.fetchone()
    if not schema_row or str(schema_row[0]) != role or bool(schema_row[1]):
        raise ProductionMigrationError(
            "public schema must be owned by the migration role with no CREATE "
            "grant to PUBLIC or another role"
        )
    return role


def _read_applied_versions(cur: Any) -> tuple[int, ...]:
    cur.execute("SELECT to_regclass('public.schema_migrations')")
    relation = cur.fetchone()
    if not relation or relation[0] is None:
        return ()
    cur.execute("LOCK TABLE public.schema_migrations IN ACCESS EXCLUSIVE MODE")
    cur.execute("SELECT version FROM public.schema_migrations ORDER BY version")
    return tuple(row[0] for row in cur.fetchall())


def _read_table_inventory(cur: Any) -> tuple[tuple[str, str], ...]:
    cur.execute(
        "SELECT c.relname, pg_get_userbyid(c.relowner) "
        "FROM pg_catalog.pg_class AS c "
        "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'p') "
        "ORDER BY c.relname"
    )
    return tuple((str(row[0]), str(row[1])) for row in cur.fetchall())


def _read_authorization_receipt(cur: Any) -> str | None:
    cur.execute(
        "SELECT pg_catalog.obj_description("
        "'public.schema_migrations'::pg_catalog.regclass, 'pg_class')"
    )
    row = cur.fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _verify_table_inventory(
    cur: Any,
    *,
    role: str,
    applied_versions: tuple[int, ...],
) -> None:
    actual = _read_table_inventory(cur)
    expected = () if not applied_versions else expected_public_tables(applied_versions)
    actual_names = tuple(name for name, _owner in actual)
    if actual_names != expected:
        raise ProductionMigrationError(
            "public table inventory is incompatible with the reviewed migration prefix"
        )
    wrong_owner = sorted(name for name, owner in actual if owner != role)
    if wrong_owner:
        raise ProductionMigrationError(
            "migration role does not own every reviewed public table"
        )


def _lock_and_verify_catalog(
    cur: Any,
    *,
    applied_versions: tuple[int, ...],
) -> str:
    """Lock the admitted tables and attest every reviewed public object."""

    expected_tables = () if not applied_versions else expected_public_tables(
        applied_versions
    )
    if expected_tables:
        for name in expected_tables:
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                raise ProductionMigrationError(
                    "reviewed table inventory contains an unsafe identifier"
                )
        qualified = ", ".join(f"public.{name}" for name in expected_tables)
        # This job runs only while serving is drained.  Locking every admitted
        # table makes the live catalog proof and any following DDL one coherent
        # transaction rather than a partial-restore snapshot that can drift.
        cur.execute(f"LOCK TABLE {qualified} IN ACCESS EXCLUSIVE MODE")
    from .postgres_schema_manifest import (
        PostgresSchemaManifestError,
        verify_catalog_manifest,
    )

    head = applied_versions[-1] if applied_versions else 0
    try:
        return verify_catalog_manifest(cur, head=head)
    except PostgresSchemaManifestError as exc:
        raise ProductionMigrationError(
            "public schema objects differ from the reviewed live-catalog "
            f"fingerprint at head {head}"
        ) from exc


def _state_allowed_by_authorization(
    state: MigrationState,
    authorization: MigrationAuthorization,
    *,
    authorization_receipt: str | None,
) -> None:
    allowed = {
        "bootstrap": {"empty", "current"},
        "upgrade": {"adjacent-predecessor", "current"},
        "verify": {"current"},
    }[authorization.mode]
    if state.name not in allowed:
        raise ProductionMigrationError(
            "database state does not match the authorized migration mode"
        )
    if state.name != "current" and state.source_head != authorization.source_head:
        raise ProductionMigrationError(
            "database source head changed after migration authorization"
        )
    if state.name == "current" and authorization.mode in {"bootstrap", "upgrade"}:
        expected_receipt = _RECEIPT_PREFIX + authorization.raw_sha256
        if authorization_receipt != expected_receipt:
            raise ProductionMigrationError(
                "current schema was not committed by these exact authorization bytes; "
                "use verify mode or the original authorization"
            )


def run_production_migration(
    conn: Any,
    authorization: MigrationAuthorization,
    *,
    now: datetime | None = None,
    rls_apply: Callable[[Any], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Apply/reconcile the exact authorized ladder and RLS policy set.

    ``conn`` must be a fresh non-autocommit migration-owner connection.  The
    schema phase is atomic.  The RLS installer then commits its own idempotent
    transaction.  Any failure raises without emitting a success report.
    """
    if bool(getattr(conn, "autocommit", False)):
        raise ProductionMigrationError(
            "production migration requires autocommit=False"
        )
    from .world_model_backends.postgres import (
        _MIGRATION_ADVISORY_LOCK,
        _MIGRATIONS_TABLE,
        MIGRATIONS,
    )

    started = (now or _utc_now()).astimezone(timezone.utc)
    role = ""
    state: MigrationState | None = None
    try:
        cur = conn.cursor()
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '30min'")
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_ADVISORY_LOCK,))
        # Authorization and evidence can expire while this connection waits for
        # the migration admission lock. Revalidate at lock admission rather
        # than relying on the earlier file/CLI parse.
        validate_authorization(
            authorization.document,
            raw_sha256=authorization.raw_sha256,
            expected_raw_sha256=authorization.raw_sha256,
            expected_lock_sha256=authorization.migration_lock_sha256,
            expected_image_digest=authorization.image_digest,
            expected_release_version=authorization.release_version,
            now=(now or _utc_now()).astimezone(timezone.utc),
        )
        role = _role_and_schema(cur)
        applied = _read_applied_versions(cur)
        state = classify_applied_versions(applied)
        authorization_receipt = (
            _read_authorization_receipt(cur) if state.name == "current" else None
        )
        _state_allowed_by_authorization(
            state,
            authorization,
            authorization_receipt=authorization_receipt,
        )
        _verify_table_inventory(cur, role=role, applied_versions=state.applied_versions)
        _lock_and_verify_catalog(cur, applied_versions=state.applied_versions)

        if state.mutation_required:
            if state.name == "empty":
                cur.execute(_MIGRATIONS_TABLE)
            pending = [
                (version, statements)
                for version, statements in MIGRATIONS
                if version not in state.applied_versions
            ]
            for version, statements in pending:
                for statement in statements:
                    cur.execute(statement)
                cur.execute(
                    "INSERT INTO public.schema_migrations(version, applied_at) "
                    "VALUES(%s, %s)",
                    (version, started.timestamp()),
                )
            cur.execute(
                "SELECT version FROM public.schema_migrations ORDER BY version"
            )
            post_versions = tuple(row[0] for row in cur.fetchall())
            post_state = classify_applied_versions(post_versions)
            if post_state.name != "current":
                raise ProductionMigrationError(
                    "schema transaction did not reach the exact target ladder"
                )
            _verify_table_inventory(
                cur,
                role=role,
                applied_versions=post_state.applied_versions,
            )
            _lock_and_verify_catalog(
                cur,
                applied_versions=post_state.applied_versions,
            )
            # The exact authorization digest is safe to interpolate: validation
            # admitted exactly 64 lowercase hexadecimal characters. Commit the
            # receipt atomically with the schema so a report-loss retry can
            # prove it is replaying the same bytes, not merely the same mode.
            receipt = _RECEIPT_PREFIX + authorization.raw_sha256
            cur.execute(
                "COMMENT ON TABLE public.schema_migrations IS "
                f"'{receipt}'"
            )
        conn.commit()
    except ProductionMigrationError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise ProductionMigrationError(
            "Postgres schema migration transaction failed"
        ) from exc

    if rls_apply is None:
        from .world_model_backends.pg_rls import apply as rls_apply
    try:
        rls_report = dict(rls_apply(conn))
    except Exception as exc:
        # The schema transaction may already be current. Serving startup still
        # refuses missing/inexact RLS; a same-authorization retry reconciles it.
        try:
            conn.rollback()
        except Exception:
            pass
        raise ProductionMigrationError(
            "schema is current but exact RLS installation did not complete; "
            "keep serving drained and retry with the same authorization"
        ) from exc
    if rls_report.get("ready") is not True:
        raise ProductionMigrationError("RLS installer did not report exact readiness")

    try:
        cur = conn.cursor()
        role_after = _role_and_schema(cur)
        applied_after = _read_applied_versions(cur)
        current = classify_applied_versions(applied_after)
        if current.name != "current" or role_after != role:
            raise ProductionMigrationError(
                "post-migration schema or migration-owner identity drifted"
            )
        if authorization.mode in {"bootstrap", "upgrade"}:
            receipt_after = _read_authorization_receipt(cur)
            if receipt_after != _RECEIPT_PREFIX + authorization.raw_sha256:
                raise ProductionMigrationError(
                    "post-migration authorization receipt is missing or mismatched"
                )
        _verify_table_inventory(
            cur,
            role=role_after,
            applied_versions=current.applied_versions,
        )
        catalog_sha256 = _lock_and_verify_catalog(
            cur,
            applied_versions=current.applied_versions,
        )
        conn.rollback()  # read-only verification; release locks explicitly
    except ProductionMigrationError:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise ProductionMigrationError(
            "post-migration schema verification failed"
        ) from exc

    assert state is not None  # established before the schema transaction commits
    result = "already-current-reconciled" if not state.mutation_required else "committed"
    finished = (now or _utc_now()).astimezone(timezone.utc)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "profile_id": PROFILE_ID,
        "operation_id": authorization.operation_id,
        "mode": authorization.mode,
        "result": result,
        "source_head": state.source_head,
        "target_head": authorization.target_head,
        "applied_versions": list(migration_versions()),
        "migration_lock_sha256": authorization.migration_lock_sha256,
        "catalog_manifest_sha256": catalog_sha256,
        "authorization_sha256": authorization.raw_sha256,
        "release_version": authorization.release_version,
        "image_digest": authorization.image_digest,
        "migration_role_sha256": hashlib.sha256(role.encode("utf-8")).hexdigest(),
        "rls_policy_count": int(rls_report.get("policy_count", 0)),
        "drain_evidence_sha256": authorization.document["drain"]["evidence_sha256"],
        "backup_evidence_sha256": authorization.document["backup"]["evidence_sha256"],
        "recovery_evidence_sha256": authorization.document["recovery"]["evidence_sha256"],
        "started_at": _format_utc(started),
        "finished_at": _format_utc(finished),
        "live_restore_performed_by_runner": False,
    }


def production_migration_contract() -> dict[str, Any]:
    """Build the canonical release contract from the packaged ladder/lock."""
    from .postgres_schema_manifest import (
        catalog_query_sha256,
        expected_catalog_sha256,
    )

    _lock, lock_sha = canonical_migration_lock()
    versions = migration_versions()
    target = versions[-1]
    predecessor = versions[-2] if len(versions) > 1 else 0
    return {
        "schema_version": 1,
        "profile_id": PROFILE_ID,
        "artifact_status": "repository-contract-not-live-qualification-evidence",
        "admitted_backend": "postgres",
        "backend_matrix": [
            {
                "backend": "postgres",
                "production_admitted": True,
                "migration_path": "separate-drained-migration-owner-job",
                "backup_semantics": "managed-snapshot-or-pitr-marker-required-for-upgrade",
            },
            {
                "backend": "sqlite",
                "production_admitted": False,
                "migration_path": "evaluation-open-time-forward-migration-only",
                "backup_semantics": "best-effort-local-copy-is-not-production-evidence",
            },
        ],
        "compatibility": {
            "policy": "bootstrap-or-adjacent-predecessor-only",
            "target_head": target,
            "adjacent_predecessor_head": predecessor,
            "target_versions": list(versions),
            "migration_lock_sha256": lock_sha,
            "live_catalog_sha256": {
                "empty": expected_catalog_sha256(0),
                str(predecessor): expected_catalog_sha256(predecessor),
                str(target): expected_catalog_sha256(target),
            },
            "catalog_reference": "PostgreSQL-16.14",
            "catalog_query_sha256": catalog_query_sha256(),
            "states": [
                {
                    "database_state": "empty-dedicated-public-schema",
                    "authorization_mode": "bootstrap",
                    "outcome": "apply-full-reviewed-ladder",
                },
                {
                    "database_state": "exact-adjacent-predecessor-prefix",
                    "authorization_mode": "upgrade",
                    "outcome": "apply-one-new-version",
                },
                {
                    "database_state": "exact-current-ladder",
                    "authorization_mode": "verify",
                    "outcome": "verify-and-reconcile-rls",
                },
                {
                    "database_state": "exact-current-ladder-after-ambiguous-report",
                    "authorization_mode": (
                        "bootstrap-or-upgrade-same-authorization-sha256"
                    ),
                    "outcome": "idempotent-verify-and-reconcile-rls",
                },
                {
                    "database_state": "newer-gapped-duplicated-out-of-order-or-older-prefix",
                    "authorization_mode": "any",
                    "outcome": "refuse-without-ddl",
                },
            ],
        },
        "authorization": {
            "format": "strict-duplicate-free-finite-json",
            "max_bytes": MAX_AUTHORIZATION_BYTES,
            "helm_binds_raw_sha256": True,
            "release_binding": [
                "profile_id",
                "release_version",
                "image_digest",
                "migration_lock_sha256",
                "source_head",
                "target_head",
            ],
            "two_distinct_approval_identifiers_required": True,
            "maximum_lifetime_seconds": int(MAX_AUTHORIZATION_LIFETIME.total_seconds()),
            "maximum_drain_age_seconds": int(MAX_DRAIN_AGE.total_seconds()),
            "maximum_backup_age_seconds": int(MAX_BACKUP_AGE.total_seconds()),
            "maximum_restore_drill_age_seconds": int(
                MAX_RECOVERY_DRILL_AGE.total_seconds()
            ),
            "drain_marker_required_for_every_mode": True,
            "upgrade_backup_marker_kinds": ["continuous-pitr", "managed-snapshot"],
            "restore_drill_marker_required_for_every_mode": True,
            "marker_limit": (
                "identifiers-and-digests-are-validated-references; the runner-does-not-"
                "authenticate-approver-identities-independently-query-the-backup-provider-"
                "or-perform-a-restore"
            ),
        },
        "execution": {
            "runner": "python -m maverick.production_migration apply",
            "serving_runtime_migrates": False,
            "migration_dsn_custody": "private-staged-file-not-serving-pod",
            "connection": "tcp-tls-verify-full-with-trusted-ca",
            "network_egress": "dns-plus-exact-database-cidrs-and-tcp-port-only",
            "scheduling_affinity": "migration-specific-not-serving-pod-affinity",
            "role": "separate-owner-nosuperuser-nobypassrls",
            "schema_authority": "migration-role-owned-no-foreign-create-grants",
            "search_path": "transaction-local-public-only",
            "admission_lock": "transaction-scoped-postgres-advisory-lock",
            "schema_metadata_lock": "access-exclusive",
            "schema_transaction": "all-pending-ddl-and-version-ledger-atomic",
            "table_inventory": "exact-reviewed-public-ordinary-table-set-and-owner",
            "catalog_inventory": (
                "exact-live-columns-constraints-indexes-sequences-functions-"
                "triggers-and-object-set"
            ),
            "rls_transaction": "separate-idempotent-exact-policy-install-after-schema-commit",
            "rls_failure": "serving-remains-drained-and-runtime-startup-refuses",
            "job_cleanup": "ttl-delete-completed-job-and-pod-after-3600-seconds",
            "network_policy_cleanup": "explicit-delete-after-terminal-evidence-capture",
            "helm_job": "deploy/helm/maverick/templates/migration-job.yaml",
        },
        "failure_and_recovery": {
            "sql_down_migrations": "forbidden",
            "precommit_failure": "postgres-transaction-rollback",
            "postcommit_pre_report_ambiguity": "retry-same-authorization-bytes",
            "incompatible_application_after_commit": (
                "keep-serving-drained-then-forward-fix-or-full-managed-restore"
            ),
            "older_binary_on_newer_schema": "refused",
            "partial_restore_or-version-ledger-repair": "forbidden",
            "required_failure_policy": FAILURE_POLICY,
        },
        "report": {
            "destination": "canonical-value-free-json-on-stdout",
            "contains_secrets_or_dsn": False,
            "contains_role_only_as_sha256": True,
            "signed_retention": "external-release-evidence-system-required",
            "report_is_live_restore_proof": False,
        },
        "repository_source_sha256": dict(_REPOSITORY_SOURCE_SHA256),
        "external_qualification_required": [
            "exact-managed-postgres-offering-and-version",
            "target-cluster-cni-runtimeclass-scheduler-and-hook-ttl-cleanup-proof",
            "predecessor-to-target-rehearsal-on-representative-data-volume",
            "writer-drain-and-zero-active-admission-proof",
            "backup-provider-snapshot-or-pitr-completion-and-retention-proof",
            "whole-database-zero-plaintext-inventory-and-rewrite-proof",
            "independent-full-restore-and-integrity-drill",
            "migration-interruption-before-commit-rollback-drill",
            "commit-before-report-loss-idempotent-reconciliation-drill",
            "post-migration-runtime-rls-tls-encryption-and-readiness-proof",
            "operator-log-collection-signing-and-long-term-retention",
            "authorization-authenticity-through-external-gitops-rbac-and-change-control",
        ],
    }


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _load_private_dsn(path: str | Path) -> str:
    from .file_lock import read_custodied_text

    dsn_path = Path(path)
    try:
        dsn = read_custodied_text(
            dsn_path,
            custody_root=dsn_path.parent,
            max_bytes=16 * 1024,
        ).strip()
    except Exception as exc:
        raise ProductionMigrationError(
            "migration-owner DSN failed private custody validation"
        ) from exc
    if not dsn:
        raise ProductionMigrationError("migration-owner DSN file is empty")
    return dsn


def _add_binding_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--authorization-file", required=True)
    parser.add_argument("--authorization-sha256", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--release-version", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser(
        "validate-authorization",
        help="validate custody, release binding, freshness, drain, backup, and recovery markers",
    )
    _add_binding_arguments(validate_parser)
    apply_parser = subparsers.add_parser(
        "apply",
        help="apply/reconcile the authorized adjacent Postgres migration while drained",
    )
    _add_binding_arguments(apply_parser)
    apply_parser.add_argument("--dsn-file", required=True)
    args = parser.parse_args(argv)

    try:
        _lock, lock_sha = canonical_migration_lock()
        authorization = read_and_validate_authorization(
            args.authorization_file,
            expected_raw_sha256=args.authorization_sha256,
            expected_lock_sha256=lock_sha,
            expected_image_digest=args.image_digest,
            expected_release_version=args.release_version,
        )
        if args.command == "validate-authorization":
            print(
                canonical_json(
                    {
                        "schema_version": 1,
                        "profile_id": PROFILE_ID,
                        "operation_id": authorization.operation_id,
                        "mode": authorization.mode,
                        "target_head": authorization.target_head,
                        "authorization_sha256": authorization.raw_sha256,
                        "migration_lock_sha256": lock_sha,
                        "valid": True,
                    }
                )
            )
            return 0

        dsn = _load_private_dsn(args.dsn_file)
        from .world_model_backends.pg_rls import connect
        from .world_model_backends.postgres import (
            _validate_production_dsn,
            _verify_production_connection,
        )

        _validate_production_dsn(dsn)
        try:
            conn = connect(dsn, autocommit=False)
        except Exception as exc:
            raise ProductionMigrationError(
                "could not connect with the migration-owner DSN"
            ) from exc
        try:
            _verify_production_connection(conn)
            report = run_production_migration(conn, authorization)
        finally:
            conn.close()
        print(canonical_json(report))
        return 0
    except ProductionMigrationError as exc:
        parser.exit(2, f"production migration refused: {exc}\n")
    except Exception:
        parser.exit(
            1,
            "production migration failed; keep serving drained and inspect the "
            "database/operator logs without exposing credentials\n",
        )
    return 1  # pragma: no cover - argparse exits above


__all__ = [
    "AUTHORIZATION_SCHEMA_VERSION",
    "FAILURE_POLICY",
    "MigrationAuthorization",
    "MigrationState",
    "PROFILE_ID",
    "ProductionMigrationError",
    "canonical_json",
    "canonical_migration_lock",
    "classify_applied_versions",
    "expected_public_tables",
    "main",
    "migration_versions",
    "parse_authorization_bytes",
    "production_migration_contract",
    "read_and_validate_authorization",
    "run_production_migration",
    "validate_authorization",
]


if __name__ == "__main__":  # pragma: no cover - exercised by the Helm job
    raise SystemExit(main())
