"""Adversarial seams for the fleet-shared worker/scheduler authority.

These tests do not claim a live Postgres service.  A transaction-serialized
model cursor executes the adapter's operational transitions, while separate
schema/migration tests pin the Postgres-specific DDL (RLS, unique occurrence
index, and ``SKIP LOCKED`` claim).  Live-service coverage remains an integration
gate for deployment CI.
"""
from __future__ import annotations

import contextlib
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


class _Rows:
    def __init__(self, rows=(), *, rowcount: int = -1):
        self.rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _AuthorityState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.transaction_time = 0.0
        self.next_id = 1
        self.jobs: dict[int, dict[str, Any]] = {}
        self.polls: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.dispatch: dict[tuple[str, str, str], dict[str, Any]] = {}
        self.statements: list[str] = []

    @staticmethod
    def job_row(job: dict[str, Any]) -> tuple[Any, ...]:
        return (
            job["id"],
            job["kind"],
            job["payload"],
            job["run_at"],
            job["status"],
            job["attempt"],
            job["error"],
            job["created"],
            job["updated"],
            job["lease_token"],
            job["lease_expires"],
        )


class _Cursor:
    def __init__(self, state: _AuthorityState) -> None:
        self.state = state
        self.result = _Rows()

    @property
    def rowcount(self) -> int:
        return int(self.result.rowcount)

    def execute(self, sql: str, params=()):  # noqa: C901 - SQL model dispatcher
        params = tuple(params)
        normalized = " ".join(str(sql).lower().split())
        self.state.statements.append(normalized)
        if normalized.startswith("select extract(epoch from transaction_timestamp())"):
            self.result = _Rows([(self.state.transaction_time,)], rowcount=1)
            return self
        if "set_config('maverick.tenant'" in normalized:
            self.result = _Rows([(params[0],)], rowcount=1)
            return self

        if normalized.startswith("insert into job_queue_jobs"):
            recurring = "on conflict (tenant_id, queue_name, recurrence_key" in normalized
            if recurring:
                tenant, queue, kind, payload, run_at, recurrence, occurrence, created, updated = params
                existing = next(
                    (
                        job for job in self.state.jobs.values()
                        if job["tenant"] == tenant
                        and job["queue"] == queue
                        and job["recurrence"] == recurrence
                        and job["occurrence"] == occurrence
                    ),
                    None,
                )
                if existing is not None:
                    self.result = _Rows([], rowcount=0)
                    return self
            else:
                tenant, queue, kind, payload, run_at, created, updated = params
                recurrence = occurrence = None
            identifier = self.state.next_id
            self.state.next_id += 1
            self.state.jobs[identifier] = {
                "id": identifier,
                "tenant": tenant,
                "queue": queue,
                "kind": kind,
                "payload": payload,
                "run_at": float(run_at),
                "status": "pending",
                "attempt": 0,
                "error": "",
                "recurrence": recurrence,
                "occurrence": occurrence,
                "lease_token": "",
                "lease_expires": -1.0,
                "created": float(created),
                "updated": float(updated),
            }
            self.result = _Rows([(identifier,)], rowcount=1)
            return self

        if normalized.startswith("select id from job_queue_jobs") and "recurrence_key" in normalized:
            tenant, queue, recurrence, occurrence = params
            row = next(
                (
                    (job["id"],) for job in self.state.jobs.values()
                    if job["tenant"] == tenant
                    and job["queue"] == queue
                    and job["recurrence"] == recurrence
                    and job["occurrence"] == occurrence
                ),
                None,
            )
            self.result = _Rows([row] if row else [])
            return self

        if normalized.startswith("with candidate as"):
            tenant, queue, cutoff, token, expires, claimed, tenant2, queue2 = params
            assert (tenant, queue) == (tenant2, queue2)
            eligible = sorted(
                (
                    job for job in self.state.jobs.values()
                    if job["tenant"] == tenant
                    and job["queue"] == queue
                    and job["status"] == "pending"
                    and job["run_at"] <= cutoff
                ),
                key=lambda job: (job["run_at"], job["id"]),
            )
            if not eligible:
                self.result = _Rows([], rowcount=0)
                return self
            job = eligible[0]
            job.update(
                status="running",
                attempt=job["attempt"] + 1,
                lease_token=token,
                lease_expires=float(expires),
                updated=float(claimed),
            )
            self.result = _Rows([self.state.job_row(job)], rowcount=1)
            return self

        if normalized.startswith("update job_queue_jobs set updated_at="):
            updated, expires, tenant, queue, identifier, epoch, token = params
            job = self.state.jobs.get(identifier)
            matched = bool(
                job
                and job["tenant"] == tenant
                and job["queue"] == queue
                and job["status"] == "running"
                and job["attempt"] == epoch
                and job["lease_token"] == token
            )
            if matched:
                job.update(updated=float(updated), lease_expires=float(expires))
            self.result = _Rows(rowcount=int(matched))
            return self

        if normalized.startswith("update job_queue_jobs set status='done'"):
            updated, tenant, queue, identifier, epoch, token = params
            job = self.state.jobs.get(identifier)
            matched = self._running_match(job, tenant, queue, epoch, token)
            if matched:
                job.update(
                    status="done",
                    lease_token="",
                    lease_expires=-1.0,
                    updated=float(updated),
                )
            self.result = _Rows(rowcount=int(matched))
            return self

        if normalized.startswith("update job_queue_jobs set payload_text="):
            payload, updated, tenant, queue, identifier, epoch, token = params
            job = self.state.jobs.get(identifier)
            matched = self._running_match(job, tenant, queue, epoch, token)
            if matched:
                job.update(payload=payload, updated=float(updated))
            self.result = _Rows(rowcount=int(matched))
            return self

        if normalized.startswith("update job_queue_jobs set status=%s"):
            retrying = "run_at=%s" in normalized
            if retrying:
                status, run_at, error, updated, tenant, queue, identifier, epoch, token = params
            else:
                status, error, updated, tenant, queue, identifier, epoch, token = params
                run_at = None
            job = self.state.jobs.get(identifier)
            matched = self._running_match(job, tenant, queue, epoch, token)
            if matched:
                job.update(
                    status=status,
                    error=error,
                    lease_token="",
                    lease_expires=-1.0,
                    updated=float(updated),
                )
                if run_at is not None:
                    job["run_at"] = float(run_at)
            self.result = _Rows(rowcount=int(matched))
            return self

        if normalized.startswith("update job_queue_jobs set status='failed'"):
            error, updated, tenant, queue, now, max_attempts = params
            count = 0
            for job in self.state.jobs.values():
                if (
                    job["tenant"] == tenant
                    and job["queue"] == queue
                    and job["status"] == "running"
                    and job["lease_expires"] <= now
                    and job["attempt"] >= max_attempts
                ):
                    job.update(
                        status="failed", error=error, lease_token="",
                        lease_expires=-1.0, updated=float(updated),
                    )
                    count += 1
            self.result = _Rows(rowcount=count)
            return self

        if normalized.startswith("update job_queue_jobs set status='pending'"):
            run_at, updated, tenant, queue, now, max_attempts = params
            count = 0
            for job in self.state.jobs.values():
                if (
                    job["tenant"] == tenant
                    and job["queue"] == queue
                    and job["status"] == "running"
                    and job["lease_expires"] <= now
                    and job["attempt"] < max_attempts
                ):
                    job.update(
                        status="pending", run_at=float(run_at), lease_token="",
                        lease_expires=-1.0, updated=float(updated),
                    )
                    count += 1
            self.result = _Rows(rowcount=count)
            return self

        if normalized.startswith("select id, kind, payload_text"):
            tenant, queue, identifier = params
            job = self.state.jobs.get(identifier)
            rows = (
                [self.state.job_row(job)]
                if job and job["tenant"] == tenant and job["queue"] == queue
                else []
            )
            self.result = _Rows(rows)
            return self

        if normalized.startswith("insert into job_queue_poll_leases"):
            tenant, queue, key, now, token, expires, updated, due_cutoff, lease_cutoff = params
            address = (tenant, queue, key)
            poll = self.state.polls.get(address)
            if poll is None:
                poll = {
                    "next_due": float(now),
                    "epoch": 1,
                    "token": token,
                    "expires": float(expires),
                    "updated": float(updated),
                }
                self.state.polls[address] = poll
            elif (
                poll["next_due"] <= due_cutoff
                and (poll["token"] == "" or poll["expires"] <= lease_cutoff)
            ):
                poll.update(
                    epoch=poll["epoch"] + 1,
                    token=token,
                    expires=float(expires),
                    updated=float(updated),
                )
            else:
                self.result = _Rows([], rowcount=0)
                return self
            self.result = _Rows([(poll["epoch"], poll["token"])], rowcount=1)
            return self

        if normalized.startswith("update job_queue_poll_leases set next_due_at="):
            next_due, updated, tenant, queue, key, epoch, token = params
            poll = self.state.polls.get((tenant, queue, key))
            matched = bool(poll and poll["epoch"] == epoch and poll["token"] == token)
            if matched:
                poll.update(
                    next_due=float(next_due), token="", expires=-1.0,
                    updated=float(updated),
                )
            self.result = _Rows(rowcount=int(matched))
            return self

        if normalized.startswith("delete from job_queue_dispatch_claims"):
            tenant, queue, cutoff = params
            expired = [
                key for key, claim in self.state.dispatch.items()
                if key[:2] == (tenant, queue) and claim["expires"] <= cutoff
            ]
            for key in expired:
                self.state.dispatch.pop(key)
            self.result = _Rows(rowcount=len(expired))
            return self

        if normalized.startswith("insert into job_queue_dispatch_claims"):
            tenant, queue, message_id, nonce_hash, expires, claimed = params
            key = (tenant, queue, message_id)
            if key in self.state.dispatch:
                self.result = _Rows(rowcount=0)
                return self
            self.state.dispatch[key] = {
                "nonce_hash": nonce_hash,
                "expires": float(expires),
                "claimed": float(claimed),
            }
            self.result = _Rows(rowcount=1)
            return self

        raise AssertionError(f"unhandled authority SQL: {normalized}")

    @staticmethod
    def _running_match(job, tenant, queue, epoch, token) -> bool:
        return bool(
            job
            and job["tenant"] == tenant
            and job["queue"] == queue
            and job["status"] == "running"
            and job["attempt"] == epoch
            and job["lease_token"] == token
        )

    def fetchone(self):
        return self.result.fetchone()

    def fetchall(self):
        return self.result.fetchall()


