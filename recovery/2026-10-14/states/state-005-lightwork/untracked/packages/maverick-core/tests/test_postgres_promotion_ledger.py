"""Shared PromotionLedger invariants without requiring a live Postgres service."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from maverick import adapter_rung as ar
from maverick import self_improvement as si


class _Rows:
    def __init__(self, rows: list[object] | None = None):
        self._rows = list(rows or [])
        self.rowcount = -1

    def fetchone(self) -> object | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[object]:
        return list(self._rows)


def _policy() -> dict[str, object]:
    expression = (
        "tenant_id = nullif(current_setting('maverick.tenant', true), '')"
    )
    return {
        "policyname": si._POSTGRES_PROMOTION_POLICY,
        "permissive": "PERMISSIVE",
        "roles": ["public"],
        "cmd": "ALL",
        "qual": expression,
        "with_check": expression,
    }


class _Catalog:
    def __init__(
        self,
        *,
        ready: bool = True,
        partial: bool = False,
        privileged: bool = False,
        bypass_rls: bool = False,
        extra_policy: bool = False,
        bad_columns: bool = False,
        bad_constraint: bool = False,
        disabled_trigger: bool = False,
        runtime_owner: bool = False,
        owner_member: bool = False,
        runtime_schema_owner: bool = False,
        schema_owner_member: bool = False,
        schema_create: bool = False,
        database_create: bool = False,
        database_temp: bool = False,
        public_table_grant: bool = False,
        unexpected_event_update: bool = False,
        table_grant_option: bool = False,
        runtime_function_execute: bool = False,
        public_function_execute: bool = False,
        owner_drift: bool = False,
    ) -> None:
        if ready:
            self.tables = set(si._POSTGRES_PROMOTION_TABLES)
        elif partial:
            self.tables = {si._POSTGRES_PROMOTION_TABLES[0]}
        else:
            self.tables: set[str] = set()
        self.rls = set(self.tables)
        self.policies = set(self.tables)
        self.trigger = ready
        self.function = ready
        self.privileged = privileged
        self.bypass_rls = bypass_rls
        self.extra_policy = extra_policy
        self.bad_columns = bad_columns
        self.bad_constraint = bad_constraint
        self.disabled_trigger = disabled_trigger
        self.runtime_owner = runtime_owner
        self.owner_member = owner_member
        self.runtime_schema_owner = runtime_schema_owner
        self.schema_owner_member = schema_owner_member
        self.schema_create = schema_create
        self.database_create = database_create
        self.database_temp = database_temp
        self.public_table_grant = public_table_grant
        self.unexpected_event_update = unexpected_event_update
        self.table_grant_option = table_grant_option
        self.runtime_function_execute = runtime_function_execute
        self.public_function_execute = public_function_execute
        self.owner_drift = owner_drift
        self.database_name = "lightwork"
        self.schema_name = "public"
        self.role_name = "maverick_runtime"
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.calls_lock = threading.Lock()
        self.locks: dict[int, threading.Lock] = {}
        self.locks_lock = threading.Lock()

    def record(self, sql: str, params: tuple[object, ...]) -> None:
        with self.calls_lock:
            self.calls.append((" ".join(sql.split()), params))

    def advisory(self, key: int) -> threading.Lock:
        with self.locks_lock:
            return self.locks.setdefault(key, threading.Lock())


class _FakePgConnection:
    """Translate the operational psycopg subset onto a shared SQLite file."""

    def __init__(self, path: Path, catalog: _Catalog):
        self._db = sqlite3.connect(
            str(path),
            isolation_level=None,
            timeout=15.0,
            check_same_thread=False,
        )
        self._db.row_factory = sqlite3.Row
        self._catalog = catalog
        self._held: list[threading.Lock] = []
        self._closed = False

    def execute(  # noqa: C901 - explicit fake-catalog SQL dispatcher
        self,
        sql: str,
        params: tuple[object, ...] = (),
    ) -> Any:
        self._catalog.record(sql, params)
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "BEGIN":
            return self._db.execute("BEGIN IMMEDIATE")
        if normalized.startswith("SELECT CURRENT_DATABASE() AS DATABASE_NAME"):
            return _Rows([{
                "database_name": self._catalog.database_name,
                "schema_name": self._catalog.schema_name,
                "role_name": self._catalog.role_name,
            }])
        if normalized.startswith("SELECT ROLSUPER"):
            return _Rows([{
                "rolsuper": self._catalog.privileged,
                "rolbypassrls": self._catalog.bypass_rls,
                "rolcreaterole": False,
                "rolcreatedb": False,
                "rolreplication": False,
            }])
        if "HAS_SCHEMA_PRIVILEGE" in normalized:
            return _Rows([{
                "role_owns": self._catalog.runtime_schema_owner,
                "owner_member": self._catalog.schema_owner_member,
                "can_use": True,
                "can_create": self._catalog.schema_create,
            }])
        if "HAS_DATABASE_PRIVILEGE" in normalized:
            return _Rows([{
                "can_create": self._catalog.database_create,
                "can_temp": self._catalog.database_temp,
            }])
        if "HAS_TABLE_PRIVILEGE" in normalized and "FROM PG_CLASS" in normalized:
            table = str(params[0])
            expected = si._POSTGRES_PROMOTION_TABLE_PRIVILEGES[table]
            row: dict[str, object] = {
                "owner_name": (
                    "other_owner"
                    if self._catalog.owner_drift and table.endswith("events")
                    else "maverick_schema_owner"
                ),
                "role_owns": self._catalog.runtime_owner,
                "owner_member": self._catalog.owner_member,
            }
            for privilege in si._POSTGRES_PROMOTION_ALL_TABLE_PRIVILEGES:
                granted = privilege in expected
                if (
                    table == "promotion_ledger_events"
                    and privilege == "UPDATE"
                    and self._catalog.unexpected_event_update
                ):
                    granted = True
                row[f"can_{privilege.lower()}"] = granted
                row[f"grant_{privilege.lower()}"] = (
                    self._catalog.table_grant_option and granted
                )
            return _Rows([row])
        if "LATERAL ACLEXPLODE" in normalized and "FROM PG_CLASS" in normalized:
            return _Rows(
                [{"privilege_type": "SELECT"}]
                if self._catalog.public_table_grant
                else []
            )
        if normalized.startswith("SELECT TO_REGCLASS"):
            table = str(params[0])
            return _Rows([{
                "table_name": table if table in self._catalog.tables else None,
            }])
        if "FROM INFORMATION_SCHEMA.COLUMNS" in normalized:
            table = str(params[0])
            columns = list(si._POSTGRES_PROMOTION_COLUMNS[table])
            if self._catalog.bad_columns and table == "promotion_ledger_heads":
                columns[-2] = ("projection_sealed", "bytea", "NO")
            return _Rows([
                {
                    "column_name": name,
                    "data_type": data_type,
                    "is_nullable": nullable,
                }
                for name, data_type, nullable in columns
            ])
        if "FROM PG_INDEX" in normalized:
            table = str(params[0])
            return _Rows([{
                "columns": list(si._POSTGRES_PROMOTION_PRIMARY_KEYS[table]),
            }])
        if "FROM PG_CONSTRAINT" in normalized and "CONTYPE='F'" in normalized:
            table = str(params[0])
            if table == "promotion_ledger_heads":
                return _Rows()
            return _Rows([{
                "conname": "fk_mvk_promotion_event_head",
                "definition": (
                    "FOREIGN KEY (tenant_id) REFERENCES "
                    "promotion_ledger_heads(tenant_id) ON DELETE RESTRICT"
                ),
                "convalidated": True,
            }])
        if "FROM PG_CONSTRAINT" in normalized:
            table = str(params[0])
            rows = [
                {
                    "conname": name,
                    "definition": f"CHECK ({definition})",
                    "convalidated": True,
                }
                for name, definition in sorted(
                    si._POSTGRES_PROMOTION_CHECKS[table].items()
                )
            ]
            if self._catalog.bad_constraint and table == "promotion_ledger_heads":
                rows[0]["definition"] = "CHECK (true)"
            return _Rows(rows)
        if "RELROWSECURITY" in normalized:
            table = str(params[0])
            return _Rows([{
                "relrowsecurity": table in self._catalog.rls,
                "relforcerowsecurity": table in self._catalog.rls,
            }])
        if "FROM PG_POLICIES" in normalized:
            table = str(params[0])
            rows: list[object] = []
            if table in self._catalog.policies:
                rows.append(_policy())
            if self._catalog.extra_policy:
                rows.append({
                    "policyname": "allow_everything",
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                    "qual": "true",
                    "with_check": "true",
                })
            return _Rows(rows)
        if "FROM PG_TRIGGER" in normalized:
            if not self._catalog.trigger:
                return _Rows()
            return _Rows([{
                "tgname": "mvk_promotion_events_append_only",
                "tgenabled": "D" if self._catalog.disabled_trigger else "O",
                "definition": (
                    "CREATE TRIGGER mvk_promotion_events_append_only "
                    "BEFORE UPDATE OR DELETE OR TRUNCATE ON "
                    "public.promotion_ledger_events "
                    "FOR EACH STATEMENT EXECUTE FUNCTION "
                    "mvk_reject_promotion_event_mutation()"
                ),
            }])
        if "LATERAL ACLEXPLODE" in normalized and "FROM PG_PROC" in normalized:
            return _Rows(
                [{"privilege_type": "EXECUTE"}]
                if self._catalog.public_function_execute
                else []
            )
        if "FROM PG_PROC" in normalized:
            if not self._catalog.function:
                return _Rows()
            return _Rows([{
                "prosecdef": False,
                "proconfig": ["search_path=pg_catalog"],
                "prosrc": (
                    "BEGIN RAISE EXCEPTION "
                    "'promotion ledger events are append-only'; END"
                ),
                "lanname": "plpgsql",
                "return_type": "trigger",
                "owner_name": "maverick_schema_owner",
                "owner_member": self._catalog.owner_member,
                "can_execute": self._catalog.runtime_function_execute,
                "grant_execute": False,
            }])
        if normalized.startswith("SELECT PG_ADVISORY_XACT_LOCK"):
            lock = self._catalog.advisory(int(params[0]))
            lock.acquire()
            self._held.append(lock)
            return _Rows([(None,)])
        if "SET_CONFIG('MAVERICK.TENANT'" in normalized:
            return _Rows([(params[0],)])
        if normalized.startswith("CREATE TABLE IF NOT EXISTS"):
            table = normalized.split()[5].lower()
            self._catalog.tables.add(table)
            return _Rows()
        if normalized.startswith("ALTER TABLE"):
            table = normalized.split()[2].lower()
            self._catalog.rls.add(table)
            return _Rows()
        if normalized.startswith("CREATE POLICY"):
            table = normalized.split(" ON ", 1)[1].split()[0].lower()
            self._catalog.policies.add(table)
            return _Rows()
        if normalized.startswith("CREATE OR REPLACE FUNCTION"):
            self._catalog.function = True
            return _Rows()
        if normalized.startswith("CREATE TRIGGER"):
            self._catalog.trigger = True
            return _Rows()
        translated = sql.replace("%s", "?").replace(" FOR UPDATE", "")
        return self._db.execute(translated, params)

    def _release(self) -> None:
        while self._held:
            self._held.pop().release()

    def commit(self) -> None:
        self._db.commit()
        self._release()

    def rollback(self) -> None:
        self._db.rollback()
        self._release()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._release()
        self._db.close()


def _create_operational_schema(path: Path) -> None:
    with sqlite3.connect(str(path)) as connection:
        connection.executescript("""
            CREATE TABLE promotion_ledger_heads (
                tenant_id TEXT NOT NULL PRIMARY KEY,
                next_sequence INTEGER NOT NULL,
                head_sha256 TEXT,
                event_count INTEGER NOT NULL,
                record_count INTEGER NOT NULL,
                prepared_count INTEGER NOT NULL,
                projection_sealed TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE promotion_ledger_events (
                tenant_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_kind TEXT NOT NULL,
                prev_sha256 TEXT,
                event_sha256 TEXT NOT NULL,
                event_sealed TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, sequence)
            );
        """)


@pytest.fixture
def fake_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "51" * 32)
    monkeypatch.setenv(
        "MAVERICK_PG_DSN",
        "postgresql://runtime:secret@fake.internal/lightwork",  # pragma: allowlist secret
    )
    monkeypatch.setattr(
        si,
        "_promotion_backend_posture",
        lambda: si._PromotionBackendPosture(True, True),
    )
    path = tmp_path / "fake-postgres.sqlite3"
    _create_operational_schema(path)
    catalog = _Catalog()

    def connect(**_kwargs):
        return _FakePgConnection(path, catalog)

    monkeypatch.setattr(si, "_connect_promotion_postgres", connect)
    return path, catalog


@pytest.fixture(autouse=True)
def _reset_adapter_caches():
    ar._reset_caches()
    yield
    ar._reset_caches()


def _record(record_id: str, *, summary: str = "change") -> si.PromotionRecord:
    return si.PromotionRecord(
        id=record_id,
        rung="prompt",
        summary=summary,
        baseline_score=0.2,
        candidate_score=0.8,
        promoted_at=1.0,
        provenance={"customer": "SECRET_PROVENANCE"},
    )


def _artifact(content: str, *, identity: str = "prompt:model-a") -> si.ArtifactRevision:
    digest = hashlib.sha256(content.encode()).hexdigest()
    return si.ArtifactRevision(identity, digest, digest[:16])


def test_shared_journal_recovery_rollback_and_encrypted_projection(fake_postgres):
    path, _catalog = fake_postgres
    before = _artifact("before")
    after = _artifact("after")
    unknown = _artifact("partial")
    ledger = si.PromotionLedger(path=Path("ignored-local-ledger.json"))

    prepared = ledger.prepare(
        _record("receipt-1", summary="SECRET_SUMMARY"),
        before=before,
        after=after,
        prepared_at=1.0,
        transaction_id="tx-1",
    )
    assert prepared.in_doubt
    conflicted = si.PromotionLedger().recover("tx-1", artifact=unknown, at=2.0)
    assert conflicted.in_doubt and conflicted.recovery_attempts == 1
    committed = si.PromotionLedger().recover("tx-1", artifact=after, at=3.0)
    assert committed.state == "committed"
    assert si.PromotionLedger().get("receipt-1") is not None
    assert si.PromotionLedger().mark_rolled_back("receipt-1", at=4.0)
    assert si.PromotionLedger().get("receipt-1").rolled_back is True

    with sqlite3.connect(str(path)) as connection:
        event_rows = connection.execute(
            "SELECT event_sealed FROM promotion_ledger_events"
        ).fetchall()
        projection = connection.execute(
            "SELECT projection_sealed FROM promotion_ledger_heads"
        ).fetchone()[0]
        kinds = [
            row[0]
            for row in connection.execute(
                "SELECT event_kind FROM promotion_ledger_events ORDER BY sequence"
            ).fetchall()
        ]
    assert kinds == ["prepare", "recovery_conflict", "commit", "rollback"]
    assert event_rows and all(row[0].startswith("MVKAR1:") for row in event_rows)
    assert projection.startswith("MVKAR1:")
    raw = projection + "".join(row[0] for row in event_rows)
    assert "SECRET_SUMMARY" not in raw
    assert "SECRET_PROVENANCE" not in raw


def test_shared_concurrent_idempotent_prepare_appends_once(fake_postgres):
    path, _catalog = fake_postgres
    before, after = _artifact("before"), _artifact("after")
    barrier = threading.Barrier(6)

    def prepare(_index: int) -> si.PromotionTransaction:
        ledger = si.PromotionLedger()
        barrier.wait(timeout=10)
        return ledger.prepare(
            _record("same-record"),
            before=before,
            after=after,
            prepared_at=1.0,
            transaction_id="same-tx",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(prepare, range(6)))
    assert {result.id for result in results} == {"same-tx"}
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM promotion_ledger_events"
        ).fetchone()[0] == 1


def test_shared_concurrent_distinct_receipts_are_not_lost(fake_postgres):
    _path, _catalog = fake_postgres
    barrier = threading.Barrier(8)

    def add(index: int) -> None:
        ledger = si.PromotionLedger()
        barrier.wait(timeout=10)
        ledger.add(_record(f"receipt-{index}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(add, range(8)))
    assert {record.id for record in si.PromotionLedger().all()} == {
        f"receipt-{index}" for index in range(8)
    }


def test_shared_retention_race_admits_only_one_reservation(fake_postgres):
    path, _catalog = fake_postgres
    barrier = threading.Barrier(2)

    def add(index: int) -> str:
        ledger = si.PromotionLedger(max_records=1)
        barrier.wait(timeout=10)
        try:
            ledger.add(_record(f"receipt-{index}"))
            return "committed"
        except si.PromotionLedgerError:
            return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(add, range(2)))
    assert sorted(outcomes) == ["committed", "refused"]
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM promotion_ledger_events"
        ).fetchone()[0] == 1


def test_shared_tenants_are_canonical_and_isolated(fake_postgres, monkeypatch):
    path, _catalog = fake_postgres
    decomposed = unicodedata.normalize("NFD", "tenant-\u00e9")
    monkeypatch.setenv("MAVERICK_TENANT", decomposed)
    si.PromotionLedger().add(_record("tenant-e"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
    assert si.PromotionLedger().all() == []
    si.PromotionLedger().add(_record("tenant-b"))
    monkeypatch.setenv("MAVERICK_TENANT", decomposed)
    assert [record.id for record in si.PromotionLedger().all()] == ["tenant-e"]
    with sqlite3.connect(str(path)) as connection:
        tenants = {
            row[0]
            for row in connection.execute(
                "SELECT tenant_id FROM promotion_ledger_heads"
            ).fetchall()
        }
    assert tenants == {unicodedata.normalize("NFC", decomposed), "tenant-b"}


def test_unbound_or_corrupt_tenant_fails_before_connection(
    fake_postgres,
    monkeypatch,
):
    _path, _catalog = fake_postgres
    opened = 0

    def counted(**_kwargs):
        nonlocal opened
        opened += 1
        raise AssertionError("must not connect")

    monkeypatch.setattr(si, "_connect_promotion_postgres", counted)
    monkeypatch.setattr("maverick.paths.current_tenant_id_strict", lambda: None)
    with pytest.raises(si.PromotionLedgerError, match="active tenant"):
        si.PromotionLedger()
    assert opened == 0

    def broken_tenant():
        raise RuntimeError("secret tenant provider detail")

    monkeypatch.setattr(
        "maverick.paths.current_tenant_id_strict",
        broken_tenant,
    )
    with pytest.raises(si.PromotionLedgerError, match="tenant boundary") as exc:
        si.PromotionLedger()
    assert "secret tenant" not in str(exc.value)
    assert opened == 0


def test_postgres_cutover_refuses_projection_or_journal_before_connection(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    _path, _catalog = fake_postgres
    opened = 0

    def counted(**_kwargs):
        nonlocal opened
        opened += 1
        raise AssertionError("cutover check must precede connection")

    monkeypatch.setattr(si, "_connect_promotion_postgres", counted)
    projection = tmp_path / "legacy.json"
    projection.write_text(json.dumps([_record("legacy").to_dict()]))
    with pytest.raises(si.PromotionLedgerError, match="migrate it before"):
        si.PromotionLedger(path=projection)
    assert opened == 0

    projection.write_text("[]")
    Path(f"{projection}.journal").write_text('{"event":"legacy"}\n')
    with pytest.raises(si.PromotionLedgerError, match="migrate it before"):
        si.PromotionLedger(path=projection)
    assert opened == 0


def test_postgres_cutover_scans_other_tenant_namespaces_before_connect(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    _path, _catalog = fake_postgres
    home = tmp_path / "home"
    legacy = home / "tenants" / "other" / "self_improvement.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps([_record("legacy").to_dict()]))
    opened = 0

    def counted(**_kwargs):
        nonlocal opened
        opened += 1
        raise AssertionError("cutover check must precede connection")

    monkeypatch.setattr(si, "_connect_promotion_postgres", counted)
    with pytest.raises(si.PromotionLedgerError, match="migrate it before"):
        si.PromotionLedger()
    assert opened == 0


def test_shared_storage_requires_working_encryption_before_connection(
    fake_postgres,
    monkeypatch,
):
    _path, _catalog = fake_postgres
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    opened = 0

    def counted(**_kwargs):
        nonlocal opened
        opened += 1
        raise AssertionError("encryption check must precede connection")

    monkeypatch.setattr(si, "_connect_promotion_postgres", counted)
    with pytest.raises(si.PromotionLedgerError, match="requires application"):
        si.PromotionLedger()
    assert opened == 0


def test_connection_failure_is_sanitized_and_never_falls_back(
    fake_postgres,
    monkeypatch,
):
    _path, _catalog = fake_postgres
    secret = "postgresql://admin:must-not-leak@db.internal/lightwork"  # pragma: allowlist secret

    def fail(**_kwargs):
        raise ConnectionError(f"could not connect to {secret}")

    monkeypatch.setattr(si, "_connect_promotion_postgres", fail)
    with pytest.raises(
        si.PromotionLedgerError,
        match="shared Postgres promotion refresh failed",
    ) as exc:
        si.PromotionLedger(path=Path("must-not-be-created.json"))
    assert secret not in str(exc.value)
    assert "must-not-leak" not in str(exc.value)
    assert not Path("must-not-be-created.json").exists()


def test_backend_selection_is_global_and_refuses_local_fallback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        si,
        "_promotion_backend_posture",
        lambda: si._PromotionBackendPosture(False, True),
    )
    with pytest.raises(si.PromotionLedgerError, match="node-local"):
        si.PromotionLedger()
    with pytest.raises(si.PromotionLedgerError, match="node-local"):
        si.PromotionLedger(path=tmp_path / "explicit.json")

    monkeypatch.setattr(
        si,
        "_promotion_backend_posture",
        lambda: si._PromotionBackendPosture(False, False),
    )
    local = si.PromotionLedger(path=tmp_path / "local.json")
    assert local.backend == "local"
    assert local.shared_backend is False

    monkeypatch.setattr(
        si,
        "_promotion_backend_posture",
        lambda: si._PromotionBackendPosture(True, True),
    )
    with pytest.raises(si.PromotionLedgerError, match="node-local"):
        local.all()


def test_backend_posture_requires_postgres_for_multiple_replicas(monkeypatch):
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    monkeypatch.delenv("MAVERICK_WORLD_BACKEND", raising=False)
    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {"world_model": {"backend": "sqlite"}},
    )
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )
    with pytest.raises(si.PromotionLedgerError, match="requires Postgres"):
        si._promotion_backend_posture()


def test_projection_drift_repairs_only_from_authoritative_journal(fake_postgres):
    path, _catalog = fake_postgres
    ledger = si.PromotionLedger()
    ledger.add(_record("record-1"))
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            "UPDATE promotion_ledger_heads SET projection_sealed='PLAINTEXT'"
        )
    assert [record.id for record in ledger.all()] == ["record-1"]
    with sqlite3.connect(str(path)) as connection:
        repaired = connection.execute(
            "SELECT projection_sealed FROM promotion_ledger_heads"
        ).fetchone()[0]
    assert repaired.startswith("MVKAR1:")


def test_unencrypted_or_hash_tampered_journal_fails_closed(fake_postgres):
    path, _catalog = fake_postgres
    si.PromotionLedger().add(_record("record-1"))
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            "UPDATE promotion_ledger_events SET event_sealed='PLAINTEXT'"
        )
    with pytest.raises(si.PromotionLedgerError, match="not encrypted"):
        si.PromotionLedger()

    with sqlite3.connect(str(path)) as connection:
        connection.execute("DELETE FROM promotion_ledger_events")
        connection.execute("DELETE FROM promotion_ledger_heads")
    ledger = si.PromotionLedger()
    ledger.add(_record("record-2"))
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            "UPDATE promotion_ledger_events SET event_sha256=?",
            ("0" * 64,),
        )
    with pytest.raises(si.PromotionLedgerError, match="hash-chain"):
        si.PromotionLedger()


def test_failed_atomic_append_rolls_back_event_and_projection(
    fake_postgres,
    monkeypatch,
):
    path, _catalog = fake_postgres
    ledger = si.PromotionLedger()
    store = ledger._postgres_store
    original = store._append_locked

    def fail_after_append(*args, **kwargs):
        original(*args, **kwargs)
        raise si.PromotionLedgerError("simulated crash after projection update")

    monkeypatch.setattr(store, "_append_locked", fail_after_append)
    with pytest.raises(si.PromotionLedgerError, match="simulated crash"):
        ledger.add(_record("must-rollback"))
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM promotion_ledger_events"
        ).fetchone()[0] == 0
        head = connection.execute(
            "SELECT event_count, record_count FROM promotion_ledger_heads"
        ).fetchone()
    assert tuple(head) == (0, 0)


def test_preprovisioned_schema_needs_no_runtime_ddl(tmp_path):
    path = tmp_path / "schema.sqlite3"
    _create_operational_schema(path)
    catalog = _Catalog()
    connection = _FakePgConnection(path, catalog)
    connection.execute("BEGIN")
    si._initialize_promotion_postgres_schema(connection)
    connection.rollback()
    connection.close()
    assert not any(
        sql.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE POLICY"))
        for sql, _params in catalog.calls
    )


def test_runtime_refuses_to_bootstrap_missing_schema(tmp_path):
    path = tmp_path / "bootstrap.sqlite3"
    _create_operational_schema(path)
    catalog = _Catalog(ready=False)
    connection = _FakePgConnection(path, catalog)
    connection.execute("BEGIN")
    with pytest.raises(si.PromotionLedgerError, match="separate owner"):
        si._initialize_promotion_postgres_schema(connection)
    assert not any(
        sql.startswith(("CREATE", "ALTER", "GRANT", "REVOKE"))
        for sql, _params in catalog.calls
    )
    connection.rollback()
    connection.close()


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        (_Catalog(privileged=True), "least-privilege runtime role"),
        (_Catalog(bypass_rls=True), "least-privilege runtime role"),
        (_Catalog(runtime_owner=True), "least-privilege runtime role"),
        (_Catalog(owner_member=True), "least-privilege runtime role"),
        (_Catalog(runtime_schema_owner=True), "least-privilege runtime role"),
        (_Catalog(schema_owner_member=True), "least-privilege runtime role"),
        (_Catalog(schema_create=True), "least-privilege runtime role"),
        (_Catalog(database_create=True), "least-privilege runtime role"),
        (_Catalog(database_temp=True), "least-privilege runtime role"),
        (_Catalog(public_table_grant=True), "least-privilege runtime role"),
        (_Catalog(unexpected_event_update=True), "least-privilege runtime role"),
        (_Catalog(table_grant_option=True), "least-privilege runtime role"),
        (_Catalog(runtime_function_execute=True), "schema is incompatible"),
        (_Catalog(public_function_execute=True), "schema is incompatible"),
        (_Catalog(owner_drift=True), "least-privilege runtime role"),
        (_Catalog(extra_policy=True), "schema is incompatible"),
        (_Catalog(ready=False, partial=True), "schema is incompatible"),
        (_Catalog(bad_columns=True), "schema is incompatible"),
        (_Catalog(bad_constraint=True), "schema is incompatible"),
        (_Catalog(disabled_trigger=True), "schema is incompatible"),
    ],
)
def test_schema_refuses_privilege_policy_constraint_and_trigger_drift(
    tmp_path,
    catalog,
    message,
):
    path = tmp_path / f"schema-{id(catalog)}.sqlite3"
    _create_operational_schema(path)
    connection = _FakePgConnection(path, catalog)
    connection.execute("BEGIN")
    with pytest.raises(si.PromotionLedgerError, match=message):
        si._initialize_promotion_postgres_schema(connection)
    assert not any(
        sql.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE POLICY"))
        for sql, _params in catalog.calls
    )
    connection.rollback()
    connection.close()


def test_schema_and_rls_drift_are_rechecked_each_transaction(fake_postgres):
    _path, catalog = fake_postgres
    ledger = si.PromotionLedger()
    ledger.add(_record("record-1"))
    catalog.extra_policy = True
    with pytest.raises(si.PromotionLedgerError, match="schema"):
        ledger.all()


@pytest.mark.parametrize("drift", ["backend", "dsn", "tenant", "posture"])
def test_existing_shared_ledger_refuses_deployment_authority_drift(
    fake_postgres,
    monkeypatch,
    drift,
):
    _path, _catalog = fake_postgres
    ledger = si.PromotionLedger()
    opened = 0

    def counted(**_kwargs):
        nonlocal opened
        opened += 1
        raise AssertionError("authority drift must fail before connection")

    monkeypatch.setattr(si, "_connect_promotion_postgres", counted)
    if drift == "backend":
        monkeypatch.setattr(
            si,
            "_promotion_backend_posture",
            lambda: si._PromotionBackendPosture(False, False),
        )
    elif drift == "dsn":
        monkeypatch.setenv(
            "MAVERICK_PG_DSN",
            "postgresql://runtime:other@different.internal/lightwork",  # pragma: allowlist secret
        )
    elif drift == "tenant":
        monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
    else:
        monkeypatch.setattr(
            si,
            "_promotion_backend_posture",
            lambda: si._PromotionBackendPosture(True, False),
        )
    with pytest.raises(si.PromotionLedgerError, match="authority changed"):
        ledger.all()
    assert opened == 0


@pytest.mark.parametrize("attribute", ["database_name", "schema_name", "role_name"])
def test_existing_shared_ledger_refuses_connected_database_identity_drift(
    fake_postgres,
    attribute,
):
    _path, catalog = fake_postgres
    ledger = si.PromotionLedger()
    setattr(catalog, attribute, f"different-{attribute}")
    with pytest.raises(
        si.PromotionLedgerError,
        match="database authority changed",
    ):
        ledger.all()


@pytest.mark.parametrize("tamper", ["truncate-event", "rewrite-head"])
def test_shared_journal_head_drift_never_normalizes_authority(
    fake_postgres,
    tamper,
):
    path, _catalog = fake_postgres
    ledger = si.PromotionLedger()
    ledger.add(_record("record-1"))
    ledger.add(_record("record-2"))
    with sqlite3.connect(str(path)) as connection:
        original = tuple(connection.execute(
            "SELECT next_sequence, head_sha256, event_count, record_count, "
            "prepared_count FROM promotion_ledger_heads"
        ).fetchone())
        if tamper == "truncate-event":
            connection.execute(
                "DELETE FROM promotion_ledger_events WHERE sequence=2"
            )
        else:
            connection.execute(
                "UPDATE promotion_ledger_heads SET next_sequence=2, "
                "event_count=1, record_count=1, head_sha256=(SELECT "
                "event_sha256 FROM promotion_ledger_events WHERE sequence=1)"
            )
    with pytest.raises(si.PromotionLedgerError, match="head and event chain"):
        si.PromotionLedger()
    with sqlite3.connect(str(path)) as connection:
        observed = tuple(connection.execute(
            "SELECT next_sequence, head_sha256, event_count, record_count, "
            "prepared_count FROM promotion_ledger_heads"
        ).fetchone())
    if tamper == "truncate-event":
        assert observed == original
    else:
        assert observed != original
        assert observed[:1] == (2,)


def test_tenant_advisory_lock_is_scoped_and_stable():
    assert si._promotion_tenant_lock_key("tenant-a") == si._promotion_tenant_lock_key(
        "tenant-a"
    )
    assert si._promotion_tenant_lock_key("tenant-a") != si._promotion_tenant_lock_key(
        "tenant-b"
    )


def _shared_pointer() -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "receipt": {
            "version": 3,
            "tenant_id": "tenant-a",
            "ledger_authority": {
                "kind": "postgres",
                "store": "promotion-ledger-v1",
            },
        },
    }


def test_shared_authority_revision_changes_across_replica_rollback(fake_postgres):
    first_replica = si.PromotionLedger()
    assert first_replica.authority_locator() == {
        "kind": "postgres",
        "store": "promotion-ledger-v1",
    }
    first_replica.add(_record("adapter-record"))
    committed_revision = first_replica.authority_revision()

    second_replica = si.PromotionLedger()
    assert second_replica.mark_rolled_back("adapter-record", at=2.0)
    rolled_back_revision = first_replica.authority_revision()

    assert rolled_back_revision[:2] == ("postgres", "tenant-a")
    assert rolled_back_revision[2] == committed_revision[2] + 1
    assert rolled_back_revision[3] != committed_revision[3]


def test_shared_adapter_receipt_uses_stable_nonsecret_authority(fake_postgres):
    ledger = si.PromotionLedger()
    pointer = {
        "authority": "governed-v1",
        "tenant_id": "tenant-a",
        "promotion_record_id": "adapter-record",
        "adapter_id": "adapter-a",
        "base_model": "model-a",
        "dataset_sha256": "22" * 32,
        "payload_sha256": "33" * 32,
        "serving": {},
    }
    authority_payload = json.loads(ar._authority_payload(
        pointer,
        artifact_identity="adapter-store:tenant-a",
        ledger_authority=ledger.authority_locator(),
    ))
    assert authority_payload["version"] == 2
    assert authority_payload["ledger_authority"] == ledger.authority_locator()
    assert "ledger_path" not in authority_payload

    payload = '{"approved":true}'
    candidate = SimpleNamespace(
        id="adapter-record",
        payload=payload,
        payload_sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        approval_signature="signed",
    )

    receipt = ar._promotion_receipt_binding(
        candidate,
        ledger,
        approval_pubkey="11" * 32,
        artifact_identity="adapter-store:tenant-a",
    )

    assert receipt["version"] == 3
    assert receipt["ledger_authority"] == ledger.authority_locator()
    assert "ledger_path" not in receipt
    encoded = json.dumps(receipt, sort_keys=True)
    assert "MAVERICK_PG_DSN" not in encoded
    assert "postgresql://" not in encoded


def test_shared_revision_invalidates_cached_pointer_on_remote_change(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    pointer = _shared_pointer()
    verified: list[str] = []
    monkeypatch.setattr(ar.AdapterStore, "active", lambda _store: pointer)
    monkeypatch.setattr(
        ar,
        "_verify_authoritative_pointer",
        lambda _pointer, *, artifact_identity: verified.append(artifact_identity),
    )
    monkeypatch.setattr(ar, "_verify_live_deployment", lambda _pointer: None)

    root = tmp_path / "adapter-store"
    assert ar._cached_active_pointer(root, tenant_id="tenant-a") is pointer
    assert len(verified) == 1

    # The pointer files remain untouched.  Only the shared journal revision
    # changes, exactly as it would when another replica commits a rollback.
    si.PromotionLedger().add(_record("remote-revision"))
    assert ar._cached_active_pointer(root, tenant_id="tenant-a") is pointer
    assert len(verified) == 2


def test_cached_shared_pointer_fails_closed_when_authority_is_unavailable(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    pointer = _shared_pointer()
    monkeypatch.setattr(ar.AdapterStore, "active", lambda _store: pointer)
    monkeypatch.setattr(
        ar,
        "_verify_authoritative_pointer",
        lambda _pointer, *, artifact_identity: None,
    )
    monkeypatch.setattr(ar, "_verify_live_deployment", lambda _pointer: None)
    root = tmp_path / "adapter-store"
    assert ar._cached_active_pointer(root, tenant_id="tenant-a") is pointer

    def unavailable(**_kwargs):
        raise ConnectionError("postgresql://admin:secret@db.internal/lightwork")  # pragma: allowlist secret

    monkeypatch.setattr(si, "_connect_promotion_postgres", unavailable)
    assert ar._cached_active_pointer(root, tenant_id="tenant-a") is None


def test_initial_cache_fill_refuses_racing_shared_revision(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    pointer = _shared_pointer()
    monkeypatch.setattr(ar.AdapterStore, "active", lambda _store: pointer)
    monkeypatch.setattr(
        ar,
        "_verify_authoritative_pointer",
        lambda _pointer, *, artifact_identity: None,
    )
    monkeypatch.setattr(ar, "_verify_live_deployment", lambda _pointer: None)
    revisions = iter((("postgres", "tenant-a", 1, "a" * 64),
                      ("postgres", "tenant-a", 2, "b" * 64)))
    monkeypatch.setattr(
        ar,
        "_receipt_integrity_stamp",
        lambda _pointer: next(revisions),
    )

    root = tmp_path / "adapter-store"
    assert ar._cached_active_pointer(root, tenant_id="tenant-a") is None
    assert not ar._ACTIVE_CACHE
