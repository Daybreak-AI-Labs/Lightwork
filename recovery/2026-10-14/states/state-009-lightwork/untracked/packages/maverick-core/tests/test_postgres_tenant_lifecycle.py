"""Fake-only tests for the drained Postgres tenant lifecycle."""
from __future__ import annotations

import hashlib
import json
import re

import pytest
from maverick import postgres_tenant_lifecycle as lifecycle
from maverick.world_model_backends.postgres import (
    _PG_SCHEMA_VERSION,
    _RLS_CHILD_TABLES,
    _RLS_POLICY_TABLES,
    MIGRATIONS,
)


class FakeCursor:
    def __init__(self, conn, name=None):
        self.conn = conn
        self.name = name
        self.itersize = 0
        self.rowcount = 0
        self._rows = []

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.conn.sql.append((normalized, params))
        self._rows = []
        self.rowcount = 0
        if self.conn.fail_on and self.conn.fail_on in normalized:
            raise RuntimeError("postgres://operator:secret@database/private")
        if normalized == "SELECT current_schema()":
            self._rows = [(self.conn.schema,)]
        elif "FROM pg_catalog.pg_roles" in normalized:
            self._rows = [(self.conn.role, self.conn.superuser, self.conn.bypass)]
        elif normalized.startswith("SELECT to_regclass"):
            value = params[0]
            self._rows = [(None if value in self.conn.missing else value,)]
        elif (
            "c.relrowsecurity" in normalized
            and "pg_catalog.pg_class" in normalized
        ):
            table = params[0].split(".")[-1]
            self._rows = [(
                self.conn.owners.get(table, self.conn.role),
                self.conn.rls,
                self.conn.force_rls,
            )]
        elif "FROM pg_catalog.pg_policies" in normalized:
            table = params[1]
            expression = self._policy_expression(table)
            self._rows = [(
                "mvk_tenant_isolation",
                "PERMISSIVE",
                self.conn.policy_roles,
                "ALL",
                expression,
                expression,
            )]
            if self.conn.extra_policy_table == table:
                self._rows.append(
                    ("broad", "PERMISSIVE", ["public"], "ALL", "true", "true")
                )
        elif "FROM pg_catalog.pg_trigger" in normalized:
            self._rows = [(self.conn.trigger_state,)]
        elif (
            "SELECT pg_get_userbyid(c.relowner)" in normalized
            and "relrowsecurity" not in normalized
        ):
            self._rows = [(self.conn.role,)]
        elif normalized == (
            "SELECT version FROM public.schema_migrations ORDER BY version"
        ):
            self._rows = [(version,) for version in self.conn.applied_versions]
        elif normalized.startswith("SELECT count(*)"):
            table = self._table_from_count(normalized)
            if params == ("acme",) and table:
                self._rows = [(self.conn.counts.get(table, 0),)]
            else:
                label = self._consistency_label(normalized)
                self._rows = [(self.conn.offenders.get(label, 0),)]
        elif normalized.startswith("DELETE FROM public."):
            table = normalized.split("DELETE FROM public.", 1)[1].split()[0]
            self.rowcount = self.conn.counts.get(table, 0)
            self.conn.counts[table] = 0
        elif self.name and "row_to_json(source)::text" in normalized:
            table = self._table_from_count(normalized)
            count = self.conn.counts.get(table, 0)
            self._rows = [
                (json.dumps({"id": index + 1, "table": table}),)
                for index in range(count)
            ]
        return self

    def _policy_expression(self, table):
        common = "nullif(current_setting('maverick.tenant', true), '')"
        if table in _RLS_CHILD_TABLES:
            parent, foreign_key = _RLS_CHILD_TABLES[table]
            return (
                f"EXISTS (SELECT 1 FROM {parent} AS mvk_parent WHERE "
                f"mvk_parent.id = {table}.{foreign_key} AND "
                f"mvk_parent.tenant_id = {common})"
            )
        return f"tenant_id = {common}"

    @staticmethod
    def _table_from_count(sql):
        match = re.search(r"FROM public\.([a-z_]+)(?: AS source)?", sql)
        return match.group(1) if match else None

    @staticmethod
    def _consistency_label(sql):
        if "btrim(tenant_id)" in sql:
            table = FakeCursor._table_from_count(sql)
            return f"{table}.tenant_id"
        for table, (_parent, foreign_key) in _RLS_CHILD_TABLES.items():
            if f"FROM public.{table} AS source" in sql and "tenant_parent" in sql:
                return f"{table}.{foreign_key}"
        if "source.parent_id" in sql:
            return "goals.parent_id"
        if "source.project_id" in sql:
            return "goals.project_id"
        if "FROM public.facts AS source" in sql:
            return "facts.source_episode_id"
        if "FROM public.fact_history AS source" in sql:
            return "fact_history.source_episode_id"
        if "FROM public.processed_messages AS source" in sql:
            return "processed_messages.goal_id"
        if "FROM public.api_idempotency AS source" in sql:
            return "api_idempotency.goal_id"
        if "FROM public.signoff_decision_outbox AS source" in sql:
            return "signoff_decision_outbox.goal_id"
        if "FROM public.approval_audit_outbox AS source" in sql:
            return "approval_audit_outbox.approval_id"
        if "FROM public.turns AS source" in sql:
            return "turns.goal_id"
        return "unknown"

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def fetchmany(self, size):
        rows = self._rows[:size]
        del self._rows[:size]
        return rows

    def close(self):
        return None


