"""Shared effect-ledger invariants without requiring a live Postgres service."""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from maverick import effect_protocol as effects
from maverick.effect_protocol import (
    ClaimOutcome,
    CompensationState,
    CompensationUnavailable,
    EffectIdentity,
    EffectProtocolError,
    EffectSpec,
    EffectState,
    EffectStore,
    InvalidTransition,
    PostgresEffectStore,
    ProviderCompensationStatus,
    ProviderReceipt,
    compensate_effect,
    reconcile_compensation,
)


def _identity(*, tenant: str = "tenant-a") -> EffectIdentity:
    return EffectIdentity(
        tenant=tenant,
        principal="user:alice",
        run_id="run-1",
        node_id="node-1",
        connector="acme",
        operation="post",
    )


def _digest(value: str = "request") -> str:
    return hashlib.sha256(value.encode()).hexdigest()


_FAKE_SCHEMA = """
CREATE TABLE effect_transactions (
    logical_key TEXT NOT NULL,
    tenant TEXT NOT NULL,
    principal TEXT NOT NULL,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    connector TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    claim_token_hash TEXT NOT NULL,
    compensation_claim_hash TEXT,
    compensation_state TEXT NOT NULL DEFAULT 'NOT_STARTED',
    compensation_result TEXT NOT NULL DEFAULT '',
    compensation_result_digest TEXT NOT NULL DEFAULT '',
    compensation_receipt_json TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    result_digest TEXT NOT NULL DEFAULT '',
    receipt_json TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (tenant, logical_key)
)
"""


class _Rows:
    def __init__(self, rows: list[object] | None = None):
        self._rows = list(rows or [])
        self.rowcount = -1

    def fetchone(self) -> object | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[object]:
        return list(self._rows)


class _FakePgConnection:
    """Translate the narrow psycopg SQL subset onto a shared SQLite file."""

    def __init__(
        self,
        path: Path,
        calls: list[str],
        calls_lock: threading.Lock,
        *,
        authority_clock=None,
        lose_commit: list[bool] | None = None,
    ):
        self._connection = sqlite3.connect(
            str(path),
            isolation_level=None,
            timeout=10.0,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._calls = calls
        self._calls_lock = calls_lock
        self._authority_clock = authority_clock or effects.time.time
        self._lose_commit = lose_commit

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> Any:
        with self._calls_lock:
            self._calls.append(" ".join(sql.split()))
        normalized = " ".join(sql.strip().upper().split())
        if normalized == "BEGIN":
            return self._connection.execute("BEGIN IMMEDIATE")
        if "FROM PG_ROLES" in normalized:
            return _Rows([{"rolsuper": False, "rolbypassrls": False}])
        if "FROM INFORMATION_SCHEMA.COLUMNS" in normalized:
            return _Rows([
                {
                    "column_name": name,
                    "data_type": shape[0],
                    "is_nullable": shape[1],
                }
                for name, shape in effects._POSTGRES_EFFECT_COLUMN_SHAPES.items()
            ])
        if "FROM PG_INDEX" in normalized:
            return _Rows([
                {"column_name": "tenant"},
                {"column_name": "logical_key"},
            ])
        if "FROM PG_CONSTRAINT" in normalized:
            return _Rows([
                {
                    "conname": name,
                    "expression": expression,
                    "convalidated": True,
                }
                for name, expression in effects._POSTGRES_EFFECT_CHECKS.items()
            ])
        if "RELROWSECURITY" in normalized:
            return _Rows([{
                "relrowsecurity": True,
                "relforcerowsecurity": True,
            }])
        if "FROM PG_POLICIES" in normalized:
            return _Rows([dict(_GOOD_POLICY)])
        if "SET_CONFIG('MAVERICK.TENANT'" in normalized:
            return _Rows([(params[0],)])
        if "CLOCK_TIMESTAMP()" in normalized:
            return _Rows([{"authority_time": self._authority_clock()}])
        translated = sql.replace("%s", "?").replace(" FOR UPDATE", "")
        return self._connection.execute(translated, params)

    def commit(self) -> None:
        self._connection.commit()
        if self._lose_commit and self._lose_commit[0]:
            self._lose_commit[0] = False
            raise OSError("response lost after committed transaction")

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


@pytest.fixture
def fake_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "1")
    monkeypatch.setenv("MAVERICK_ENCRYPTION_KEY", "42" * 32)
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    db_path = tmp_path / "fake-postgres.sqlite3"
    with sqlite3.connect(str(db_path)) as connection:
        connection.execute(_FAKE_SCHEMA)
    calls: list[str] = []
    calls_lock = threading.Lock()

    def factory() -> _FakePgConnection:
        return _FakePgConnection(db_path, calls, calls_lock)

    return db_path, calls, factory


