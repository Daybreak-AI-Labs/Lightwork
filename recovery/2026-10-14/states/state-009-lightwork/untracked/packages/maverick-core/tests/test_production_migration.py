"""Adverse tests for the first-profile production migration runner."""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone

import pytest
from maverick import production_migration as pm

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)
LOCK_SHA = "a" * 64
AUTH_SHA = "b" * 64
IMAGE_DIGEST = "sha256:" + ("c" * 64)
RELEASE_VERSION = "0.1.7"


class _FakeCursor:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn
        self.rows: list[tuple] = []

    def execute(self, sql: str, params=None) -> None:
        self.conn.statements.append((sql, params))
        normalized = " ".join(sql.split()).lower()
        if self.conn.fail_on and self.conn.fail_on in normalized:
            raise RuntimeError("synthetic database failure")
        if normalized == "select current_schema()":
            self.rows = [("public",)]
        elif "from pg_catalog.pg_roles" in normalized:
            self.rows = [(self.conn.role, False, False)]
        elif "from pg_catalog.pg_namespace" in normalized:
            self.rows = [
                (self.conn.schema_owner, self.conn.schema_foreign_create)
            ]
        elif "pg_catalog.obj_description" in normalized:
            self.rows = [(self.conn.authorization_receipt,)]
        elif "to_regclass('public.schema_migrations')" in normalized:
            value = "schema_migrations" if "schema_migrations" in self.conn.tables else None
            self.rows = [(value,)]
        elif normalized.startswith(
            "select version from public.schema_migrations order by version"
        ):
            self.rows = [(version,) for version in sorted(self.conn.versions)]
        elif "from pg_catalog.pg_class as c" in normalized:
            self.rows = [
                (name, owner)
                for name, owner in sorted(self.conn.tables.items())
            ]
        elif normalized.startswith("insert into public.schema_migrations"):
            self.conn.versions.append(int(params[0]))
            self.rows = []
        elif normalized.startswith("comment on table public.schema_migrations"):
            self.conn.authorization_receipt = sql.split("'", 2)[1]
            self.rows = []
        else:
            for match in re.finditer(
                r"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?"
                r"(?:public\.)?\"?([a-z_][a-z0-9_]*)\"?",
                sql,
                re.IGNORECASE,
            ):
                self.conn.tables.setdefault(match.group(1).lower(), self.conn.role)
            self.rows = []

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _FakeConnection:
    autocommit = False

    def __init__(
        self,
        *,
        versions: list[int],
        tables: dict[str, str],
        fail_on: str = "",
    ) -> None:
        self.versions = list(versions)
        self.tables = dict(tables)
        self.fail_on = fail_on
        self.role = "migration_owner"
        self.schema_owner = self.role
        self.schema_foreign_create = False
        self.authorization_receipt = None
        self.statements: list[tuple[str, object]] = []
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _authorization(mode: str = "upgrade") -> dict:
    source = {"bootstrap": 0, "upgrade": 40, "verify": 41}[mode]
    if mode == "upgrade":
        backup = {
            "status": "completed",
            "kind": "managed-snapshot",
            "source_head": source,
            "completed_at": "2026-08-04T11:42:00Z",
            "evidence_id": "backup:prod:123",
            "evidence_sha256": "d" * 64,
        }
    else:
        backup = {
            "status": (
                "not-applicable-empty-database"
                if mode == "bootstrap"
                else "not-required-schema-current"
            ),
            "kind": "none-empty-database" if mode == "bootstrap" else "none-schema-current",
            "source_head": source,
            "completed_at": None,
            "evidence_id": None,
            "evidence_sha256": None,
        }
    return {
        "schema_version": 1,
        "profile_id": pm.PROFILE_ID,
        "operation_id": "migration:20260804:001",
        "mode": mode,
        "source_head": source,
        "target_head": 41,
        "migration_lock_sha256": LOCK_SHA,
        "release_version": RELEASE_VERSION,
        "image_digest": IMAGE_DIGEST,
        "issued_at": "2026-08-04T11:45:00Z",
        "expires_at": "2026-08-04T13:00:00Z",
        "approvals": ["user:release-owner", "user:database-owner"],
        "drain": {
            "confirmed": True,
            "completed_at": "2026-08-04T11:40:00Z",
            "evidence_id": "drain:prod:123",
            "evidence_sha256": "e" * 64,
        },
        "backup": backup,
        "recovery": {
            "completed_at": "2026-07-15T10:00:00Z",
            "evidence_id": "restore-drill:prod:123",
            "evidence_sha256": "f" * 64,
        },
        "failure_policy": pm.FAILURE_POLICY,
    }


