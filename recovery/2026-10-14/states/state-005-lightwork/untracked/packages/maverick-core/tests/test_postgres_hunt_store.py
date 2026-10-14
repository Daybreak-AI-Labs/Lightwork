"""Shared hunter authority invariants without claiming a live Postgres run."""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from maverick import env_hunt
from maverick.platform_hunt import postgres_store as pg
from maverick.platform_hunt.store import RevisionConflict


class _Rows:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None, *, rowcount: int = -1):
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Catalog:
    def __init__(
        self,
        *,
        superuser: bool = False,
        bypass_rls: bool = False,
        extra_policy: bool = False,
        extra_column: bool = False,
        weak_check: bool = False,
        weak_index: bool = False,
    ) -> None:
        self.superuser = superuser
        self.bypass_rls = bypass_rls
        self.extra_policy = extra_policy
        self.extra_column = extra_column
        self.weak_check = weak_check
        self.weak_index = weak_index
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._calls_lock = threading.Lock()

    def record(self, sql: str, params: tuple[Any, ...]) -> None:
        with self._calls_lock:
            self.calls.append((" ".join(sql.split()), params))


class _FakeCursor:
    """Translate operational psycopg SQL to a shared SQLite database."""

    def __init__(self, connection: sqlite3.Connection, catalog: _Catalog) -> None:
        self._connection = connection
        self._catalog = catalog
        self._result: Any = _Rows()

    @property
    def rowcount(self) -> int:
        return int(self._result.rowcount)

    def execute(self, sql: str, params: tuple[Any, ...] = ()):
        params = tuple(params)
        self._catalog.record(sql, params)
        normalized = " ".join(sql.lower().split())
        if "from pg_roles" in normalized:
            self._result = _Rows([(
                self._catalog.superuser,
                self._catalog.bypass_rls,
            )])
        elif "to_regclass" in normalized:
            self._result = _Rows([(pg._TABLE,)])
        elif "information_schema.columns" in normalized:
            rows = [
                (name, data_type, nullable)
                for name, (data_type, nullable) in pg._COLUMNS.items()
            ]
            if self._catalog.extra_column:
                rows.append(("shadow_tenant", "text", "YES"))
            self._result = _Rows(rows)
        elif "i.indisprimary" in normalized:
            self._result = _Rows([(pg._PRIMARY_KEY,)])
        elif "contype='c'" in normalized:
            rows = [
                (name, next(iter(variants)))
                for name, variants in pg._expected_check_expressions().items()
            ]
            if self._catalog.weak_check:
                rows = [
                    (
                        name,
                        "revision >= 0"
                        if name == "mvk_hunter_revision_bound"
                        else definition,
                    )
                    for name, definition in rows
                ]
            self._result = _Rows(rows)
        elif "from pg_indexes" in normalized:
            rows = []
            for name, columns in pg._INDEXES.items():
                rendered = ", ".join(
                    f"{column} DESC" if column == "updated_at" else column
                    for column in columns
                )
                if self._catalog.weak_index and name == "idx_hunter_records_lookup":
                    rendered = rendered.replace("updated_at DESC", "record_key")
                rows.append((
                    name,
                    f"CREATE INDEX {name} ON {pg._TABLE} ({rendered})",
                ))
            self._result = _Rows(rows)
        elif "relrowsecurity" in normalized:
            self._result = _Rows([(True, True)])
        elif "from pg_policies" in normalized:
            expression = (
                "tenant_id = nullif(current_setting('maverick.tenant', true), '')"
            )
            rows = [(
                pg._POLICY,
                "PERMISSIVE",
                ("public",),
                "ALL",
                expression,
                expression,
            )]
            if self._catalog.extra_policy:
                rows.append((
                    "allow_all",
                    "PERMISSIVE",
                    ("public",),
                    "ALL",
                    "true",
                    "true",
                ))
            self._result = _Rows(rows)
        elif "pg_advisory_xact_lock" in normalized or "set_config(" in normalized:
            self._result = _Rows([(None,)])
        else:
            translated = sql.replace("%s", "?").replace(" FOR UPDATE", "")
            self._result = self._connection.execute(translated, params)
        return self

    def fetchone(self):
        return self._result.fetchone()

    def fetchall(self):
        return self._result.fetchall()


