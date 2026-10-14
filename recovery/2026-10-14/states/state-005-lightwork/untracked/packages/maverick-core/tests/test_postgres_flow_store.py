from __future__ import annotations

import base64
import contextlib
import contextvars
import copy
import threading
import time

import pytest
from maverick.flow import postgres_store as pg
from maverick.flow import store
from maverick.flow.ir import Flow, FlowNode


class _SchemaCursor:
    def __init__(
        self,
        *,
        superuser: bool = False,
        bypass_rls: bool = False,
        extra_policy: bool = False,
        extra_column: bool = False,
        weakened_check: bool = False,
        weakened_index: bool = False,
    ):
        self.superuser = superuser
        self.bypass_rls = bypass_rls
        self.extra_policy = extra_policy
        self.extra_column = extra_column
        self.weakened_check = weakened_check
        self.weakened_index = weakened_index
        self.statements: list[str] = []
        self._rows: list[tuple] = []
        self.rowcount = 0

    def execute(self, sql: str, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.lower().split())
        if "from pg_roles" in normalized:
            self._rows = [(self.superuser, self.bypass_rls)]
        elif "to_regclass" in normalized:
            self._rows = [(pg._TABLE,)]
        elif "information_schema.columns" in normalized:
            self._rows = [
                (name, data_type, nullable)
                for name, (data_type, nullable) in pg._COLUMNS.items()
            ]
            if self.extra_column:
                self._rows.append(("shadow_tenant", "text", "YES"))
        elif "i.indisprimary" in normalized:
            self._rows = [(pg._PRIMARY_KEY,)]
        elif "contype='c'" in normalized:
            expressions = pg._expected_check_expressions()
            self._rows = [
                (name, next(iter(variants)))
                for name, variants in expressions.items()
            ]
            if self.weakened_check:
                self._rows = [
                    (
                        name,
                        "revision >= 0"
                        if name == "mvk_flow_revision_bounds"
                        else definition,
                    )
                    for name, definition in self._rows
                ]
        elif "from pg_indexes" in normalized:
            self._rows = [
                (
                    name,
                    f"CREATE INDEX {name} ON {pg._TABLE} "
                    "("
                    + ", ".join(
                        f"{column} DESC" if column == "updated_at" else column
                        for column in columns
                    )
                    + ")",
                )
                for name, columns in pg._INDEXES.items()
            ]
            if self.weakened_index:
                self._rows = [
                    (name, definition.replace("updated_at DESC", "updated_at"))
                    for name, definition in self._rows
                ]
        elif "relrowsecurity" in normalized:
            self._rows = [(True, True)]
        elif "from pg_policies" in normalized:
            expected = (
                pg._POLICY,
                "PERMISSIVE",
                ("public",),
                "ALL",
                "tenant_id = nullif(current_setting('maverick.tenant', true), '')",
                "tenant_id = nullif(current_setting('maverick.tenant', true), '')",
            )
            self._rows = [expected]
            if self.extra_policy:
                self._rows.append((
                    "allow_all",
                    "PERMISSIVE",
                    ("public",),
                    "ALL",
                    "true",
                    "true",
                ))
        else:
            self._rows = []
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def _schema_factory(cursor: _SchemaCursor):
    @contextlib.contextmanager
    def factory():
        yield cursor

    return factory


def test_verify_first_accepts_least_privilege_role_without_ddl(monkeypatch):
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    cursor = _SchemaCursor()

    backend = pg.PostgresFlowStore(
        transaction_factory=_schema_factory(cursor),
        check_legacy=False,
    )

    assert backend._schema_ready is True
    normalized = [" ".join(sql.lower().split()) for sql in cursor.statements]
    assert not any(sql.startswith(("create ", "alter ", "drop ")) for sql in normalized)
    assert not any("pg_advisory_xact_lock" in sql for sql in normalized)