def _store(factory) -> PostgresEffectStore:
    return PostgresEffectStore(
        require_shared=True,
        _connection_factory=factory,
        _initialize_schema=False,
    )


def test_shared_claim_race_has_one_owner_and_one_deterministic_loser(
    fake_postgres,
):
    _path, calls, factory = fake_postgres
    stores = (_store(factory), _store(factory))
    barrier = threading.Barrier(2)

    def claim(store: PostgresEffectStore) -> ClaimOutcome:
        barrier.wait(timeout=5)
        return store.claim(_identity(), _digest()).outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, stores))

    assert sorted(outcomes, key=lambda item: item.value) == sorted(
        [ClaimOutcome.OWNER, ClaimOutcome.IN_PROGRESS],
        key=lambda item: item.value,
    )
    assert any(
        "ON CONFLICT (tenant, logical_key) DO NOTHING RETURNING *" in sql
        for sql in calls
    )
    assert _store(factory).claim(_identity(), _digest("changed")).outcome is (
        ClaimOutcome.CONFLICT
    )


def test_shared_prepared_claim_recovery_uses_db_time_and_fences_old_owner(
    fake_postgres,
    monkeypatch,
):
    db_path, calls, _factory = fake_postgres
    authority_time = [1_000.0]
    calls_lock = threading.Lock()

    def factory():
        return _FakePgConnection(
            db_path,
            calls,
            calls_lock,
            authority_clock=lambda: authority_time[0],
        )

    first_store = _store(factory)
    first = first_store.claim(_identity(), _digest())
    assert first.outcome is ClaimOutcome.OWNER

    # A hostile node clock cannot expire the PREPARED owner early.
    monkeypatch.setattr(effects.time, "time", lambda: 9_999_999_999.0)
    authority_time[0] = 1_299.0
    assert _store(factory).claim(_identity(), _digest()).outcome is (
        ClaimOutcome.IN_PROGRESS
    )

    # Once the database lease reaches its bound, the stable effect identity and
    # digest recover ownership with a new token that fences the lost owner.
    authority_time[0] = 1_300.0
    recovered_store = _store(factory)
    recovered = recovered_store.claim(_identity(), _digest())
    assert recovered.outcome is ClaimOutcome.OWNER
    assert recovered.claim_token != first.claim_token
    with pytest.raises(InvalidTransition):
        first_store.mark_sent(first)
    assert recovered_store.mark_sent(recovered).state is EffectState.SENT
    assert any("clock_timestamp()" in sql for sql in calls)


def test_shared_prepared_claim_converges_after_commit_response_loss(
    fake_postgres,
):
    db_path, calls, _factory = fake_postgres
    authority_time = [4_000.0]
    lose_commit = [True]
    calls_lock = threading.Lock()

    def factory():
        return _FakePgConnection(
            db_path,
            calls,
            calls_lock,
            authority_clock=lambda: authority_time[0],
            lose_commit=lose_commit,
        )

    with pytest.raises(EffectProtocolError, match="transaction failed"):
        _store(factory).claim(_identity(), _digest())

    authority_time[0] = 4_299.0
    assert _store(factory).claim(_identity(), _digest()).outcome is (
        ClaimOutcome.IN_PROGRESS
    )
    authority_time[0] = 4_300.0
    recovered_store = _store(factory)
    recovered = recovered_store.claim(_identity(), _digest())
    assert recovered.outcome is ClaimOutcome.OWNER
    assert recovered_store.mark_sent(recovered).state is EffectState.SENT


def test_shared_lifecycle_encrypts_summaries_and_receipts(fake_postgres):
    db_path, calls, factory = fake_postgres
    store = _store(factory)
    claim = store.claim(_identity(), _digest())
    store.mark_sent(claim)
    record = store.commit(
        claim,
        "provider body with customer data",
        ProviderReceipt(
            provider_id="remote-1",
            status="created",
            metadata={"authorization": "Bearer must-not-persist"},
        ),
    )

    assert record.state is EffectState.COMMITTED
    assert record.result == "committed result content withheld; bytes=32"
    assert record.receipt and record.receipt.provider_id == "remote-1"
    with sqlite3.connect(str(db_path)) as connection:
        raw = connection.execute(
            "SELECT result, receipt_json FROM effect_transactions "
            "WHERE tenant = ?",
            ("tenant-a",),
        ).fetchone()
    assert raw is not None
    assert raw[0].startswith("MVKAR1:")
    assert raw[1].startswith("MVKAR1:")
    assert "customer data" not in " ".join(raw)
    assert "must-not-persist" not in " ".join(raw)

    operational = [
        sql for sql in calls
        if sql.startswith("SELECT * FROM effect_transactions")
        or sql.startswith("UPDATE effect_transactions")
    ]
    assert operational
    assert all("tenant = %s" in sql for sql in operational)