def _create_operational_schema(path: Path) -> None:
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(f"""
            CREATE TABLE {pg._TABLE} (
                tenant_id TEXT NOT NULL,
                hunter_kind TEXT NOT NULL,
                record_kind TEXT NOT NULL,
                record_key TEXT NOT NULL,
                revision INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT '',
                payload_text TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL DEFAULT '',
                prev_hash TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                delivered_at REAL NOT NULL DEFAULT -1,
                expires_at REAL NOT NULL DEFAULT -1,
                PRIMARY KEY (tenant_id, hunter_kind, record_kind, record_key)
            );
            CREATE INDEX idx_hunter_records_lookup ON {pg._TABLE}(
                tenant_id, hunter_kind, record_kind, status, updated_at DESC, record_key
            );
            CREATE INDEX idx_hunter_outbox_pending ON {pg._TABLE}(
                tenant_id, hunter_kind, record_kind, delivered_at, created_at, record_key
            );
        """)


def _factory(path: Path, catalog: _Catalog):
    @contextlib.contextmanager
    def transaction():
        connection = sqlite3.connect(
            str(path),
            timeout=20.0,
            check_same_thread=False,
        )
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield _FakeCursor(connection, catalog)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    return transaction


@pytest.fixture
def shared_hunter(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "51" * 32)
    database = tmp_path / "fake-postgres.sqlite3"
    _create_operational_schema(database)
    catalog = _Catalog()

    def build(*, recorder=lambda _kind, _payload: True, environment=False):
        return pg.PostgresHuntStore(
            tmp_path / ("environment.sqlite3" if environment else "platform.sqlite3"),
            audit_recorder=recorder,
            record_change_event_kind=(
                "env_hunt_record_changed"
                if environment
                else "threat_hunt_record_changed"
            ),
            transaction_factory=_factory(database, catalog),
            check_legacy=False,
            _allow_test_time=True,
        )

    return database, catalog, build


def _finding(identifier: str = "finding-1") -> dict[str, Any]:
    return {
        "finding_id": identifier,
        "rule_id": "LW-SHARED",
        "title": "shared authority finding",
        "status": "open",
        "evidence": [{"event_id": "event-1", "sha256": "a" * 64}],
    }


def test_shared_projection_journal_outbox_and_restart(shared_hunter):
    database, _catalog, build = shared_hunter
    accepting = {"value": False}
    audit: list[tuple[str, dict[str, Any]]] = []

    def recorder(kind, payload):
        audit.append((kind, payload))
        return accepting["value"]

    store = build(recorder=recorder)
    assert store.create_finding(_finding(), actor="alice@example.com")["revision"] == 1
    assert store.pending_audit_count() == 1
    accepting["value"] = True
    assert store.flush_audit_outbox() == 1
    assert store.update_finding(
        "finding-1",
        {"status": "triaged"},
        expected_revision=1,
        actor="bob@example.com",
    )["revision"] == 2
    assert store.verify_journal() == []

    restarted = build(recorder=recorder)
    assert restarted.get_finding("finding-1")["status"] == "triaged"
    assert restarted.list_findings(status="triaged")[0]["finding_id"] == "finding-1"
    with sqlite3.connect(str(database)) as conn:
        payloads = [str(row[0]) for row in conn.execute(
            f"SELECT payload_text FROM {pg._TABLE}"
        )]
    assert payloads
    assert all(payload.startswith("MVKAR1:") for payload in payloads)
    assert all("shared authority finding" not in payload for payload in payloads)
    assert all("alice@example.com" not in str(payload) for _kind, payload in audit)