def _validate(monkeypatch, document: dict) -> pm.MigrationAuthorization:
    monkeypatch.setattr(pm, "migration_versions", lambda: (1, 10, 40, 41))
    return pm.validate_authorization(
        document,
        raw_sha256=AUTH_SHA,
        expected_raw_sha256=AUTH_SHA,
        expected_lock_sha256=LOCK_SHA,
        expected_image_digest=IMAGE_DIGEST,
        expected_release_version=RELEASE_VERSION,
        now=NOW,
    )


def _runner_authorization(mode: str, source: int, target: int) -> pm.MigrationAuthorization:
    document = _authorization(mode)
    document["mode"] = mode
    document["source_head"] = source
    document["target_head"] = target
    document["backup"]["source_head"] = source
    return pm.MigrationAuthorization(
        document=document,
        raw_sha256=AUTH_SHA,
        mode=mode,
        operation_id=document["operation_id"],
        source_head=source,
        target_head=target,
        migration_lock_sha256=LOCK_SHA,
        release_version=RELEASE_VERSION,
        image_digest=IMAGE_DIGEST,
    )


def _synthetic_ladder(monkeypatch):
    from maverick.world_model_backends import postgres

    ladder = [
        (1, ["CREATE TABLE IF NOT EXISTS base (id INTEGER PRIMARY KEY)"]),
        (2, ["CREATE TABLE IF NOT EXISTS quota (id INTEGER PRIMARY KEY)"]),
    ]
    monkeypatch.setattr(postgres, "MIGRATIONS", ladder)
    monkeypatch.setattr(pm, "migration_versions", lambda: (1, 2))
    monkeypatch.setattr(
        pm,
        "_lock_and_verify_catalog",
        lambda _cur, *, applied_versions: "c" * 64,
    )
    return postgres


def test_strict_authorization_parser_rejects_duplicate_nonfinite_and_oversize() -> None:
    with pytest.raises(pm.ProductionMigrationError, match="duplicate"):
        pm.parse_authorization_bytes(b'{"schema_version":1,"schema_version":1}')
    with pytest.raises(pm.ProductionMigrationError, match="non-finite"):
        pm.parse_authorization_bytes(b'{"value":NaN}')
    with pytest.raises(pm.ProductionMigrationError, match="byte length"):
        pm.parse_authorization_bytes(b" " * (pm.MAX_AUTHORIZATION_BYTES + 1))
    with pytest.raises(pm.ProductionMigrationError, match="must be an object"):
        pm.parse_authorization_bytes(b"[]")


def test_applied_version_classifier_only_admits_exact_three_states() -> None:
    expected = (1, 10, 40, 41)
    assert pm.classify_applied_versions((), expected_versions=expected).name == "empty"
    prior = pm.classify_applied_versions((1, 10, 40), expected_versions=expected)
    assert prior.name == "adjacent-predecessor" and prior.source_head == 40
    assert (
        pm.classify_applied_versions(expected, expected_versions=expected).name
        == "current"
    )


@pytest.mark.parametrize(
    "applied",
    [
        (1,),  # arbitrary older prefix is not a supported production upgrade
        (1, 40),  # missing middle
        (1, 10, 41),  # missing predecessor but target present
        (1, 10, 40, 41, 42),  # newer database / older binary
        (1, 10, 40, 40),  # duplicate ledger version
        (10, 1, 40),  # out of order
        (True, 10, 40),  # bool is not an exact integer
    ],
)
def test_applied_version_classifier_fails_closed(applied: tuple[object, ...]) -> None:
    with pytest.raises(pm.ProductionMigrationError):
        pm.classify_applied_versions(applied, expected_versions=(1, 10, 40, 41))


@pytest.mark.parametrize("mode", ["bootstrap", "upgrade", "verify"])
def test_exact_authorization_modes_are_accepted(monkeypatch, mode: str) -> None:
    authorization = _validate(monkeypatch, _authorization(mode))
    assert authorization.mode == mode
    assert authorization.target_head == 41


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda d: d.update(extra=True), "unexpected schema"),
        (lambda d: d.update(schema_version=1.0), "schema_version"),
        (lambda d: d.update(profile_id="another-profile"), "profile_id"),
        (lambda d: d.update(source_head=39), "source/target"),
        (lambda d: d.update(target_head=42), "source/target"),
        (lambda d: d.update(migration_lock_sha256="0" * 64), "migration lock"),
        (lambda d: d.update(image_digest="sha256:" + ("0" * 64)), "image digest"),
        (lambda d: d.update(release_version="0.1.8"), "release version"),
        (lambda d: d.update(expires_at="2026-08-04T11:59:59Z"), "expired"),
        (lambda d: d.update(approvals=["user:same", "user:same"]), "distinct"),
        (lambda d: d["drain"].update(confirmed=False), "must be drained"),
        (
            lambda d: d["drain"].update(completed_at="2026-08-04T09:00:00Z"),
            "drain evidence",
        ),
        (lambda d: d["backup"].update(status="pending"), "completed managed"),
        (
            lambda d: d["backup"].update(completed_at="2026-08-02T09:00:00Z"),
            "backup evidence",
        ),
        (
            lambda d: d["recovery"].update(completed_at="2026-01-01T00:00:00Z"),
            "restore-drill",
        ),
        (lambda d: d.update(failure_policy="down-migrate"), "failure policy"),
    ],
)
def test_authorization_contract_fails_closed(monkeypatch, mutation, match: str) -> None:
    document = copy.deepcopy(_authorization())
    mutation(document)
    with pytest.raises(pm.ProductionMigrationError, match=match):
        _validate(monkeypatch, document)