def test_delayed_compensator_survives_fast_reconciler_not_started_status(
    fake_postgres,
    monkeypatch,
):
    db_path, calls, _factory = fake_postgres
    authority_time = [2_000.0]
    calls_lock = threading.Lock()

    def factory():
        return _FakePgConnection(
            db_path,
            calls,
            calls_lock,
            authority_clock=lambda: authority_time[0],
        )

    owner_store = _store(factory)
    reconciler_store = _store(factory)
    claim = owner_store.claim(_identity(), _digest())
    owner_store.mark_sent(claim)
    owner_store.commit(
        claim,
        "created",
        ProviderReceipt(provider_id="create-1", status="created"),
    )

    started = threading.Event()
    release = threading.Event()
    compensator_calls: list[str] = []
    resolver_calls: list[CompensationState] = []

    def delayed_compensator(record):
        compensator_calls.append(record.logical_key)
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test compensator was not released")
        return ProviderReceipt(provider_id="undo-1", status="reverted")

    def negative_resolver(record):
        resolver_calls.append(record.compensation_state)
        return ProviderCompensationStatus(
            CompensationState.NOT_STARTED,
            result="provider lookup has not observed the in-flight undo",
        )

    spec = EffectSpec(
        connector="acme",
        compensator=delayed_compensator,
        compensation_status_resolver=negative_resolver,
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(compensate_effect, owner_store, _identity(), spec)
        try:
            assert started.wait(timeout=5)
            authority_time[0] = 2_301.0
            monkeypatch.setattr(effects.time, "time", lambda: 99_999_999_999.0)
            with pytest.raises(CompensationUnavailable, match="not terminal proof"):
                reconcile_compensation(
                    reconciler_store,
                    _identity(),
                    spec,
                    stale_after_seconds=300.0,
                )
            still_owned = reconciler_store.get(_identity())
            assert still_owned is not None
            assert still_owned.compensation_state is CompensationState.COMPENSATING
        finally:
            release.set()
        compensated = pending.result(timeout=5)

    assert compensated.compensation_state is CompensationState.COMPENSATED
    assert compensator_calls == [_identity().logical_key]
    assert resolver_calls == [CompensationState.COMPENSATING]
    with pytest.raises(CompensationUnavailable):
        compensate_effect(reconciler_store, _identity(), spec)
    assert compensator_calls == [_identity().logical_key]


def test_strict_store_rejects_cross_tenant_identity_before_connect(
    fake_postgres,
):
    _path, _calls, factory = fake_postgres
    opened = 0

    def counted_factory():
        nonlocal opened
        opened += 1
        return factory()

    store = _store(counted_factory)
    with pytest.raises(EffectProtocolError, match="does not match"):
        store.claim(_identity(tenant="tenant-b"), _digest())
    assert opened == 0


def test_strict_store_rejects_unbound_or_corrupt_tenant_resolution(
    fake_postgres,
    monkeypatch,
):
    _path, _calls, factory = fake_postgres
    store = _store(factory)
    monkeypatch.setattr("maverick.paths.current_tenant_id_strict", lambda: None)
    with pytest.raises(EffectProtocolError, match="explicitly bound tenant"):
        store.get(_identity())

    def broken_tenant():
        raise RuntimeError("corrupt binding with secret detail")

    monkeypatch.setattr(
        "maverick.paths.current_tenant_id_strict",
        broken_tenant,
    )
    with pytest.raises(EffectProtocolError, match="resolve the tenant boundary") as exc:
        store.get(_identity())
    assert "secret detail" not in str(exc.value)


def test_unbound_nonstrict_store_rejects_caller_selected_tenant(
    fake_postgres,
    monkeypatch,
):
    _path, _calls, factory = fake_postgres
    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (True, False))
    monkeypatch.setattr("maverick.paths.current_tenant_id_strict", lambda: None)
    store = PostgresEffectStore(
        _connection_factory=factory,
        _initialize_schema=False,
    )
    with pytest.raises(EffectProtocolError, match="only the internal shared tenant"):
        store.get(_identity(tenant="caller-selected"))


