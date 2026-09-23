from __future__ import annotations

import importlib.util
import inspect
import threading
from contextlib import contextmanager

import pytest
from maverick.world_model_backends import postgres
from maverick.world_model_backends.postgres import PostgresWorldModel

_HAS_PSYCOPG = importlib.util.find_spec("psycopg") is not None


class RecordingCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...] | None]] = []

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.calls.append((sql, params))

    def fetchone(self):
        return (None,)


class RetentionCursor(RecordingCursor):
    rowcount = 2

    def fetchone(self):
        return (2,)


class TemporalDeleteCursor(RecordingCursor):
    rowcount = 1

    def fetchone(self):
        return None


class FactTrustSnapshotCursor(RecordingCursor):
    def fetchall(self):
        return [("risk:vendor", "external value", 0)]


class EmptyFactCursor(RecordingCursor):
    def fetchone(self):
        return None

    def fetchall(self):
        return []


class ErasureReceiptCursor(RecordingCursor):
    def fetchone(self):
        return (2,)

    def fetchall(self):
        return [(201,), (202,)]


class SubjectFactPurgeCursor(RecordingCursor):
    def fetchall(self):
        # An own + compatibility-legacy row may share the same natural key.
        return [
            ("user:sms:alice:email",),
            ("user:sms:alice:email",),
            ("user:sms:alice:phone",),
        ]


class FactHistoryCursor(RecordingCursor):
    def fetchall(self):
        return [
            (1, "k", "legacy", 300.0, None, "old", 2, "internal", None),
            (2, "k", "own", 200.0, 300.0, "new", 3, "internal", "acme"),
        ]


def test_effective_history_splits_legacy_only_across_own_overlap():
    rows = [
        (1, "user:sms:alice:email", "legacy", 100.0, None, "old", 2, "internal", None),
        (2, "user:sms:alice:email", "own", 200.0, 300.0, "new", 3, "internal", "acme"),
    ]

    projected = postgres._effective_fact_history_rows(rows, "acme")

    assert [
        (row[2], row[3], row[4], row[8])
        for row in projected
    ] == [
        ("legacy", 100.0, 200.0, None),
        ("own", 200.0, 300.0, "acme"),
        ("legacy", 300.0, None, None),
    ]
    # The unbound administrative history keeps its prior raw multi-tenant view.
    assert postgres._effective_fact_history_rows(rows, None) == rows


def test_subject_fact_purge_locks_tables_and_accounts_returned_keys(monkeypatch):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    monkeypatch.setattr(postgres, "_strict_tenant_isolation", lambda: False)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = SubjectFactPurgeCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    assert world.delete_facts_matching("sms:alice") == [
        "user:sms:alice:email",
        "user:sms:alice:phone",
    ]
    assert len(cur.calls) == 3
    lock_sql, lock_params = cur.calls[0]
    live_sql, live_params = cur.calls[1]
    history_sql, history_params = cur.calls[2]
    assert lock_sql == (
        "LOCK TABLE fact_history, facts IN SHARE ROW EXCLUSIVE MODE"
    )
    assert lock_params is None
    assert live_sql.startswith("DELETE FROM facts")
    assert live_sql.endswith("RETURNING key")
    assert history_sql.startswith("DELETE FROM fact_history")
    for sql in (live_sql, history_sql):
        assert "(tenant_id = %s OR tenant_id IS NULL)" in sql
    assert live_params == ("user:sms:alice:%", "acme")
    assert history_params == ("user:sms:alice:%", "acme")


def test_compatibility_history_projects_before_database_side_limit(monkeypatch):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    monkeypatch.setattr(postgres, "_strict_tenant_isolation", lambda: False)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = FactHistoryCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    history = world.fact_history("k", limit=2)

    assert [version.value for version in history] == ["legacy", "own"]
    [(sql, params)] = cur.calls
    assert "WITH scoped AS" in sql
    assert "JOIN LATERAL" in sql
    assert "LAG(id) OVER (ORDER BY segment_start)" in sql
    assert sql.endswith("ORDER BY MIN(segment_start) DESC, id DESC LIMIT %s")
    assert params == ("k", "acme", "acme", 2)