def _factory(state: _AuthorityState):
    @contextlib.contextmanager
    def transaction():
        with state.lock:
            yield _Cursor(state)

    return transaction


def _queue(state: _AuthorityState, monkeypatch, tenant: str = "tenant-a"):
    from maverick.job_queue_postgres import PostgresJobQueue

    monkeypatch.setenv("MAVERICK_TENANT", tenant)
    return PostgresJobQueue(
        namespace="dashboard-automation",
        transaction_factory=_factory(state),
        check_legacy=False,
        ensure_schema=False,
        verify_schema=False,
        require_sealing=False,
    )


def test_atomic_claim_divides_one_job_between_replicas(monkeypatch):
    state = _AuthorityState()
    first = _queue(state, monkeypatch)
    second = _queue(state, monkeypatch)
    identifier = first.enqueue("work", {"tenant": "tenant-a"}, run_at=10.0)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda q: q.claim(now=20.0, lease_seconds=30), (first, second)))

    claimed = [job for job in claims if job is not None]
    assert [job.id for job in claimed] == [identifier]
    assert claimed[0].attempts == 1
    assert len(claimed[0].lease_token) == 32
    assert any("for update skip locked" in sql for sql in state.statements)


def test_recurring_occurrence_materializes_once_under_replica_race(monkeypatch):
    state = _AuthorityState()
    first = _queue(state, monkeypatch)
    second = _queue(state, monkeypatch)
    payload = {"__cron__": "*/5 * * * *", "schedule_id": "schedule-a"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(
            lambda q: q.enqueue_recurring("start_goal", payload, run_at=300.0),
            (first, second),
        ))

    assert ids[0] == ids[1]
    assert len(state.jobs) == 1