def test_postgres_cutover_refuses_nonempty_legacy_ledger(
    fake_postgres,
    tmp_path,
    monkeypatch,
):
    _path, _calls, factory = fake_postgres

    def isolated_data_dir(*parts: str, tenant: str | None = None) -> Path:
        base = tmp_path / "legacy"
        if tenant:
            base = base / tenant
        return base.joinpath(*parts)

    monkeypatch.setattr("maverick.paths.data_dir", isolated_data_dir)
    legacy_path = isolated_data_dir(
        "effects",
        "transactions.sqlite3",
        tenant="tenant-a",
    )
    legacy = EffectStore(legacy_path)
    legacy.claim(_identity(), _digest())
    opened = 0

    def counted_factory():
        nonlocal opened
        opened += 1
        return factory()

    with pytest.raises(EffectProtocolError, match="migrate it before Postgres cutover"):
        _store(counted_factory).claim(_identity(), _digest())
    assert opened == 0


_GOOD_POLICY = {
    "policyname": "mvk_effect_tenant_isolation",
    "permissive": "PERMISSIVE",
    "roles": ["public"],
    "cmd": "ALL",
    "qual": (
        "tenant = nullif(current_setting('maverick.tenant', true), '')"
    ),
    "with_check": (
        "tenant = nullif(current_setting('maverick.tenant', true), '')"
    ),
}


class _SchemaConnection:
    def __init__(
        self,
        *,
        extra_policy: bool = False,
        preexisting_ready: bool = False,
        privileged_role: bool = False,
        weakened_constraint: bool = False,
    ):
        self.calls: list[str] = []
        self.committed = False
        self.rolled_back = False
        self.policy_created = False
        self.extra_policy = extra_policy
        self.preexisting_ready = preexisting_ready
        self.privileged_role = privileged_role
        self.weakened_constraint = weakened_constraint

    def execute(self, sql: str, _params: tuple[object, ...] = ()) -> _Rows:
        normalized = " ".join(sql.split())
        self.calls.append(normalized)
        if normalized.startswith("CREATE POLICY"):
            self.policy_created = True
            return _Rows()
        if "FROM pg_roles" in normalized:
            return _Rows([{
                "rolsuper": self.privileged_role,
                "rolbypassrls": False,
            }])
        if "to_regclass" in normalized:
            return _Rows([{
                "table_exists": self.preexisting_ready or self.extra_policy,
            }])
        if "FROM information_schema.columns" in normalized:
            return _Rows([
                {
                    "column_name": name,
                    "data_type": shape[0],
                    "is_nullable": shape[1],
                }
                for name, shape in effects._POSTGRES_EFFECT_COLUMN_SHAPES.items()
            ])
        if "FROM pg_index" in normalized:
            return _Rows([
                {"column_name": "tenant"},
                {"column_name": "logical_key"},
            ])
        if "FROM pg_constraint" in normalized:
            rows = [
                {
                    "conname": name,
                    "expression": expression,
                    "convalidated": True,
                }
                for name, expression in effects._POSTGRES_EFFECT_CHECKS.items()
            ]
            if self.weakened_constraint:
                rows[0]["expression"] = "true"
            return _Rows(rows)
        if "relrowsecurity" in normalized:
            return _Rows([{
                "relrowsecurity": True,
                "relforcerowsecurity": True,
            }])
        if "FROM pg_policies" in normalized:
            policies: list[object] = []
            if self.policy_created or self.extra_policy or self.preexisting_ready:
                policies.append(dict(_GOOD_POLICY))
            if self.extra_policy:
                policies.append({
                    "policyname": "allow_everything",
                    "permissive": "PERMISSIVE",
                    "roles": ["public"],
                    "cmd": "ALL",
                    "qual": "true",
                    "with_check": "true",
                })
            return _Rows(policies)
        return _Rows()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def test_schema_bootstrap_is_advisory_locked_and_forces_exact_rls_policy():
    connection = _SchemaConnection()
    effects._initialize_postgres_effect_schema(connection)

    joined = "\n".join(connection.calls)
    lock_at = next(
        i for i, sql in enumerate(connection.calls) if "pg_advisory_xact_lock" in sql
    )
    create_at = next(
        i for i, sql in enumerate(connection.calls)
        if sql.startswith("CREATE TABLE IF NOT EXISTS effect_transactions")
    )
    assert lock_at < create_at
    assert "ENABLE ROW LEVEL SECURITY" in joined
    assert "FORCE ROW LEVEL SECURITY" in joined
    assert "CREATE POLICY mvk_effect_tenant_isolation" in joined
    assert connection.committed is True


def test_schema_bootstrap_rejects_extra_permissive_policy():
    connection = _SchemaConnection(extra_policy=True)
    with pytest.raises(EffectProtocolError, match="policy is not fail-closed"):
        effects._initialize_postgres_effect_schema(connection)
    assert connection.rolled_back is True
    assert connection.committed is False