def test_fact_upsert_advances_write_order_sequence(monkeypatch):
    """A same-timestamp update must sort after a newer key on Postgres too."""
    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    monkeypatch.setattr(
        "maverick.world_model._temporal_memory_enabled", lambda: False
    )
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = RecordingCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)
    world.upsert_fact("older-key", "updated last")

    sql, _ = cur.calls[-1]
    assert "write_seq = EXCLUDED.write_seq" in sql
    assert "SET id =" not in sql
    source = inspect.getsource(PostgresWorldModel.upsert_fact)
    assert '"SELECT MAX(updated_at)' not in source
    assert "fact-write-order:" not in source
    recall_source = inspect.getsource(PostgresWorldModel.get_facts)
    assert "ORDER BY write_seq DESC" in recall_source


def test_fact_value_and_trust_share_one_snapshot(monkeypatch):
    """A recall cannot splice a value and trust tier from separate writes."""
    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = FactTrustSnapshotCursor()
    tx_count = 0

    @contextmanager
    def tx():
        nonlocal tx_count
        tx_count += 1
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    assert world.get_facts_with_trust() == {
        "risk:vendor": ("external value", 0)
    }
    assert tx_count == 1
    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert "SELECT key, value, trust_tier FROM (" in sql
    assert "FROM facts" in sql
    assert "ORDER BY write_seq DESC" in sql
    assert params == ()


@pytest.mark.parametrize(
    ("method", "args", "kwargs", "expected_order"),
    [
        ("get_facts", (), {}, "ORDER BY write_seq DESC"),
        ("get_facts_with_trust", (), {}, "ORDER BY write_seq DESC"),
        ("get_fact", ("missing",), {}, "ORDER BY write_seq DESC LIMIT 1"),
        (
            "get_fact",
            ("missing",),
            {"as_of": 100.0},
            "ORDER BY valid_from DESC, id DESC LIMIT 1",
        ),
        ("list_facts", ("prefix:",), {}, "ORDER BY write_seq DESC"),
        (
            "search_facts",
            ("prefix:", "needle"),
            {},
            "ORDER BY write_seq DESC",
        ),
    ],
)
def test_unbound_fact_reads_omit_tenant_precedence(
    monkeypatch,
    method,
    args,
    kwargs,
    expected_order,
):
    """Unbound Postgres reads must never emit invalid ``ORDER BY 0`` SQL."""
    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    monkeypatch.setattr(
        "maverick.crypto_at_rest.at_rest_enabled",
        lambda: False,
    )
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = EmptyFactCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    getattr(world, method)(*args, **kwargs)

    [(sql, _params)] = cur.calls
    assert expected_order in sql
    assert "ORDER BY 0" not in sql
    assert "CASE WHEN" not in sql
    assert postgres._tenant_precedence() == ("", [])


def test_erasure_receipt_episode_count_scopes_through_parent_goal(monkeypatch):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    monkeypatch.setattr(postgres, "_strict_tenant_isolation", lambda: False)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = ErasureReceiptCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    assert world.count_erasure_receipt_store(
        "episodes",
        conversation_ids=[1],
        goal_ids=[11],
    ) == 2

    assert len(cur.calls) == 2
    select_sql, select_params = cur.calls[0]
    count_sql, count_params = cur.calls[1]
    for sql in (select_sql, count_sql):
        assert "FROM episodes e JOIN goals g ON g.id = e.goal_id" in sql
        assert "(g.tenant_id = %s OR g.tenant_id IS NULL)" in sql
        assert "e.tenant_id" not in sql
    assert select_params == ([11], "acme")
    assert count_params == ([201, 202], "acme")


def test_temporal_delete_uses_same_per_key_advisory_lock(monkeypatch):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    monkeypatch.setattr(
        "maverick.world_model._temporal_memory_enabled", lambda: True
    )
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = TemporalDeleteCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)
    assert world.delete_fact("shared-key") == 1

    sql = [statement for statement, _params in cur.calls]
    lock_index = next(
        index
        for index, statement in enumerate(sql)
        if "pg_advisory_xact_lock" in statement
    )
    delete_index = next(
        index
        for index, statement in enumerate(sql)
        if statement.startswith("DELETE FROM facts")
    )
    assert lock_index < delete_index
    assert "fact:acme:shared-key" in str(cur.calls[lock_index][1])


def test_rls_sets_fail_closed_sentinel_without_active_tenant(monkeypatch):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._rls = True
    cur = RecordingCursor()

    world._set_tenant_guc(cur)

    assert cur.calls == [
        ("SELECT set_config('maverick.tenant', %s, true)", ("__maverick_no_tenant__",))
    ]