def test_authorization_bytes_are_bound_out_of_band(monkeypatch) -> None:
    document = _authorization()
    with pytest.raises(pm.ProductionMigrationError, match="Helm-bound"):
        monkeypatch.setattr(pm, "migration_versions", lambda: (1, 10, 40, 41))
        pm.validate_authorization(
            document,
            raw_sha256="1" * 64,
            expected_raw_sha256=AUTH_SHA,
            expected_lock_sha256=LOCK_SHA,
            expected_image_digest=IMAGE_DIGEST,
            expected_release_version=RELEASE_VERSION,
            now=NOW,
        )


def test_evidence_freshness_is_measured_at_execution_not_issuance(monkeypatch) -> None:
    document = copy.deepcopy(_authorization())
    with pytest.raises(pm.ProductionMigrationError, match="drain evidence"):
        monkeypatch.setattr(pm, "migration_versions", lambda: (1, 10, 40, 41))
        pm.validate_authorization(
            document,
            raw_sha256=AUTH_SHA,
            expected_raw_sha256=AUTH_SHA,
            expected_lock_sha256=LOCK_SHA,
            expected_image_digest=IMAGE_DIGEST,
            expected_release_version=RELEASE_VERSION,
            now=datetime(2026, 8, 4, 12, 59, 0, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "completed_at",
    ["2026-08-04T11:39:59Z", "2026-08-04T11:40:00Z"],
)
def test_upgrade_backup_must_follow_the_completed_drain(
    monkeypatch,
    completed_at: str,
) -> None:
    document = copy.deepcopy(_authorization())
    document["backup"]["completed_at"] = completed_at
    with pytest.raises(pm.ProductionMigrationError, match="does not postdate"):
        _validate(monkeypatch, document)


def test_runner_revalidates_expiry_after_waiting_for_admission_lock(
    monkeypatch,
) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1],
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
    )
    times = iter(
        [
            NOW,
            datetime(2026, 8, 4, 13, 1, 0, tzinfo=timezone.utc),
        ]
    )
    monkeypatch.setattr(pm, "_utc_now", lambda: next(times))

    with pytest.raises(pm.ProductionMigrationError, match="expired"):
        pm.run_production_migration(
            conn,
            _runner_authorization("upgrade", 1, 2),
            rls_apply=lambda _conn: pytest.fail("RLS must not run"),
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert not any(
        "create table if not exists quota" in sql.lower()
        for sql, _params in conn.statements
    )


def test_adjacent_upgrade_commits_exact_tail_and_reconciles_rls(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1],
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
    )

    def apply_rls(connection):
        connection.commit()
        return {"ready": True, "policy_count": 2}

    report = pm.run_production_migration(
        conn,
        _runner_authorization("upgrade", 1, 2),
        now=NOW,
        rls_apply=apply_rls,
    )

    assert conn.versions == [1, 2]
    assert conn.authorization_receipt == pm._RECEIPT_PREFIX + AUTH_SHA
    assert set(conn.tables) == {"base", "quota", "schema_migrations"}
    assert conn.commits == 2
    assert conn.rollbacks == 1  # final read-only verification releases its locks
    assert report["result"] == "committed"
    assert report["applied_versions"] == [1, 2]
    assert report["rls_policy_count"] == 2
    assert "migration_owner" not in pm.canonical_json(report)


def test_empty_dedicated_schema_bootstraps_the_full_ladder(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(versions=[], tables={})

    def apply_rls(connection):
        connection.commit()
        return {"ready": True, "policy_count": 2}

    report = pm.run_production_migration(
        conn,
        _runner_authorization("bootstrap", 0, 2),
        now=NOW,
        rls_apply=apply_rls,
    )

    assert conn.versions == [1, 2]
    assert set(conn.tables) == {"base", "quota", "schema_migrations"}
    assert report["source_head"] == 0
    assert report["result"] == "committed"


@pytest.mark.parametrize("versions", [[1, 3], [1, 2, 3], [1, 1]])
def test_runner_refuses_gapped_newer_or_out_of_order_before_ddl(
    monkeypatch,
    versions: list[int],
) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=versions,
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
    )
    called = False

    def apply_rls(_connection):
        nonlocal called
        called = True
        return {"ready": True, "policy_count": 2}

    with pytest.raises(pm.ProductionMigrationError, match="newer, gapped"):
        pm.run_production_migration(
            conn,
            _runner_authorization("upgrade", 1, 2),
            now=NOW,
            rls_apply=apply_rls,
        )

    assert called is False
    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert not any("create table if not exists quota" in sql.lower() for sql, _ in conn.statements)