def test_concurrent_revision_cas_has_one_winner(shared_hunter):
    _database, _catalog, build = shared_hunter
    first = build()
    second = build()
    first.create_finding(_finding(), actor="creator")
    barrier = threading.Barrier(2)

    def update(store, status):
        barrier.wait(timeout=5)
        try:
            return store.update_finding(
                "finding-1",
                {"status": status},
                expected_revision=1,
                actor=status,
            )["status"]
        except RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, (first, second), ("a", "b")))
    assert results.count("conflict") == 1
    assert first.get_finding("finding-1")["revision"] == 2
    assert first.verify_journal() == []


def test_concurrent_distinct_writes_keep_one_contiguous_journal(shared_hunter):
    _database, _catalog, build = shared_hunter
    stores = [build() for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def create(item):
        index, store = item
        barrier.wait(timeout=5)
        return store.create_finding(
            _finding(f"finding-{index}"),
            actor=f"worker-{index}",
        )["finding_id"]

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        created = list(pool.map(create, enumerate(stores)))
    assert len(set(created)) == len(stores)
    assert stores[0].verify_journal() == []


def test_projection_journal_and_outbox_roll_back_together(shared_hunter, monkeypatch):
    _database, _catalog, build = shared_hunter
    store = build()

    def fail_queue(*_args, **_kwargs):
        raise ValueError("injected outbox failure")

    monkeypatch.setattr(store, "_queue_audit", fail_queue)
    with pytest.raises(ValueError, match="injected outbox failure"):
        store.create_finding(_finding(), actor="creator")
    assert store.get_finding("finding-1") is None
    assert store.verify_journal() == []


def test_journal_head_detects_tail_deletion(shared_hunter):
    database, _catalog, build = shared_hunter
    store = build()
    store.create_finding(_finding(), actor="creator")
    with sqlite3.connect(str(database)) as conn:
        conn.execute(
            f"DELETE FROM {pg._TABLE} WHERE record_kind=?",
            (pg._KIND_JOURNAL,),
        )
    assert "head_mismatch" in {item["reason"] for item in store.verify_journal()}


def test_journal_refuses_to_append_after_tail_ciphertext_corruption(shared_hunter):
    database, _catalog, build = shared_hunter
    store = build()
    store.create_finding(_finding(), actor="creator")
    with sqlite3.connect(str(database)) as conn:
        conn.execute(
            f"UPDATE {pg._TABLE} SET payload_text=? WHERE record_kind=?",
            ("MVKAR1:not-valid-ciphertext", pg._KIND_JOURNAL),
        )

    with pytest.raises(
        pg.PostgresHuntStoreUnavailable,
        match="journal head failed its tail binding",
    ):
        store.update_finding(
            "finding-1",
            {"status": "triaged"},
            expected_revision=1,
            actor="analyst",
        )
    assert store.get_finding("finding-1")["revision"] == 1


def test_response_claim_is_one_shot_across_instances_and_survives_restart(
    shared_hunter,
):
    _database, _catalog, build = shared_hunter
    first = build(environment=True)
    second = build(environment=True)
    barrier = threading.Barrier(2)
    digest = "b" * 64

    def claim(store):
        barrier.wait(timeout=5)
        return store.claim_response_execution(
            proposal_id="proposal-1",
            proposal_sha256=digest,
            approval_id="approval-1",
            executor="edr",
            actor="analyst",
        )["new_claim"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, (first, second)))
    assert sorted(outcomes) == [False, True]
    receipt = {"outcome": "contained", "provider_id": "case-1"}
    assert first.complete_response_execution(
        proposal_id="proposal-1",
        proposal_sha256=digest,
        approval_id="approval-1",
        executor="edr",
        receipt=receipt,
        actor="analyst",
    ) == receipt
    restarted = build(environment=True)
    assert restarted.get_response_execution("proposal-1")["receipt"] == receipt
    assert restarted.claim_response_execution(
        proposal_id="proposal-1",
        proposal_sha256=digest,
        approval_id="approval-1",
        executor="edr",
        actor="analyst",
    )["new_claim"] is False