def test_rls_binds_the_active_tenant_on_every_transaction(monkeypatch):
    # The GUC the RLS policy keys on must carry the ACTIVE tenant verbatim, set
    # transaction-locally (is_local=true), so the database enforces the boundary
    # even on a query that carries no app-layer predicate.
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._rls = True
    cur = RecordingCursor()

    world._set_tenant_guc(cur)

    assert cur.calls == [
        ("SELECT set_config('maverick.tenant', %s, true)", ("acme",))
    ]


def test_no_guc_is_written_when_rls_is_off(monkeypatch):
    # With RLS off the app-layer _tenant_scope predicate is the boundary and the
    # backend must not touch the GUC (nothing to fail-close against).
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._rls = False
    cur = RecordingCursor()

    world._set_tenant_guc(cur)

    assert cur.calls == []


@pytest.mark.parametrize(
    "method,table",
    [
        ("purge_episodes_before", "episodes"),
        ("purge_goal_events_before", "goal_events"),
        ("purge_messages_before", "messages"),
    ],
)
def test_retention_purge_uses_exact_active_tenant(monkeypatch, method, table):
    monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = RetentionCursor()

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    assert getattr(world, method)(123.0, dry_run=True) == 2
    sql, params = cur.calls[0]
    assert f"FROM {table}" in sql
    assert "tenant_id = %s" in sql
    assert "IS NULL" not in sql
    assert params == (123.0, "acme")


def test_retention_purge_rejects_missing_active_tenant(monkeypatch):
    from maverick.paths import TenantPolicyError

    monkeypatch.setattr(postgres, "_active_tenant", lambda: None)
    world = PostgresWorldModel.__new__(PostgresWorldModel)

    with pytest.raises(TenantPolicyError, match="refusing unbound Postgres"):
        world.purge_goal_events_before(123.0)


def test_rls_policy_does_not_allow_unset_tenant_bypass():
    sql = PostgresWorldModel._rls_policy_sql("goals").lower()

    assert "current_setting('maverick.tenant'" in sql
    assert "tenant_id = nullif" in sql
    assert " with check " in f" {sql} "
    assert " or " not in sql
    assert "is null" not in sql


def test_rls_apply_raises_when_policy_cannot_be_installed_or_verified(monkeypatch):
    world = PostgresWorldModel.__new__(PostgresWorldModel)

    @contextmanager
    def failing_tx():
        raise PermissionError("no alter table")
        yield  # pragma: no cover

    monkeypatch.setattr(postgres, "_TENANT_TABLES", ["goals"])
    monkeypatch.setattr(world, "_tx", failing_tx)
    monkeypatch.setattr(world, "_rls_policy_is_active", lambda table: False)

    with pytest.raises(RuntimeError, match="could not be installed or verified on goals"):
        world._apply_rls()

def test_rls_covers_tables_added_after_the_v10_tenant_migration():
    # Council #7: projects (v15) and fact_history (v18) carry tenant_id but were
    # absent from the RLS set, so a PG deployment left them app-layer-only. They
    # must be RLS-scoped, while staying OUT of the v10 column-add migration list.
    assert "projects" in postgres._RLS_TABLES
    assert "fact_history" in postgres._RLS_TABLES
    assert "erasure_receipts" in postgres._RLS_TABLES
    assert "approval_audit_outbox" in postgres._RLS_TABLES
    assert "projects" not in postgres._TENANT_TABLES      # not in the v10 ALTER set
    assert "fact_history" not in postgres._TENANT_TABLES
    assert "erasure_receipts" not in postgres._TENANT_TABLES
    assert "approval_audit_outbox" not in postgres._TENANT_TABLES
    # Every v10 tenant table is still RLS-covered.
    assert set(postgres._TENANT_TABLES).issubset(set(postgres._RLS_TABLES))


# --- #51/#57: enterprise auto-on for strict isolation + RLS ----------------

@pytest.fixture
def _clean_toggle_env(monkeypatch):
    for var in ("MAVERICK_STRICT_TENANT_ISOLATION", "MAVERICK_PG_RLS"):
        monkeypatch.delenv(var, raising=False)
    # No config and not enterprise unless a test opts in.
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("maverick.enterprise.enterprise_enabled", lambda: False)