def test_session_and_transaction_locks_share_tenant_namespace(monkeypatch):
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    monkeypatch.setattr(
        "maverick.crypto_at_rest.at_rest_enabled",
        lambda: True,
    )
    schema_cursor = _SchemaCursor()
    session_calls: list[tuple[str, tuple[object, ...]]] = []

    class SessionCursor:
        def execute(self, sql, params=()):
            session_calls.append((sql, tuple(params)))
            return self

        def fetchone(self):
            return (False, False)

        def close(self):
            return None

    class SessionConnection:
        def cursor(self):
            return SessionCursor()

        def close(self):
            return None

    backend = pg.PostgresFlowStore(
        transaction_factory=_schema_factory(schema_cursor),
        session_connection_factory=SessionConnection,
        check_legacy=False,
    )
    monkeypatch.setattr(backend, "_tenant_key", lambda: "tenant-a")

    with backend._session_lock("definitions"):
        pass

    lock_params = [
        params
        for sql, params in session_calls
        if "pg_advisory_lock" in sql and "unlock" not in sql
    ]
    assert lock_params == [(pg._lock_id("tenant-a", "definitions"),)]
    assert lock_params[0] != (pg._lock_id("definitions"),)


@pytest.mark.parametrize("extra_policy", [False, True])
def test_schema_rejects_bypass_roles_and_extra_permissive_policies(
    monkeypatch,
    extra_policy,
):
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    cursor = _SchemaCursor(
        superuser=not extra_policy,
        extra_policy=extra_policy,
    )

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="incompatible"):
        pg.PostgresFlowStore(
            transaction_factory=_schema_factory(cursor),
            check_legacy=False,
        )


@pytest.mark.parametrize(
    "options",
    [
        {"bypass_rls": True},
        {"extra_column": True},
        {"weakened_check": True},
        {"weakened_index": True},
    ],
)
def test_schema_rejects_role_and_catalog_drift(monkeypatch, options):
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    cursor = _SchemaCursor(**options)

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="incompatible"):
        pg.PostgresFlowStore(
            transaction_factory=_schema_factory(cursor),
            check_legacy=False,
        )


def test_schema_ddl_contains_database_bounds_and_sealed_payload_floor():
    ddl = " ".join(pg._CREATE_TABLE_SQL.lower().split())
    assert "primary key (tenant_id, record_kind, record_key)" in ddl
    assert "record_key ~ '^[0-9a-f]{64}" in ddl
    assert "revision between 1 and 9223372036854775807" in ddl
    assert "octet_length(payload_text) <= 8388608" in ddl
    assert "payload_text ~ '^mvkar1:" in ddl
    assert "flow_key = '' or flow_key ~ '^[0-9a-f]{64}$'" in ddl


def test_shared_payload_never_accepts_plaintext(monkeypatch):
    import maverick.crypto_at_rest as crypto

    monkeypatch.setattr(crypto, "at_rest_enabled", lambda: False)
    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="encryption"):
        pg._encode_payload({"prompt": "sensitive"})

    monkeypatch.setattr(crypto, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(crypto, "is_sealed_str", lambda value: False)
    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="unsealed"):
        pg._decode_payload('{"prompt":"plaintext"}')


def test_cutover_refuses_any_nonempty_legacy_flow_state(tmp_path):
    (tmp_path / "flows" / "runs").mkdir(parents=True)
    (tmp_path / "flows" / ".definitions").write_text("", encoding="utf-8")
    pg.assert_no_legacy_flow_state(tmp_path)

    (tmp_path / "flows" / "runs" / "queued.json").write_text(
        "{}",
        encoding="utf-8",
    )
    with pytest.raises(pg.FlowLegacyMigrationRequired, match="explicit Postgres import"):
        pg.assert_no_legacy_flow_state(tmp_path)


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

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="local fallback"):
        pg._selection()


def _production_bound_backend(monkeypatch):
    selection = pg._Selection(
        postgres=True,
        shared_required=True,
        dsn="postgresql://authority.internal/lightwork",
    )
    current = {"selection": selection}
    monkeypatch.setattr(pg, "_selection", lambda: current["selection"])
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    monkeypatch.setattr("maverick.crypto_at_rest.at_rest_enabled", lambda: True)
    cursor = _SchemaCursor()
    backend = pg.PostgresFlowStore(
        transaction_factory=_schema_factory(cursor),
        check_legacy=False,
        _authority_token=pg._authority_token(selection),
    )
    monkeypatch.setattr(backend, "_tenant_key", lambda: "tenant-a")
    return backend, cursor, current