def test_ambiguous_response_never_reopens_execution(shared_hunter):
    _database, _catalog, build = shared_hunter
    store = build(environment=True)
    binding = {
        "proposal_id": "proposal-ambiguous",
        "proposal_sha256": "c" * 64,
        "approval_id": "approval-1",
        "executor": "edr",
    }
    store.claim_response_execution(**binding, actor="analyst")
    store.mark_response_execution_ambiguous(
        **binding,
        error_kind="TimeoutError",
        actor="analyst",
    )
    store.mark_response_execution_ambiguous(
        **binding,
        error_kind="TimeoutError",
        actor="analyst",
    )
    assert store.get_response_execution(binding["proposal_id"])["status"] == "ambiguous"
    with pytest.raises(RevisionConflict, match="reconciliation"):
        store.complete_response_execution(
            **binding,
            receipt={"outcome": "unknown"},
            actor="analyst",
        )


def test_response_receipt_requires_finite_json(shared_hunter):
    _database, _catalog, build = shared_hunter
    store = build(environment=True)
    binding = {
        "proposal_id": "proposal-json",
        "proposal_sha256": "d" * 64,
        "approval_id": "approval-1",
        "executor": "edr",
    }
    store.claim_response_execution(**binding, actor="analyst")
    with pytest.raises(ValueError, match="finite JSON"):
        store.complete_response_execution(
            **binding,
            receipt={"outcome": object()},
            actor="analyst",
        )
    assert store.get_response_execution(binding["proposal_id"])["status"] == "claimed"


def test_scheduler_lease_claim_is_contention_safe(shared_hunter):
    _database, _catalog, build = shared_hunter
    first = build()
    second = build()
    barrier = threading.Barrier(2)

    def acquire(store, owner):
        barrier.wait(timeout=5)
        return store.try_acquire_scheduler_lease(
            "platform",
            owner=owner,
            lease_seconds=30,
            now=100,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(acquire, (first, second), ("a", "b")))
    assert sorted(outcomes) == [False, True]
    assert second.try_acquire_scheduler_lease(
        "platform",
        owner="b",
        lease_seconds=30,
        now=130,
    ) is True


def test_shared_scheduler_rejects_public_wall_clock_override(shared_hunter):
    database, catalog, _build = shared_hunter
    store = pg.PostgresHuntStore(
        Path("unused.sqlite3"),
        transaction_factory=_factory(database, catalog),
        check_legacy=False,
    )
    with pytest.raises(ValueError, match="database-authoritative"):
        store.try_acquire_scheduler_lease(
            "platform",
            owner="worker",
            lease_seconds=30,
            now=32_503_679_000,
        )


def test_platform_and_environment_namespaces_and_tenants_do_not_alias(
    shared_hunter,
    monkeypatch,
):
    _database, _catalog, build = shared_hunter
    platform = build()
    environment = build(environment=True)
    platform.create_finding(_finding(), actor="platform")
    environment.create_finding(_finding(), actor="environment")
    assert platform.get_finding("finding-1") is not None
    assert environment.get_finding("finding-1") is not None

    monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
    assert platform.get_finding("finding-1") is None
    platform.create_finding(_finding(), actor="tenant-b")
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    assert platform.get_finding("finding-1")["revision"] == 1


