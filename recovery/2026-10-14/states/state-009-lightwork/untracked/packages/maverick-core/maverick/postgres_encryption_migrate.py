"""Whole-database Postgres sealed-field inventory and rewrite.

The serving role is intentionally unable to bypass forced tenant RLS, so a
runtime startup check can only attest rows visible to its current tenant.  This
module is the migration-owner maintenance path: it takes an exclusive lock on
every sensitive table, transactionally suspends *owner* enforcement of forced
RLS, authenticates every sealed value under its row tenant, rewrites legacy
plaintext, verifies the result, restores forced RLS, and only then commits.

Run it only from a drained, separately credentialled migration job.  The
transaction and access-exclusive locks mean a failure restores both RLS and
trigger posture before any serving query can proceed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .crypto_at_rest import (
    EncryptionUnavailable,
    at_rest_enabled,
    is_sealed_str,
    per_tenant_at_rest,
    seal_to_str,
    unseal_from_str,
)
from .paths import tenant_scope
from .world_model_backends.postgres import (
    _MIGRATION_ADVISORY_LOCK,
    _PG_SCHEMA_VERSION,
    _SEALED_TEXT_COLUMNS,
    MIGRATIONS,
    validate_applied_migration_prefix,
)

_SCHEMA = "public"
_BATCH_SIZE = 500
_IMMUTABLE_TABLES = frozenset({"flow_authority_records"})
_IMMUTABLE_TRIGGERS: dict[str, tuple[str, ...]] = {
    "flow_authority_records": ("flow_authority_no_immutable_update",),
}

# Some older Postgres tables inherit tenant identity through a parent FK rather
# than carrying tenant_id themselves.  The exclusive migration transaction
# derives that identity with a LEFT JOIN so an orphan or nullable parent is
# visible and refused instead of silently skipped.
_TENANT_SOURCES: dict[str, tuple[str, str]] = {
    "goals": ("source.tenant_id", ""),
    "facts": ("source.tenant_id", ""),
    "approvals": ("source.tenant_id", ""),
    "projects": ("source.tenant_id", ""),
    "fact_history": ("source.tenant_id", ""),
    "flow_authority_records": ("source.tenant_id", ""),
    "api_idempotency": ("source.tenant_id", ""),
    "signoff_decision_outbox": ("source.tenant_id", ""),
    "turns": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.conversations AS tenant_parent "
        "ON tenant_parent.id = source.conversation_id",
    ),
    "messages": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
    "questions": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
    "goal_events": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
    "episodes": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
    "signoffs": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
    "artifacts": (
        "tenant_parent.tenant_id",
        "LEFT JOIN public.goals AS tenant_parent ON tenant_parent.id = source.goal_id",
    ),
}


class PostgresEncryptionMigrationError(RuntimeError):
    """The privileged inventory could not establish a complete safe result."""


def column_manifest_sha256() -> str:
    """Digest the exact table/column inventory used by the migration report."""

    payload = json.dumps(
        [list(item) for item in _SEALED_TEXT_COLUMNS],
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _tables() -> tuple[str, ...]:
    tables = tuple(dict.fromkeys(table for table, _column in _SEALED_TEXT_COLUMNS))
    missing = sorted(set(tables) - set(_TENANT_SOURCES))
    if missing:
        raise PostgresEncryptionMigrationError(
            f"sealed-field tables lack a tenant derivation: {missing}"
        )
    return tables


def _qualified(table: str) -> str:
    if table not in _tables():  # fixed allow-set; never interpolate caller input
        raise PostgresEncryptionMigrationError(
            f"unrecognized sealed-field table {table!r}"
        )
    return f"{_SCHEMA}.{table}"


def _verify_exact_catalog(cur, *, head: int) -> None:
    from .postgres_schema_manifest import (
        PostgresSchemaManifestError,
        verify_catalog_manifest,
    )

    try:
        verify_catalog_manifest(cur, head=head)
    except PostgresSchemaManifestError as exc:
        raise PostgresEncryptionMigrationError(
            "live Postgres catalog differs from the reviewed schema fingerprint"
        ) from exc


def _verify_migration_owner(conn) -> tuple[str, int]:
    """Require the exact schema and a non-superuser owner of every table."""

    with conn.cursor() as cur:
        cur.execute("SELECT current_schema()")
        row = cur.fetchone()
        if not row or row[0] != _SCHEMA:
            raise PostgresEncryptionMigrationError(
                "Postgres encryption migration requires current_schema()=public"
            )
        cur.execute(
            "SELECT rolname, rolsuper, rolbypassrls FROM pg_catalog.pg_roles "
            "WHERE rolname = current_user"
        )
        role_row = cur.fetchone()
        if not role_row:
            raise PostgresEncryptionMigrationError(
                "could not resolve the Postgres migration role"
            )
        role, superuser, bypass_rls = role_row
        if superuser or bypass_rls:
            raise PostgresEncryptionMigrationError(
                "migration role must be NOSUPERUSER and NOBYPASSRLS"
            )
        cur.execute(
            "SELECT version FROM public.schema_migrations ORDER BY version"
        )
        applied = tuple(row[0] for row in cur.fetchall())
        try:
            validate_applied_migration_prefix(iter(applied), MIGRATIONS)
        except ValueError as exc:
            raise PostgresEncryptionMigrationError(
                "database schema migration ledger is not an exact reviewed prefix"
            ) from exc
        expected = tuple(version for version, _statements in MIGRATIONS)
        if applied != expected:
            found = applied[-1] if applied else 0
            raise PostgresEncryptionMigrationError(
                "database schema version differs from the exact runtime ladder: "
                f"found {found}, expected {_PG_SCHEMA_VERSION}"
            )
        _verify_exact_catalog(cur, head=_PG_SCHEMA_VERSION)
        for table in _tables():
            cur.execute(
                "SELECT pg_get_userbyid(c.relowner), c.relrowsecurity, "
                "c.relforcerowsecurity FROM pg_catalog.pg_class AS c "
                "WHERE c.oid = to_regclass(%s)",
                (f"{_SCHEMA}.{table}",),
            )
            metadata = cur.fetchone()
            if not metadata:
                raise PostgresEncryptionMigrationError(
                    f"required sealed-field table is missing: {_qualified(table)}"
                )
            owner, rls_enabled, rls_forced = metadata
            if owner != role:
                raise PostgresEncryptionMigrationError(
                    f"migration role does not own {_qualified(table)}"
                )
            if not rls_enabled or not rls_forced:
                raise PostgresEncryptionMigrationError(
                    f"{_qualified(table)} must have enabled and forced RLS"
                )
    return str(role), applied[-1]


def _configure_and_lock(conn) -> None:
    tables = ", ".join(_qualified(table) for table in sorted(_tables()))
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SET LOCAL statement_timeout = '30min'")
        # Use the schema runner's admission lock.  A distinct encryption-only
        # key would allow a schema transaction to cross this posture check.
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            (_MIGRATION_ADVISORY_LOCK,),
        )
        cur.execute(f"LOCK TABLE {tables} IN ACCESS EXCLUSIVE MODE")


def _verify_immutable_triggers(conn) -> None:
    """Require the exact enabled user-trigger set maintenance suspends."""

    with conn.cursor() as cur:
        for table in sorted(_IMMUTABLE_TABLES.intersection(_tables())):
            cur.execute(
                "SELECT t.tgname, t.tgenabled "
                "FROM pg_catalog.pg_trigger AS t "
                "WHERE t.tgrelid = to_regclass(%s) AND NOT t.tgisinternal "
                "ORDER BY t.tgname",
                (f"{_SCHEMA}.{table}",),
            )
            actual = tuple((str(name), str(enabled)) for name, enabled in cur.fetchall())
            expected = tuple((name, "O") for name in _IMMUTABLE_TRIGGERS[table])
            if actual != expected:
                raise PostgresEncryptionMigrationError(
                    f"{_qualified(table)} has an inexact or disabled immutable "
                    "trigger set"
                )


def _set_force_rls(conn, *, force: bool) -> None:
    operation = "FORCE" if force else "NO FORCE"
    with conn.cursor() as cur:
        for table in _tables():
            cur.execute(
                f"ALTER TABLE {_qualified(table)} {operation} ROW LEVEL SECURITY"
            )


def _set_immutable_triggers(conn, *, enabled: bool) -> None:
    operation = "ENABLE" if enabled else "DISABLE"
    with conn.cursor() as cur:
        for table in sorted(_IMMUTABLE_TABLES.intersection(_tables())):
            cur.execute(
                f"ALTER TABLE {_qualified(table)} {operation} TRIGGER USER"
            )


def _scan_column(
    conn,
    table: str,
    column: str,
    *,
    rewrite: bool,
    pass_number: int,
) -> dict[str, int]:
    """Authenticate one column and optionally replace plaintext in batches."""

    counters = {"rows": 0, "sealed_verified": 0, "plaintext_found": 0, "rewritten": 0}
    cursor_name = f"mvk_enc_{pass_number}_{table}_{column}"[:63]
    tenant_expression, join = _TENANT_SOURCES[table]
    with conn.cursor(name=cursor_name) as reader, conn.cursor() as writer:
        reader.itersize = _BATCH_SIZE
        reader.execute(
            f"SELECT source.ctid::text, {tenant_expression}, source.{column} "
            f"FROM {_qualified(table)} AS source {join} "
            f"WHERE source.{column} IS NOT NULL ORDER BY source.ctid"
        )
        while True:
            rows = reader.fetchmany(_BATCH_SIZE)
            if not rows:
                break
            for ctid, tenant_id, value in rows:
                counters["rows"] += 1
                tenant = str(tenant_id or "").strip()
                if not tenant:
                    raise PostgresEncryptionMigrationError(
                        f"{_qualified(table)}.{column} contains an unscoped row"
                    )
                if not isinstance(value, str):
                    raise PostgresEncryptionMigrationError(
                        f"{_qualified(table)}.{column} contains a non-text value"
                    )
                with tenant_scope(tenant=tenant):
                    if is_sealed_str(value):
                        try:
                            unseal_from_str(value)
                        except Exception as exc:
                            raise PostgresEncryptionMigrationError(
                                f"{_qualified(table)}.{column} contains ciphertext "
                                "that cannot be authenticated for its tenant"
                            ) from exc
                        counters["sealed_verified"] += 1
                        continue
                    counters["plaintext_found"] += 1
                    if not rewrite:
                        continue
                    try:
                        sealed = seal_to_str(value)
                    except Exception as exc:
                        raise PostgresEncryptionMigrationError(
                            f"could not seal {_qualified(table)}.{column}"
                        ) from exc
                writer.execute(
                    f"UPDATE {_qualified(table)} SET {column} = %s "
                    "WHERE ctid = %s::tid",
                    (sealed, ctid),
                )
                if writer.rowcount != 1:
                    raise PostgresEncryptionMigrationError(
                        f"lost the locked row while rewriting "
                        f"{_qualified(table)}.{column}"
                    )
                counters["rewritten"] += 1
    return counters


def migrate_postgres_encryption(
    conn,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inventory or rewrite all tenant-scoped Postgres sealed fields.

    The caller supplies a fresh, non-autocommit migration-owner connection and
    retains the returned JSON-compatible report as deployment evidence.
    ``dry_run`` always rolls the transaction back and reports ``ready=False``
    when any plaintext remains. A write run commits only after a second full
    authentication pass reports zero plaintext.
    """

    if not at_rest_enabled():
        raise EncryptionUnavailable("Postgres migration requires at-rest encryption")
    if not per_tenant_at_rest():
        raise EncryptionUnavailable(
            "Postgres migration requires per-tenant envelope encryption"
        )
    if getattr(conn, "autocommit", False):
        raise PostgresEncryptionMigrationError(
            "Postgres encryption migration requires autocommit=False"
        )

    try:
        _configure_and_lock(conn)
        role, schema_version = _verify_migration_owner(conn)
        _verify_immutable_triggers(conn)
        _set_force_rls(conn, force=False)
        if not dry_run:
            _set_immutable_triggers(conn, enabled=False)

        columns: dict[str, dict[str, int]] = {}
        for table, column in _SEALED_TEXT_COLUMNS:
            columns[f"{table}.{column}"] = _scan_column(
                conn,
                table,
                column,
                rewrite=not dry_run,
                pass_number=1,
            )

        plaintext_found = sum(
            item["plaintext_found"] for item in columns.values()
        )
        if dry_run:
            _set_force_rls(conn, force=True)
            role_after, schema_version_after = _verify_migration_owner(conn)
            _verify_immutable_triggers(conn)
            if (role_after, schema_version_after) != (role, schema_version):
                raise PostgresEncryptionMigrationError(
                    "migration-owner or schema version drifted during inventory"
                )
            conn.rollback()
            plaintext_remaining = plaintext_found
        else:
            _set_immutable_triggers(conn, enabled=True)
            verification = {
                f"{table}.{column}": _scan_column(
                    conn,
                    table,
                    column,
                    rewrite=False,
                    pass_number=2,
                )
                for table, column in _SEALED_TEXT_COLUMNS
            }
            plaintext_remaining = sum(
                item["plaintext_found"] for item in verification.values()
            )
            if plaintext_remaining:
                raise PostgresEncryptionMigrationError(
                    "plaintext remained after the Postgres encryption rewrite"
                )
            _set_force_rls(conn, force=True)
            role_after, schema_version_after = _verify_migration_owner(conn)
            _verify_immutable_triggers(conn)
            if (role_after, schema_version_after) != (role, schema_version):
                raise PostgresEncryptionMigrationError(
                    "migration-owner or schema version drifted during rewrite"
                )
            conn.commit()

        completed = now or datetime.now(timezone.utc)
        if completed.tzinfo is None:
            completed = completed.replace(tzinfo=timezone.utc)
        return {
            "schema": "lightwork.postgres-encryption-migration.v1",
            "mode": "inventory" if dry_run else "rewrite",
            "database_schema_version": schema_version,
            "migration_role": role,
            "column_manifest_sha256": column_manifest_sha256(),
            "completed_at": completed.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "columns": columns,
            "plaintext_found": plaintext_found,
            "plaintext_remaining": plaintext_remaining,
            "ready": plaintext_remaining == 0,
        }
    except BaseException:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


__all__ = [
    "PostgresEncryptionMigrationError",
    "column_manifest_sha256",
    "migrate_postgres_encryption",
]
