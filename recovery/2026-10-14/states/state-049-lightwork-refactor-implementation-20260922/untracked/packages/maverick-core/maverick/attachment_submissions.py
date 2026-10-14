"""Transactional submission receipts and serialized attachment quota admission.

Files are staged separately. A durable draft explains any interrupted file/DB
boundary; only admitted submissions may claim execution. Both backends use one
transaction per operation, including pooled PostgreSQL.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass

SQLITE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS attachment_submissions ("
    "submission_key TEXT PRIMARY KEY, tenant_id TEXT, owner TEXT NOT NULL, "
    "goal_id INTEGER NOT NULL UNIQUE REFERENCES goals(id) ON DELETE CASCADE, fingerprint TEXT NOT NULL, "
    "state TEXT NOT NULL, detail TEXT NOT NULL, updated_at REAL NOT NULL)"
)
POSTGRES_SCHEMA = SQLITE_SCHEMA.replace("updated_at REAL", "updated_at DOUBLE PRECISION")


class SubmissionConflict(ValueError):
    pass


@dataclass(frozen=True)
class AttachmentAdmission:
    """Metadata admission preserves an existing stored path verbatim."""

    filename: str
    mime: str
    size_bytes: int
    sha256: str
    path: str


class SubmissionStore:
    def __init__(self, world):
        from .paths import current_tenant_id_strict
        from .world_model import WorldModel, _dec_field, _enc_field

        self.world = world
        self.sqlite = isinstance(world, WorldModel)
        self._context_tenant = current_tenant_id_strict()
        self.tenant = None
        self.seal, self.open = _enc_field, _dec_field
        if not self.sqlite:
            from .world_model_backends.postgres import (
                PostgresWorldModel,
                _active_tenant,
                _seal,
                _unseal,
            )

            if not isinstance(world, PostgresWorldModel):
                raise TypeError("Submission admission requires a supported transactional world store")
            self.tenant = _active_tenant()
            self.seal, self.open = _seal, _unseal

    @contextmanager
    def transaction(self, lock: str):
        self._check_context()
        if self.sqlite:
            with self.world._writing() as connection:
                if not connection.in_transaction:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        else:
            key = int.from_bytes(hashlib.sha256(f"{self.tenant}\0{lock}".encode()).digest()[:8], "big", signed=True)
            with self.world._tx() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (key,))
                yield cursor

    def execute(self, connection, sql, parameters=()):
        if self.sqlite:
            return connection.execute(sql, parameters)
        connection.execute(sql.replace("?", "%s"), parameters)
        return connection

    def scope(self, column="tenant_id"):
        self._check_context()
        return (f"{column} IS NULL", ()) if self.tenant is None else (f"{column} = ?", (self.tenant,))

    def _check_context(self):
        from .paths import current_tenant_id_strict

        if current_tenant_id_strict() != self._context_tenant:
            raise SubmissionConflict("Submission tenant context changed; rebuild its authority-bound store")

    def _receipt(self, connection, key):
        scope, values = self.scope()
        row = self.execute(connection, "SELECT goal_id, fingerprint, state, detail FROM attachment_submissions "
                           f"WHERE submission_key = ? AND {scope}", (key, *values)).fetchone()
        if row is None:
            return None
        return {"key": key, "goal_id": int(row[0]), "fingerprint": row[1], "state": row[2],
                "detail": json.loads(self.open(row[3]))}

    def token_key(self, token, owner):
        if not isinstance(token, str) or not 1 <= len(token) <= 200 or any(ord(c) < 32 for c in token):
            raise SubmissionConflict("Invalid submission key")
        return hashlib.sha256(json.dumps([self.tenant, owner, token], separators=(",", ":")).encode()).hexdigest()

    def lookup(self, *, token, owner):
        """Read an exact owner/tenant receipt without admitting or retrying work."""
        key = self.token_key(token, owner)
        scope, values = self.scope()
        sql = "SELECT goal_id,fingerprint,state,detail FROM attachment_submissions "
        sql += f"WHERE submission_key=? AND owner=? AND {scope}"
        if self.sqlite:
            row = self.world._read_one(sql, (key, owner, *values))
        else:
            with self.world._tx() as cursor:
                row = self.execute(cursor, sql, (key, owner, *values)).fetchone()
        if row is None:
            return None
        return {"key": key, "goal_id": int(row[0]), "fingerprint": row[1], "state": row[2],
                "detail": json.loads(self.open(row[3]))}

    def create(self, *, token, fingerprint, owner, title, description, manifest,
               parent_id=None, domain="", project_id=None):
        key = self.token_key(token, owner)
        with self.transaction("submission:" + key) as connection:
            prior = self._receipt(connection, key)
            if prior is not None:
                if prior["fingerprint"] != fingerprint:
                    raise SubmissionConflict("Submission key already belongs to different content")
                prior["created"] = False
                return prior
            now = time.time()
            if parent_id is not None:
                parent_sql, parent_values = "SELECT id FROM goals WHERE id=?", (parent_id,)
                if not self.sqlite:
                    scope, scoped_values = self.scope()
                    parent_sql += " AND " + scope
                    parent_values += scoped_values
                if self.execute(connection, parent_sql, parent_values).fetchone() is None:
                    raise SubmissionConflict("Parent task is unavailable in this tenant")
            columns = "parent_id,title,description,status,created_at,updated_at,owner,domain,project_id"
            values = [parent_id, self.seal(title), self.seal(description), "draft", now, now, owner, domain, project_id]
            if not self.sqlite:
                columns += ",tenant_id"
                values.append(self.tenant)
            sql = f"INSERT INTO goals({columns}) VALUES({','.join('?' for _ in values)})"
            cursor = self.execute(connection, sql + ("" if self.sqlite else " RETURNING id"), tuple(values))
            goal_id = int(cursor.lastrowid if self.sqlite else cursor.fetchone()[0])
            detail = {"manifest": manifest, "message": "Submission admitted as a draft; preparing attachments."}
            self.execute(connection, "INSERT INTO attachment_submissions "
                         "(submission_key,tenant_id,owner,goal_id,fingerprint,state,detail,updated_at) "
                         "VALUES(?,?,?,?,?,'draft',?,?)",
                         (key, self.tenant, owner, goal_id, fingerprint, self.seal(json.dumps(detail)), now))
            return {**self._receipt(connection, key), "created": True}

    def update(self, key, *, state, message, coverage=None):
        if state not in {"draft", "admitted", "ready", "rejected"}:
            raise ValueError("Invalid submission state")
        with self.transaction("submission:" + key) as connection:
            receipt = self._receipt(connection, key)
            if receipt is None:
                raise LookupError("Unknown submission")
            if receipt["state"] in {"dispatching", "dispatched"}:
                return receipt
            detail = {**receipt["detail"], "message": message}
            if coverage is not None:
                detail["coverage"] = coverage
            self.execute(connection, "UPDATE attachment_submissions SET state=?,detail=?,updated_at=? WHERE submission_key=?",
                         (state, self.seal(json.dumps(detail)), time.time(), key))
            self.execute(connection, "UPDATE goals SET result=? WHERE id=? AND status='draft'",
                         (self.seal(message), receipt["goal_id"]))
            return self._receipt(connection, key)

    def claim_dispatch(self, key):
        with self.transaction("submission:" + key) as connection:
            receipt = self._receipt(connection, key)
            if receipt is None or receipt["state"] != "ready":
                return False
            changed = self.execute(connection, "UPDATE goals SET status='pending',updated_at=? WHERE id=? AND status='draft'",
                                   (time.time(), receipt["goal_id"]))
            if changed.rowcount != 1:
                return False
            self.execute(connection, "UPDATE attachment_submissions SET state='dispatching',updated_at=? WHERE submission_key=?",
                         (time.time(), key))
            return True

    def record_dispatch_intent(self, key, intent):
        """Retain reviewed runner context before the cross-store dispatch boundary."""
        with self.transaction("submission:" + key) as connection:
            receipt = self._receipt(connection, key)
            if receipt is None:
                raise LookupError("Unknown submission")
            if receipt["state"] in {"dispatching", "dispatched"}:
                return
            detail = {**receipt["detail"], "dispatch_intent": intent}
            self.execute(connection, "UPDATE attachment_submissions SET detail=?,updated_at=? WHERE submission_key=?",
                         (self.seal(json.dumps(detail)), time.time(), key))

    def for_goal(self, goal_id):
        scope, values = self.scope()
        sql = "SELECT submission_key,goal_id,fingerprint,state,detail FROM attachment_submissions "
        sql += f"WHERE goal_id=? AND {scope}"
        if self.sqlite:
            row = self.world._read_one(sql, (goal_id, *values))
        else:
            with self.world._tx() as cursor:
                row = self.execute(cursor, sql, (goal_id, *values)).fetchone()
        if row is None:
            return None
        return {"key": row[0], "goal_id": int(row[1]), "fingerprint": row[2], "state": row[3],
                "detail": json.loads(self.open(row[4]))}

    def admit(self, goal_id, records, *, publish=None):
        from .attachments import MAX_FILE_BYTES, MAX_GOAL_BYTES, AttachmentRejected

        if not records:
            return []
        if len(records) > 128:
            raise AttachmentRejected("Too many attachments in one batch")
        with self.transaction(f"attachments:{goal_id}") as connection:
            params = (goal_id,)
            sql = "SELECT id FROM goals WHERE id=?"
            if not self.sqlite:
                scope, values = self.scope()
                sql += " AND " + scope + " FOR UPDATE"
                params += values
            if self.execute(connection, sql, params).fetchone() is None:
                raise AttachmentRejected("Unknown goal for the active tenant")
            identities = [(r.filename, r.mime, int(r.size_bytes), r.sha256) for r in records]
            total = int(self.execute(connection, "SELECT COALESCE(SUM(size_bytes),0) FROM attachments WHERE goal_id=?",
                                     (goal_id,)).fetchone()[0])
            terms = " OR ".join("(filename=? AND mime=? AND size_bytes=? AND sha256=?)" for _ in identities)
            values = (goal_id, *(value for identity in identities for value in identity))
            existing = self.execute(connection, "SELECT id,filename,mime,size_bytes,sha256 FROM attachments "
                                    f"WHERE goal_id=? AND ({terms})", values).fetchall()
            known = {(row[1], row[2], int(row[3]), row[4]): int(row[0]) for row in existing}
            novel = set(identities) - known.keys()
            if any(not 0 < item[2] <= MAX_FILE_BYTES for item in identities) or total + sum(item[2] for item in novel) > MAX_GOAL_BYTES:
                raise AttachmentRejected("Per-goal attachment quota exceeded")
            ids = []
            for record, identity in zip(records, identities, strict=True):
                if identity not in known:
                    if publish is not None:
                        record = publish(record)
                    sql = "INSERT INTO attachments(goal_id,filename,mime,size_bytes,sha256,path,created_at) VALUES(?,?,?,?,?,?,?)"
                    cursor = self.execute(connection, sql + ("" if self.sqlite else " RETURNING id"),
                                          (goal_id, *identity[:3], identity[3], str(record.path), time.time()))
                    known[identity] = int(cursor.lastrowid if self.sqlite else cursor.fetchone()[0])
                ids.append(known[identity])
            return ids