@pytest.mark.parametrize(
    "catalog",
    [
        _Catalog(superuser=True),
        _Catalog(bypass_rls=True),
        _Catalog(extra_policy=True),
        _Catalog(extra_column=True),
        _Catalog(weak_check=True),
        _Catalog(weak_index=True),
    ],
)
def test_schema_rejects_privileged_roles_and_catalog_drift(
    tmp_path,
    monkeypatch,
    catalog,
):
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "61" * 32)
    database = tmp_path / "fake-postgres.sqlite3"
    _create_operational_schema(database)
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="incompatible"):
        pg.PostgresHuntStore(
            tmp_path / "local.sqlite3",
            transaction_factory=_factory(database, catalog),
            check_legacy=False,
        )


def test_every_operation_rechecks_schema_and_role(shared_hunter):
    _database, catalog, build = shared_hunter
    store = build()
    catalog.extra_policy = True
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="schema or database role"):
        store.pending_audit_count()


def test_outbox_fails_closed_if_recorder_changes_tenant(shared_hunter, monkeypatch):
    _database, _catalog, build = shared_hunter

    def recorder(_kind, _payload):
        monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
        return True

    store = build(recorder=recorder)
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="tenant changed"):
        store.create_finding(_finding(), actor="creator")
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    assert store.pending_audit_count() == 1


def test_custody_witness_rejects_a_preplanted_event_binding(shared_hunter):
    _database, _catalog, build = shared_hunter
    store = build()
    timestamp = 100.0
    with store._transaction() as (cur, tenant):
        stored, digest = pg._encode_payload({
            "event_kind": pg._AUDIT_CUSTODY_EVENT_KIND,
            "payload": {
                "event_id": pg._AUDIT_CUSTODY_EVENT_ID,
                "version": 999,
                "purpose": "forged",
                "occurred_at": timestamp,
            },
        })
        cur.execute(
            f"INSERT INTO {pg._TABLE} "
            "(tenant_id,hunter_kind,record_kind,record_key,revision,status,"
            "payload_text,payload_sha256,prev_hash,created_at,updated_at,"
            "delivered_at,expires_at) VALUES "
            "(%s,%s,%s,%s,1,'pending',%s,%s,'',%s,%s,-1,-1)",
            (
                tenant,
                pg._HUNTER_PLATFORM,
                pg._KIND_OUTBOX,
                pg._AUDIT_CUSTODY_EVENT_ID,
                stored,
                digest,
                timestamp,
                timestamp,
            ),
        )
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="failed its binding"):
        store.ensure_audit_custody_witness()


def test_schema_contract_has_sealed_payload_bounds_and_exact_rls_floor():
    ddl = " ".join(pg._CREATE_TABLE_SQL.lower().split())
    assert "primary key (tenant_id, hunter_kind, record_kind, record_key)" in ddl
    assert "payload_text ~ '^mvkar1:" in ddl
    assert "octet_length(payload_text) <= 8388608" in ddl
    assert "revision between 0 and 9223372036854775807" in ddl
    policy = pg.PostgresHuntStore._policy_sql().lower()
    assert "using (tenant_id = nullif(current_setting('maverick.tenant'" in policy
    assert "with check (tenant_id = nullif(current_setting('maverick.tenant'" in policy


def test_cutover_refuses_local_rows_unknown_schema_and_wal(tmp_path):
    path = tmp_path / "hunt.sqlite3"
    with sqlite3.connect(str(path)) as conn:
        conn.execute("CREATE TABLE hunt_findings (id TEXT)")
    pg.assert_no_legacy_hunt_state(path)
    with sqlite3.connect(str(path)) as conn:
        conn.execute("INSERT INTO hunt_findings VALUES ('finding-1')")
    with pytest.raises(pg.HuntLegacyMigrationRequired, match="explicit Postgres import"):
        pg.assert_no_legacy_hunt_state(path)

    unknown = tmp_path / "unknown.sqlite3"
    with sqlite3.connect(str(unknown)) as conn:
        conn.execute("CREATE TABLE shadow_authority (id TEXT)")
    with pytest.raises(pg.HuntLegacyMigrationRequired, match="unrecognized schema"):
        pg.assert_no_legacy_hunt_state(unknown)

    absent = tmp_path / "absent.sqlite3"
    Path(f"{absent}-wal").write_bytes(b"uncheckpointed")
    with pytest.raises(pg.HuntLegacyMigrationRequired, match="explicit Postgres import"):
        pg.assert_no_legacy_hunt_state(absent)