@pytest.mark.parametrize(
    "drift",
    [
        pg._Selection(False, False),
        pg._Selection(True, True, "postgresql://other.internal/lightwork"),
        pg._Selection(True, False, "postgresql://authority.internal/lightwork"),
    ],
    ids=["postgres-to-local", "dsn", "enforcement-floor"],
)
def test_bound_backend_refuses_deployment_authority_drift_before_connection(
    monkeypatch,
    drift,
):
    backend, cursor, current = _production_bound_backend(monkeypatch)
    before = len(cursor.statements)
    current["selection"] = drift

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="authority changed"):
        with backend._transaction():
            pass
    assert len(cursor.statements) == before


def test_bound_backend_sanitizes_selector_failure_before_connection(monkeypatch):
    backend, cursor, _current = _production_bound_backend(monkeypatch)
    before = len(cursor.statements)

    def fail_selection():
        raise RuntimeError("secret selector detail")

    monkeypatch.setattr(pg, "_selection", fail_selection)
    with pytest.raises(
        pg.PostgresFlowStoreUnavailable,
        match="cannot be revalidated",
    ) as caught:
        with backend._transaction():
            pass
    assert "secret selector detail" not in str(caught.value)
    assert len(cursor.statements) == before


def test_mid_transaction_authority_drift_aborts_before_next_write(monkeypatch):
    backend, cursor, current = _production_bound_backend(monkeypatch)

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="authority changed"):
        with backend._transaction() as (cur, _state):
            current["selection"] = pg._Selection(False, False)
            cur.execute(f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s", ("tenant-a",))
    assert not any(
        statement.lstrip().upper().startswith("DELETE")
        for statement in cursor.statements
    )


def test_caught_mid_transaction_drift_still_marks_transaction_for_rollback(
    monkeypatch,
):
    backend, cursor, current = _production_bound_backend(monkeypatch)
    original = current["selection"]

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="marked for rollback"):
        with backend._transaction() as (cur, _state):
            current["selection"] = pg._Selection(False, False)
            with pytest.raises(pg.PostgresFlowStoreUnavailable, match="authority changed"):
                cur.execute(
                    f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s",
                    ("tenant-a",),
                )
            current["selection"] = original
    assert not any(
        statement.lstrip().upper().startswith("DELETE")
        for statement in cursor.statements
    )


def test_mid_transaction_tenant_drift_aborts_before_next_write(monkeypatch):
    backend, cursor, _current = _production_bound_backend(monkeypatch)
    tenant = {"value": "tenant-a"}
    monkeypatch.setattr(backend, "_tenant_key", lambda: tenant["value"])

    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="tenant changed"):
        with backend._transaction() as (cur, _state):
            tenant["value"] = "tenant-b"
            cur.execute(f"DELETE FROM {pg._TABLE} WHERE tenant_id=%s", ("tenant-a",))
    assert not any(
        statement.lstrip().upper().startswith("DELETE")
        for statement in cursor.statements
    )


def test_session_lock_revalidates_after_inflight_authority_drift(monkeypatch):
    selection = pg._Selection(
        True,
        True,
        "postgresql://authority.internal/lightwork",
    )
    current = {"selection": selection}
    monkeypatch.setattr(pg, "_selection", lambda: current["selection"])
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    monkeypatch.setattr("maverick.crypto_at_rest.at_rest_enabled", lambda: True)
    schema_cursor = _SchemaCursor()
    session_calls: list[str] = []

    class SessionCursor:
        def execute(self, sql, params=()):
            session_calls.append(" ".join(sql.split()))
            if "pg_advisory_lock" in sql and "unlock" not in sql:
                current["selection"] = pg._Selection(False, False)
            return self

        def fetchone(self):
            return (False, False)

        def close(self):
            return None

    class SessionConnection:
        def cursor(self):
            return SessionCursor()

        def close(self):
            return None

    backend = pg.PostgresFlowStore(
        transaction_factory=_schema_factory(schema_cursor),
        session_connection_factory=SessionConnection,
        check_legacy=False,
        _authority_token=pg._authority_token(selection),
    )
    monkeypatch.setattr(backend, "_tenant_key", lambda: "tenant-a")

    yielded = False
    with pytest.raises(pg.PostgresFlowStoreUnavailable, match="authority changed"):
        with backend._session_lock("definitions"):
            yielded = True
    assert yielded is False
    assert any("pg_advisory_unlock" in sql for sql in session_calls)