def test_stale_worker_cannot_heartbeat_mutate_or_settle_after_aba(monkeypatch):
    state = _AuthorityState()
    queue = _queue(state, monkeypatch)
    identifier = queue.enqueue("work", {"v": 1}, run_at=1.0)
    stale = queue.claim(now=10.0, lease_seconds=5.0)
    assert stale is not None
    # Expiry is inclusive: a lease ending exactly at ``now`` is reclaimable.
    assert queue.reclaim_stale(0.0, now=15.0, max_attempts=5) == 1
    current = queue.claim(now=17.0, lease_seconds=5.0)
    assert current is not None and current.attempts == 2

    assert not queue.heartbeat(
        identifier,
        now=18.0,
        lease_seconds=5.0,
        expected_attempts=stale.attempts,
        expected_lease_token=stale.lease_token,
    )
    assert not queue.set_payload(
        identifier,
        {"v": "stale"},
        expected_attempts=stale.attempts,
        expected_lease_token=stale.lease_token,
    )
    assert not queue.fail(
        identifier,
        "stale",
        expected_attempts=stale.attempts,
        expected_lease_token=stale.lease_token,
    )
    assert not queue.complete(
        identifier,
        expected_attempts=stale.attempts,
        expected_lease_token=stale.lease_token,
    )
    assert queue.complete(
        identifier,
        expected_attempts=current.attempts,
        expected_lease_token=current.lease_token,
    )
    settled = queue.get(identifier)
    assert settled is not None and settled.status == "done"
    assert settled.payload == {"v": 1}


def test_skewed_node_clocks_cannot_create_two_live_lease_owners(monkeypatch):
    """Only the shared Postgres clock decides expiry, never either host clock."""
    import time as node_time

    state = _AuthorityState()
    state.transaction_time = 100.0
    first = _queue(state, monkeypatch)
    second = _queue(state, monkeypatch)
    identifier = first.enqueue("work", {}, run_at=1.0)

    local_clock = {"value": 10_000.0}
    monkeypatch.setattr(node_time, "time", lambda: local_clock["value"])
    owner = first.claim(lease_seconds=10.0)
    assert owner is not None and owner.lease_expires_at == 110.0

    # The second host is arbitrarily far ahead, but the database transaction is
    # still at t=105. It cannot expire or claim the first host's live lease.
    local_clock["value"] = 1_000_000.0
    state.transaction_time = 105.0
    assert second.reclaim_stale(10.0) == 0
    assert second.claim(lease_seconds=10.0) is None
    assert state.jobs[identifier]["attempt"] == 1
    assert state.jobs[identifier]["lease_token"] == owner.lease_token

    # Once the shared clock crosses the exact expiry, normal fenced recovery
    # proceeds and a fresh attempt/token becomes the sole owner.
    state.transaction_time = 111.0
    assert second.reclaim_stale(10.0) == 1
    successor = second.claim(lease_seconds=10.0)
    assert successor is not None and successor.attempts == 2
    assert successor.lease_token != owner.lease_token


def test_production_queue_refuses_explicit_node_time_override(monkeypatch):
    import maverick.job_queue_postgres as pg

    state = _AuthorityState()
    selection = pg._Selection(True, False, "postgres://authority")
    monkeypatch.setattr(pg, "_selection", lambda: selection)
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    queue = pg.PostgresJobQueue(
        namespace="core",
        transaction_factory=_factory(state),
        check_legacy=False,
        ensure_schema=False,
        verify_schema=False,
        require_sealing=False,
        authority_token=pg._authority_token(selection),
    )
    queue.enqueue("work", {}, run_at=1.0)

    with pytest.raises(ValueError, match="only to isolated tests"):
        queue.claim(now=10.0, lease_seconds=5.0)