def test_toggles_default_off_without_enterprise(_clean_toggle_env):
    assert postgres._strict_tenant_isolation() is False
    assert postgres._rls_enabled() is False
    assert postgres._rls_explicitly_set() is False


def test_toggles_auto_on_under_enterprise(_clean_toggle_env, monkeypatch):
    monkeypatch.setattr("maverick.enterprise.enterprise_enabled", lambda: True)
    assert postgres._strict_tenant_isolation() is True
    assert postgres._rls_enabled() is True
    # Auto-on is NOT an explicit operator choice -> the boot preflight applies.
    assert postgres._rls_explicitly_set() is False


def test_explicit_env_off_overrides_enterprise(_clean_toggle_env, monkeypatch):
    monkeypatch.setattr("maverick.enterprise.enterprise_enabled", lambda: True)
    monkeypatch.setenv("MAVERICK_PG_RLS", "0")
    monkeypatch.setenv("MAVERICK_STRICT_TENANT_ISOLATION", "off")
    assert postgres._rls_enabled() is False
    assert postgres._strict_tenant_isolation() is False
    assert postgres._rls_explicitly_set() is True


def test_list_goals_filters_by_domain_and_project_id(monkeypatch):
    # W1-49: SQLite's list_goals accepts domain=/project_id= (used by the
    # dashboard's project-detail page and domain-scoped inbox); the Postgres
    # port didn't, so both callers 500'd with a TypeError on this backend.
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = RecordingCursor()
    cur.fetchall = list

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    world.list_goals(domain="finance_sox", project_id=7, order="desc")

    assert len(cur.calls) == 1
    sql, params = cur.calls[0]
    assert "domain = %s" in sql
    assert "project_id = %s" in sql
    assert params == ("finance_sox", 7)


def test_list_goals_omits_domain_and_project_id_predicates_when_unset(monkeypatch):
    # No false positives: a plain call must not filter on either column.
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    cur = RecordingCursor()
    cur.fetchall = list

    @contextmanager
    def tx():
        yield cur

    monkeypatch.setattr(world, "_tx", tx)

    world.list_goals()

    sql, params = cur.calls[0]
    assert "domain, project_id FROM goals" in sql
    assert "domain = %s" not in sql
    assert "project_id = %s" not in sql
    assert params == ()


def test_explicit_env_on_is_explicit(_clean_toggle_env, monkeypatch):
    monkeypatch.setenv("MAVERICK_PG_RLS", "1")
    assert postgres._rls_enabled() is True
    assert postgres._rls_explicitly_set() is True


def test_config_toggle_resolves_when_env_absent(_clean_toggle_env, monkeypatch):
    monkeypatch.setattr(
        "maverick.config.load_config",
        lambda *a, **k: {"world_model": {"rls": True, "strict_tenant_isolation": "yes"}},
    )
    assert postgres._rls_enabled() is True
    assert postgres._strict_tenant_isolation() is True
    assert postgres._rls_explicitly_set() is True


def test_preflight_refuses_boot_on_legacy_null_rows_when_auto_on(monkeypatch):
    # Enterprise auto-on (not explicit) + a table with NULL-tenant rows -> refuse.
    monkeypatch.setattr(postgres, "_rls_explicitly_set", lambda: False)
    monkeypatch.setattr(
        "maverick.world_model_backends.pg_rls.preflight",
        lambda conn: {"role": "app", "ready": False, "tables": {
            "goals": {"null_tenant_rows": 3, "owned_by_current_role": True},
            "facts": {"null_tenant_rows": 0, "owned_by_current_role": True},
        }},
    )
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._pool = None
    world.conn = object()
    with pytest.raises(RuntimeError, match="tenant backfill"):
        world._preflight_rls_or_die()


def test_preflight_passes_when_no_null_rows(monkeypatch):
    monkeypatch.setattr(postgres, "_rls_explicitly_set", lambda: False)
    monkeypatch.setattr(
        "maverick.world_model_backends.pg_rls.preflight",
        lambda conn: {"role": "app", "ready": True, "tables": {
            "goals": {"null_tenant_rows": 0, "owned_by_current_role": True},
        }},
    )
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._pool = None
    world.conn = object()
    world._preflight_rls_or_die()  # no raise