def test_directly_injected_backend_does_not_consult_deployment_selector(monkeypatch):
    monkeypatch.setattr(pg, "_probe_encryption", lambda: None)
    monkeypatch.setattr("maverick.crypto_at_rest.at_rest_enabled", lambda: True)
    cursor = _SchemaCursor()
    backend = pg.PostgresFlowStore(
        transaction_factory=_schema_factory(cursor),
        check_legacy=False,
    )
    monkeypatch.setattr(backend, "_tenant_key", lambda: "tenant-a")
    monkeypatch.setattr(
        pg,
        "_selection",
        lambda: (_ for _ in ()).throw(AssertionError("selector consulted")),
    )

    with backend._transaction():
        pass


def test_process_binding_never_rotates_to_local_authority(monkeypatch):
    selection = {
        "value": pg._Selection(
            True,
            True,
            "postgresql://authority.internal/lightwork",
        )
    }

    class DummyStore:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            return None

    pg.reset_shared_flow_store_for_tests()
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    monkeypatch.setattr(pg, "assert_no_legacy_flow_state", lambda: None)
    monkeypatch.setattr(pg, "PostgresFlowStore", DummyStore)
    try:
        assert pg.selected_shared_flow_store() is not None
        selection["value"] = pg._Selection(False, False)
        with pytest.raises(pg.PostgresFlowStoreUnavailable, match="process binding"):
            pg.selected_shared_flow_store()
    finally:
        pg.reset_shared_flow_store_for_tests()


class _MemoryPostgres(pg.PostgresFlowStore):
    """Transaction/row fake exercising backend algorithms without a server."""

    def __init__(self):
        self._require_bound_tenant = True
        self._check_legacy = False
        self._schema_ready = True
        self._legacy_home_override = None
        self._tx_state = contextvars.ContextVar(
            f"test_flow_pg_{id(self)}",
            default=None,
        )
        self._held_run_revisions = contextvars.ContextVar(
            f"test_flow_pg_runs_{id(self)}",
            default=None,
        )
        self._records: dict[tuple[str, str, str], list] = {}
        self._lock = threading.RLock()
        self.transaction_starts = 0

    @contextlib.contextmanager
    def _session_lock(self, *parts):
        with self._lock:
            yield

    @contextlib.contextmanager
    def _transaction(self):
        active = self._tx_state.get()
        if active is not None:
            yield self, active
            return
        with self._lock:
            self.transaction_starts += 1
            before = copy.deepcopy(self._records)
            state = pg._TransactionState(tenant="tenant-a", cursor=self)
            token = self._tx_state.set(state)
            try:
                yield self, state
            except BaseException:
                self._records = before
                raise
            finally:
                self._tx_state.reset(token)

    @staticmethod
    def _advisory_lock(cur, *parts):
        return None

    def _select_record(
        self,
        cur,
        tenant,
        kind,
        key,
        *,
        for_update=False,
    ):
        row = self._records.get((tenant, kind, key))
        return tuple(row) if row is not None else None

    def _insert_record(
        self,
        cur,
        tenant,
        kind,
        key,
        payload,
        *,
        flow_key="",
        owner_key="",
        status="",
        created_at=None,
        updated_at=None,
    ):
        record = (tenant, kind, key)
        if record in self._records:
            return False
        now = time.time()
        created = now if created_at is None else float(created_at)
        updated = now if updated_at is None else float(updated_at)
        self._records[record] = [
            1,
            pg._encode_payload(payload),
            flow_key,
            owner_key,
            status,
            created,
            updated,
        ]
        return True

    def _update_record(
        self,
        cur,
        tenant,
        kind,
        key,
        expected_revision,
        payload,
        *,
        flow_key="",
        owner_key="",
        status="",
        updated_at=None,
    ):
        record = (tenant, kind, key)
        row = self._records.get(record)
        if row is None or int(row[0]) != int(expected_revision):
            return None
        updated = time.time() if updated_at is None else float(updated_at)
        row[:] = [
            int(row[0]) + 1,
            pg._encode_payload(payload),
            flow_key,
            owner_key,
            status,
            row[5],
            updated,
        ]
        return int(row[0])

    def _list_runs_locked(
        self,
        cur,
        state,
        *,
        flow_id=None,
        owner=None,
        limit=50,
    ):
        rows = [
            tuple(row)
            for (tenant, kind, _key), row in self._records.items()
            if tenant == state.tenant and kind == pg._KIND_RUN
        ]
        runs = [self._run_from_row(row) for row in rows]
        if flow_id is not None:
            runs = [run for run in runs if run.flow_id == flow_id]
        if owner is not None:
            runs = [run for run in runs if run.owner == owner]
        runs.sort(key=lambda run: run.updated, reverse=True)
        return runs if limit is None else runs[: max(0, int(limit))]