def test_preprovisioned_exact_schema_needs_no_runtime_ddl_privilege():
    connection = _SchemaConnection(preexisting_ready=True)
    effects._initialize_postgres_effect_schema(connection)

    joined = "\n".join(connection.calls)
    assert connection.committed is True
    assert "CREATE TABLE IF NOT EXISTS effect_transactions" not in joined
    assert "ALTER TABLE effect_transactions" not in joined


def test_schema_rejects_a_role_that_can_bypass_forced_rls():
    connection = _SchemaConnection(
        preexisting_ready=True,
        privileged_role=True,
    )

    with pytest.raises(EffectProtocolError, match="non-bypass Postgres role"):
        effects._initialize_postgres_effect_schema(connection)

    assert connection.rolled_back is True
    assert connection.committed is False


def test_preprovisioned_schema_rejects_weakened_authority_constraint():
    connection = _SchemaConnection(
        preexisting_ready=True,
        weakened_constraint=True,
    )

    with pytest.raises(EffectProtocolError, match="constraints are incompatible"):
        effects._initialize_postgres_effect_schema(connection)

    assert connection.rolled_back is True
    assert connection.committed is False


def test_backend_selection_refuses_required_local_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (False, True))
    with pytest.raises(EffectProtocolError, match="node-local effect storage"):
        EffectStore()
    with pytest.raises(EffectProtocolError, match="node-local effect storage"):
        EffectStore(tmp_path / "explicit.sqlite3")

    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (True, False))
    with pytest.raises(EffectProtocolError, match="node-local effect storage"):
        EffectStore(tmp_path / "postgres-selected.sqlite3")

    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (False, False))
    local = EffectStore(tmp_path / "single-host.sqlite3")
    assert local.backend == "sqlite"


def test_postgres_selection_never_falls_back_after_backend_failure(monkeypatch):
    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (True, True))

    def fail_init(self, *args, **kwargs):
        del self, args, kwargs
        raise EffectProtocolError("shared Postgres effect storage is unavailable")

    monkeypatch.setattr(PostgresEffectStore, "__init__", fail_init)
    with pytest.raises(EffectProtocolError, match="Postgres effect storage"):
        EffectStore()


def test_default_store_selects_postgres_backend(monkeypatch):
    monkeypatch.setattr(effects, "_effect_backend_posture", lambda: (True, False))
    monkeypatch.setattr(
        PostgresEffectStore,
        "__init__",
        lambda self, **_kwargs: None,
    )
    assert isinstance(EffectStore(), PostgresEffectStore)


def test_connection_failure_does_not_expose_dsn_or_fallback(
    fake_postgres,
):
    _path, _calls, _factory = fake_postgres
    secret_dsn = "postgresql://admin:must-not-leak@db.internal/lightwork"  # pragma: allowlist secret

    def fail_connection():
        raise ConnectionError(f"could not connect to {secret_dsn}")

    store = _store(fail_connection)
    with pytest.raises(EffectProtocolError, match="storage is unavailable") as exc:
        store.claim(_identity(), _digest())
    assert secret_dsn not in str(exc.value)
    assert "must-not-leak" not in str(exc.value)


def test_replica_declaration_raises_shared_floor(monkeypatch):
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "2")
    monkeypatch.delenv("MAVERICK_WORLD_BACKEND", raising=False)
    monkeypatch.setattr("maverick.config.load_global_config", dict)
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )
    assert effects._effect_backend_posture() == (False, True)


def test_effect_backend_uses_global_environment_precedence(monkeypatch):
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "sqlite")
    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {"world_model": {"backend": "postgres"}},
    )
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )

    assert effects._effect_backend_posture() == (False, False)


def test_governed_records_postgres_selects_shared_effect_backend(monkeypatch):
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "sqlite")
    monkeypatch.setattr(
        "maverick.config.load_global_config",
        lambda: {
            "world_model": {"backend": "sqlite"},
            "governed_records": {"backend": "postgres"},
        },
    )
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )

    assert effects._effect_backend_posture() == (True, False)


def test_invalid_effect_backend_fails_closed_without_selecting_postgres(monkeypatch):
    monkeypatch.setenv("MAVERICK_WORLD_BACKEND", "ambiguous")
    monkeypatch.setattr("maverick.config.load_global_config", dict)
    monkeypatch.setattr(
        "maverick.config.config_source_errors",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        "maverick.enterprise.deployment_enterprise_enabled",
        lambda **_kwargs: False,
    )

    assert effects._effect_backend_posture() == (False, True)
