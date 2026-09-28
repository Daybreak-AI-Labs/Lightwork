"""Where consent records are kept: files (default) or shared Postgres.

The translation and settings tests always run. The live tests run where a
Postgres service is available (``MAVERICK_PG_DSN``, the CI postgres job):
they run the real consent preference store on Postgres, check SQLite's
guarantees hold there, and copy a workspace's records across.
"""
from __future__ import annotations

import os
import sqlite3
import uuid

import pytest
from maverick import consent_storage as storage

_DSN = os.environ.get("MAVERICK_PG_DSN", "")
live = pytest.mark.skipif(not _DSN, reason="MAVERICK_PG_DSN not set (no Postgres service)")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    from maverick import config

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in (storage.BACKEND_ENV, storage.DEFAULT_DSN_ENV, "MAVERICK_CONFIG",
                 "MAVERICK_CONFIG_OVERLAY", "ACME_CONSENT_DSN"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    storage._reset_for_testing()
    yield
    storage._reset_for_testing()
    config.reset_config_cache()


def _config(backend="postgres", **extra):
    return {"consent_preferences": {"storage": {"backend": backend, **extra}}}


# ------------------------------------------------------------------ settings
def test_files_on_this_server_are_the_default():
    assert storage.storage_settings({}) == storage.StorageSelection("sqlite")
    assert storage.shared() is False
    with pytest.raises(storage.ConsentStorageError):
        storage.connect("/tmp/x.sqlite3")


def test_postgres_needs_a_connection_and_the_environment_wins(monkeypatch):
    with pytest.raises(storage.ConsentStorageError, match="needs a database connection"):
        storage.storage_settings(_config())
    saved = storage.storage_settings(_config(dsn="postgresql://saved@db/one"))
    assert saved.dsn == "postgresql://saved@db/one" and "saved" not in repr(saved)
    monkeypatch.setenv(storage.DEFAULT_DSN_ENV, "postgresql://env@db/two")
    assert storage.storage_settings(_config(dsn="postgresql://saved@db/one")).dsn.startswith(
        "postgresql://env@")
    monkeypatch.setenv("ACME_CONSENT_DSN", "postgresql://named@db/three")
    named = storage.storage_settings(_config(dsn_secret_ref="$ACME_CONSENT_DSN"))
    assert named.dsn.startswith("postgresql://named@") and named.dsn_env == "ACME_CONSENT_DSN"
    monkeypatch.setenv(storage.BACKEND_ENV, "sqlite")
    assert storage.storage_settings(_config(dsn="x")).backend == "sqlite"


@pytest.mark.parametrize("ref", ["$PATH", "lower_case", "$GITHUB_TOKEN"])
def test_the_connection_name_must_be_a_consent_setting(ref):
    with pytest.raises(storage.ConsentStorageError):
        storage.storage_settings(_config(dsn_secret_ref=ref, dsn="postgresql://x@db/y"))


def test_the_choice_is_read_once_per_process(monkeypatch):
    assert storage.shared() is False
    monkeypatch.setenv(storage.BACKEND_ENV, "postgres")
    monkeypatch.setenv(storage.DEFAULT_DSN_ENV, "postgresql://x@db/y")
    assert storage.shared() is False  # takes effect at restart
    storage._reset_for_testing()
    assert storage.shared() is True


def test_sqlite_stores_are_unchanged_when_nothing_is_set(tmp_path):
    from maverick.consent_preferences import ConsentPreferenceStore

    store = ConsentPreferenceStore(tmp_path / "consent.sqlite3")
    assert store.path.is_file()
    with sqlite3.connect(store.path) as connection:
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master")}
    assert "consent_receipts" in tables and "lw_sqlite_pragma" not in tables


# --------------------------------------------------------------- translation
def test_statements_are_translated_to_postgres():
    t = storage.translate
    assert " ".join(t("INSERT OR IGNORE INTO m(key, value) VALUES (?, ?)", ("a", "b")).sql.split()) \
        == "INSERT INTO m(key, value) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    assert "strpos(lower(a), lower(%s))" in t("SELECT 1 FROM x WHERE instr(lower(a), lower(?))",
                                              ("q",)).sql
    assert "GREATEST(a, %s)" in t("SELECT max(a, ?) FROM x", (1,)).sql
    assert "max(a)" in t("SELECT max(a) FROM x").sql
    numeric = t("SELECT 1 FROM x WHERE json_extract(p,'$.until')>?", (1.5,)).sql
    assert "((p)::jsonb #>> '{until}'))::double precision>%s" in numeric
    text = t("SELECT 1 FROM x WHERE json_extract(p,'$.id')=?", ("a",)).sql
    assert "::double precision" not in text
    ordered = t("SELECT a FROM x ORDER BY a, b DESC LIMIT ?", (5,)).sql
    assert "a NULLS FIRST, b DESC NULLS LAST LIMIT %s" in ordered
    assert t("SELECT '50%' FROM x WHERE a = ?", (1,)).sql == "SELECT '50%' FROM x WHERE a = %s"
    assert "%%" in t("SELECT a % ? FROM x", (2,)).sql
    assert t("UPDATE x SET a=:a WHERE b=:b", {"a": 1, "b": 2}).sql == \
        "UPDATE x SET a=%(a)s WHERE b=%(b)s"
    assert t("BEGIN IMMEDIATE").kind == "begin" and t("COMMIT").kind == "commit"
    assert t("PRAGMA table_info(t)").pragma == "table_info"


