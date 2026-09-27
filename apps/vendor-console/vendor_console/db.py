"""SQLite storage for the vendor console.

One file, WAL mode, foreign keys on. SQLite is right for an internal, low-
concurrency control plane; the schema is Postgres-portable (a later migration
to the platform's postgres backend is a swap of this module, not the callers).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 4

SCHEMA = """
CREATE TABLE IF NOT EXISTS staff (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    role          TEXT NOT NULL DEFAULT 'viewer',   -- owner|admin|support|viewer
    pw_hash       TEXT NOT NULL,
    totp_secret   TEXT NOT NULL DEFAULT '',
    totp_enrolled INTEGER NOT NULL DEFAULT 0,
    totp_last_step INTEGER NOT NULL DEFAULT 0,  -- single-use TOTP: reject step <= this
    session_epoch INTEGER NOT NULL DEFAULT 0,   -- bumped on logout to revoke live sessions
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    primary_contact TEXT NOT NULL DEFAULT '',
    contact_email   TEXT NOT NULL DEFAULT '',
    posture         TEXT NOT NULL DEFAULT 'connected',  -- connected|vpc|airgapped
    status          TEXT NOT NULL DEFAULT 'trial',      -- trial|active|suspended|churned
    notes           TEXT NOT NULL DEFAULT '',
    serve_token_hash TEXT NOT NULL DEFAULT '',          -- sha256 of the deployment serve token
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS licenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    license_id   TEXT NOT NULL UNIQUE,
    tier         TEXT NOT NULL,
    suites_json  TEXT NOT NULL DEFAULT '[]',
    features_json TEXT NOT NULL DEFAULT '[]',
    seats        INTEGER,
    issued_at    TEXT NOT NULL,
    expires_at   TEXT,
    grace_days   INTEGER NOT NULL DEFAULT 14,
    key_id       TEXT NOT NULL DEFAULT '',
    doc_json     TEXT NOT NULL,                        -- the full signed license
    revoked_at   REAL,
    note         TEXT NOT NULL DEFAULT '',
    created_by   TEXT NOT NULL DEFAULT '',
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_licenses_customer ON licenses(customer_id);

CREATE TABLE IF NOT EXISTS checkins (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id   INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    deployment_id TEXT NOT NULL DEFAULT '',
    version       TEXT NOT NULL DEFAULT '',
    license_status TEXT NOT NULL DEFAULT '',
    ip            TEXT NOT NULL DEFAULT '',
    at            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_checkins_customer ON checkins(customer_id, at);

CREATE TABLE IF NOT EXISTS releases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version        TEXT NOT NULL,
    channel        TEXT NOT NULL DEFAULT 'stable',   -- stable | edge
    min_from       TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    migrations_json TEXT NOT NULL DEFAULT '[]',
    artifacts_json TEXT NOT NULL DEFAULT '[]',
    manifest_json  TEXT NOT NULL,                    -- the full signed release manifest
    key_id         TEXT NOT NULL DEFAULT '',
    published_by   TEXT NOT NULL DEFAULT '',
    yanked_at      REAL,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_releases_channel ON releases(channel, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_releases_ver_chan ON releases(version, channel);

CREATE TABLE IF NOT EXISTS tickets (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id    INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    correlation_id TEXT NOT NULL DEFAULT '',
    subject        TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'open',     -- open|in_progress|waiting|resolved|closed
    priority       TEXT NOT NULL DEFAULT 'normal',   -- low|normal|high|urgent
    tier           TEXT NOT NULL DEFAULT '',
    agent_version  TEXT NOT NULL DEFAULT '',
    summary_json   TEXT NOT NULL DEFAULT '{}',       -- redacted ticket_summary
    bundle_json    TEXT NOT NULL DEFAULT '{}',       -- the redacted support bundle
    assignee       TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets(status, id);
CREATE INDEX IF NOT EXISTS ix_tickets_customer ON tickets(customer_id);
CREATE INDEX IF NOT EXISTS ix_tickets_correlation ON tickets(correlation_id);

CREATE TABLE IF NOT EXISTS ticket_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    author     TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT 'note',         -- note | status
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_comments_ticket ON ticket_comments(ticket_id, id);

CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    actor       TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}',
    prev_hash   TEXT NOT NULL DEFAULT '',
    hash        TEXT NOT NULL
);

-- Single-use set-up links for new team members (v4). Only a SHA-256 of the
-- random token is stored; the link itself is shown once to the owner.
CREATE TABLE IF NOT EXISTS staff_invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    staff_id    INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    used_at     REAL                                  -- set on use OR when superseded
);
CREATE INDEX IF NOT EXISTS ix_staff_invites_staff ON staff_invites(staff_id, used_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    env = os.environ.get("VENDOR_CONSOLE_DB")
    if env:
        return Path(env).expanduser()
    return Path(os.environ.get("DAYBREAK_HOME", Path.home() / ".daybreak")) / "vendor-console.db"


class _Connection(sqlite3.Connection):
    """Keep connection coordinates for independent transaction ownership."""

    database_target: str
    database_uri: bool


def _open(target: str, *, uri: bool = False) -> _Connection:
    conn = sqlite3.connect(
        target, check_same_thread=False, timeout=5.0, uri=uri, factory=_Connection)
    conn.database_target = target
    conn.database_uri = uri
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
    except BaseException:
        conn.close()
        raise
    return conn


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    p = Path(path) if path else default_db_path()
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    if str(p) == ":memory:":
        # A unique private shared-memory database permits an owned transaction
        # connection while this anchor keeps it alive. Separate connect calls
        # still create separate databases.
        return _open(f"file:vendor-console-{secrets.token_hex(16)}?mode=memory&cache=shared", uri=True)
    return _open(str(p.resolve()))


@contextmanager
def transaction(source: sqlite3.Connection):
    """A dedicated writer owns its BEGIN IMMEDIATE through commit/rollback.

    Other handlers may share ``source`` and commit their own operations. They
    cannot commit or join this connection's transaction. SQLite serializes
    writers across processes, including the audit read-tail/insert sequence.
    There is no write retry after an uncertain commit.
    """
    if isinstance(source, _Connection):
        target, uri = source.database_target, source.database_uri
    else:
        rows = source.execute("PRAGMA database_list").fetchall()
        target = next((row[2] for row in rows if row[1] == "main"), "")
        if not target:
            raise ValueError("in-memory transactions require vendor_console.db.connect")
        uri = False
    owned = _open(target, uri=uri)
    try:
        owned.execute("BEGIN IMMEDIATE")
        yield owned
        owned.commit()
    except BaseException:
        owned.rollback()
        raise
    finally:
        owned.close()


def init_db(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT name FROM sqlite_master WHERE name='meta'").fetchone():
        version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if version is not None and not 1 <= int(version[0]) <= SCHEMA_VERSION:
            raise ValueError("unsupported vendor console schema version")
    conn.executescript(SCHEMA)
    # Additive column migrations (CREATE TABLE IF NOT EXISTS won't alter an
    # existing table). Each is idempotent — a duplicate-column error means it's
    # already there. Add new columns here as the schema grows.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(customers)")}
    if "channel" not in columns:
        conn.execute("ALTER TABLE customers ADD COLUMN channel TEXT NOT NULL DEFAULT 'stable'")
    conn.commit()
    with transaction(conn) as tx:
        _migrate_support_intake(tx)
        tx.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(SCHEMA_VERSION),))


def _migrate_support_intake(conn: sqlite3.Connection) -> None:
    """Add a customer-scoped active identity without deleting legacy tickets.

    For existing duplicate active tickets the newest id is the deterministic
    intake target; the others remain visible for staff reconciliation.
    """
    version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if version is not None and not 1 <= int(version[0]) <= SCHEMA_VERSION:
        raise ValueError("unsupported vendor console schema version")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_ticket_intake_lookup "
                 "ON tickets(customer_id,correlation_id,status,id)")
    conn.execute("CREATE TABLE IF NOT EXISTS support_intake_keys ("
                 "customer_scope TEXT NOT NULL, correlation_id TEXT NOT NULL, "
                 "ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE, "
                 "PRIMARY KEY(customer_scope,correlation_id))")
    if version is None or int(version[0]) < 3:
        conn.execute("INSERT INTO support_intake_keys(customer_scope,correlation_id,ticket_id) "
                     "SELECT CASE WHEN customer_id IS NULL THEN 'unassigned' ELSE 'customer:' || customer_id END, "
                     "correlation_id, MAX(id) FROM tickets "
                     "WHERE correlation_id<>'' AND status NOT IN ('closed','resolved') "
                     "GROUP BY customer_id,correlation_id")
    # Triggers keep the lookup aligned even for existing internal store callers.
    # A closed ticket may reopen only if no newer active identity owns the key.
    scope = "CASE WHEN NEW.customer_id IS NULL THEN 'unassigned' ELSE 'customer:' || NEW.customer_id END"
    for name, event, removal in (
        ("support_intake_insert", "INSERT", ""),
        ("support_intake_update", "UPDATE OF customer_id,correlation_id,status",
         "DELETE FROM support_intake_keys WHERE ticket_id=OLD.id;"),
    ):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON tickets BEGIN "
                     f"{removal} INSERT INTO support_intake_keys(customer_scope,correlation_id,ticket_id) "
                     f"SELECT {scope}, NEW.correlation_id, NEW.id "
                     "WHERE NEW.correlation_id<>'' AND NEW.status NOT IN ('closed','resolved'); END")