@pytest.fixture
def fake_crypto_codec(monkeypatch):
    import maverick.crypto_at_rest as crypto

    def seal(raw: str) -> str:
        return "MVKAR1:" + base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def unseal(stored: str) -> str:
        return base64.b64decode(stored.removeprefix("MVKAR1:")).decode("utf-8")

    monkeypatch.setattr(crypto, "at_rest_enabled", lambda: True)
    monkeypatch.setattr(crypto, "seal_to_str", seal)
    monkeypatch.setattr(
        crypto,
        "is_sealed_str",
        lambda value: isinstance(value, str) and value.startswith("MVKAR1:"),
    )
    monkeypatch.setattr(crypto, "unseal_from_str", unseal)


def _flow(brief: str = "work") -> Flow:
    return Flow(
        id="shared-flow",
        name="Shared",
        start="n",
        owner="user:alice",
        nodes={"n": FlowNode(id="n", kind="agent", brief=brief)},
    )


def test_fake_postgres_definition_cas_publication_and_object_integrity(
    fake_crypto_codec,
):
    backend = _MemoryPostgres()
    saved = backend.save_flow(_flow())
    first_reader = backend.load_flow(saved.id)
    stale_reader = backend.load_flow(saved.id)
    assert first_reader is not None and stale_reader is not None
    first_reader.nodes["n"].brief = "first writer"
    updated = backend.save_flow(
        first_reader,
        expected_version=saved.version,
        expected_revision=saved.revision,
    )
    stale_reader.nodes["n"].brief = "stale writer"
    with pytest.raises(store.FlowVersionConflict):
        backend.save_flow(
            stale_reader,
            expected_version=saved.version,
            expected_revision=saved.revision,
        )

    release = backend.publish_flow(
        saved.id,
        expected_version=updated.version,
        expected_revision=updated.revision,
    )
    published = backend.load_published_bundle(saved.id)
    assert published is not None
    assert published[1]["release_id"] == release["release_id"]
    digest = release["definition_digest"]
    object_row = backend._records[("tenant-a", pg._KIND_OBJECT, digest)]
    tampered = pg._decode_payload(object_row[1])
    tampered["name"] = "tampered"
    object_row[1] = pg._encode_payload(tampered)
    with pytest.raises(store.FlowSnapshotError, match="integrity"):
        backend.load_flow_snapshot(digest)


def _run(run_id: str, *, idem_key: str = "") -> store.FlowRun:
    now = time.time()
    return store.FlowRun(
        run_id=run_id,
        flow_id="shared-flow",
        status="queued",
        owner="user:alice",
        idem_key=idem_key,
        created=now,
        updated=now,
    )