def test_tables_and_triggers_keep_sqlite_types_and_rules():
    table = storage.translate(
        "CREATE TABLE IF NOT EXISTS t (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
        "key TEXT NOT NULL, at REAL, n INTEGER, blob BLOB, "
        "FOREIGN KEY (key) REFERENCES p(key) ON DELETE CASCADE)").sql
    assert "sequence BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY" in table
    assert 'key TEXT COLLATE "C" NOT NULL' in table and "at DOUBLE PRECISION" in table
    assert "n BIGINT" in table and "blob BYTEA" in table
    assert "ON DELETE CASCADE DEFERRABLE INITIALLY IMMEDIATE" in table
    function, trigger = storage.translate(
        "CREATE TRIGGER IF NOT EXISTS t_no_update BEFORE UPDATE OF a, b ON t "
        "BEGIN SELECT RAISE(ABORT, 'rows are immutable'); END").statements
    assert "RAISE EXCEPTION USING ERRCODE = '23000', MESSAGE = 'rows are immutable'" in function
    assert 'BEFORE UPDATE OF a, b ON t FOR EACH ROW EXECUTE FUNCTION "lw_trg_t_no_update"()' \
        in trigger
    assert storage.split_statements(
        "CREATE TABLE a(x TEXT); CREATE TRIGGER g AFTER INSERT ON a BEGIN "
        "INSERT INTO b VALUES (NEW.x); SELECT 1; END; CREATE TABLE c(y TEXT);")[1].endswith("END")


def test_refused_statements_come_back_as_sqlite_errors():
    class Diag:
        message_primary = "consent receipts are immutable"
        constraint_name = None

    class Raised(Exception):
        sqlstate = "23000"
        diag = Diag()

    class Duplicate(Exception):
        sqlstate = "23505"
        diag = type("D", (), {"constraint_name": "t_pkey", "message_primary": "Key (x)=(secret)"})

    assert str(storage._mapped_error(Raised())) == "consent receipts are immutable"
    mapped = storage._mapped_error(Duplicate())
    assert isinstance(mapped, sqlite3.IntegrityError) and "secret" not in str(mapped)
    locked = type("L", (Exception,), {"sqlstate": "55P03"})()
    assert str(storage._mapped_error(locked)) == "database is locked"
    assert issubclass(storage.ConsentStorageError, sqlite3.Error)