def test_selector_disables_local_fallback_for_multiple_replicas(monkeypatch):
    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {"world_model": {"backend": "sqlite"}},
    )
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **kwargs: False,
    )
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    monkeypatch.delenv("MAVERICK_WORLD_BACKEND", raising=False)
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="local fallback"):
        pg._selection()


def _production_bound_hunt_backend(shared_hunter, monkeypatch):
    database, catalog, _build = shared_hunter
    selection = pg._Selection(
        postgres=True,
        shared_required=True,
        dsn="postgresql://authority.internal/lightwork",
    )
    current = {"selection": selection}
    monkeypatch.setattr(pg, "_selection", lambda: current["selection"])
    store = pg.PostgresHuntStore(
        Path("unused.sqlite3"),
        transaction_factory=_factory(database, catalog),
        check_legacy=False,
        _allow_test_time=True,
        _authority_token=pg._authority_token(selection),
    )
    return store, catalog, current


@pytest.mark.parametrize(
    "drift",
    [
        pg._Selection(False, False),
        pg._Selection(True, True, "postgresql://other.internal/lightwork"),
        pg._Selection(True, False, "postgresql://authority.internal/lightwork"),
    ],
    ids=["postgres-to-local", "dsn", "enforcement-floor"],
)
def test_bound_hunter_refuses_deployment_authority_drift_before_connection(
    shared_hunter,
    monkeypatch,
    drift,
):
    store, catalog, current = _production_bound_hunt_backend(
        shared_hunter,
        monkeypatch,
    )
    before = len(catalog.calls)
    current["selection"] = drift

    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="authority changed"):
        store.pending_audit_count()
    assert len(catalog.calls) == before


def test_bound_hunter_sanitizes_selector_failure_before_connection(
    shared_hunter,
    monkeypatch,
):
    store, catalog, _current = _production_bound_hunt_backend(
        shared_hunter,
        monkeypatch,
    )
    before = len(catalog.calls)

    def fail_selection():
        raise RuntimeError("secret selector detail")

    monkeypatch.setattr(pg, "_selection", fail_selection)
    with pytest.raises(
        pg.PostgresHuntStoreUnavailable,
        match="cannot be revalidated",
    ) as caught:
        store.pending_audit_count()
    assert "secret selector detail" not in str(caught.value)
    assert len(catalog.calls) == before


def test_mid_transaction_hunter_authority_drift_aborts_before_next_write(
    shared_hunter,
    monkeypatch,
):
    store, catalog, current = _production_bound_hunt_backend(
        shared_hunter,
        monkeypatch,
    )
    before = len(catalog.calls)

    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="authority changed"):
        with store._transaction() as (cur, _tenant):
            current["selection"] = pg._Selection(False, False)
            cur.execute(
                f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s",
                ("tenant-a",),
            )
    new_calls = catalog.calls[before:]
    assert not any(sql.lstrip().upper().startswith("DELETE") for sql, _ in new_calls)


def test_caught_hunter_drift_still_marks_transaction_for_rollback(
    shared_hunter,
    monkeypatch,
):
    store, catalog, current = _production_bound_hunt_backend(
        shared_hunter,
        monkeypatch,
    )
    before = len(catalog.calls)
    original = current["selection"]

    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="marked for rollback"):
        with store._transaction() as (cur, _tenant):
            current["selection"] = pg._Selection(False, False)
            with pytest.raises(pg.PostgresHuntStoreUnavailable, match="authority changed"):
                cur.execute(
                    f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s",
                    ("tenant-a",),
                )
            current["selection"] = original
    new_calls = catalog.calls[before:]
    assert not any(sql.lstrip().upper().startswith("DELETE") for sql, _ in new_calls)