def test_preflight_skipped_when_explicitly_opted_in(monkeypatch):
    # Explicit MAVERICK_PG_RLS=1 keeps the old fail-closed path: no boot refusal
    # even with NULL rows present (operator knowingly opted in).
    monkeypatch.setattr(postgres, "_rls_explicitly_set", lambda: True)
    called = {"preflight": False}

    def _should_not_run(conn):  # pragma: no cover -- asserted not called
        called["preflight"] = True
        return {"tables": {}}

    monkeypatch.setattr(
        "maverick.world_model_backends.pg_rls.preflight", _should_not_run)
    world = PostgresWorldModel.__new__(PostgresWorldModel)
    world._pool = None
    world.conn = object()
    world._preflight_rls_or_die()
    assert called["preflight"] is False


@pytest.mark.skipif(not _HAS_PSYCOPG, reason="psycopg not installed")
class TestReconnectOnBrokenConnection:
    """W1-54: the single shared (non-pooled) connection must self-heal after
    a managed-Postgres failover or idle-connection reset instead of raising
    OperationalError on every call until the process restarts."""

    class _FakeCursor:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=None):
            self._conn.statements.append((sql, params))

        def close(self):
            pass

    class _FakeConnection:
        def __init__(self, *, breaks_on_cursor=False):
            self.closed = 0
            self._breaks_on_cursor = breaks_on_cursor
            self.committed = False
            self.rolled_back = False
            self.statements = []

        def cursor(self):
            if self._breaks_on_cursor:
                import psycopg
                raise psycopg.OperationalError(
                    "server closed the connection unexpectedly")
            return TestReconnectOnBrokenConnection._FakeCursor(self)

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = 1

    def _world(self):
        world = PostgresWorldModel.__new__(PostgresWorldModel)
        world._pool = None
        world._rls = False
        world._lock = threading.RLock()
        world._dsn = "postgresql://fake"
        world._connect_kwargs = {"autocommit": False}
        world._io_timeout_seconds = 0.25
        return world

    def test_already_closed_connection_is_reopened_before_use(self, monkeypatch):
        world = self._world()
        world._rls = True
        monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
        dead = self._FakeConnection()
        dead.closed = 1  # e.g. explicitly closed by a prior broken _tx()
        world.conn = dead
        fresh = self._FakeConnection()
        connect_calls = []

        def _fake_connect(dsn, *, io_timeout_seconds, autocommit=False):
            connect_calls.append((dsn, autocommit, io_timeout_seconds))
            return fresh

        from maverick.postgres_io import BoundedConnection
        monkeypatch.setattr(BoundedConnection, "connect", _fake_connect)

        with world._tx() as cur:
            cur.execute("SELECT 1")

        assert connect_calls == [("postgresql://fake", False, 0.25)]
        assert world.conn is fresh
        assert fresh.committed is True
        assert fresh.statements == [
            ("SELECT set_config('maverick.tenant', %s, true)", ("acme",)),
            ("SELECT 1", None),
        ]

    def test_connection_broken_mid_transaction_is_closed_not_left_dangling(
        self, monkeypatch,
    ):
        world = self._world()
        dying = self._FakeConnection(breaks_on_cursor=True)
        world.conn = dying
        import psycopg

        with pytest.raises(psycopg.OperationalError):
            with world._tx():
                pass  # never reached -- cursor() raises first

        assert dying.closed == 1  # explicitly closed, not left half-dead

    def test_next_tx_reconnects_after_a_broken_transaction(self, monkeypatch):
        world = self._world()
        world._rls = True
        monkeypatch.setattr(postgres, "_active_tenant", lambda: "acme")
        dying = self._FakeConnection(breaks_on_cursor=True)
        world.conn = dying
        import psycopg

        with pytest.raises(psycopg.OperationalError):
            with world._tx():
                pass

        fresh = self._FakeConnection()
        connect_calls = []

        def _fake_connect(dsn, *, io_timeout_seconds, autocommit=False):
            connect_calls.append((dsn, autocommit, io_timeout_seconds))
            return fresh

        from maverick.postgres_io import BoundedConnection
        monkeypatch.setattr(BoundedConnection, "connect", _fake_connect)

        with world._tx() as cur:
            cur.execute("SELECT 1")

        assert world.conn is fresh
        assert fresh.committed is True
        assert connect_calls == [("postgresql://fake", False, 0.25)]
        assert fresh.statements == [
            ("SELECT set_config('maverick.tenant', %s, true)", ("acme",)),
            ("SELECT 1", None),
        ]