def test_dispatch_expiry_and_cleanup_use_shared_database_clock(monkeypatch):
    state = _AuthorityState()
    queue = _queue(state, monkeypatch)
    state.transaction_time = 100.0
    assert queue.claim_dispatch_envelope(
        "message-a", "nonce-a", expires_at=110.0,
    )

    # Before the database reaches expiry the claim is retained and replay is
    # rejected, irrespective of either application's local wall clock.
    state.transaction_time = 109.0
    assert queue.prune_dispatch_envelope_claims() == 0
    assert not queue.claim_dispatch_envelope(
        "message-a", "nonce-a", expires_at=110.0,
    )

    state.transaction_time = 110.0
    assert queue.prune_dispatch_envelope_claims() == 1
    # The same already-expired envelope cannot be re-consumed after cleanup.
    assert not queue.claim_dispatch_envelope(
        "message-a", "nonce-a", expires_at=110.0,
    )
    assert state.dispatch == {}


def test_recurring_materialization_uses_shared_clock_when_after_is_implicit():
    from maverick.scheduler import schedule_cron

    class SharedSchedule:
        def __init__(self) -> None:
            self.calls = []

        def authority_time(self):
            self.calls.append(("clock",))
            return 61.0

        def enqueue_recurring(self, kind, payload, *, run_at):
            self.calls.append((kind, payload, run_at))
            return 42

    queue = SharedSchedule()
    identifier, run_at = schedule_cron(
        queue, "* * * * *", "work", {"__cron__": "* * * * *"},
    )

    assert (identifier, run_at) == (42, 120.0)
    assert queue.calls == [
        ("clock",),
        ("work", {"__cron__": "* * * * *"}, 120.0),
    ]


def test_poll_occurrence_has_one_owner_and_stale_epoch_cannot_advance(monkeypatch):
    state = _AuthorityState()
    first = _queue(state, monkeypatch)
    second = _queue(state, monkeypatch)

    old = first.claim_poll("trigger-a", now=100.0, lease_seconds=10.0)
    assert old is not None
    assert second.claim_poll("trigger-a", now=105.0, lease_seconds=10.0) is None
    current = second.claim_poll("trigger-a", now=111.0, lease_seconds=10.0)
    assert current is not None and current.epoch == old.epoch + 1
    assert not first.finish_poll(
        old, interval_seconds=60.0, success=True, now=112.0,
    )
    assert second.finish_poll(
        current, interval_seconds=60.0, success=True, now=112.0,
    )
    assert first.claim_poll("trigger-a", now=171.0, lease_seconds=10.0) is None
    assert first.claim_poll("trigger-a", now=172.0, lease_seconds=10.0) is not None


def test_queue_rows_are_partitioned_by_bound_tenant(monkeypatch):
    state = _AuthorityState()
    tenant_a = _queue(state, monkeypatch, "tenant-a")
    a_id = tenant_a.enqueue_recurring(
        "work", {"__cron__": "* * * * *"}, run_at=60.0,
    )
    tenant_b = _queue(state, monkeypatch, "tenant-b")
    b_id = tenant_b.enqueue_recurring(
        "work", {"__cron__": "* * * * *"}, run_at=60.0,
    )
    assert a_id != b_id
    assert len(state.jobs) == 2

    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    assert tenant_a.get(a_id) is not None
    assert tenant_a.get(b_id) is None
    with pytest.raises(RuntimeError, match="tenant changed"):
        tenant_b.get(b_id)


def test_authority_dsn_drift_fails_before_mutation(monkeypatch):
    import maverick.job_queue_postgres as pg

    state = _AuthorityState()
    current = {"selection": pg._Selection(True, False, "postgres://authority-a")}
    monkeypatch.setattr(pg, "_selection", lambda: current["selection"])
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    queue = pg.PostgresJobQueue(
        namespace="core",
        transaction_factory=_factory(state),
        check_legacy=False,
        ensure_schema=False,
        verify_schema=False,
        require_sealing=False,
        authority_token=pg._authority_token(current["selection"]),
    )
    current["selection"] = pg._Selection(True, False, "postgres://authority-b")

    with pytest.raises(pg.SharedJobQueueUnavailable, match="changed after construction"):
        queue.enqueue("work", {}, run_at=1.0)
    assert state.jobs == {}