def test_fake_postgres_run_compare_and_swap_and_lock_replacement(fake_crypto_codec):
    backend = _MemoryPostgres()
    initial = _run("run-cas")
    backend.save_run(initial, initial.to_dict())
    first = backend.load_run(initial.run_id)
    stale = backend.load_run(initial.run_id)
    assert first is not None and stale is not None
    first.status = "claimed"
    first.updated = time.time()
    backend.save_run(first, first.to_dict())
    stale.status = "completed"
    stale.updated = time.time()
    with pytest.raises(store.FlowVersionConflict):
        backend.save_run(stale, stale.to_dict())

    with backend.run_lock(initial.run_id):
        starts_before_write = backend.transaction_starts
        current = backend.load_run(initial.run_id)
        replacement = _run(initial.run_id)
        replacement.created = current.created
        replacement.updated = time.time()
        replacement.status = "completed"
        backend.save_run(replacement, replacement.to_dict())
        # The session mutex spans independently committed read/write
        # transactions; execution state is not held uncommitted until unlock.
        assert backend.transaction_starts >= starts_before_write + 2
    assert backend.load_run(initial.run_id).status == "completed"


def test_fake_postgres_idempotency_claim_is_first_writer_across_threads(
    fake_crypto_codec,
):
    backend = _MemoryPostgres()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def reserve(suffix: str) -> None:
        barrier.wait()
        with backend.idempotency_lock(
            "shared-flow",
            "event-1",
            owner="user:alice",
        ):
            existing = backend.find_run_by_idem(
                "shared-flow",
                "event-1",
                owner="user:alice",
            )
            if existing is not None:
                outcomes.append(existing.run_id)
                return
            run = _run(f"run-{suffix}", idem_key="event-1")
            backend.save_run(run, run.to_dict())
            assert backend.save_idempotency_claim(
                run.flow_id,
                run.idem_key,
                run.run_id,
                owner=run.owner,
            )
            outcomes.append(run.run_id)

    threads = [
        threading.Thread(target=reserve, args=("a",)),
        threading.Thread(target=reserve, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(outcomes) == 2
    assert len(set(outcomes)) == 1
    assert sum(
        kind == pg._KIND_CLAIM for (_tenant, kind, _key) in backend._records
    ) == 1
    assert sum(
        kind == pg._KIND_RUN for (_tenant, kind, _key) in backend._records
    ) == 1


def test_run_and_idempotency_claim_are_atomic_without_cooperative_lock(
    fake_crypto_codec,
):
    backend = _MemoryPostgres()
    barrier = threading.Barrier(2)
    winners: list[str] = []
    conflicts: list[str] = []

    def reserve(suffix: str) -> None:
        run = _run(f"unlocked-{suffix}", idem_key="same-event")
        barrier.wait()
        try:
            backend.save_run(run, run.to_dict())
            winners.append(run.run_id)
        except store.FlowVersionConflict:
            conflicts.append(run.run_id)

    threads = [
        threading.Thread(target=reserve, args=("a",)),
        threading.Thread(target=reserve, args=("b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(winners) == 1
    assert len(conflicts) == 1
    assert sum(
        kind == pg._KIND_CLAIM for (_tenant, kind, _key) in backend._records
    ) == 1
    assert sum(
        kind == pg._KIND_RUN for (_tenant, kind, _key) in backend._records
    ) == 1


def test_public_store_delegates_and_preserves_run_redaction(
    monkeypatch,
    fake_crypto_codec,
):
    backend = _MemoryPostgres()
    monkeypatch.setattr(store, "_shared_store", lambda: backend)

    saved = store.save_flow(_flow())
    assert store.load_flow(saved.id).revision == saved.revision
    run = store.FlowRun(
        run_id="delegated-run",
        flow_id=saved.id,
        status="queued",
        data={"api_key": "secret-value"},  # pragma: allowlist secret
    )
    store.save_run(run)

    loaded = store.load_run(run.run_id)
    assert loaded is not None
    assert loaded.data == {"api_key": "***redacted***"}
    loaded.status = "claimed"
    store.save_run(loaded)
    assert store.load_run(run.run_id).status == "claimed"
