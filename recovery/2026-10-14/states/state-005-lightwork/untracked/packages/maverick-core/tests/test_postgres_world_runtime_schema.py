"""Least-privilege Postgres world-model startup admission.

These catalog doubles keep the runtime path testable without a live Postgres
service.  The live RLS test exercises the same path with a real non-owner role
when ``MAVERICK_PG_DSN`` is available.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from maverick.world_model_backends import postgres as pg


@dataclass
class _Result:
    rows: list[tuple]


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection
        self._current = _Result([])
        self.rowcount = -1

    def execute(self, query, params=()) -> None:
        if not self._connection.results:
            raise AssertionError(f"unexpected catalog query: {query}")
        self._connection.statements.append((str(query), tuple(params)))
        self._current = self._connection.results.pop(0)

    def fetchone(self):
        return self._current.rows[0] if self._current.rows else None

    def fetchall(self):
        return list(self._current.rows)

    def close(self) -> None:
        return None


class _Connection:
    def __init__(self, results: list[_Result]) -> None:
        self.results = list(results)
        self.statements: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _schema_role_row(*, can_create: bool = False) -> tuple:
    return ("public", False, False, can_create)


def _runtime_schema_row() -> tuple:
    return ("public", False, False, True, False)


def _ledger_access_row(*, can_insert: bool = False) -> tuple:
    privileges = (True, can_insert, False, False, False, False, False)
    return (
        "maverick_owner",
        False,
        False,
        "r",
        "p",
        True,
        True,
        True,
        True,
        True,
        True,
        *privileges,
        *(False for _unused in pg._SCHEMA_TABLE_PRIVILEGES),
    )


def _primary_row() -> tuple:
    return (
        "schema_migrations_pkey",
        "PRIMARY KEY (version)",
        True,
        True,
        False,
        False,
        ("version",),
        1,
        1,
        True,
        True,
        True,
        True,
        "i",
        "p",
        "maverick_owner",
    )


def _runtime_results(
    status: str = "ready",
    *,
    malformed_primary: bool = False,
) -> list[_Result]:
    results = [
        _Result([_schema_role_row()]),
        _Result([_runtime_schema_row()]),
    ]
    if status == "missing":
        return [*results, _Result([(None,)])]
    results.extend(
        [
            _Result([("schema_migrations",)]),
            _Result([_ledger_access_row(can_insert=status == "incompatible")]),
        ]
    )
    if status == "incompatible":
        return results
    versions = [(version, 1.0) for version, _statements in pg.MIGRATIONS]
    if status == "stale":
        versions = versions[:-1]
    elif status == "future":
        versions.append((pg._PG_SCHEMA_VERSION + 1, 1.0))
    primary = _primary_row()
    if malformed_primary:
        primary = (*primary[:7], "not-an-integer", *primary[8:])
    results.extend(
        [
            _Result([]),  # no PUBLIC table ACL
            _Result([]),  # no column ACL
            _Result(list(pg._SCHEMA_COLUMNS)),
            _Result([primary]),
            _Result([]),  # PG14-17 have no NOT NULL constraint rows
            _Result([]),  # no extra constraints
            _Result([]),  # no extra indexes
            _Result([]),  # no policies
            _Result(versions),
        ]
    )
    return results


def _open_with_catalog(monkeypatch, results: list[_Result]):
    connection = _Connection(results)
    fake_psycopg = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setattr(pg, "_pool_size", lambda: 0)
    monkeypatch.setattr(pg, "_rls_enabled", lambda: False)
    return connection, pg.PostgresWorldModel("postgres://runtime")


def _assert_no_mutation(connection: _Connection) -> None:
    mutation = re.compile(
        r"^\s*(?:CREATE|ALTER|DROP|INSERT|UPDATE|DELETE|TRUNCATE|GRANT|REVOKE)\b",
        re.IGNORECASE,
    )
    assert connection.statements
    assert not [query for query, _params in connection.statements if mutation.search(query)]


def test_runtime_open_after_v38_is_read_only(monkeypatch):
    connection, world = _open_with_catalog(monkeypatch, _runtime_results())
    try:
        assert world.schema_version == 38
        assert connection.results == []
        _assert_no_mutation(connection)
    finally:
        world.close()
    assert connection.closed is True


@pytest.mark.parametrize(
    ("status", "message"),
    [
            ("missing", "migration v38 is required"),
            ("stale", "migration v38 is required"),
        ("incompatible", "migration-ledger authority is incompatible"),
        ("future", "migration-ledger authority is incompatible"),
    ],
)
def test_runtime_open_fails_closed_without_ddl(monkeypatch, status, message):
    connection = _Connection(_runtime_results(status))
    fake_psycopg = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setattr(pg, "_pool_size", lambda: 0)
    monkeypatch.setattr(pg, "_rls_enabled", lambda: False)

    with pytest.raises(RuntimeError, match=message):
        pg.PostgresWorldModel("postgres://runtime")

    _assert_no_mutation(connection)


def test_malformed_primary_catalog_is_incompatible_not_uncaught(monkeypatch):
    connection = _Connection(_runtime_results(malformed_primary=True))
    fake_psycopg = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setattr(pg, "_pool_size", lambda: 0)
    monkeypatch.setattr(pg, "_rls_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="migration-ledger authority is incompatible"):
        pg.PostgresWorldModel("postgres://runtime")

    _assert_no_mutation(connection)


def test_pg18_not_null_constraint_rows_are_semantically_checked():
    valid = (
        "schema_migrations_applied_at_not_null",
        ("applied_at",),
        True,
        True,
        False,
        False,
        False,
    )
    assert pg._schema_not_null_constraints_are_safe([valid])
    assert not pg._schema_not_null_constraints_are_safe(
        [valid, ("duplicate", *valid[1:])]
    )
    assert not pg._schema_not_null_constraints_are_safe(
        [(valid[0], ("unknown",), *valid[2:])]
    )


def test_schema_create_without_owner_authority_is_refused(monkeypatch):
    connection = _Connection([_Result([_schema_role_row(can_create=True)])])
    fake_psycopg = SimpleNamespace(connect=lambda *_args, **_kwargs: connection)
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setattr(pg, "_pool_size", lambda: 0)
    monkeypatch.setattr(pg, "_rls_enabled", lambda: False)

    with pytest.raises(RuntimeError, match="reserved for the migration owner"):
        pg.PostgresWorldModel("postgres://runtime")

    _assert_no_mutation(connection)


def test_owner_capability_preserves_migration_bootstrap(monkeypatch):
    world = object.__new__(pg.PostgresWorldModel)
    calls: list[str] = []
    monkeypatch.setattr(world, "_schema_migration_capable", lambda: True)
    monkeypatch.setattr(world, "_migrate", lambda: calls.append("migrate"))
    monkeypatch.setattr(
        world,
        "_verify_runtime_schema",
        lambda: pytest.fail("owner path must not use runtime admission"),
    )

    world._prepare_schema()

    assert calls == ["migrate"]