class FakeConnection:
    autocommit = False

    def __init__(self, counts=None):
        self.schema = "public"
        self.role = "maverick_migrator"
        self.superuser = False
        self.bypass = False
        self.rls = True
        self.force_rls = True
        self.trigger_state = "O"
        self.version = _PG_SCHEMA_VERSION
        self.migration_count = len(MIGRATIONS)
        self.applied_versions = [version for version, _statements in MIGRATIONS]
        self.owners = {}
        self.missing = set()
        self.extra_policy_table = None
        self.policy_roles = ["public"]
        self.offenders = {}
        self.fail_on = None
        self.counts = dict.fromkeys(_RLS_POLICY_TABLES, 0)
        self.counts.update(counts or {})
        self.sql = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self, name=None):
        return FakeCursor(self, name=name)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _all_one_counts():
    counts = dict.fromkeys(_RLS_POLICY_TABLES, 1)
    counts["erasure_receipts"] = 2
    return counts


def test_manifest_exactly_covers_runtime_rls_and_flow_authority():
    manifest = lifecycle.table_manifest()
    covered = set(manifest["direct"]) | set(manifest["children"])
    assert covered == set(_RLS_POLICY_TABLES)
    assert "flow_authority_records" in covered
    assert "api_idempotency" in covered
    assert "signoff_decision_outbox" in covered
    assert manifest["retained_by_default"] == ["erasure_receipts"]
    assert manifest["not_covered"] == ["governed_records"]


def test_inventory_is_value_free_and_restores_forced_rls():
    conn = FakeConnection(_all_one_counts())

    report = lifecycle.inventory_tenant(conn, "acme", drained=True)

    assert report["counts"] == _all_one_counts()
    assert report["rows_total"] == len(_RLS_POLICY_TABLES) + 1
    assert "acme" not in json.dumps(report)
    assert conn.role not in json.dumps(report)
    assert conn.commits == 1 and conn.rollbacks == 0
    sql = [item[0] for item in conn.sql]
    assert sum(" NO FORCE ROW LEVEL SECURITY" in item for item in sql) == len(
        _RLS_POLICY_TABLES
    )
    assert sum(" FORCE ROW LEVEL SECURITY" in item for item in sql) == 2 * len(
        _RLS_POLICY_TABLES
    )


def test_inventory_refuses_extra_permissive_policy_and_rolls_back():
    conn = FakeConnection()
    conn.extra_policy_table = "goals"

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="one exact RLS policy",
    ):
        lifecycle.inventory_tenant(conn, "acme", drained=True)

    assert conn.commits == 0 and conn.rollbacks == 1


def test_inventory_requires_the_exact_migration_version_set():
    conn = FakeConnection()
    conn.applied_versions[1] = 12

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="exact runtime ladder",
    ):
        lifecycle.inventory_tenant(conn, "acme", drained=True)

    assert conn.rollbacks == 1


def test_inventory_refuses_policy_not_granted_to_public():
    conn = FakeConnection()
    conn.policy_roles = ["unrelated_role"]

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="not exact and fail-closed",
    ):
        lifecycle.inventory_tenant(conn, "acme", drained=True)

    assert conn.rollbacks == 1


def test_inventory_refuses_cross_tenant_secondary_relationship():
    conn = FakeConnection()
    conn.offenders["turns.goal_id"] = 1

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="cross-tenant",
    ):
        lifecycle.inventory_tenant(conn, "acme", drained=True)

    assert conn.rollbacks == 1


def test_export_streams_exact_private_artifact(monkeypatch, tmp_path):
    from maverick.file_lock import ensure_private_directory

    private = ensure_private_directory(tmp_path / "private")
    destination = private / "acme.json"
    conn = FakeConnection({"goals": 2, "flow_authority_records": 1})

    report = lifecycle.export_tenant(
        conn,
        "acme",
        destination,
        drained=True,
    )

    artifact = destination.read_bytes()
    payload = json.loads(artifact)
    assert payload["tenant_id"] == "acme"
    assert payload["scope"] == lifecycle.LIFECYCLE_SCOPE
    assert payload["not_covered"] == ["governed_records"]
    assert len(payload["tables"]["goals"]) == 2
    assert len(payload["tables"]["flow_authority_records"]) == 1
    assert set(payload["tables"]) == set(_RLS_POLICY_TABLES)
    assert payload["value_encoding"].endswith("fields remain ciphertext")
    assert payload["schema_source_sha256"] == lifecycle.schema_source_sha256()
    assert payload["table_sha256"] == report["table_sha256"]
    assert set(payload["table_sha256"]) == set(_RLS_POLICY_TABLES)
    assert all(len(value) == 64 for value in payload["table_sha256"].values())
    assert report["artifact_sha256"] == hashlib.sha256(artifact).hexdigest()
    body = dict(report)
    digest = body.pop("report_sha256")
    assert digest == lifecycle._digest(body)
    assert conn.commits == 1