# ------------------------------------------------------------------ live Postgres
@pytest.fixture()
def shared(monkeypatch, tmp_path):
    monkeypatch.setenv(storage.BACKEND_ENV, "postgres")
    monkeypatch.setenv(storage.DEFAULT_DSN_ENV, _DSN)
    storage._reset_for_testing()
    schemas: list[str] = []
    yield schemas
    import psycopg

    with psycopg.connect(_DSN, autocommit=True) as connection:
        for schema in schemas:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _policy(store, tenant="tenant-a"):
    return store.publish_policy(tenant, "privacy-notice", 1,
                                {"analytics": "consent", "marketing": "consent"}, "a" * 64,
                                1_700_000_000.0, expected_current_version=0)


@live
def test_the_preference_store_runs_on_postgres_with_sqlite_guarantees(shared, tmp_path):
    from maverick.consent_preferences import ConsentPreferenceStore

    path = tmp_path / f"{uuid.uuid4().hex}.sqlite3"
    shared.append(storage.schema_name(path))
    store = ConsentPreferenceStore(path, clock=lambda: 1_700_000_100.0)
    assert not path.exists()  # nothing is written on this server
    created = _policy(store)
    assert created["version"] == 1
    connection = storage.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE consent_policies SET notice_sha256 = ?", ("b" * 64,))
        # The refused statement did not spoil the transaction, as in SQLite.
        row = connection.execute("SELECT version FROM consent_policies").fetchone()
        assert row["VERSION"] == 1 and dict(row) == {"version": 1}
        connection.execute("ROLLBACK")
    finally:
        connection.close()
    other = tmp_path / "other" / f"{uuid.uuid4().hex}.sqlite3"
    shared.append(storage.schema_name(other))
    other_store = ConsentPreferenceStore(other)
    connection = storage.connect(other)
    try:
        assert connection.execute("SELECT count(*) FROM consent_policies").fetchone()[0] == 0
    finally:
        connection.close()
    assert other_store.path == other


@live
def test_local_records_are_copied_verified_and_never_doubled(shared, monkeypatch, tmp_path):
    from maverick.consent_preferences import ConsentPreferenceStore
    from maverick.paths import data_dir

    workspace = "copy-" + uuid.uuid4().hex[:10]
    path = data_dir("consent_preferences", "consent-preferences.sqlite3", tenant=workspace)
    monkeypatch.setenv(storage.BACKEND_ENV, "sqlite")
    storage._reset_for_testing()
    local = ConsentPreferenceStore(path, clock=lambda: 1_700_000_100.0)
    _policy(local)
    with sqlite3.connect(path) as connection:
        expected = connection.execute("SELECT count(*) FROM consent_policies").fetchone()[0]
    monkeypatch.setenv(storage.BACKEND_ENV, "postgres")
    storage._reset_for_testing()
    shared.append(storage.schema_name(path))
    preview = storage.preview_move()
    mine = [s for s in preview["stores"] if s["workspace"] and workspace in s["workspace"]]
    assert mine and mine[0]["state"] == "ready" and mine[0]["records"] >= expected
    result = storage.move_local_records()
    assert not [r for r in result["refused"] if workspace in r["workspace"]]
    connection = storage.connect(path)
    try:
        assert connection.execute("SELECT count(*) FROM consent_policies").fetchone()[0] == expected
    finally:
        connection.close()
    assert [s for s in storage.preview_move()["stores"]
            if s["workspace"] and workspace in s["workspace"]][0]["state"] == "moved"
    again = storage.move_local_records()  # nothing new is copied
    assert sum(m["copied"] for m in again["moved"] if workspace in m["workspace"]) == 0
    assert path.is_file()  # the original file is kept
    # Different records already in the shared database: that store is refused.
    connection = storage.connect(path)
    try:
        connection.execute("INSERT INTO consent_meta(key, value) VALUES ('extra', 'x')")
    finally:
        connection.close()
    refused = storage.move_local_records()["refused"]
    assert any(workspace in r["workspace"] for r in refused)
