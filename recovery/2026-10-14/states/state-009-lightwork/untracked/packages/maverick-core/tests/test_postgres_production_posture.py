"""Fail-closed production Postgres TLS, timeout, and RLS-role posture."""
from __future__ import annotations

import pytest
from maverick.world_model_backends import postgres


class _Cursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        results, self.results = self.results, []
        return results


class _Connection:
    def __init__(self, results):
        self.cursor_value = _Cursor(results)

    def cursor(self):
        return self.cursor_value


class _Transaction:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *_args):
        return False


class _InventoryCursor:
    def __init__(self, values):
        self.values = dict(values)
        self.current = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=None):
        key = next(
            (
                (table, column)
                for table, column in postgres._SEALED_TEXT_COLUMNS
                if f"FROM {table} " in sql and f"SELECT {column} " in sql
            ),
            None,
        )
        self.current = list(self.values.get(key, []))

    def fetchmany(self, _size):
        if not self.current:
            return []
        values, self.current = self.current[:500], self.current[500:]
        return [(value,) for value in values]


class _InventoryConnection:
    def __init__(self, values):
        self.values = values

    def cursor(self):
        return _InventoryCursor(self.values)


def test_production_dsn_requires_verify_full_and_ca_bundle(monkeypatch):
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.delenv("PGSSLROOTCERT", raising=False)

    with pytest.raises(RuntimeError, match="sslmode=verify-full"):
        postgres._validate_production_dsn(
            "host=db.internal dbname=maverick sslmode=require"
        )
    with pytest.raises(RuntimeError, match="sslrootcert"):
        postgres._validate_production_dsn(
            "host=db.internal dbname=maverick sslmode=verify-full"
        )

    postgres._validate_production_dsn(
        "host=db.internal dbname=maverick sslmode=verify-full sslrootcert=system"
    )


def test_postgres_dsn_resolves_from_required_file_provider(monkeypatch, tmp_path):
    from maverick.file_lock import atomic_write_text, prepare_private_directory

    secret_dir = tmp_path / "secrets"
    prepare_private_directory(secret_dir)
    dsn = (
        "host=db.internal dbname=maverick sslmode=verify-full "
        "sslrootcert=system"
    )
    atomic_write_text(secret_dir / "MAVERICK_PG_DSN", dsn)
    monkeypatch.setenv("MAVERICK_SECRETS_REQUIRED", "1")
    monkeypatch.setenv("MAVERICK_SECRETS_BACKEND", "file")
    monkeypatch.setenv("MAVERICK_SECRETS_DIR", str(secret_dir))
    monkeypatch.setenv("MAVERICK_PG_DSN", "environment-must-not-win")

    assert postgres._resolve_dsn() == dsn


def test_required_provider_missing_postgres_dsn_fails_closed(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_SECRETS_REQUIRED", "1")
    monkeypatch.setenv("MAVERICK_SECRETS_BACKEND", "file")
    monkeypatch.setenv("MAVERICK_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("MAVERICK_PG_DSN", "environment-must-not-win")

    with pytest.raises(RuntimeError, match="required secret"):
        postgres._resolve_dsn()


def test_production_explicit_dsn_cannot_bypass_required_provider(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(postgres, "_production_surface_required", lambda: True)
    monkeypatch.setenv("MAVERICK_SECRETS_REQUIRED", "1")
    monkeypatch.setenv("MAVERICK_SECRETS_BACKEND", "file")
    monkeypatch.setenv("MAVERICK_SECRETS_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="required secret"):
        postgres._resolve_dsn(
            "host=ambient.example dbname=maverick sslmode=verify-full "
            "sslrootcert=system"
        )


def test_production_dsn_rejects_unix_socket_and_missing_ca_file(tmp_path):
    with pytest.raises(RuntimeError, match="TCP hostname"):
        postgres._validate_production_dsn(
            "host=/var/run/postgresql dbname=maverick sslmode=verify-full "
            "sslrootcert=system"
        )
    missing = tmp_path / "missing.pem"
    with pytest.raises(RuntimeError, match="sslrootcert is unavailable"):
        postgres._validate_production_dsn(
            f"host=db.internal dbname=maverick sslmode=verify-full "
            f"sslrootcert='{missing}'"
        )


@pytest.mark.parametrize("role", [(True, False), (False, True)])
def test_live_runtime_role_cannot_bypass_rls(role):
    conn = _Connection([(True,), role])

    with pytest.raises(RuntimeError, match="NOSUPERUSER NOBYPASSRLS"):
        postgres._verify_production_connection(conn)


def test_live_connection_requires_tls():
    conn = _Connection([(False,)])

    with pytest.raises(RuntimeError, match="not using TLS"):
        postgres._verify_production_connection(conn)


def test_live_connection_accepts_tls_least_privilege_role():
    conn = _Connection([(True,), (False, False)])

    postgres._verify_production_connection(conn)

    assert "pg_stat_ssl" in conn.cursor_value.executed[0][0]
    assert "rolbypassrls" in conn.cursor_value.executed[1][0]


def test_runtime_role_must_not_own_tenant_tables():
    with pytest.raises(RuntimeError, match="separate migration owner"):
        postgres._verify_production_runtime_ownership(_Connection([("goals",)]))

    postgres._verify_production_runtime_ownership(_Connection([None]))


def test_production_runtime_requires_exact_preapplied_migration_ladder(monkeypatch):
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)
    expected = [(version,) for version, _statements in postgres.MIGRATIONS]
    cursor = _Cursor(expected)
    world._tx = lambda: _Transaction(cursor)
    verified = []
    monkeypatch.setattr(
        postgres,
        "_verify_production_catalog",
        lambda cur: verified.append(cur) or "a" * 64,
    )

    world._verify_production_schema_current()

    assert cursor.executed == [
        (
            "SELECT pg_advisory_xact_lock(%s)",
            (postgres._MIGRATION_ADVISORY_LOCK,),
        ),
        ("SELECT version FROM schema_migrations ORDER BY version", None)
    ]
    assert verified == [cursor]


def test_production_runtime_refuses_live_catalog_drift(monkeypatch):
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)
    expected = [(version,) for version, _statements in postgres.MIGRATIONS]
    cursor = _Cursor(expected)
    world._tx = lambda: _Transaction(cursor)
    monkeypatch.setattr(
        postgres,
        "_verify_production_catalog",
        lambda _cur: (_ for _ in ()).throw(RuntimeError("live catalog drift")),
    )

    with pytest.raises(RuntimeError, match="live catalog drift"):
        world._verify_production_schema_current()


def test_production_runtime_refuses_missing_preapplied_migration():
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)
    applied = [
        (version,)
        for version, _statements in postgres.MIGRATIONS
        if version != postgres._PG_SCHEMA_VERSION
    ]
    world._tx = lambda: _Transaction(_Cursor(applied))

    with pytest.raises(
        RuntimeError, match=rf"missing=.*{postgres._PG_SCHEMA_VERSION}"
    ):
        world._verify_production_schema_current()


def test_production_runtime_refuses_a_gapped_preapplied_migration_ladder():
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)
    applied = [
        (version,)
        for version, _statements in postgres.MIGRATIONS
        if version != postgres.MIGRATIONS[1][0]
    ]
    world._tx = lambda: _Transaction(_Cursor(applied))

    with pytest.raises(RuntimeError, match="exact ordered reviewed prefix"):
        world._verify_production_schema_current()