def test_mid_transaction_hunter_tenant_drift_aborts_before_next_write(
    shared_hunter,
    monkeypatch,
):
    store, catalog, _current = _production_bound_hunt_backend(
        shared_hunter,
        monkeypatch,
    )
    before = len(catalog.calls)
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")

    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="tenant changed"):
        with store._transaction() as (cur, _tenant):
            monkeypatch.setenv("MAVERICK_TENANT", "tenant-b")
            cur.execute(
                f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s",
                ("tenant-a",),
            )
    new_calls = catalog.calls[before:]
    assert not any(sql.lstrip().upper().startswith("DELETE") for sql, _ in new_calls)


def test_direct_hunter_backend_does_not_consult_deployment_selector(
    shared_hunter,
    monkeypatch,
):
    _database, _catalog, build = shared_hunter
    store = build()
    monkeypatch.setattr(
        pg,
        "_selection",
        lambda: (_ for _ in ()).throw(AssertionError("selector consulted")),
    )

    assert store.pending_audit_count() == 0


@pytest.mark.parametrize(
    "drift",
    [
        pg._Selection(False, False),
        pg._Selection(True, True, "postgresql://other.internal/lightwork"),
        pg._Selection(True, False, "postgresql://authority.internal/lightwork"),
    ],
    ids=["postgres-to-local", "dsn", "enforcement-floor"],
)
def test_hunter_process_binding_never_rotates_authority(monkeypatch, drift):
    selection = {
        "value": pg._Selection(
            True,
            True,
            "postgresql://authority.internal/lightwork",
        )
    }

    class DummyStore:
        def __init__(self, *_args, **kwargs):
            self.kwargs = kwargs

    pg.reset_shared_hunt_store_for_tests()
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    monkeypatch.setattr(pg, "PostgresHuntStore", DummyStore)
    try:
        assert pg.selected_shared_hunt_store(
            Path("unused.sqlite3"),
            None,
            record_change_event_kind="threat_hunt_record_changed",
        ) is not None
        selection["value"] = drift
        with pytest.raises(pg.PostgresHuntStoreUnavailable, match="process binding"):
            pg.selected_shared_hunt_store(
                Path("unused.sqlite3"),
                None,
                record_change_event_kind="threat_hunt_record_changed",
            )
    finally:
        pg.reset_shared_hunt_store_for_tests()


def test_environment_facade_preserves_credential_value_rejection(
    shared_hunter,
    monkeypatch,
    tmp_path,
):
    _database, _catalog, build = shared_hunter
    backend = build(environment=True)
    monkeypatch.setattr(
        pg,
        "selected_shared_hunt_store",
        lambda *_args, **_kwargs: backend,
    )
    store = env_hunt.HuntStore(tmp_path / "unused.sqlite3")
    with pytest.raises(ValueError, match="credential-like"):
        store.create_finding(
            {
                **_finding(),
                "metadata": {"note": "Authorization: Bearer abcdefghijklmnop"},
            },
            actor="analyst",
        )


def test_shared_payload_decode_rejects_plaintext_and_duplicate_keys(monkeypatch):
    import maverick.crypto_at_rest as crypto

    monkeypatch.setattr(crypto, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(crypto, "is_sealed_str", lambda _value: False)
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="unsealed"):
        pg._decode_payload('{"secret":"plaintext"}')  # pragma: allowlist secret

    monkeypatch.setattr(crypto, "is_sealed_str", lambda _value: True)
    monkeypatch.setattr(
        crypto,
        "unseal_from_str",
        lambda _value: '{"finding_id":"a","finding_id":"b"}',
    )
    with pytest.raises(pg.PostgresHuntStoreUnavailable, match="invalid"):
        pg._decode_payload("MVKAR1:AAAA")
