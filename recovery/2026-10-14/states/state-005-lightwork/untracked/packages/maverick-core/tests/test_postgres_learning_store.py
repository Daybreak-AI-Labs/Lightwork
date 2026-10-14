"""Shared self-learning authority invariants without a live Postgres service."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from maverick import learning_store as ls


def _constraint_definitions(table: str) -> dict[str, str]:
    tenant = (
        "CHECK ((char_length(tenant_id) >= 1) AND "
        "(char_length(tenant_id) <= 201))"
    )
    definitions = {
        "shared_harness_addenda": {
            "shared_harness_addenda_tenant_len": tenant,
            "shared_harness_addenda_key_len": (
                "CHECK ((octet_length(key) >= 1) AND "
                f"(octet_length(key) <= {ls._MAX_KEY_BYTES}))"
            ),
            "shared_harness_addenda_block_size": (
                f"CHECK (octet_length(block) <= {ls._MAX_PAYLOAD_BYTES})"
            ),
        },
        "shared_harness_line_meta": {
            "shared_harness_line_meta_tenant_len": tenant,
            "shared_harness_line_meta_id_len": (
                "CHECK ((octet_length(line_id) >= 1) AND "
                f"(octet_length(line_id) <= {ls._MAX_KEY_BYTES}))"
            ),
            "shared_harness_line_meta_record_size": (
                f"CHECK (octet_length(record) <= {ls._MAX_PAYLOAD_BYTES})"
            ),
        },
        "shared_harness_transfer_tried": {
            "shared_harness_transfer_tried_tenant_len": tenant,
            "shared_harness_transfer_tried_id_len": (
                "CHECK ((octet_length(line_id) >= 1) AND "
                f"(octet_length(line_id) <= {ls._MAX_KEY_BYTES}))"
            ),
        },
        "shared_harness_corpus": {
            "shared_harness_corpus_tenant_len": tenant,
            "shared_harness_corpus_kind": (
                "CHECK (kind IN ('live', 'pending', 'rejected', 'extra'))"
            ),
            "shared_harness_corpus_key_len": (
                "CHECK ((octet_length(key) >= 1) AND "
                f"(octet_length(key) <= {ls._MAX_KEY_BYTES}))"
            ),
            "shared_harness_corpus_seq": "CHECK (seq >= 0)",
            "shared_harness_corpus_row_size": (
                f"CHECK (octet_length(row) <= {ls._MAX_PAYLOAD_BYTES})"
            ),
        },
    }
    return definitions[table]


def _policy(*, permissive: str = "PERMISSIVE") -> dict[str, object]:
    expression = (
        "tenant_id = nullif(current_setting('maverick.tenant', true), '')"
    )
    return {
        "policyname": ls._POSTGRES_POLICY,
        "permissive": permissive,
        "roles": ["public"],
        "cmd": "ALL",
        "qual": expression,
        "with_check": expression,
    }


class _Rows:
    def __init__(self, rows: list[object] | None = None):
        self.rows = list(rows or [])

    def fetchone(self) -> object | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[object]:
        return list(self.rows)


class _Catalog:
    def __init__(
        self,
        *,
        ready: bool = True,
        privileged: bool = False,
        bypass_rls: bool = False,
        extra_policy: bool = False,
        restrictive_policy: bool = False,
        partial: bool = False,
        bad_columns: bool = False,
        extra_check: bool = False,
        legacy_present: bool = True,
        legacy_rows: bool = False,
    ) -> None:
        self.tables = set(ls._POSTGRES_TABLES if ready else ())
        if legacy_present:
            self.tables.update(ls._LEGACY_POSTGRES_TABLES)
        if partial:
            self.tables = {ls._POSTGRES_TABLES[0]}
        self.privileged = privileged
        self.bypass_rls = bypass_rls
        self.extra_policy = extra_policy
        self.restrictive_policy = restrictive_policy
        self.bad_columns = bad_columns
        self.extra_check = extra_check
        self.legacy_rows = legacy_rows
        self.rls: set[str] = set(self.tables)
        self.policy: set[str] = set(self.tables)
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.calls_lock = threading.Lock()
        self.connection_count = 0
        self.locks: dict[int, threading.Lock] = {}
        self.locks_lock = threading.Lock()

    def record(self, sql: str, params: tuple[object, ...]) -> None:
        with self.calls_lock:
            self.calls.append((" ".join(sql.split()), params))

    def advisory(self, key: int) -> threading.Lock:
        with self.locks_lock:
            return self.locks.setdefault(key, threading.Lock())


class _FakePgConnection:
    """Translate operational psycopg SQL onto one shared SQLite database."""

    def __init__(self, path: Path, catalog: _Catalog):
        self._db = sqlite3.connect(
            str(path),
            timeout=10.0,
            check_same_thread=False,
        )
        self._catalog = catalog
        with self._catalog.calls_lock:
            self._catalog.connection_count += 1
        self._held: list[threading.Lock] = []
        self._closed = False

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        self._catalog.record(sql, params)
        normalized = " ".join(sql.strip().upper().split())
        if "FROM PG_ROLES" in normalized:
            return _Rows([{
                "rolsuper": self._catalog.privileged,
                "rolbypassrls": self._catalog.bypass_rls,
            }])
        if normalized.startswith("SELECT TO_REGCLASS"):
            table = str(params[0])
            return _Rows([{
                "table_name": table if table in self._catalog.tables else None,
            }])
        if normalized.startswith("SELECT EXISTS (SELECT 1 FROM HARNESS_"):
            return _Rows([{"has_rows": self._catalog.legacy_rows}])
        if "FROM INFORMATION_SCHEMA.COLUMNS" in normalized:
            table = str(params[0])
            columns = list(ls._POSTGRES_COLUMNS[table])
            if self._catalog.bad_columns and table == "shared_harness_addenda":
                columns[2] = ("block", "bytea", "NO")
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
            return _Rows([{"columns": list(ls._POSTGRES_PRIMARY_KEYS[table])}])
        if "FROM PG_CONSTRAINT" in normalized:
            table = str(params[0])
            rows = [
                {"conname": name, "definition": definition}
                for name, definition in sorted(
                    _constraint_definitions(table).items()
                )
            ]
            if self._catalog.extra_check and table == "shared_harness_addenda":
                rows.append({"conname": "allow_odd", "definition": "CHECK (true)"})
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
            if table in self._catalog.policy:
                rows.append(_policy(
                    permissive=(
                        "RESTRICTIVE"
                        if self._catalog.restrictive_policy
                        else "PERMISSIVE"
                    )
                ))
            if self._catalog.extra_policy:
                rows.append({
                    "policyname": "allow_all",
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                    "qual": "true",
                    "with_check": "true",
                })
            return _Rows(rows)
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
            self._catalog.policy.add(table)
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
    with sqlite3.connect(str(path)) as conn:
        conn.executescript("""
            CREATE TABLE shared_harness_addenda (
                tenant_id TEXT NOT NULL, key TEXT NOT NULL,
                block TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, key)
            );
            CREATE TABLE shared_harness_line_meta (
                tenant_id TEXT NOT NULL, line_id TEXT NOT NULL,
                record TEXT NOT NULL, updated_at REAL NOT NULL,
                PRIMARY KEY (tenant_id, line_id)
            );
            CREATE TABLE shared_harness_transfer_tried (
                tenant_id TEXT NOT NULL, line_id TEXT NOT NULL,
                ts REAL NOT NULL, PRIMARY KEY (tenant_id, line_id)
            );
            CREATE TABLE shared_harness_corpus (
                tenant_id TEXT NOT NULL, kind TEXT NOT NULL,
                key TEXT NOT NULL, seq INTEGER NOT NULL, row TEXT NOT NULL,
                PRIMARY KEY (tenant_id, kind, key, seq)
            );
        """)


@pytest.fixture
def fake_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "41" * 32)
    monkeypatch.setattr(ls, "_postgres", lambda: True)
    path = tmp_path / "fake-postgres.sqlite3"
    _create_operational_schema(path)
    catalog = _Catalog()

    def connect(*, autocommit: bool = False):
        assert autocommit is False
        return _FakePgConnection(path, catalog)

    monkeypatch.setattr(ls, "_connect_postgres", connect)
    return path, catalog


def test_shared_round_trip_is_tenant_scoped_and_encrypts_every_payload(
    fake_postgres,
    monkeypatch,
):
    path, catalog = fake_postgres
    ls.write_addenda_db({"model-a": "SECRET_GUIDANCE_A"})
    ls.write_line_meta_db({"line-a": {"reason": "SECRET_REASON_A"}})
    ls.write_transfer_tried_db({"line-a": 123.0})
    ls.write_corpus_db("live", {
        "model-a": [{"goal": "SECRET_GOAL_A"}],
        "_meta": {"owner": "SECRET_OWNER_A"},
    })
    ls.write_corpus_db("pending", {
        "model-a": [{"goal": "SECRET_PENDING_A"}],
    })

    monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
    assert ls.load_addenda_db() == {}
    ls.write_addenda_db({"model-b": "SECRET_GUIDANCE_B"})
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")

    assert ls.load_addenda_db() == {"model-a": "SECRET_GUIDANCE_A"}
    assert ls.load_line_meta_db() == {
        "line-a": {"reason": "SECRET_REASON_A"},
    }
    assert ls.load_transfer_tried_db() == {"line-a": 123.0}
    assert ls.load_corpus_db("live") == {
        "model-a": [{"goal": "SECRET_GOAL_A"}],
        "_meta": {"owner": "SECRET_OWNER_A"},
    }
    assert ls.load_corpus_db("pending") == {
        "model-a": [{"goal": "SECRET_PENDING_A"}],
    }

    with sqlite3.connect(str(path)) as conn:
        payloads = [
            row[0]
            for table, column in (
                ("shared_harness_addenda", "block"),
                ("shared_harness_line_meta", "record"),
                ("shared_harness_corpus", "row"),
            )
            for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()
        ]
        tenants = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT tenant_id FROM shared_harness_addenda"
            ).fetchall()
        }
    assert payloads and all(value.startswith("MVKAR1:") for value in payloads)
    assert not any("SECRET_" in value for value in payloads)
    assert tenants == {"tenant-a", "tenant-b"}

    authority_sql = [
        sql for sql, _params in catalog.calls
        if sql.startswith(("SELECT key", "SELECT line_id", "DELETE FROM", "INSERT INTO"))
    ]
    assert authority_sql
    assert all("tenant_id" in sql for sql in authority_sql)


def test_shared_tenant_is_canonical_and_unbound_resolution_fails_before_connect(
    fake_postgres,
    monkeypatch,
):
    path, _catalog = fake_postgres
    decomposed = unicodedata.normalize("NFD", "tenant-é")
    monkeypatch.setenv("MAVERICK_TENANT", decomposed)
    ls.write_addenda_db({"model": "line"})
    with sqlite3.connect(str(path)) as conn:
        stored = conn.execute(
            "SELECT tenant_id FROM shared_harness_addenda"
        ).fetchone()[0]
    assert stored == unicodedata.normalize("NFC", decomposed)

    opened = 0

    def counted(*, autocommit: bool = False):
        nonlocal opened
        del autocommit
        opened += 1
        raise AssertionError("must not connect")

    monkeypatch.setattr(ls, "_connect_postgres", counted)
    monkeypatch.setattr("maverick.paths.current_tenant_id_strict", lambda: None)
    with pytest.raises(ls.LearningStoreError, match="active tenant"):
        ls.load_addenda_db()
    assert opened == 0

    def broken_tenant():
        raise RuntimeError("credential-bearing tenant resolver detail")

    monkeypatch.setattr(
        "maverick.paths.current_tenant_id_strict",
        broken_tenant,
    )
    with pytest.raises(ls.LearningStoreError, match="tenant boundary") as exc:
        ls.load_addenda_db()
    assert "credential-bearing" not in str(exc.value)
    assert opened == 0


def test_schema_bootstrap_is_locked_and_forces_rls_on_every_table(tmp_path):
    catalog = _Catalog(ready=False)
    path = tmp_path / "bootstrap.sqlite3"
    _create_operational_schema(path)
    connection = _FakePgConnection(path, catalog)
    ls._initialize_postgres_schema(connection)

    joined = "\n".join(sql for sql, _params in catalog.calls)
    lock_index = next(
        index for index, (sql, _params) in enumerate(catalog.calls)
        if "pg_advisory_xact_lock" in sql
    )
    create_index = next(
        index for index, (sql, _params) in enumerate(catalog.calls)
        if sql.startswith("CREATE TABLE IF NOT EXISTS")
    )
    assert lock_index < create_index
    for table in ls._POSTGRES_TABLES:
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in joined
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in joined
        assert f"CREATE POLICY {ls._POSTGRES_POLICY} ON {table}" in joined
    connection.rollback()
    connection.close()


@pytest.mark.parametrize(
    ("catalog", "message"),
    [
        (_Catalog(privileged=True), "non-bypass Postgres role"),
        (_Catalog(bypass_rls=True), "non-bypass Postgres role"),
        (_Catalog(extra_policy=True), "schema is incompatible"),
        (_Catalog(restrictive_policy=True), "schema is incompatible"),
        (_Catalog(ready=False, partial=True), "schema is incompatible"),
        (_Catalog(bad_columns=True), "schema is incompatible"),
        (_Catalog(extra_check=True), "schema is incompatible"),
    ],
)
def test_schema_refuses_privilege_policy_and_partial_schema_drift(
    tmp_path,
    catalog,
    message,
):
    path = tmp_path / f"schema-{id(catalog)}.sqlite3"
    _create_operational_schema(path)
    connection = _FakePgConnection(path, catalog)
    with pytest.raises(ls.LearningStoreError, match=message):
        ls._initialize_postgres_schema(connection)
    assert not any(
        sql.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE POLICY"))
        for sql, _params in catalog.calls
    )
    connection.rollback()
    connection.close()


@pytest.mark.parametrize(
    "definition",
    [
        "CHECK (NOT (octet_length(block) > 8388608))",
        "CHECK ((octet_length(block) <= 8388608) AND (block = block))",
        "CHECK ((octet_length(block) <= 8388608) OR (block = block))",
        "CHECK (8388608 >= octet_length(block))",
        "CHECK (octet_length(block)::bigint <= 8388608::bigint)",
    ],
)
def test_check_verifier_rejects_noncanonical_bypass_expressions(definition):
    assert not ls._check_definition_is_exact(
        "shared_harness_addenda_block_size",
        definition,
    )


@pytest.mark.parametrize(
    ("qual", "with_check"),
    [
        (
            "NOT (tenant_id <> nullif(current_setting("
            "'maverick.tenant', true), ''))",
            "tenant_id = nullif(current_setting('maverick.tenant', true), '')",
        ),
        (
            "nullif(current_setting('maverick.tenant', true), '') = tenant_id",
            "tenant_id = nullif(current_setting('maverick.tenant', true), '')",
        ),
        (
            "tenant_id = tenant_id AND tenant_id = nullif(current_setting("
            "'maverick.tenant', true), '')",
            "tenant_id = nullif(current_setting('maverick.tenant', true), '')",
        ),
    ],
)
def test_policy_verifier_rejects_not_inversion_and_self_equality(
    qual,
    with_check,
):
    policy = _policy()
    policy["qual"] = qual
    policy["with_check"] = with_check
    assert not ls._policy_rows_are_exact([policy])


@pytest.mark.parametrize("cast", ["integer", "bigint"])
def test_policy_verifier_rejects_tenant_collapsing_numeric_casts(cast):
    policy = _policy()
    expression = (
        f"tenant_id::{cast} = nullif(current_setting("
        f"'maverick.tenant', true), '')::{cast}"
    )
    policy["qual"] = expression
    policy["with_check"] = expression
    assert not ls._policy_rows_are_exact([policy])


def test_policy_verifier_accepts_only_the_expected_catalog_text_casts():
    policy = _policy()
    expression = (
        "tenant_id = nullif(current_setting("
        "'maverick.tenant'::text, true), ''::text)"
    )
    policy["qual"] = expression
    policy["with_check"] = expression
    assert ls._policy_rows_are_exact([policy])

    policy["qual"] = (
        "tenant_id::text = nullif(current_setting("
        "'maverick.tenant'::text, true), ''::text)"
    )
    assert not ls._policy_rows_are_exact([policy])


def test_preprovisioned_schema_needs_no_runtime_ddl_and_drift_is_rechecked(
    fake_postgres,
):
    _path, catalog = fake_postgres
    ls.write_addenda_db({"model": "safe"})
    assert not any(
        sql.startswith(("CREATE TABLE", "ALTER TABLE", "CREATE POLICY"))
        for sql, _params in catalog.calls
    )
    catalog.extra_policy = True
    with pytest.raises(ls.LearningStoreError, match="schema is incompatible"):
        ls.load_addenda_db()


def test_empty_legacy_postgres_tables_coexist_read_only(fake_postgres):
    _path, catalog = fake_postgres
    assert ls.load_addenda_db() == {}
    legacy_reads = [
        sql for sql, _params in catalog.calls
        if sql.startswith("SELECT EXISTS (SELECT 1 FROM harness_")
    ]
    assert {
        table
        for table in ls._LEGACY_POSTGRES_TABLES
        if any(f"FROM {table} " in sql for sql in legacy_reads)
    } == set(ls._LEGACY_POSTGRES_TABLES)
    assert not any(
        sql.startswith(("INSERT INTO harness_", "DELETE FROM harness_"))
        for sql, _params in catalog.calls
    )


def test_nonempty_legacy_postgres_state_refuses_before_shared_serving(
    fake_postgres,
):
    path, catalog = fake_postgres
    # Model a pre-v33 catalog: only the released untenant tables are visible.
    catalog.tables.difference_update(ls._POSTGRES_TABLES)
    catalog.rls.difference_update(ls._POSTGRES_TABLES)
    catalog.policy.difference_update(ls._POSTGRES_TABLES)
    catalog.legacy_rows = True
    with pytest.raises(ls.LearningStoreError, match="migrate it before"):
        ls.write_addenda_db({"model": "must-not-write"})
    with sqlite3.connect(str(path)) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM shared_harness_addenda"
        ).fetchone()[0] == 0
    assert not any(
        sql.startswith((
            "CREATE TABLE IF NOT EXISTS shared_harness_",
            "INSERT INTO shared_harness_",
            "DELETE FROM shared_harness_",
        ))
        for sql, _params in catalog.calls
    )


def test_shared_reads_fail_closed_on_plaintext_or_malformed_payloads(
    fake_postgres,
):
    path, _catalog = fake_postgres
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "INSERT INTO shared_harness_addenda VALUES (?, ?, ?, ?)",
            ("tenant-a", "model", "PLAINTEXT_GUIDANCE", 1.0),
        )
    with pytest.raises(ls.LearningStoreError, match="unencrypted payload"):
        ls.load_addenda_db()

    with sqlite3.connect(str(path)) as conn:
        conn.execute("DELETE FROM shared_harness_addenda")
        conn.execute(
            "INSERT INTO shared_harness_line_meta VALUES (?, ?, ?, ?)",
            (
                "tenant-a",
                "line",
                ls._encode_shared_payload("not-json"),
                1.0,
            ),
        )
    with pytest.raises(ls.LearningStoreError, match="metadata is malformed"):
        ls.load_line_meta_db()


def test_tenant_scoped_rmw_lock_preserves_concurrent_promotions(fake_postgres):
    _path, _catalog = fake_postgres

    def promote(index: int) -> None:
        with ls.rmw_lock():
            current = ls.load_addenda_db()
            time.sleep(0.005)
            current[f"model-{index}"] = f"line-{index}"
            ls.write_addenda_db(current)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(promote, range(8)))

    assert ls.load_addenda_db() == {
        f"model-{index}": f"line-{index}" for index in range(8)
    }
    assert ls._tenant_lock_key("tenant-a") != ls._tenant_lock_key("tenant-b")


def test_rmw_reuses_one_connection_and_rolls_back_caught_multi_family_fault(
    fake_postgres,
    monkeypatch,
):
    path, catalog = fake_postgres
    ls.write_corpus_db("pending", {"model": [{"goal": "baseline"}]})
    before = catalog.connection_count
    original_encode = ls._encode_shared_payload

    def fail_one_payload(value: str) -> str:
        if "FAULT_AFTER_DELETE" in value:
            raise ls.LearningStoreError("injected corpus encoding fault")
        return original_encode(value)

    with pytest.raises(ls.LearningStoreError, match="marked for rollback"):
        with ls.rmw_lock():
            ls.write_addenda_db({"model": "new guidance"})
            ls.write_corpus_db("live", {"model": [{"goal": "new live"}]})
            monkeypatch.setattr(ls, "_encode_shared_payload", fail_one_payload)
            try:
                ls.write_corpus_db(
                    "pending",
                    {"model": [{"goal": "FAULT_AFTER_DELETE"}]},
                )
            except ls.LearningStoreError:
                # Even a caller that catches the inner failure cannot commit
                # the earlier addenda/live writes or the pending DELETE.
                pass
    assert catalog.connection_count == before + 1
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shared_harness_addenda"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shared_harness_corpus WHERE kind='live'"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM shared_harness_corpus WHERE kind='pending'"
        ).fetchone()[0] == 1


def test_caught_nested_rmw_body_failure_marks_outer_transaction_rollback_only(
    fake_postgres,
):
    path, _catalog = fake_postgres
    with pytest.raises(ls.LearningStoreError, match="marked for rollback"):
        with ls.rmw_lock():
            ls.write_addenda_db({"model": "must roll back"})
            try:
                with ls.rmw_lock():
                    raise RuntimeError("nested body failed")
            except RuntimeError:
                pass
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shared_harness_addenda"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("selector_failure", ["flip", "raise"])
def test_backend_drift_inside_rmw_never_routes_to_sqlite_and_rolls_back(
    fake_postgres,
    monkeypatch,
    selector_failure,
):
    path, _catalog = fake_postgres
    state = {"failed": False}

    def selector():
        if not state["failed"]:
            return True
        if selector_failure == "raise":
            raise RuntimeError("credential-bearing selector detail")
        return False

    monkeypatch.setattr(ls, "_postgres", selector)
    with pytest.raises(ls.LearningStoreError, match="marked for rollback"):
        with ls.rmw_lock():
            ls.write_addenda_db({"model": "must roll back"})
            state["failed"] = True
            try:
                ls.write_line_meta_db({"line": {"unsafe": True}})
            except ls.LearningStoreError as exc:
                assert "credential-bearing" not in str(exc)
            else:  # pragma: no cover - a selector flip must always fail closed
                raise AssertionError("backend drift unexpectedly succeeded")
    with sqlite3.connect(str(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM shared_harness_addenda"
        ).fetchone()[0] == 0
    assert not ls._sqlite_path().exists()


def test_postgres_cutover_refuses_file_or_sqlite_state_before_connect(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "42" * 32)
    monkeypatch.setattr(ls, "_postgres", lambda: True)
    opened = 0

    def connect(*, autocommit: bool = False):
        nonlocal opened
        del autocommit
        opened += 1
        raise AssertionError("cutover check must run before connection")

    monkeypatch.setattr(ls, "_connect_postgres", connect)
    legacy = home / "harness" / "addenda.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"model": "legacy"}', encoding="utf-8")
    with pytest.raises(ls.LearningStoreError, match="migrate it before"):
        ls.load_addenda_db()
    assert opened == 0

    legacy.write_text("{}", encoding="utf-8")
    db = home / "world.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(
            "CREATE TABLE harness_line_meta ("
            "line_id TEXT PRIMARY KEY, record TEXT NOT NULL, updated_at REAL NOT NULL)"
        )
        conn.execute(
            "INSERT INTO harness_line_meta VALUES ('line', '{}', 1.0)"
        )
    with pytest.raises(ls.LearningStoreError, match="migrate it before"):
        ls.load_addenda_db()
    assert opened == 0


def test_postgres_cli_migration_uses_fingerprint_scope_and_imports_all_families(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    from click.testing import CliRunner
    from maverick import self_harness as harness
    from maverick import self_harness_eval as evaluation
    from maverick.cli import main

    _db_path, catalog = fake_postgres
    corpus_path = tmp_path / "operator-corpus.json"
    monkeypatch.setattr(ls, "enabled", lambda: True)
    monkeypatch.setattr(ls, "shared_required", lambda: False)
    monkeypatch.setattr(
        harness,
        "settings",
        lambda: {"store": "world", "eval_corpus": str(corpus_path)},
    )
    addenda_path = harness._store_path()
    addenda_path.parent.mkdir(parents=True, exist_ok=True)
    sources = {
        addenda_path: {
            "model": "Operating guidance learned for this model:\n- verify first",
        },
        harness._meta_path(addenda_path): {
            "line": {"model_id": "model", "text": "verify first"},
        },
        harness._transfer_tried_path(addenda_path): {"pair": 123.0},
        corpus_path: {
            "model": [{"goal": "live goal", "expected": "live result"}],
            "_meta": {"owner": "operator"},
        },
        evaluation.pending_corpus_path(corpus_path): {
            "model": [{"goal": "pending goal", "expected": "pending result"}],
        },
        evaluation.rejected_corpus_path(corpus_path): {
            "model": ["rejected goal"],
        },
    }
    for path, payload in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    before_connections = catalog.connection_count
    result = CliRunner().invoke(main, ["self-harness", "migrate-store"])
    assert result.exit_code == 0, result.output
    assert catalog.connection_count == before_connections + 1
    assert "imported 1 addenda key(s), 1 provenance record(s)" in result.output
    assert "corpus: merged 1 live case(s), 1 pending, 1 rejected" in result.output
    for source in sources:
        assert not source.exists()
        assert source.with_name(source.name + ".migrated").is_file()
    assert ls.load_addenda_db()["model"].endswith("- verify first")
    assert ls.load_line_meta_db()["line"]["text"] == "verify first"
    assert ls.load_transfer_tried_db() == {"pair": 123.0}
    assert ls.load_corpus_db("live")["model"][0]["goal"] == "live goal"
    assert ls.load_corpus_db("live")["_meta"] == {"owner": "operator"}
    assert ls.load_corpus_db("pending")["model"][0]["goal"] == "pending goal"
    assert ls.load_corpus_db("rejected") == {"model": ["rejected goal"]}


def test_postgres_cli_migrates_multiple_tenants_sequentially_without_bypass(
    fake_postgres,
    monkeypatch,
):
    from click.testing import CliRunner
    from maverick import self_harness as harness
    from maverick.cli import main
    from maverick.paths import tenant_scope

    _db_path, catalog = fake_postgres
    monkeypatch.setattr(ls, "enabled", lambda: True)
    monkeypatch.setattr(ls, "shared_required", lambda: False)
    monkeypatch.setattr(
        harness,
        "settings",
        lambda: {"store": "world", "eval_corpus": None},
    )

    sources: dict[str, Path] = {}
    for tenant in ("tenant-a", "tenant-b"):
        with tenant_scope(tenant=tenant):
            source = harness._store_path()
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps({
                "model": (
                    "Operating guidance learned for this model:\n"
                    f"- guidance for {tenant}"
                ),
            }), encoding="utf-8")
            sources[tenant] = source

    with tenant_scope(tenant="tenant-a"):
        result = CliRunner().invoke(main, ["self-harness", "migrate-store"])
    assert result.exit_code == 0, result.output
    assert not sources["tenant-a"].exists()
    assert sources["tenant-a"].with_name(
        sources["tenant-a"].name + ".migrated"
    ).is_file()
    assert sources["tenant-b"].is_file()

    # Tenant A's active fingerprint allowance must not authorize serving or
    # importing tenant B. B remains fail-closed until its own migration.
    before = catalog.connection_count
    with tenant_scope(tenant="tenant-b"):
        with pytest.raises(ls.LearningStoreError, match="migrate it before"):
            ls.load_addenda_db()
    assert catalog.connection_count == before

    with tenant_scope(tenant="tenant-b"):
        result = CliRunner().invoke(main, ["self-harness", "migrate-store"])
    assert result.exit_code == 0, result.output
    assert not sources["tenant-b"].exists()
    assert sources["tenant-b"].with_name(
        sources["tenant-b"].name + ".migrated"
    ).is_file()

    for tenant in ("tenant-a", "tenant-b"):
        with tenant_scope(tenant=tenant):
            assert ls.load_addenda_db()["model"].endswith(
                f"- guidance for {tenant}"
            )


def test_migration_allowance_cannot_cross_tenant_mid_scope(
    tmp_path,
    monkeypatch,
):
    from maverick.paths import tenant_scope

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    with tenant_scope(tenant="tenant-a"):
        source = tmp_path / "tenant-a.json"
        source.write_text('{"model": "tenant-a"}', encoding="utf-8")
        plan = ls._prepare_legacy_file_migration((source,))
        with ls._legacy_file_migration_scope(plan):
            with tenant_scope(tenant="tenant-b"):
                with pytest.raises(
                    ls.LearningStoreError,
                    match="migration tenant changed",
                ):
                    # A direct PG connection must reject the changed tenant
                    # before an import can bind the authorized bytes to B.
                    with ls._pg_conn():
                        pass


def test_migration_allowance_is_one_shot_and_content_fingerprint_bound(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    source = tmp_path / "addenda.json"
    source.write_text('{"a": 1}', encoding="utf-8")
    plan = ls._prepare_legacy_file_migration((source,))
    source.write_text('{"b": 1}', encoding="utf-8")  # same byte length, new digest
    with pytest.raises(ls.LearningStoreError, match="changed after authorization"):
        with ls._legacy_file_migration_scope(plan):
            pass
    with pytest.raises(ls.LearningStoreError, match="invalid or used"):
        with ls._legacy_file_migration_scope(plan):
            pass


def test_cutover_file_and_tenant_enumeration_are_bounded_before_read(
    tmp_path,
    monkeypatch,
):
    oversized = tmp_path / "oversized.json"
    with oversized.open("wb") as handle:
        handle.truncate(ls._MAX_LEGACY_FILE_BYTES + 1)
    with pytest.raises(ls.LearningStoreError, match="exceeds its size limit"):
        ls._file_has_legacy_state(oversized)

    target = tmp_path / "target.json"
    link = tmp_path / "legacy-link.json"
    target.write_text('{"state": true}', encoding="utf-8")
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pass  # Windows developer mode may not permit symlink creation.
    else:
        with pytest.raises(ls.LearningStoreError, match="not a regular file"):
            ls._file_has_legacy_state(link)

    home = tmp_path / "home"
    tenant_root = home / "tenants"
    for index in range(3):
        (tenant_root / f"tenant-{index}").mkdir(parents=True)
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setattr(ls, "_MAX_TENANT_DIRS", 2)
    with pytest.raises(ls.LearningStoreError, match="bounded Postgres cutover scan"):
        tuple(ls._legacy_bases())


def test_exported_corpus_migrator_fails_closed_without_read_or_archive(
    tmp_path,
    monkeypatch,
):
    from maverick import self_harness as harness
    from maverick import self_harness_eval as evaluation

    corpus = tmp_path / "malformed-corpus.json"
    corpus.write_bytes(b"{not-json")
    monkeypatch.setattr(ls, "enabled", lambda: True)
    monkeypatch.setattr(ls, "shared_required", lambda: False)
    monkeypatch.setattr(
        harness,
        "settings",
        lambda: {"store": "world", "eval_corpus": str(corpus)},
    )

    before = corpus.read_bytes()
    with pytest.raises(ls.LearningStoreError, match="migrate-store"):
        evaluation.migrate_corpus_files(corpus)
    assert corpus.read_bytes() == before
    assert not corpus.with_name(corpus.name + ".migrated").exists()

    other = tmp_path / "not-routed.json"
    assert evaluation.migrate_corpus_files(other) is None


def test_cli_migration_refuses_malformed_corpus_without_import_or_archive(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    from click.testing import CliRunner
    from maverick import self_harness as harness
    from maverick.cli import main

    _db_path, catalog = fake_postgres
    corpus = tmp_path / "malformed-corpus.json"
    corpus.write_bytes(b"{not-json")
    monkeypatch.setattr(ls, "enabled", lambda: True)
    monkeypatch.setattr(ls, "shared_required", lambda: False)
    monkeypatch.setattr(
        harness,
        "settings",
        lambda: {"store": "world", "eval_corpus": str(corpus)},
    )

    before_connections = catalog.connection_count
    result = CliRunner().invoke(main, ["self-harness", "migrate-store"])
    assert result.exit_code != 0
    assert "legacy learning JSON is malformed" in result.output
    assert catalog.connection_count == before_connections
    assert corpus.read_bytes() == b"{not-json"
    assert not corpus.with_name(corpus.name + ".migrated").exists()


def test_shared_backend_selection_is_global_and_file_overrides_are_forbidden(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {
            "self_harness": {"store": "files"},
            "world_model": {"backend": "postgres"},
        },
    )
    with pytest.raises(ls.LearningStoreError, match="store = world"):
        ls.enabled()

    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {
            "self_harness": {"store": "world"},
            "world_model": {"backend": "sqlite"},
        },
    )
    with pytest.raises(ls.LearningStoreError, match="require Postgres"):
        ls.enabled()

    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {
            "self_harness": {"store": "world"},
            "world_model": {"backend": "postgres"},
        },
    )
    monkeypatch.setattr(
        "maverick.self_harness.settings",
        lambda: {"store": "files", "eval_corpus": str(tmp_path / "corpus.json")},
    )
    from maverick import self_harness as harness
    from maverick import self_harness_eval as evaluation

    assert ls.enabled() is True
    with pytest.raises(ls.LearningStoreError, match="file-store override"):
        harness._world_routed(tmp_path / "override.json")
    with pytest.raises(ls.LearningStoreError, match="corpus file override"):
        evaluation._world_corpus_kind(tmp_path / "other-corpus.json")


def test_connection_failure_is_sanitized_and_never_falls_back(
    tmp_path,
    monkeypatch,
):
    secret = "postgresql://admin:must-not-leak@db.internal/lightwork"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "43" * 32)
    monkeypatch.setattr(ls, "_postgres", lambda: True)

    def fail(*, autocommit: bool = False):
        del autocommit
        raise ConnectionError(f"could not connect to {secret}")

    monkeypatch.setattr(ls, "_connect_postgres", fail)
    with pytest.raises(
        ls.LearningStoreError,
        match="shared learning-store addenda read failed",
    ) as exc:
        ls.load_addenda_db()
    assert secret not in str(exc.value)
    assert "must-not-leak" not in str(exc.value)


def test_shared_store_requires_working_application_encryption(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(ls, "_postgres", lambda: True)
    opened = 0

    def counted(*, autocommit: bool = False):
        nonlocal opened
        del autocommit
        opened += 1
        raise AssertionError("encryption gate must run before connection")

    monkeypatch.setattr(ls, "_connect_postgres", counted)
    with pytest.raises(ls.LearningStoreError, match="requires application"):
        ls.load_addenda_db()
    assert opened == 0