def test_production_inventory_rejects_one_legacy_plaintext_value():
    with pytest.raises(RuntimeError, match="unsealed data in goals.title"):
        postgres._verify_production_encryption_inventory(
            _InventoryConnection({("goals", "title"): ["legacy plaintext"]})
        )


def test_production_inventory_accepts_authenticated_ciphertext():
    from maverick.crypto_at_rest import seal_to_str

    postgres._verify_production_encryption_inventory(
        _InventoryConnection({("goals", "title"): [seal_to_str("secret")]})
    )


def test_flow_authority_document_is_in_production_encryption_inventory():
    assert ("flow_authority_records", "document") in postgres._SEALED_TEXT_COLUMNS

    with pytest.raises(
        RuntimeError,
        match="unsealed data in flow_authority_records.document",
    ):
        postgres._verify_production_encryption_inventory(
            _InventoryConnection(
                {("flow_authority_records", "document"): ['{"status":"running"}']}
            )
        )


@pytest.mark.parametrize(
    ("disabled", "message"),
    [
        ("at_rest_enabled", "at-rest encryption"),
        ("per_tenant_at_rest", "per-tenant envelope"),
        ("strict_at_rest", "strict encrypted-field"),
    ],
)
def test_production_encryption_policy_is_strict(monkeypatch, disabled, message):
    import maverick.crypto_at_rest as crypto

    for name in ("at_rest_enabled", "per_tenant_at_rest", "strict_at_rest"):
        monkeypatch.setattr(crypto, name, lambda: True)
    monkeypatch.setattr(crypto, disabled, lambda: False)

    with pytest.raises(RuntimeError, match=message):
        postgres._verify_production_encryption_policy()


@pytest.mark.parametrize("value", ["0", "51", "invalid"])
def test_production_pool_size_is_bounded(monkeypatch, value):
    monkeypatch.setattr(
        postgres, "_production_surface_required", lambda: True
    )
    monkeypatch.setenv("MAVERICK_PG_POOL_SIZE", value)

    with pytest.raises(RuntimeError, match="MAVERICK_PG_POOL_SIZE"):
        postgres._pool_size()


def test_production_timeout_defaults_and_invalid_override(monkeypatch):
    monkeypatch.setattr(
        postgres, "_production_surface_required", lambda: True
    )
    for name in (
        "MAVERICK_PG_CONNECT_TIMEOUT_SECONDS",
        "MAVERICK_PG_STATEMENT_TIMEOUT_MS",
        "MAVERICK_PG_LOCK_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert postgres._connect_timeout_seconds() == 10
    assert postgres._statement_timeout_ms() == 30_000
    assert postgres._lock_timeout_ms() == 5_000

    monkeypatch.setenv("MAVERICK_PG_STATEMENT_TIMEOUT_MS", "0")
    with pytest.raises(RuntimeError, match="between 100"):
        postgres._statement_timeout_ms()


def test_every_transaction_gets_statement_and_lock_timeouts():
    world = postgres.PostgresWorldModel.__new__(postgres.PostgresWorldModel)
    world._statement_timeout = 30_000
    world._lock_timeout = 5_000
    cursor = _Cursor([])

    world._set_transaction_limits(cursor)

    assert cursor.executed == [
        (
            "SELECT set_config('statement_timeout', %s, true)",
            ("30000ms",),
        ),
        ("SELECT set_config('lock_timeout', %s, true)", ("5000ms",)),
    ]


def test_nonproduction_keeps_legacy_unbounded_defaults(monkeypatch):
    monkeypatch.setattr(
        postgres, "_production_surface_required", lambda: False
    )
    monkeypatch.delenv("MAVERICK_PG_POOL_SIZE", raising=False)
    monkeypatch.delenv("MAVERICK_PG_CONNECT_TIMEOUT_SECONDS", raising=False)

    assert postgres._pool_size() == 0
    assert postgres._connect_timeout_seconds() == 0
