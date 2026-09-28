"""Transactional template-event goal admission and durable delivery receipts.

Receipts intentionally outlive goal erasure: a historical external event may
never recreate deleted customer data or repeat its work. They contain only a
digest, a local goal ID and delivery state, not event fields or credentials.
"""
from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_goal_admissions (
    admission_key TEXT PRIMARY KEY,
    trigger_key TEXT NOT NULL,
    goal_id INTEGER NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_pending ON event_goal_admissions(trigger_key,delivery_state);
CREATE TABLE IF NOT EXISTS event_trigger_batches (
    trigger_key TEXT PRIMARY KEY,
    batch_digest TEXT NOT NULL,
    sealed_batch TEXT NOT NULL
);
"""
POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_goal_admissions (
    tenant_id TEXT NOT NULL,
    admission_key TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    goal_id INTEGER NOT NULL,
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(tenant_id, admission_key)
);
CREATE INDEX IF NOT EXISTS idx_event_pending ON event_goal_admissions(tenant_id,trigger_key,delivery_state);
CREATE TABLE IF NOT EXISTS event_trigger_batches (
    tenant_id TEXT NOT NULL,
    trigger_key TEXT NOT NULL,
    batch_digest TEXT NOT NULL,
    sealed_batch TEXT NOT NULL,
    PRIMARY KEY(tenant_id,trigger_key)
);
"""


class EventDispatchUncertain(RuntimeError):
    """A non-idempotent dispatch needs operator reconciliation, never replay."""


@contextmanager
def _transaction(world, admission_key):
    from .paths import current_tenant_id_strict
    from .task_file_recovery import mutation_gate
    from .workspace_recovery import assert_bootstrap_start_allowed
    from .world_model import WorldModel
    from .world_model_backends.postgres import PostgresWorldModel, _active_tenant

    context_tenant = current_tenant_id_strict()
    with mutation_gate():
        assert_bootstrap_start_allowed()
        if isinstance(world, WorldModel):
            if world._event_admission_tenant != context_tenant:
                raise ValueError("event world belongs to a different tenant context")
            with world._writing() as conn:
                if not conn.in_transaction:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn, "?", "admission_key=?", (admission_key,), False
                if current_tenant_id_strict() != context_tenant:
                    raise ValueError("event admission tenant context changed")
        elif isinstance(world, PostgresWorldModel):
            tenant = _active_tenant() or ""
            lock = int.from_bytes(hashlib.sha256(
                f"{tenant}:{admission_key}".encode()).digest()[:8], "big", signed=True)
            with world._tx() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
                yield cur, "%s", "tenant_id=%s AND admission_key=%s", (tenant, admission_key), True
                if current_tenant_id_strict() != context_tenant:
                    raise ValueError("event admission tenant context changed")
        else:
            raise TypeError("template event admission requires a transactional world model")


def admit(world, admission_key: str, title: str, description: str, *,
          owner: str, origin: str, trigger_key: str) -> tuple[int, str]:
    """Commit goal, origin and event receipt together, or reuse the original."""
    _, goal_id, state = admit_batch(
        world, [(admission_key, title, description)], owner=owner, origin=origin,
        trigger_key=trigger_key)[0]
    return goal_id, state


def admit_batch(world, entries: list[tuple[str, str, str]], *, owner: str,
                origin: str, trigger_key: str) -> list[tuple[str, int, str]]:
    """Commit the entire bounded polled batch before dispatching any of it."""
    if len(entries) > 25:
        raise ValueError("event admission batch exceeds poll limit")
    admitted = []
    with _transaction(world, trigger_key) as (db, mark, where, scope, postgres):
        for admission_key, title, description in entries:
            key = (*scope[:-1], admission_key)
            result = db.execute(
                f"SELECT goal_id,delivery_state FROM event_goal_admissions WHERE {where}", key)
            row = (db if postgres else result).fetchone()
            if row is not None:
                admitted.append((admission_key, int(row[0]), str(row[1])))
                continue
            now = time.time()
            if postgres:
                from .world_model_backends.postgres import _active_tenant, _seal
                db.execute(
                    "INSERT INTO goals(title,description,status,created_at,updated_at,owner,tenant_id) "
                    "VALUES(%s,%s,'pending',%s,%s,%s,%s) RETURNING id",
                    (_seal(title), _seal(description), now, now, owner, _active_tenant()))
                goal_id = int(db.fetchone()[0])
            else:
                goal_id = world.create_goal(title, description, owner=owner)
            db.execute(
                f"INSERT INTO goal_origins(goal_id,kind,ref,created_at) VALUES({mark},{mark},{mark},{mark})",
                (goal_id, "event", origin, now))
            columns = "tenant_id,admission_key" if postgres else "admission_key"
            marks = ",".join([mark] * (len(key) + 3))
            db.execute(
                f"INSERT INTO event_goal_admissions({columns},goal_id,created_at,trigger_key) VALUES({marks})",
                (*key, goal_id, now, trigger_key))
            admitted.append((admission_key, goal_id, "pending"))
    return admitted


def transition(world, admission_key: str, before: str, after: str) -> bool:
    with _transaction(world, admission_key) as (db, mark, where, key, _):
        result = db.execute(
            f"UPDATE event_goal_admissions SET delivery_state={mark} "
            f"WHERE {where} AND delivery_state={mark}", (after, *key, before))
        return (db if result is None else result).rowcount == 1


def pending(world, trigger_key: str) -> list[tuple[str, int, str]]:
    """Repair admitted work even when its source has already dropped the item."""
    with _transaction(world, trigger_key) as (db, mark, _, key, postgres):
        tenant_clause = f"tenant_id={mark} AND " if postgres else ""
        args = (key[0], trigger_key) if postgres else (trigger_key,)
        result = db.execute(
            "SELECT admission_key,goal_id,delivery_state FROM event_goal_admissions WHERE "
            f"{tenant_clause}trigger_key={mark} AND delivery_state NOT IN ('delivered','retired') "
            "ORDER BY created_at,admission_key LIMIT 25", args)
        return [(str(r[0]), int(r[1]), str(r[2]))
                for r in (db if postgres else result).fetchall()]


def poll_batch(world, trigger_key: str, candidate: dict | None = None) -> dict | None:
    """Durably retain all observed events before any goal/flow dispatch.

    Only one unpublished batch exists for a trigger generation. Competing
    pollers reuse it. Event fields and cursor are sealed like goal content.
    """
    with _transaction(world, trigger_key) as (db, mark, _, scope, postgres):
        where = f"tenant_id={mark} AND trigger_key={mark}" if postgres else f"trigger_key={mark}"
        result = db.execute(f"SELECT batch_digest,sealed_batch FROM event_trigger_batches WHERE {where}", scope)
        row = (db if postgres else result).fetchone()
        if row is not None:
            if postgres:
                from .world_model_backends.postgres import _unseal as open_batch
            else:
                from .world_model import _dec_field as open_batch
            return {**json.loads(open_batch(row[1])), "batch_digest": row[0]}
        if candidate is None:
            return None
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > 8 * 1024 * 1024 or len(candidate["events"]) > 25:
            raise ValueError("event batch exceeds durable intake bound")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        if postgres:
            from .world_model_backends.postgres import _seal as seal_batch
        else:
            from .world_model import _enc_field as seal_batch
        columns = "tenant_id,trigger_key" if postgres else "trigger_key"
        marks = ",".join([mark] * (len(scope) + 2))
        db.execute(f"INSERT INTO event_trigger_batches({columns},batch_digest,sealed_batch) VALUES({marks})",
                   (*scope, digest, seal_batch(encoded)))
        return {**candidate, "batch_digest": digest}


def complete_poll_batch(world, trigger_key: str, digest: str) -> None:
    """Retire only the batch whose cursor publication was confirmed."""
    with _transaction(world, trigger_key) as (db, mark, _, scope, postgres):
        where = f"tenant_id={mark} AND trigger_key={mark}" if postgres else f"trigger_key={mark}"
        db.execute(f"DELETE FROM event_trigger_batches WHERE {where} AND batch_digest={mark}", (*scope, digest))