def test_runner_refuses_unexpected_table_or_foreign_owner_before_ddl(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    for tables in (
        {
            "base": "migration_owner",
            "rogue": "migration_owner",
            "schema_migrations": "migration_owner",
        },
        {"base": "other_owner", "schema_migrations": "migration_owner"},
    ):
        conn = _FakeConnection(versions=[1], tables=tables)
        with pytest.raises(pm.ProductionMigrationError):
            pm.run_production_migration(
                conn,
                _runner_authorization("upgrade", 1, 2),
                now=NOW,
                rls_apply=lambda _conn: pytest.fail("RLS must not run"),
            )
        assert conn.commits == 0
        assert conn.rollbacks == 1


@pytest.mark.parametrize(
    ("schema_owner", "foreign_create"),
    [("database_owner", False), ("migration_owner", True)],
)
def test_runner_refuses_unsafe_public_schema_authority_before_ddl(
    monkeypatch,
    schema_owner: str,
    foreign_create: bool,
) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1],
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
    )
    conn.schema_owner = schema_owner
    conn.schema_foreign_create = foreign_create

    with pytest.raises(pm.ProductionMigrationError, match="public schema"):
        pm.run_production_migration(
            conn,
            _runner_authorization("upgrade", 1, 2),
            now=NOW,
            rls_apply=lambda _conn: pytest.fail("RLS must not run"),
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1
    assert not any("create table if not exists quota" in sql.lower() for sql, _ in conn.statements)


def test_schema_failure_rolls_back_without_running_rls(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1],
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
        fail_on="create table if not exists quota",
    )

    with pytest.raises(pm.ProductionMigrationError, match="transaction failed"):
        pm.run_production_migration(
            conn,
            _runner_authorization("upgrade", 1, 2),
            now=NOW,
            rls_apply=lambda _conn: pytest.fail("RLS must not run"),
        )

    assert conn.commits == 0
    assert conn.rollbacks == 1


def test_commit_before_rls_report_is_reconciled_by_same_authorization(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1],
        tables={"base": "migration_owner", "schema_migrations": "migration_owner"},
    )
    authorization = _runner_authorization("upgrade", 1, 2)

    with pytest.raises(pm.ProductionMigrationError, match="keep serving drained"):
        pm.run_production_migration(
            conn,
            authorization,
            now=NOW,
            rls_apply=lambda _conn: (_ for _ in ()).throw(RuntimeError("lost report")),
        )
    assert conn.versions == [1, 2]

    def apply_rls(connection):
        connection.commit()
        return {"ready": True, "policy_count": 2}

    report = pm.run_production_migration(
        conn,
        authorization,
        now=NOW,
        rls_apply=apply_rls,
    )
    assert report["result"] == "already-current-reconciled"
    assert conn.versions == [1, 2]


def test_current_schema_refuses_a_different_upgrade_authorization(monkeypatch) -> None:
    _synthetic_ladder(monkeypatch)
    conn = _FakeConnection(
        versions=[1, 2],
        tables={
            "base": "migration_owner",
            "quota": "migration_owner",
            "schema_migrations": "migration_owner",
        },
    )
    conn.authorization_receipt = pm._RECEIPT_PREFIX + AUTH_SHA
    another = _runner_authorization("upgrade", 1, 2)
    another = pm.MigrationAuthorization(
        document=another.document,
        raw_sha256="9" * 64,
        mode=another.mode,
        operation_id="migration:20260804:other",
        source_head=another.source_head,
        target_head=another.target_head,
        migration_lock_sha256=another.migration_lock_sha256,
        release_version=another.release_version,
        image_digest=another.image_digest,
    )

    with pytest.raises(pm.ProductionMigrationError, match="exact authorization bytes"):
        pm.run_production_migration(
            conn,
            another,
            now=NOW,
            rls_apply=lambda _conn: pytest.fail("RLS must not run"),
        )


def test_canonical_json_refuses_nonfinite_values() -> None:
    with pytest.raises(ValueError):
        pm.canonical_json({"not_finite": float("nan")})
    encoded = pm.canonical_json(_authorization())
    assert json.loads(encoded)["operation_id"] == "migration:20260804:001"
