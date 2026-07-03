"""Tamper-evident vendor audit log — a SHA-256 hash chain over every console
action (issue license, revoke, create customer, staff login, ...).

Each event's ``hash = sha256(prev_hash + canonical(event))`` so any edit or
deletion of a past row breaks the chain from that point on. This mirrors the
product's own audit chain: the console's *operations* are as auditable as the
product it governs — the same story we sell to a CISO, applied to ourselves.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from .models import AuditEvent


def _canon(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def record(conn: sqlite3.Connection, *, actor: str, action: str, target: str = "",
           detail: dict | None = None, at: float | None = None) -> AuditEvent:
    """Append one event, chaining it onto the current tail."""
    ts = at if at is not None else time.time()
    detail = detail or {}
    row = conn.execute("SELECT hash FROM audit ORDER BY id DESC LIMIT 1").fetchone()
    prev = row["hash"] if row else ""
    body = {"ts": round(ts, 6), "actor": actor, "action": action,
            "target": target, "detail": detail, "prev": prev}
    digest = hashlib.sha256((prev + _canon(body)).encode("utf-8")).hexdigest()
    cur = conn.execute(
        "INSERT INTO audit(ts, actor, action, target, detail_json, prev_hash, hash) "
        "VALUES (?,?,?,?,?,?,?)",
        (ts, actor, action, target, _canon(detail), prev, digest))
    conn.commit()
    return AuditEvent(id=cur.lastrowid, ts=ts, actor=actor, action=action,
                      target=target, detail=detail, prev_hash=prev, hash=digest)


def verify_chain(conn: sqlite3.Connection) -> tuple[bool, int]:
    """Recompute every hash. Returns (ok, first_broken_id_or_0)."""
    prev = ""
    for r in conn.execute("SELECT * FROM audit ORDER BY id ASC"):
        body = {"ts": round(r["ts"], 6), "actor": r["actor"], "action": r["action"],
                "target": r["target"], "detail": json.loads(r["detail_json"]),
                "prev": prev}
        expected = hashlib.sha256((prev + _canon(body)).encode("utf-8")).hexdigest()
        if expected != r["hash"] or r["prev_hash"] != prev:
            return False, r["id"]
        prev = r["hash"]
    return True, 0


def recent(conn: sqlite3.Connection, *, limit: int = 200) -> list[AuditEvent]:
    rows = conn.execute(
        "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [AuditEvent.from_row(r) for r in rows]