def _legacy_queue(path: Path, *, status: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO jobs(id, status) VALUES (1, ?)", (status,))


def test_postgres_cutover_refuses_live_jobs_markers_and_poll_lock(tmp_path):
    from maverick.job_queue_postgres import (
        JobQueueMigrationRequired,
        assert_no_legacy_job_authority,
    )

    live = tmp_path / "automation-jobs.db"
    _legacy_queue(live, status="pending")
    with pytest.raises(JobQueueMigrationRequired, match="explicit Postgres import"):
        assert_no_legacy_job_authority(live)

    terminal = tmp_path / "terminal.db"
    _legacy_queue(terminal, status="done")
    assert_no_legacy_job_authority(terminal)
    marker_dir = tmp_path / "automation-poll"
    marker_dir.mkdir()
    (marker_dir / "trigger-a").write_text("", encoding="utf-8")
    with pytest.raises(JobQueueMigrationRequired, match="poll markers"):
        assert_no_legacy_job_authority(terminal, poll_artifacts=(marker_dir,))

    empty_dir = tmp_path / "empty-poll"
    empty_dir.mkdir()
    lock = tmp_path / "automation-poll.lock"
    lock.touch()
    with pytest.raises(JobQueueMigrationRequired, match="poll lock"):
        assert_no_legacy_job_authority(
            terminal,
            poll_artifacts=(empty_dir, lock),
        )


def test_explicit_local_queue_cannot_bypass_postgres_selection(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg
    from maverick.job_queue import JobQueue

    monkeypatch.setattr(
        pg,
        "_selection",
        lambda: pg._Selection(True, True, "postgres://unreachable"),
    )
    local_path = tmp_path / "jobs.db"
    with pytest.raises(pg.SharedJobQueueUnavailable):
        JobQueue(db_path=local_path)
    assert not local_path.exists()


def test_explicit_local_queue_is_allowed_only_in_local_single_replica_mode(
    monkeypatch,
    tmp_path,
):
    import maverick.job_queue_postgres as pg
    from maverick.job_queue import JobQueue

    monkeypatch.setattr(pg, "_selection", lambda: pg._Selection(False, False))
    local = JobQueue(db_path=tmp_path / "jobs.db")
    assert local.is_shared is False
    assert local.enqueue("work", {}) == 1


def test_implicit_queue_never_falls_back_after_shared_open_failure(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg
    from maverick.job_queue import JobQueue

    def unavailable(**_kwargs):
        raise pg.SharedJobQueueUnavailable("database unavailable")

    monkeypatch.setattr(pg, "selected_postgres_job_queue", unavailable)
    local_path = tmp_path / "must-not-open.db"
    with pytest.raises(pg.SharedJobQueueUnavailable, match="database unavailable"):
        JobQueue(local_db_path=local_path)
    assert not local_path.exists()


def test_direct_real_dsn_requires_selector_authority_token(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg

    selection = pg._Selection(True, False, "postgres://selected")
    monkeypatch.setattr(pg, "_selection", lambda: selection)
    with pytest.raises(pg.SharedJobQueueUnavailable, match="selector authority token"):
        pg.PostgresJobQueue(
            namespace="core",
            dsn=selection.dsn,
            legacy_db_path=tmp_path / "jobs.db",
            check_legacy=False,
        )


def test_direct_real_dsn_cannot_differ_from_selected_connection(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg

    selection = pg._Selection(True, True, "postgres://selected")
    monkeypatch.setattr(pg, "_selection", lambda: selection)
    with pytest.raises(pg.SharedJobQueueUnavailable, match="DSN does not match"):
        pg.PostgresJobQueue(
            namespace="core",
            dsn="postgres://other",
            legacy_db_path=tmp_path / "jobs.db",
            check_legacy=False,
            authority_token=pg._authority_token(selection),
        )


def test_direct_real_dsn_is_rejected_under_local_policy(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg

    token = pg._authority_token(
        pg._Selection(True, False, "postgres://not-selected")
    )
    monkeypatch.setattr(pg, "_selection", lambda: pg._Selection(False, False))
    with pytest.raises(pg.SharedJobQueueUnavailable, match="deployment policy"):
        pg.PostgresJobQueue(
            namespace="core",
            dsn="postgres://not-selected",
            legacy_db_path=tmp_path / "jobs.db",
            check_legacy=False,
            authority_token=token,
        )


def test_direct_real_dsn_requires_active_cutover_protocol(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg

    selection = pg._Selection(True, False, "postgres://selected")
    monkeypatch.setattr(pg, "_selection", lambda: selection)
    with pytest.raises(pg.SharedJobQueueUnavailable, match="active cutover protocol"):
        pg.PostgresJobQueue(
            namespace="core",
            dsn=selection.dsn,
            legacy_db_path=tmp_path / "jobs.db",
            check_legacy=False,
            authority_token=pg._authority_token(selection),
        )


def test_injected_transactions_require_the_all_guards_off_test_seam():
    import maverick.job_queue_postgres as pg

    @contextlib.contextmanager
    def injected():
        yield object()

    with pytest.raises(pg.SharedJobQueueUnavailable, match="all-guards-off"):
        pg.PostgresJobQueue(
            namespace="core",
            transaction_factory=injected,
            check_legacy=False,
            ensure_schema=False,
            verify_schema=False,
            # Leaving sealing enabled makes this unlike the explicit unit seam.
        )


def test_selector_path_constructs_inside_cutover_and_publishes_marker(
    monkeypatch,
    tmp_path,
):
    import maverick.job_queue_postgres as pg

    selection = pg._Selection(True, True, "postgres://selected")
    monkeypatch.setattr(pg, "_selection", lambda: selection)
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setattr(pg.PostgresJobQueue, "_probe_sealing", lambda self: None)
    monkeypatch.setattr(
        pg.PostgresJobQueue,
        "_ensure_schema",
        lambda self: setattr(self, "_schema_ready", True),
    )
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=None))
    legacy = tmp_path / "jobs.db"

    queue = pg.selected_postgres_job_queue(
        namespace="core",
        legacy_db_path=legacy,
    )

    assert queue is not None and queue._check_legacy is True
    assert queue._cutover_fence is not None
    assert queue._cutover_fence.legacy_db_path == legacy.resolve()
    assert pg._cutover_marker_path(legacy).is_file()
    assert pg._active_cutover_fence() is None


def test_failed_postgres_admission_rolls_back_without_fencing_local_queue(
    monkeypatch,
    tmp_path,
):
    import maverick.job_queue_postgres as pg
    from maverick.job_queue import JobQueue

    selection = {"value": pg._Selection(True, True, "postgres://selected")}
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setattr(pg.PostgresJobQueue, "_probe_sealing", lambda self: None)
    monkeypatch.setattr(
        pg.PostgresJobQueue,
        "_ensure_schema",
        lambda self: (_ for _ in ()).throw(
            pg.SharedJobQueueUnavailable("schema admission failed")
        ),
    )
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=None))
    legacy = tmp_path / "jobs.db"

    with pytest.raises(pg.SharedJobQueueUnavailable, match="schema admission failed"):
        pg.selected_postgres_job_queue(
            namespace="core",
            legacy_db_path=legacy,
        )

    assert not pg._cutover_marker_path(legacy).exists()
    assert pg._active_cutover_fence() is None
    selection["value"] = pg._Selection(False, False)
    assert JobQueue(db_path=legacy).enqueue("work", {}) == 1


def test_selector_drift_during_admission_leaves_no_cutover_marker(
    monkeypatch,
    tmp_path,
):
    import maverick.job_queue_postgres as pg

    selection = {"value": pg._Selection(True, True, "postgres://selected")}
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    monkeypatch.setenv("MAVERICK_TENANT", "tenant-a")
    monkeypatch.setattr(pg.PostgresJobQueue, "_probe_sealing", lambda self: None)

    def finish_schema_probe(queue):
        queue._schema_ready = True
        selection["value"] = pg._Selection(False, False)

    monkeypatch.setattr(pg.PostgresJobQueue, "_ensure_schema", finish_schema_probe)
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=None))
    legacy = tmp_path / "jobs.db"

    with pytest.raises(
        pg.SharedJobQueueUnavailable,
        match="changed after construction",
    ):
        pg.selected_postgres_job_queue(
            namespace="core",
            legacy_db_path=legacy,
        )

    assert not pg._cutover_marker_path(legacy).exists()
    assert pg._active_cutover_fence() is None


def test_v37_migration_provisions_relational_fences_and_rls():
    from maverick.world_model import (
        MIGRATIONS as SQLITE_MIGRATIONS,
    )
    from maverick.world_model import (
        SCHEMA_VERSION,
    )
    from maverick.world_model_backends.postgres import MIGRATIONS

    migration = dict(MIGRATIONS)[37]
    rendered = "\n".join(migration).lower()
    assert SCHEMA_VERSION == 38 and SQLITE_MIGRATIONS[38] == []
    assert "create table if not exists job_queue_jobs" in rendered
    assert "create table if not exists job_queue_poll_leases" in rendered
    assert "unique index if not exists uq_mvk_jobs_occurrence" in rendered
    assert "force row level security" in rendered
    assert "lease_token" in rendered and "attempt_epoch" in rendered


def test_schema_verifier_rejects_weakened_or_mislabeled_indexes():
    import maverick.job_queue_postgres as pg

    rows = [
        (
            name,
            ("CREATE UNIQUE INDEX " if unique else "CREATE INDEX ")
            + f"{name} ON public.{table} USING btree ("
            + ", ".join(columns)
            + ")"
            + (f" WHERE ({predicate.replace('isnotnull', ' IS NOT NULL')})" if predicate else ""),
            True,
            True,
            True,
            True,
            "i",
            "p",
            "maverick_owner",
        )
        for name, (table, columns, unique, predicate) in pg._INDEX_SPECS.items()
    ]
    assert pg._indexes_are_exact(rows)

    weakened = [
        (row[0], row[1].replace("lease_expires_at", "updated_at"), *row[2:])
        for row in rows
    ]
    assert not pg._indexes_are_exact(weakened)
    non_unique = [
        (row[0], row[1].replace("CREATE UNIQUE INDEX", "CREATE INDEX"), *row[2:])
        for row in rows
    ]
    assert not pg._indexes_are_exact(non_unique)

    invalid = [(*row[:2], False, *row[3:]) for row in rows]
    assert not pg._indexes_are_exact(invalid)
    unlogged = [(*row[:7], "u", row[8]) for row in rows]
    assert not pg._indexes_are_exact(unlogged)
    wrong_owner = [(*row[:8], "runtime") for row in rows]
    assert not pg._indexes_are_exact(wrong_owner, owner="maverick_owner")
    included = list(rows)
    included[0] = (*included[0][:1], included[0][1] + " INCLUDE (payload_text)", *included[0][2:])
    assert not pg._indexes_are_exact(included)


def test_column_admission_pins_defaults_identity_and_generation():
    import maverick.job_queue_postgres as pg

    rows = []
    for name, (data_type, nullable) in pg._COLUMN_SHAPES[pg._JOBS_TABLE].items():
        generation = pg._IDENTITY_COLUMNS[pg._JOBS_TABLE].get(name)
        default = pg._COLUMN_DEFAULTS[pg._JOBS_TABLE].get(name)
        if default is not None:
            default = f"({default}::text)" if name in {"status", "last_error_text", "lease_token"} else default
        rows.append(
            (
                name,
                data_type,
                nullable,
                default,
                "YES" if generation else "NO",
                generation,
                "NEVER",
                None,
                "pg_catalog",
                pg._BUILTIN_UDT_NAMES[data_type],
                None,
                None,
                None,
                None,
            )
        )
    # No named collation is the information-schema representation of the
    # database-default collation used by the owner migration.
    assert pg._columns_are_exact(rows, pg._JOBS_TABLE)

    status = next(index for index, row in enumerate(rows) if row[0] == "status")
    wrong_default = list(rows)
    wrong_default[status] = (*wrong_default[status][:3], "'done'::text", *wrong_default[status][4:])
    assert not pg._columns_are_exact(wrong_default, pg._JOBS_TABLE)

    wrong_identity = list(rows)
    wrong_identity[0] = (*wrong_identity[0][:4], "NO", None, *wrong_identity[0][6:])
    assert not pg._columns_are_exact(wrong_identity, pg._JOBS_TABLE)

    tenant = next(index for index, row in enumerate(rows) if row[0] == "tenant_id")
    domain_tenant = list(rows)
    domain_tenant[tenant] = (
        *domain_tenant[tenant][:10],
        "public",
        "casefolded_tenant",
        *domain_tenant[tenant][12:],
    )
    assert not pg._columns_are_exact(domain_tenant, pg._JOBS_TABLE)

    custom_collation = list(rows)
    custom_collation[tenant] = (
        *custom_collation[tenant][:12],
        "public",
        "case_insensitive",
    )
    assert not pg._columns_are_exact(custom_collation, pg._JOBS_TABLE)

    wrong_builtin = list(rows)
    wrong_builtin[tenant] = (
        *wrong_builtin[tenant][:9],
        "varchar",
        *wrong_builtin[tenant][10:],
    )
    assert not pg._columns_are_exact(wrong_builtin, pg._JOBS_TABLE)


def test_constraint_admission_checks_definitions_and_validation_state():
    import maverick.job_queue_postgres as pg

    rows = [
        (name, f"CHECK (({definition}))", True, True, False, False, False)
        for name, definition in pg._CHECK_EXPRESSIONS[pg._JOBS_TABLE].items()
    ]
    tenant = next(index for index, row in enumerate(rows) if row[0] == "mvk_job_tenant_bound")
    status = next(index for index, row in enumerate(rows) if row[0] == "mvk_job_status_bound")
    rows[tenant] = (
        "mvk_job_tenant_bound",
        "CHECK (((char_length(tenant_id) >= 1) AND "
        "(char_length(tenant_id) <= 201)))",
        True,
        True,
        False,
        False,
        False,
    )
    rows[status] = (
        "mvk_job_status_bound",
        "CHECK ((status = ANY (ARRAY['pending'::text, 'running'::text, "
        "'done'::text, 'failed'::text])))",
        True,
        True,
        False,
        False,
        False,
    )
    assert pg._check_constraints_are_exact(rows, pg._JOBS_TABLE)

    weakened = list(rows)
    weakened[status] = (
        *weakened[status][:1],
        "CHECK (status IN ('pending','running','done'))",
        *weakened[status][2:],
    )
    assert not pg._check_constraints_are_exact(weakened, pg._JOBS_TABLE)
    unvalidated = list(rows)
    unvalidated[tenant] = (*unvalidated[tenant][:2], False, *unvalidated[tenant][3:])
    assert not pg._check_constraints_are_exact(unvalidated, pg._JOBS_TABLE)


def test_postgres18_not_null_catalog_rows_must_remain_enforced_and_exact():
    import maverick.job_queue_postgres as pg

    row = (
        "job_queue_jobs_tenant_id_not_null",
        ("tenant_id",),
        True,
        True,
        False,
        False,
        False,
    )
    assert pg._not_null_constraints_are_safe([row], pg._JOBS_TABLE)
    assert not pg._not_null_constraints_are_safe(
        [(*row[:3], False, *row[4:])],
        pg._JOBS_TABLE,
    )
    assert not pg._not_null_constraints_are_safe(
        [(row[0], ("recurrence_key",), *row[2:])],
        pg._JOBS_TABLE,
    )


class _CatalogCursor:
    def __init__(self, results):
        self._results = iter(results)
        self._current = _Rows()

    def execute(self, _sql, _params=()):
        self._current = next(self._results)
        return self

    def fetchone(self):
        return self._current.fetchone()

    def fetchall(self):
        return self._current.fetchall()


def test_relation_acl_admission_is_exact_and_owner_separated():
    import maverick.job_queue_postgres as pg

    granted = [
        privilege in pg._TABLE_PRIVILEGES[pg._POLLS_TABLE]
        for privilege in pg._ALL_TABLE_PRIVILEGES
    ]
    access = (
        "maverick_owner",
        False,
        False,
        "r",
        "p",
        True,
        True,
        True,
        True,
        *granted,
        *([False] * len(pg._ALL_TABLE_PRIVILEGES)),
    )
    cursor = _CatalogCursor([_Rows([access]), _Rows(), _Rows()])
    assert pg._table_access_owner(cursor, pg._POLLS_TABLE) == "maverick_owner"

    extra = list(access)
    extra[9 + pg._ALL_TABLE_PRIVILEGES.index("DELETE")] = True
    cursor = _CatalogCursor([_Rows([tuple(extra)]), _Rows(), _Rows()])
    assert pg._table_access_owner(cursor, pg._POLLS_TABLE) is None
    cursor = _CatalogCursor([_Rows([access]), _Rows([("SELECT",)]), _Rows()])
    assert pg._table_access_owner(cursor, pg._POLLS_TABLE) is None


@pytest.mark.parametrize(
    "catalog_flag",
    [5, 6, 7, 8],
    ids=["subclasses", "triggers", "rules", "inheritance"],
)
def test_relation_admission_rejects_shape_drift(catalog_flag):
    import maverick.job_queue_postgres as pg

    granted = [
        privilege in pg._TABLE_PRIVILEGES[pg._JOBS_TABLE]
        for privilege in pg._ALL_TABLE_PRIVILEGES
    ]
    access = [
        "maverick_owner",
        False,
        False,
        "r",
        "p",
        True,
        True,
        True,
        True,
        *granted,
        *([False] * len(pg._ALL_TABLE_PRIVILEGES)),
    ]
    access[catalog_flag] = False
    cursor = _CatalogCursor([_Rows([tuple(access)]), _Rows(), _Rows()])

    assert pg._table_access_owner(cursor, pg._JOBS_TABLE) is None


def test_sequence_admission_allows_only_usage_without_grant_option():
    import maverick.job_queue_postgres as pg

    access = (
        "maverick_owner",
        False,
        False,
        "S",
        "p",
        True,
        True,
        False,
        False,
        False,
        False,
        False,
    )
    properties = ("bigint", 1, 1, 9223372036854775807, 1, False, 1)
    cursor = _CatalogCursor([_Rows([access]), _Rows(), _Rows([properties])])
    assert pg._sequence_access_is_exact(cursor, "maverick_owner")

    extra_select = list(access)
    extra_select[7] = True
    cursor = _CatalogCursor([_Rows([tuple(extra_select)]), _Rows(), _Rows([properties])])
    assert not pg._sequence_access_is_exact(cursor, "maverick_owner")


def test_runtime_never_lazy_provisions_missing_queue_schema(monkeypatch):
    import maverick.job_queue_postgres as pg

    queue = _queue(_AuthorityState(), monkeypatch)
    monkeypatch.setattr(queue, "_schema_status", lambda: "missing")
    with pytest.raises(pg.SharedJobQueueUnavailable, match="migration v37"):
        queue._ensure_schema()


def test_cutover_gate_publishes_marker_before_old_local_queue_can_resume(
    monkeypatch,
    tmp_path,
):
    import maverick.job_queue_postgres as pg
    from maverick.file_lock import cross_process_lock
    from maverick.job_queue import JobQueue

    selection = {"value": pg._Selection(False, False)}
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    legacy = tmp_path / "jobs.db"
    local = JobQueue(db_path=legacy)
    selection["value"] = pg._Selection(True, True, "postgres://authority")
    token = pg._authority_token(selection["value"])
    marker = pg._cutover_marker_path(legacy)
    entered = threading.Event()

    def cutover():
        entered.set()
        with pg._postgres_cutover_guard(
            legacy,
            poll_artifacts=(),
            dsn_sha256=token.dsn_sha256,
        ):
            pass

    with ThreadPoolExecutor(max_workers=1) as pool:
        with cross_process_lock(marker, strict=True):
            future = pool.submit(cutover)
            assert entered.wait(2.0)
            assert not future.done()
        future.result(timeout=5.0)
    assert marker.is_file()
    with pytest.raises(pg.SharedJobQueueUnavailable, match="fenced"):
        local.counts()


def test_cutover_refuses_pending_state_without_leaving_a_marker(monkeypatch, tmp_path):
    import maverick.job_queue_postgres as pg
    from maverick.job_queue import JobQueue

    selection = {"value": pg._Selection(False, False)}
    monkeypatch.setattr(pg, "_selection", lambda: selection["value"])
    legacy = tmp_path / "jobs.db"
    local = JobQueue(db_path=legacy)
    local.enqueue("work", {})
    selection["value"] = pg._Selection(True, True, "postgres://authority")
    token = pg._authority_token(selection["value"])
    with pytest.raises(pg.JobQueueMigrationRequired, match="explicit Postgres import"):
        with pg._postgres_cutover_guard(
            legacy,
            poll_artifacts=(),
            dsn_sha256=token.dsn_sha256,
        ):
            pass
    assert not pg._cutover_marker_path(legacy).exists()


def test_cutover_marker_is_bound_to_selected_dsn(tmp_path):
    import maverick.job_queue_postgres as pg

    legacy = tmp_path / "jobs.db"
    first = pg._authority_token(pg._Selection(True, True, "postgres://first"))
    second = pg._authority_token(pg._Selection(True, True, "postgres://second"))
    with pg._postgres_cutover_guard(
        legacy,
        poll_artifacts=(),
        dsn_sha256=first.dsn_sha256,
    ):
        pass
    with pytest.raises(pg.SharedJobQueueUnavailable, match="does not match"):
        with pg._postgres_cutover_guard(
            legacy,
            poll_artifacts=(),
            dsn_sha256=second.dsn_sha256,
        ):
            pass
