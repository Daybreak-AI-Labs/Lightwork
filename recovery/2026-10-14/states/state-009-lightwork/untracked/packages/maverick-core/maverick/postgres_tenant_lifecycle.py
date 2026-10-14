"""Privileged, drained tenant lifecycle for the shared Postgres data plane.

The serving role is intentionally unable to inventory or erase another tenant
through forced RLS.  This module is the separate migration-owner path used for
an exact tenant export and for transactional offboarding.  It never resolves a
DSN itself and never accepts a table name from an operator: every identifier is
drawn from the runtime's fixed RLS allow-set.

All operations require a drained service, the exact current schema, a
``NOSUPERUSER NOBYPASSRLS`` owner role, enabled/forced fail-closed RLS, and one
exclusive transaction over the complete table set.  Owner RLS bypass is
enabled only transactionally with ``NO FORCE`` and is restored before commit;
rollback restores the original posture on every failure.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import canonical_tenant_id
from .world_model_backends.postgres import (
    _PG_SCHEMA_VERSION,
    _RLS_CHILD_TABLES,
    _RLS_POLICY_TABLES,
    _RLS_TABLES,
    MIGRATIONS,
)

_SCHEMA = "public"
_POLICY = "mvk_tenant_isolation"
_FLOW_TRIGGER = "flow_authority_no_immutable_update"
_LIFECYCLE_LOCK = 0x6D766B74656E  # ``mvkten``
_BATCH_SIZE = 500

EXPORT_SCHEMA = "lightwork.postgres-tenant-export.v1"
REPORT_SCHEMA = "lightwork.postgres-tenant-lifecycle-report.v1"
LIFECYCLE_SCOPE = "governed-world-model-and-flow-authority-manifest"
SEPARATE_POSTGRES_SURFACES = ("governed_records",)

# ``flow_authority_records`` is deliberately asserted even though it is now in
# _RLS_TABLES.  Losing it from a future refactor must make lifecycle admission
# fail rather than silently omit executable-flow authority.
_DIRECT_TABLES = tuple(dict.fromkeys((*_RLS_TABLES, "flow_authority_records")))
_CHILD_TABLES = tuple(_RLS_CHILD_TABLES)
_TABLES = tuple(dict.fromkeys((*_DIRECT_TABLES, *_CHILD_TABLES)))
if set(_TABLES) != set(_RLS_POLICY_TABLES) or "flow_authority_records" not in _TABLES:
    raise RuntimeError("Postgres tenant lifecycle table allow-set is incomplete")

_RETAINED_TABLES = frozenset({"erasure_receipts"})
_DELETE_DIRECT_ORDER = (
    "tenant_quota_reservations",
    "tenant_quota_usage",
    "approval_audit_outbox",
    "api_idempotency",
    "api_idempotency_tombstones",
    "signoff_decision_outbox",
    "processed_messages",
    "fact_history",
    "facts",
    "conversations",
    "approvals",
    "goals",
    "projects",
    "flow_authority_records",
)
if set(_DELETE_DIRECT_ORDER) != set(_DIRECT_TABLES) - _RETAINED_TABLES:
    raise RuntimeError("Postgres tenant lifecycle delete order is incomplete")

# Relationships whose tenant identity is not fully expressed by the primary
# parent-derived RLS map.  Every check is global: corruption elsewhere blocks a
# privileged lifecycle run instead of letting the owner normalize or obscure it.
_SECONDARY_RELATIONSHIPS: tuple[tuple[str, str], ...] = (
    (
        "goals.parent_id",
        "SELECT count(*) FROM public.goals AS source "
        "LEFT JOIN public.goals AS related ON related.id = source.parent_id "
        "WHERE source.parent_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "goals.project_id",
        "SELECT count(*) FROM public.goals AS source "
        "LEFT JOIN public.projects AS related ON related.id = source.project_id "
        "WHERE source.project_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "facts.source_episode_id",
        "SELECT count(*) FROM public.facts AS source "
        "LEFT JOIN public.episodes AS linked ON linked.id = source.source_episode_id "
        "LEFT JOIN public.goals AS related ON related.id = linked.goal_id "
        "WHERE source.source_episode_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "fact_history.source_episode_id",
        "SELECT count(*) FROM public.fact_history AS source "
        "LEFT JOIN public.episodes AS linked ON linked.id = source.source_episode_id "
        "LEFT JOIN public.goals AS related ON related.id = linked.goal_id "
        "WHERE source.source_episode_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "processed_messages.goal_id",
        "SELECT count(*) FROM public.processed_messages AS source "
        "LEFT JOIN public.goals AS related ON related.id = source.goal_id "
        "WHERE source.goal_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "api_idempotency.goal_id",
        "SELECT count(*) FROM public.api_idempotency AS source "
        "LEFT JOIN public.goals AS related ON related.id = source.goal_id "
        "WHERE source.goal_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM source.tenant_id)",
    ),
    (
        "signoff_decision_outbox.goal_id",
        "SELECT count(*) FROM public.signoff_decision_outbox AS source "
        "LEFT JOIN public.goals AS related ON related.id = source.goal_id "
        "WHERE related.id IS NULL OR "
        "related.tenant_id IS DISTINCT FROM source.tenant_id",
    ),
    (
        "approval_audit_outbox.approval_id",
        "SELECT count(*) FROM public.approval_audit_outbox AS source "
        "LEFT JOIN public.approvals AS related ON related.id = source.approval_id "
        "WHERE related.id IS NULL OR "
        "related.tenant_id IS DISTINCT FROM source.tenant_id",
    ),
    (
        "turns.goal_id",
        "SELECT count(*) FROM public.turns AS source "
        "JOIN public.conversations AS owner "
        "ON owner.id = source.conversation_id "
        "LEFT JOIN public.goals AS related ON related.id = source.goal_id "
        "WHERE source.goal_id IS NOT NULL AND "
        "(related.id IS NULL OR related.tenant_id IS DISTINCT FROM owner.tenant_id)",
    ),
)


class PostgresTenantLifecycleError(RuntimeError):
    """A complete, isolated tenant lifecycle result could not be established."""


def _canonical(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise PostgresTenantLifecycleError(
            "Postgres tenant lifecycle report is not canonical JSON"
        ) from exc


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_tenant_id(value: str) -> str:
    """Return one non-empty, portable, canonical tenant identity."""

    if not isinstance(value, str) or value != value.strip() or not value:
        raise PostgresTenantLifecycleError(
            "Postgres tenant lifecycle requires a non-empty canonical tenant id"
        )
    if len(value.encode("utf-8")) > 256 or any(ord(char) < 32 for char in value):
        raise PostgresTenantLifecycleError("Postgres tenant id is invalid")
    try:
        canonical = canonical_tenant_id(value)
    except (TypeError, ValueError) as exc:
        raise PostgresTenantLifecycleError("Postgres tenant id is invalid") from exc
    if canonical != value:
        raise PostgresTenantLifecycleError(
            "Postgres tenant id must use its canonical Unicode spelling"
        )
    return canonical


def table_manifest() -> dict[str, Any]:
    """Return the exact fixed table and retention contract."""

    return {
        "scope": LIFECYCLE_SCOPE,
        "direct": list(_DIRECT_TABLES),
        "children": {
            table: {"parent": parent, "foreign_key": foreign_key}
            for table, (parent, foreign_key) in _RLS_CHILD_TABLES.items()
        },
        "retained_by_default": sorted(_RETAINED_TABLES),
        "not_covered": list(SEPARATE_POSTGRES_SURFACES),
    }


def table_manifest_sha256() -> str:
    return _digest(table_manifest())


def schema_source_sha256() -> str:
    """Digest the exact forward-only schema source admitted by this binary."""

    payload = {
        "migrations": [
            {"version": version, "statements": list(statements)}
            for version, statements in MIGRATIONS
        ]
    }
    return _digest(payload)


def _require_transaction(conn, *, drained: bool, erase: bool = False) -> None:
    if not drained:
        raise PostgresTenantLifecycleError(
            "refusing Postgres tenant lifecycle until serving writers are drained"
        )
    if erase is False and bool(getattr(conn, "autocommit", False)):
        raise PostgresTenantLifecycleError(
            "Postgres tenant lifecycle requires autocommit=False"
        )
    if erase and bool(getattr(conn, "autocommit", False)):
        raise PostgresTenantLifecycleError(
            "Postgres tenant erasure requires autocommit=False"
        )


def _policy_expression_is_fail_closed(table: str, expression: object) -> bool:
    text = " ".join(str(expression or "").lower().split())
    common = (
        "tenant_id" in text
        and "current_setting" in text
        and "maverick.tenant" in text
        and "nullif" in text
        and " or " not in text
        and "is null" not in text
    )
    if not common:
        return False
    if table in _RLS_CHILD_TABLES:
        parent, foreign_key = _RLS_CHILD_TABLES[table]
        return (
            "exists" in text
            and parent in text
            and foreign_key in text
            and "mvk_parent" in text
        )
    return table in _DIRECT_TABLES


def _role_and_schema(cur) -> str:
    cur.execute("SELECT current_schema()")
    schema = cur.fetchone()
    if not schema or schema[0] != _SCHEMA:
        raise PostgresTenantLifecycleError(
            "Postgres tenant lifecycle requires current_schema()=public"
        )
    cur.execute(
        "SELECT rolname, rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
        "WHERE rolname = current_user"
    )
    row = cur.fetchone()
    if not row:
        raise PostgresTenantLifecycleError(
            "Postgres migration-owner role could not be resolved"
        )
    if bool(row[1]) or bool(row[2]):
        raise PostgresTenantLifecycleError(
            "Postgres migration owner must be NOSUPERUSER NOBYPASSRLS"
        )
    return str(row[0])


def _table_posture(cur, table: str, role: str) -> None:
    cur.execute(
        "SELECT pg_get_userbyid(c.relowner), c.relrowsecurity, "
        "c.relforcerowsecurity FROM pg_catalog.pg_class AS c "
        "WHERE c.oid = to_regclass(%s)",
        (f"{_SCHEMA}.{table}",),
    )
    row = cur.fetchone()
    if not row:
        raise PostgresTenantLifecycleError(
            f"required tenant lifecycle table is missing: {_SCHEMA}.{table}"
        )
    if row[0] != role:
        raise PostgresTenantLifecycleError(
            f"migration role does not own tenant lifecycle table {_SCHEMA}.{table}"
        )
    if not bool(row[1]) or not bool(row[2]):
        raise PostgresTenantLifecycleError(
            f"tenant lifecycle table lacks enabled and forced RLS: {_SCHEMA}.{table}"
        )
    cur.execute(
        "SELECT policyname, permissive, roles, cmd, qual, with_check "
        "FROM pg_catalog.pg_policies WHERE schemaname = %s AND tablename = %s",
        (_SCHEMA, table),
    )
    policies = list(cur.fetchall())
    if len(policies) != 1:
        raise PostgresTenantLifecycleError(
            f"tenant lifecycle table must have one exact RLS policy: {_SCHEMA}.{table}"
        )
    policy = policies[0]
    roles = policy[2]
    public_roles = (
        {str(role).lower() for role in roles}
        if isinstance(roles, (list, tuple, set))
        else {str(roles).strip("{}").lower()}
    )
    if (
        policy[0] != _POLICY
        or str(policy[1]).upper() != "PERMISSIVE"
        or public_roles != {"public"}
        or str(policy[3]).upper() != "ALL"
        or not _policy_expression_is_fail_closed(table, policy[4])
        or not _policy_expression_is_fail_closed(table, policy[5])
    ):
        raise PostgresTenantLifecycleError(
            f"tenant lifecycle table RLS policy is not exact and fail-closed: "
            f"{_SCHEMA}.{table}"
        )


def _verify_flow_trigger(cur) -> None:
    cur.execute(
        "SELECT t.tgenabled FROM pg_catalog.pg_trigger AS t "
        "WHERE t.tgrelid = to_regclass(%s) AND t.tgname = %s "
        "AND NOT t.tgisinternal",
        (f"{_SCHEMA}.flow_authority_records", _FLOW_TRIGGER),
    )
    row = cur.fetchone()
    if not row or row[0] != "O":
        raise PostgresTenantLifecycleError(
            "flow-authority immutability trigger is missing or not enabled"
        )


def _admit_and_lock(conn) -> tuple[str, int]:
    cur = conn.cursor()
    role = _role_and_schema(cur)
    # Resolve every relation before interpolating the fixed allow-set in LOCK.
    for table in ("schema_migrations", *_TABLES):
        cur.execute("SELECT to_regclass(%s)", (f"{_SCHEMA}.{table}",))
        row = cur.fetchone()
        if not row or row[0] is None:
            raise PostgresTenantLifecycleError(
                f"required tenant lifecycle table is missing: {_SCHEMA}.{table}"
            )
    cur.execute("SET LOCAL lock_timeout = '5s'")
    cur.execute("SET LOCAL statement_timeout = '30min'")
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (_LIFECYCLE_LOCK,))
    relations = ", ".join(
        f"{_SCHEMA}.{table}" for table in ("schema_migrations", *sorted(_TABLES))
    )
    cur.execute(f"LOCK TABLE {relations} IN ACCESS EXCLUSIVE MODE")

    # Recheck every mutable admission fact after the locks.
    role = _role_and_schema(cur)
    cur.execute(
        "SELECT pg_get_userbyid(c.relowner) FROM pg_catalog.pg_class AS c "
        "WHERE c.oid = to_regclass(%s)",
        (f"{_SCHEMA}.schema_migrations",),
    )
    owner = cur.fetchone()
    if not owner or owner[0] != role:
        raise PostgresTenantLifecycleError(
            "migration role does not own public.schema_migrations"
        )
    cur.execute("SELECT version FROM public.schema_migrations ORDER BY version")
    applied_versions = tuple(int(row[0]) for row in cur.fetchall())
    expected_versions = tuple(version for version, _statements in MIGRATIONS)
    if applied_versions != expected_versions:
        raise PostgresTenantLifecycleError(
            "database schema migration set differs from the exact runtime ladder"
        )
    version = applied_versions[-1] if applied_versions else 0
    for table in _TABLES:
        _table_posture(cur, table, role)
    _verify_flow_trigger(cur)
    return role, version


def _set_force_rls(conn, *, force: bool) -> None:
    operation = "FORCE" if force else "NO FORCE"
    cur = conn.cursor()
    for table in _TABLES:
        cur.execute(
            f"ALTER TABLE {_SCHEMA}.{table} {operation} ROW LEVEL SECURITY"
        )


def _set_flow_trigger(conn, *, enabled: bool) -> None:
    operation = "ENABLE" if enabled else "DISABLE"
    conn.cursor().execute(
        f"ALTER TABLE {_SCHEMA}.flow_authority_records {operation} "
        f"TRIGGER {_FLOW_TRIGGER}"
    )


def _verify_relationships(conn) -> None:
    cur = conn.cursor()
    offenders: dict[str, int] = {}
    for table in _DIRECT_TABLES:
        cur.execute(
            f"SELECT count(*) FROM {_SCHEMA}.{table} "
            "WHERE tenant_id IS NULL OR btrim(tenant_id) = ''"
        )
        count = int(cur.fetchone()[0])
        if count:
            offenders[f"{table}.tenant_id"] = count
    for table, (parent, foreign_key) in _RLS_CHILD_TABLES.items():
        cur.execute(
            f"SELECT count(*) FROM {_SCHEMA}.{table} AS source "
            f"LEFT JOIN {_SCHEMA}.{parent} AS tenant_parent "
            f"ON tenant_parent.id = source.{foreign_key} "
            "WHERE tenant_parent.id IS NULL OR tenant_parent.tenant_id IS NULL "
            "OR btrim(tenant_parent.tenant_id) = ''"
        )
        count = int(cur.fetchone()[0])
        if count:
            offenders[f"{table}.{foreign_key}"] = count
    for label, sql in _SECONDARY_RELATIONSHIPS:
        cur.execute(sql)
        count = int(cur.fetchone()[0])
        if count:
            offenders[label] = count
    if offenders:
        detail = ", ".join(
            f"{label}={count}" for label, count in sorted(offenders.items())
        )
        raise PostgresTenantLifecycleError(
            "cross-tenant, orphaned, or unscoped relationships must be repaired: "
            + detail
        )


def _scope_sql(table: str, *, select: str) -> str:
    if table in _RLS_CHILD_TABLES:
        parent, foreign_key = _RLS_CHILD_TABLES[table]
        return (
            f"SELECT {select} FROM {_SCHEMA}.{table} AS source "
            f"JOIN {_SCHEMA}.{parent} AS tenant_parent "
            f"ON tenant_parent.id = source.{foreign_key} "
            "WHERE tenant_parent.tenant_id = %s"
        )
    if table in _DIRECT_TABLES:
        return (
            f"SELECT {select} FROM {_SCHEMA}.{table} AS source "
            "WHERE source.tenant_id = %s"
        )
    raise PostgresTenantLifecycleError("table is outside the lifecycle allow-set")


def _counts(conn, tenant_id: str) -> dict[str, int]:
    cur = conn.cursor()
    counts: dict[str, int] = {}
    for table in _TABLES:
        cur.execute(_scope_sql(table, select="count(*)"), (tenant_id,))
        counts[table] = int(cur.fetchone()[0])
    return counts


def _base_report(
    *,
    operation: str,
    tenant_id: str,
    role: str,
    schema_version: int,
    counts: Mapping[str, int],
    now: datetime | None,
) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "scope": LIFECYCLE_SCOPE,
        "not_covered": list(SEPARATE_POSTGRES_SURFACES),
        "operation": operation,
        "tenant_sha256": hashlib.sha256(tenant_id.encode("utf-8")).hexdigest(),
        "migration_role_sha256": hashlib.sha256(role.encode("utf-8")).hexdigest(),
        "database_schema_version": schema_version,
        "schema_source_sha256": schema_source_sha256(),
        "table_manifest_sha256": table_manifest_sha256(),
        "completed_at": _utc(now),
        "counts": {table: int(counts[table]) for table in _TABLES},
        "rows_total": sum(int(counts[table]) for table in _TABLES),
    }


def _seal_report(report: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(report)
    body["report_sha256"] = _digest(body)
    return body


def inventory_tenant(
    conn,
    tenant_id: str,
    *,
    drained: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a value-free exact row inventory for one tenant."""

    tenant = validate_tenant_id(tenant_id)
    _require_transaction(conn, drained=drained)
    try:
        role, version = _admit_and_lock(conn)
        _set_force_rls(conn, force=False)
        _verify_relationships(conn)
        counts = _counts(conn, tenant)
        _set_force_rls(conn, force=True)
        conn.commit()
        return _seal_report(
            {
                **_base_report(
                    operation="inventory",
                    tenant_id=tenant,
                    role=role,
                    schema_version=version,
                    counts=counts,
                    now=now,
                ),
                "scope_complete": True,
                "complete": False,
            }
        )
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def _export_chunks(
    conn,
    tenant: str,
    *,
    drained: bool,
    now: datetime | None,
    report_box: list[dict[str, Any]],
) -> Iterator[str]:
    _require_transaction(conn, drained=drained)
    try:
        role, version = _admit_and_lock(conn)
        _set_force_rls(conn, force=False)
        _verify_relationships(conn)
        counts = _counts(conn, tenant)
        header = {
            "schema": EXPORT_SCHEMA,
            "scope": LIFECYCLE_SCOPE,
            "not_covered": list(SEPARATE_POSTGRES_SURFACES),
            "tenant_id": tenant,
            "database_schema_version": version,
            "schema_source_sha256": schema_source_sha256(),
            "table_manifest_sha256": table_manifest_sha256(),
            "exported_at": _utc(now),
            "counts": counts,
            "value_encoding": "database-native; application-sealed fields remain ciphertext",
        }
        encoded = json.dumps(
            header,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        # Replace the final object brace with the streamed table collection.
        yield encoded[:-1] + ',"tables":{'
        table_digests: dict[str, str] = {}
        for table_index, table in enumerate(_TABLES):
            if table_index:
                yield ","
            yield json.dumps(table, ensure_ascii=True) + ":["
            table_digest = hashlib.sha256(b"[")
            cursor_name = f"mvk_tenant_export_{table}"[:63]
            cur = conn.cursor(name=cursor_name)
            cur.itersize = _BATCH_SIZE
            try:
                cur.execute(
                    _scope_sql(table, select="row_to_json(source)::text")
                    + " ORDER BY source.ctid",
                    (tenant,),
                )
                seen = 0
                first = True
                while True:
                    rows = cur.fetchmany(_BATCH_SIZE)
                    if not rows:
                        break
                    for row in rows:
                        raw = row[0]
                        if not isinstance(raw, str):
                            raise PostgresTenantLifecycleError(
                                "Postgres tenant export returned a non-JSON row"
                            )
                        try:
                            json.loads(raw)
                        except (json.JSONDecodeError, TypeError, ValueError) as exc:
                            raise PostgresTenantLifecycleError(
                                "Postgres tenant export returned malformed JSON"
                            ) from exc
                        if not first:
                            yield ","
                            table_digest.update(b",")
                        table_digest.update(raw.encode("utf-8"))
                        yield raw
                        first = False
                        seen += 1
                if seen != counts[table]:
                    raise PostgresTenantLifecycleError(
                        f"tenant export count changed while locked for table {table}"
                    )
            finally:
                close = getattr(cur, "close", None)
                if callable(close):
                    close()
            yield "]"
            table_digest.update(b"]")
            table_digests[table] = table_digest.hexdigest()
        yield '},"table_sha256":'
        yield json.dumps(
            table_digests,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        yield "}"
        _set_force_rls(conn, force=True)
        conn.commit()
        report_box.append(
            _seal_report(
                {
                    **_base_report(
                        operation="export",
                        tenant_id=tenant,
                        role=role,
                        schema_version=version,
                        counts=counts,
                        now=now,
                    ),
                    "scope_complete": True,
                    "complete": False,
                    "local_backup_required": True,
                    "table_sha256": table_digests,
                }
            )
        )
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def export_tenant(
    conn,
    tenant_id: str,
    destination: str | Path,
    *,
    drained: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Atomically publish a private, exact raw-row tenant export.

    Application-sealed fields remain sealed.  The artifact is therefore an
    exact recovery/offboarding export, and must be paired with the tenant-local
    backup and governed key escrow for restore or portability.
    """

    from .file_lock import atomic_create_text_chunks, prepare_private_directory

    tenant = validate_tenant_id(tenant_id)
    path = Path(destination).expanduser()
    if not path.name:
        raise PostgresTenantLifecycleError("tenant export destination is invalid")
    try:
        prepare_private_directory(path.parent)
    except (OSError, PermissionError) as exc:
        raise PostgresTenantLifecycleError(
            "tenant export destination directory must be private"
        ) from exc
    if os.path.lexists(path):
        raise PostgresTenantLifecycleError(
            "refusing to overwrite an existing tenant export artifact"
        )
    report_box: list[dict[str, Any]] = []
    digest = hashlib.sha256()

    def hashing(chunks: Iterable[str]) -> Iterator[str]:
        for chunk in chunks:
            digest.update(chunk.encode("utf-8"))
            yield chunk

    try:
        atomic_create_text_chunks(
            path,
            hashing(
                _export_chunks(
                    conn,
                    tenant,
                    drained=drained,
                    now=now,
                    report_box=report_box,
                )
            ),
            mode=0o600,
        )
    except PostgresTenantLifecycleError:
        raise
    except Exception as exc:
        raise PostgresTenantLifecycleError(
            "private Postgres tenant export publication failed"
        ) from exc
    if len(report_box) != 1:
        raise PostgresTenantLifecycleError(
            "Postgres tenant export did not produce a committed report"
        )
    report = dict(report_box[0])
    report.pop("report_sha256", None)
    report["artifact_sha256"] = digest.hexdigest()
    return _seal_report(report)


def _delete_tenant_rows(conn, tenant_id: str) -> dict[str, int]:
    cur = conn.cursor()
    deleted: dict[str, int] = dict.fromkeys(_TABLES, 0)
    for table in _CHILD_TABLES:
        parent, foreign_key = _RLS_CHILD_TABLES[table]
        cur.execute(
            f"DELETE FROM {_SCHEMA}.{table} AS source WHERE EXISTS ("
            f"SELECT 1 FROM {_SCHEMA}.{parent} AS tenant_parent "
            f"WHERE tenant_parent.id = source.{foreign_key} "
            "AND tenant_parent.tenant_id = %s)",
            (tenant_id,),
        )
        deleted[table] = int(cur.rowcount)
    for table in _DELETE_DIRECT_ORDER:
        cur.execute(
            f"DELETE FROM {_SCHEMA}.{table} WHERE tenant_id = %s",
            (tenant_id,),
        )
        deleted[table] = int(cur.rowcount)
    return deleted


def erase_tenant(
    conn,
    tenant_id: str,
    *,
    drained: bool,
    confirmed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Erase one tenant atomically and prove exact post-delete residuals.

    Immutable subject-erasure receipts are retained by default.  They can be
    retired only through their existing deadline-enforcing retention path; this
    workflow never disables those triggers or treats retained receipts as an
    erasable residual.
    """

    tenant = validate_tenant_id(tenant_id)
    if not confirmed:
        raise PostgresTenantLifecycleError(
            "refusing Postgres tenant erasure without explicit confirmation"
        )
    _require_transaction(conn, drained=drained, erase=True)
    try:
        role, version = _admit_and_lock(conn)
        _set_force_rls(conn, force=False)
        _verify_relationships(conn)
        before = _counts(conn, tenant)
        _set_flow_trigger(conn, enabled=False)
        try:
            deleted = _delete_tenant_rows(conn, tenant)
        finally:
            _set_flow_trigger(conn, enabled=True)
        residuals = _counts(conn, tenant)
        unexpected = {
            table: count
            for table, count in residuals.items()
            if table not in _RETAINED_TABLES and count
        }
        expected_deleted = {
            table: before[table]
            for table in _TABLES
            if table not in _RETAINED_TABLES
        }
        mismatches = {
            table: (expected_deleted[table], deleted[table])
            for table in expected_deleted
            if expected_deleted[table] != deleted[table]
        }
        if unexpected or mismatches:
            raise PostgresTenantLifecycleError(
                "Postgres tenant erasure did not reach an exact zero residual"
            )
        if residuals["erasure_receipts"] != before["erasure_receipts"]:
            raise PostgresTenantLifecycleError(
                "immutable erasure-receipt retention changed during tenant erasure"
            )
        _set_force_rls(conn, force=True)
        conn.commit()
        report = {
            **_base_report(
                operation="erase",
                tenant_id=tenant,
                role=role,
                schema_version=version,
                counts=before,
                now=now,
            ),
            "deleted": {table: int(deleted[table]) for table in _TABLES},
            "residuals": {table: int(residuals[table]) for table in _TABLES},
            "retained_erasure_receipts": residuals["erasure_receipts"],
            "scope_complete": True,
            "complete": False,
            "committed": True,
        }
        return _seal_report(report)
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def validate_erasure_report(
    report: Mapping[str, Any], *, expected_tenant: str
) -> dict[str, Any]:
    """Validate the proof gate used before local tenant-tree finalization."""

    tenant = validate_tenant_id(expected_tenant)
    if not isinstance(report, Mapping):
        raise PostgresTenantLifecycleError("tenant erasure report is invalid")
    value = dict(report)
    digest = value.pop("report_sha256", None)
    if not isinstance(digest, str) or digest != _digest(value):
        raise PostgresTenantLifecycleError("tenant erasure report digest is invalid")
    expected_fields = {
        "schema",
        "scope",
        "not_covered",
        "operation",
        "tenant_sha256",
        "migration_role_sha256",
        "database_schema_version",
        "schema_source_sha256",
        "table_manifest_sha256",
        "completed_at",
        "counts",
        "rows_total",
        "deleted",
        "residuals",
        "retained_erasure_receipts",
        "scope_complete",
        "complete",
        "committed",
    }
    if (
        set(value) != expected_fields
        or value.get("schema") != REPORT_SCHEMA
        or value.get("scope") != LIFECYCLE_SCOPE
        or value.get("not_covered") != list(SEPARATE_POSTGRES_SURFACES)
        or value.get("operation") != "erase"
        or value.get("tenant_sha256")
        != hashlib.sha256(tenant.encode("utf-8")).hexdigest()
        or value.get("table_manifest_sha256") != table_manifest_sha256()
        or value.get("schema_source_sha256") != schema_source_sha256()
        or value.get("database_schema_version") != _PG_SCHEMA_VERSION
        or value.get("scope_complete") is not True
        or value.get("complete") is not False
        or value.get("committed") is not True
    ):
        raise PostgresTenantLifecycleError(
            "tenant erasure report does not authorize local finalization"
        )
    counts = value.get("counts")
    deleted = value.get("deleted")
    residuals = value.get("residuals")
    if any(
        not isinstance(inventory, Mapping) or set(inventory) != set(_TABLES)
        for inventory in (counts, deleted, residuals)
    ):
        raise PostgresTenantLifecycleError(
            "tenant erasure report table inventory is incomplete"
        )
    try:
        normalized_counts = {
            table: int(count) for table, count in counts.items()
        }
        normalized_deleted = {
            table: int(count) for table, count in deleted.items()
        }
        normalized_residuals = {
            table: int(count) for table, count in residuals.items()
        }
        invalid_number = any(
            type(raw) is not int or raw < 0
            for inventory in (counts, deleted, residuals)
            for raw in inventory.values()
        )
    except (OverflowError, TypeError, ValueError) as exc:
        raise PostgresTenantLifecycleError(
            "tenant erasure report residual inventory is invalid"
        ) from exc
    if invalid_number:
        raise PostgresTenantLifecycleError(
            "tenant erasure report table inventory is invalid"
        )
    if (
        type(value.get("rows_total")) is not int
        or value.get("rows_total") != sum(normalized_counts.values())
        or any(
            normalized_residuals[table] != 0
            or normalized_deleted[table] != normalized_counts[table]
            for table in _TABLES
            if table not in _RETAINED_TABLES
        )
        or normalized_deleted["erasure_receipts"] != 0
        or normalized_residuals["erasure_receipts"]
        != normalized_counts["erasure_receipts"]
        or type(value.get("retained_erasure_receipts")) is not int
        or value.get("retained_erasure_receipts")
        != normalized_residuals["erasure_receipts"]
    ):
        raise PostgresTenantLifecycleError(
            "tenant erasure report does not prove exact retained residuals"
        )
    return dict(report)


__all__ = [
    "EXPORT_SCHEMA",
    "LIFECYCLE_SCOPE",
    "REPORT_SCHEMA",
    "SEPARATE_POSTGRES_SURFACES",
    "PostgresTenantLifecycleError",
    "erase_tenant",
    "export_tenant",
    "inventory_tenant",
    "schema_source_sha256",
    "table_manifest",
    "table_manifest_sha256",
    "validate_erasure_report",
    "validate_tenant_id",
]