def test_export_refuses_existing_destination_without_db_access(tmp_path):
    destination = tmp_path / "exists.json"
    destination.write_text("retain", encoding="utf-8")
    conn = FakeConnection()

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="overwrite",
    ):
        lifecycle.export_tenant(conn, "acme", destination, drained=True)

    assert destination.read_text(encoding="utf-8") == "retain"
    assert not conn.sql


def test_export_never_overwrites_a_concurrently_planted_destination(
    monkeypatch, tmp_path,
):
    from maverick import file_lock

    destination = tmp_path / "race.json"
    conn = FakeConnection()
    real_create = file_lock.atomic_create_text_chunks

    def plant_then_create(path, chunks, **kwargs):
        destination.write_text("planted", encoding="utf-8")
        return real_create(path, chunks, **kwargs)

    monkeypatch.setattr(file_lock, "atomic_create_text_chunks", plant_then_create)

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="publication failed",
    ):
        lifecycle.export_tenant(conn, "acme", destination, drained=True)

    assert destination.read_text(encoding="utf-8") == "planted"


def test_erase_deletes_every_scope_but_retains_immutable_receipts():
    conn = FakeConnection(_all_one_counts())

    report = lifecycle.erase_tenant(
        conn,
        "acme",
        drained=True,
        confirmed=True,
    )

    assert report["scope_complete"] is True
    assert report["complete"] is False and report["committed"] is True
    assert report["residuals"] == {
        **dict.fromkeys(_RLS_POLICY_TABLES, 0),
        "erasure_receipts": 2,
    }
    assert report["deleted"]["erasure_receipts"] == 0
    assert report["retained_erasure_receipts"] == 2
    lifecycle.validate_erasure_report(report, expected_tenant="acme")
    sql = [item[0] for item in conn.sql]
    disable = "ALTER TABLE public.flow_authority_records DISABLE TRIGGER"
    enable = "ALTER TABLE public.flow_authority_records ENABLE TRIGGER"
    assert sum(item.startswith(disable) for item in sql) == 1
    assert sum(item.startswith(enable) for item in sql) == 1
    assert not any("DELETE FROM public.erasure_receipts" in item for item in sql)


def test_zero_row_erasure_rerun_is_successful():
    conn = FakeConnection({"erasure_receipts": 3})

    report = lifecycle.erase_tenant(
        conn,
        "acme",
        drained=True,
        confirmed=True,
    )

    assert report["rows_total"] == 3
    assert sum(report["deleted"].values()) == 0
    assert report["residuals"]["erasure_receipts"] == 3
    assert conn.commits == 1


def test_logically_inconsistent_redigested_erasure_report_is_refused():
    conn = FakeConnection(_all_one_counts())
    report = lifecycle.erase_tenant(
        conn,
        "acme",
        drained=True,
        confirmed=True,
    )
    body = dict(report)
    body.pop("report_sha256")
    body["residuals"]["goals"] = 1
    forged = lifecycle._seal_report(body)

    with pytest.raises(
        lifecycle.PostgresTenantLifecycleError,
        match="exact retained residuals",
    ):
        lifecycle.validate_erasure_report(forged, expected_tenant="acme")


def test_erase_failure_rolls_back_and_restores_flow_trigger():
    conn = FakeConnection(_all_one_counts())
    conn.fail_on = "DELETE FROM public.goal_events"

    with pytest.raises(RuntimeError, match="operator:secret"):
        lifecycle.erase_tenant(
            conn,
            "acme",
            drained=True,
            confirmed=True,
        )

    sql = [item[0] for item in conn.sql]
    assert any("ENABLE TRIGGER flow_authority" in item for item in sql)
    assert conn.commits == 0 and conn.rollbacks == 1


@pytest.mark.parametrize("tenant", ["", " acme", "acme ", "a\n"])
def test_tenant_id_is_strict(tenant):
    conn = FakeConnection()
    with pytest.raises(lifecycle.PostgresTenantLifecycleError):
        lifecycle.inventory_tenant(conn, tenant, drained=True)
    assert not conn.sql


def test_operations_require_drained_transaction_and_confirmation():
    conn = FakeConnection()
    with pytest.raises(lifecycle.PostgresTenantLifecycleError, match="drained"):
        lifecycle.inventory_tenant(conn, "acme", drained=False)
    with pytest.raises(lifecycle.PostgresTenantLifecycleError, match="confirmation"):
        lifecycle.erase_tenant(
            conn,
            "acme",
            drained=True,
            confirmed=False,
        )
    conn.autocommit = True
    with pytest.raises(lifecycle.PostgresTenantLifecycleError, match="autocommit"):
        lifecycle.inventory_tenant(conn, "acme", drained=True)
