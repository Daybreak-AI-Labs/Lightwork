"""Privileged whole-Postgres sealed-field inventory and rewrite."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from maverick import postgres_encryption_migrate as migration
from maverick.paths import current_tenant_id


class _Cursor:
    def __init__(self, conn: _Connection, *, name: str | None = None) -> None:
        self.conn = conn
        self.name = name
        self.results: list[tuple] = []
        self.offset = 0
        self.rowcount = 0
        self.itersize = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self.conn.calls.append((sql, params, self.name))
        normalized = " ".join(sql.split())
        lowered = normalized.lower()
        self.rowcount = 0
        self.results = []
        self.offset = 0
        if lowered == "select current_schema()":
            self.results = [(self.conn.schema,)]
        elif "from pg_catalog.pg_roles" in lowered:
            self.results = [
                (self.conn.role, self.conn.superuser, self.conn.bypass_rls)
            ]
        elif lowered == (
            "select version from public.schema_migrations order by version"
        ):
            self.results = [(version,) for version in self.conn.applied_versions]
        elif "from pg_catalog.pg_class" in lowered:
            table = str((params or ("",))[0]).split(".")[-1]
            metadata = self.conn.metadata.get(table)
            if metadata is not None:
                self.results = [metadata]
        elif "from pg_catalog.pg_trigger" in lowered:
            table = str((params or ("",))[0]).split(".")[-1]
            self.results = list(self.conn.triggers.get(table, ()))
        elif lowered.startswith("select source.ctid::text"):
            before_from, after_from = normalized.split(" FROM ", 1)
            column = before_from.split(",")[-1].strip().split(".")[-1]
            table = after_from.split()[0].split(".")[-1]
            self.results = [tuple(row) for row in self.conn.rows[(table, column)]]
        elif lowered.startswith("update public."):
            table = normalized.split()[1].split(".")[-1]
            column = normalized.split(" SET ", 1)[1].split(" =", 1)[0]
            value, ctid = params or (None, None)
            if not self.conn.lose_updates:
                for row in self.conn.rows[(table, column)]:
                    if row[0] == ctid:
                        row[2] = value
                        self.rowcount = 1
                        break

    def fetchone(self):
        if not self.results:
            return None
        return self.results[0]

    def fetchmany(self, size: int):
        batch = self.results[self.offset : self.offset + size]
        self.offset += len(batch)
        return batch

    def fetchall(self):
        return list(self.results)


class _Connection:
    def __init__(self) -> None:
        self.autocommit = False
        self.schema = "public"
        self.role = "maverick_migrator"
        self.superuser = False
        self.bypass_rls = False
        self.applied_versions = [1, 32]
        self.metadata = {
            "goals": (self.role, True, True),
            "flow_authority_records": (self.role, True, True),
        }
        self.triggers = {
            "flow_authority_records": [
                ("flow_authority_no_immutable_update", "O")
            ],
        }
        self.rows = {
            ("goals", "title"): [],
            ("flow_authority_records", "document"): [],
        }
        self.calls: list[tuple[str, tuple | None, str | None]] = []
        self.commits = 0
        self.rollbacks = 0
        self.lose_updates = False

    def cursor(self, name: str | None = None):
        return _Cursor(self, name=name)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


@pytest.fixture(autouse=True)
def _small_inventory(monkeypatch):
    monkeypatch.setattr(
        migration,
        "_SEALED_TEXT_COLUMNS",
        (("goals", "title"), ("flow_authority_records", "document")),
    )
    monkeypatch.setattr(migration, "_PG_SCHEMA_VERSION", 32)
    monkeypatch.setattr(migration, "MIGRATIONS", [(1, []), (32, [])])
    monkeypatch.setattr(migration, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(migration, "per_tenant_at_rest", lambda: True)
    monkeypatch.setattr(
        migration,
        "_verify_exact_catalog",
        lambda _cur, *, head: None,
    )

    def _is_sealed(value: object) -> bool:
        return isinstance(value, str) and value.startswith("sealed:")

    def _unseal(value: str) -> str:
        _prefix, tenant, plaintext = value.split(":", 2)
        if tenant != current_tenant_id() or plaintext == "tampered":
            raise ValueError("authentication failed")
        return plaintext

    def _seal(value: str) -> str:
        return f"sealed:{current_tenant_id()}:{value}"

    monkeypatch.setattr(migration, "is_sealed_str", _is_sealed)
    monkeypatch.setattr(migration, "unseal_from_str", _unseal)
    monkeypatch.setattr(migration, "seal_to_str", _seal)


def _run(conn: _Connection, *, dry_run: bool = False):
    return migration.migrate_postgres_encryption(
        conn,
        dry_run=dry_run,
        now=datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc),
    )


def test_dry_run_inventories_every_tenant_without_rewriting() -> None:
    conn = _Connection()
    conn.rows[("goals", "title")] = [
        ["(1,1)", "acme", "plaintext"],
        ["(1,2)", "beta", "sealed:beta:already"],
    ]

    report = _run(conn, dry_run=True)

    assert report["mode"] == "inventory"
    assert report["plaintext_found"] == 1
    assert report["plaintext_remaining"] == 1
    assert report["ready"] is False
    assert conn.rows[("goals", "title")][0][2] == "plaintext"
    assert conn.commits == 0
    assert conn.rollbacks == 1
    sql = "\n".join(call[0] for call in conn.calls)
    assert "ACCESS EXCLUSIVE MODE" in sql
    assert "NO FORCE ROW LEVEL SECURITY" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "DISABLE TRIGGER USER" not in sql


def test_rewrite_authenticates_and_seals_then_commits_with_controls_restored() -> None:
    conn = _Connection()
    conn.rows[("goals", "title")] = [
        ["(1,1)", "acme", "plaintext"],
        ["(1,2)", "beta", "sealed:beta:already"],
    ]
    conn.rows[("flow_authority_records", "document")] = [
        ["(2,1)", "acme", "flow document"],
    ]

    report = _run(conn)

    assert report["mode"] == "rewrite"
    assert report["plaintext_found"] == 2
    assert report["plaintext_remaining"] == 0
    assert report["ready"] is True
    assert report["completed_at"] == "2026-08-03T12:00:00Z"
    assert conn.rows[("goals", "title")][0][2] == "sealed:acme:plaintext"
    assert conn.rows[("flow_authority_records", "document")][0][2] == (
        "sealed:acme:flow document"
    )
    assert conn.commits == 1
    assert conn.rollbacks == 0
    sql = "\n".join(call[0] for call in conn.calls)
    assert sql.index("DISABLE TRIGGER USER") < sql.index("UPDATE public")
    assert sql.index("ENABLE TRIGGER USER") < sql.rindex(
        "FORCE ROW LEVEL SECURITY"
    )


def test_tampered_ciphertext_rolls_back_without_committing() -> None:
    conn = _Connection()
    conn.rows[("goals", "title")] = [
        ["(1,1)", "acme", "sealed:acme:tampered"],
    ]

    with pytest.raises(
        migration.PostgresEncryptionMigrationError,
        match="cannot be authenticated",
    ):
        _run(conn)

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_unscoped_sensitive_row_is_never_assigned_an_implicit_tenant() -> None:
    conn = _Connection()
    conn.rows[("goals", "title")] = [["(1,1)", None, "plaintext"]]

    with pytest.raises(
        migration.PostgresEncryptionMigrationError,
        match="unscoped row",
    ):
        _run(conn)

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_lost_locked_update_rolls_back() -> None:
    conn = _Connection()
    conn.lose_updates = True
    conn.rows[("goals", "title")] = [["(1,1)", "acme", "plaintext"]]

    with pytest.raises(
        migration.PostgresEncryptionMigrationError,
        match="lost the locked row",
    ):
        _run(conn)

    assert conn.commits == 0
    assert conn.rollbacks == 1


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda conn: setattr(conn, "autocommit", True), "autocommit=False"),
        (lambda conn: setattr(conn, "schema", "attacker"), "current_schema"),
        (lambda conn: setattr(conn, "superuser", True), "NOSUPERUSER"),
        (lambda conn: setattr(conn, "bypass_rls", True), "NOBYPASSRLS"),
        (lambda conn: setattr(conn, "applied_versions", [1]), "schema version"),
        (
            lambda conn: setattr(conn, "applied_versions", [32]),
            "migration ledger",
        ),
        (
            lambda conn: setattr(conn, "applied_versions", [1, 1, 32]),
            "migration ledger",
        ),
        (
            lambda conn: conn.metadata.__setitem__(
                "goals", ("other-owner", True, True)
            ),
            "does not own",
        ),
        (
            lambda conn: conn.metadata.__setitem__(
                "goals", (conn.role, True, False)
            ),
            "enabled and forced RLS",
        ),
        (lambda conn: conn.metadata.pop("goals"), "table is missing"),
    ],
)
def test_migration_owner_posture_is_fail_closed(mutate, message: str) -> None:
    conn = _Connection()
    mutate(conn)

    with pytest.raises(Exception, match=message):
        _run(conn)

    assert conn.commits == 0
    assert conn.rollbacks == (0 if conn.autocommit else 1)


def test_shared_schema_lock_precedes_posture_and_posture_is_rechecked() -> None:
    conn = _Connection()

    _run(conn)

    sql = [" ".join(call[0].split()) for call in conn.calls]
    lock_index = next(
        index for index, statement in enumerate(sql)
        if statement == "SELECT pg_advisory_xact_lock(%s)"
    )
    table_lock_index = next(
        index for index, statement in enumerate(sql)
        if statement.startswith("LOCK TABLE ")
    )
    ledger_indexes = [
        index for index, statement in enumerate(sql)
        if statement == "SELECT version FROM public.schema_migrations ORDER BY version"
    ]
    trigger_indexes = [
        index for index, statement in enumerate(sql)
        if "FROM pg_catalog.pg_trigger" in statement
    ]
    assert conn.calls[lock_index][1] == (migration._MIGRATION_ADVISORY_LOCK,)
    assert lock_index < table_lock_index < ledger_indexes[0]
    assert len(ledger_indexes) == 2
    assert len(trigger_indexes) == 2
    assert ledger_indexes[-1] < trigger_indexes[-1]
    assert sql[-1].startswith("SELECT t.tgname, t.tgenabled")


def test_disabled_immutable_trigger_is_refused_under_lock() -> None:
    conn = _Connection()
    conn.triggers["flow_authority_records"] = [
        ("flow_authority_no_immutable_update", "D")
    ]

    with pytest.raises(
        migration.PostgresEncryptionMigrationError,
        match="inexact or disabled immutable trigger",
    ):
        _run(conn)

    sql = [" ".join(call[0].split()) for call in conn.calls]
    assert sql.index("SELECT pg_advisory_xact_lock(%s)") < next(
        index for index, statement in enumerate(sql)
        if "FROM pg_catalog.pg_trigger" in statement
    )
    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_manifest_digest_changes_when_inventory_changes(monkeypatch) -> None:
    first = migration.column_manifest_sha256()
    monkeypatch.setattr(
        migration,
        "_SEALED_TEXT_COLUMNS",
        migration._SEALED_TEXT_COLUMNS + (("facts", "value"),),
    )
    assert migration.column_manifest_sha256() != first


def test_real_sealed_inventory_has_an_exact_tenant_derivation() -> None:
    from maverick.world_model_backends.postgres import (
        _SEALED_TEXT_COLUMNS,
    )

    assert {table for table, _column in _SEALED_TEXT_COLUMNS} == set(
        migration._TENANT_SOURCES
    )
    assert ("flow_authority_records", "document") in _SEALED_TEXT_COLUMNS
    assert ("signoff_decision_outbox", "result_json") in _SEALED_TEXT_COLUMNS
