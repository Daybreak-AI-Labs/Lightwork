"""Cross-process live-delivery deduplication for the durable job queue."""
from __future__ import annotations

import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from lightwork_test_support.spawn import (
    collect_process_messages,
    finish_spawn_processes,
    start_spawn_processes,
)


def _concurrent_enqueue(
    database: str,
    ready,
    start,
    results,
) -> None:
    """Spawn-safe contender used by the cross-process uniqueness proof."""
    from maverick.job_queue import JobQueue

    announced = False
    try:
        queue = JobQueue(db_path=Path(database))
        ready.put(("ready",))
        announced = True
        if not start.wait(timeout=15):
            raise TimeoutError("enqueue start barrier timed out")
        job_id = queue.enqueue(
            "run_goal",
            {"goal_id": 7, "options": {"alpha": 1, "beta": 2}},
            dedup_key="delivery:http:req-123",
        )
        results.put(("ok", job_id))
    except BaseException as exc:  # pragma: no cover - asserted in parent
        failure = ("error", type(exc).__name__, str(exc))
        (results if announced else ready).put(failure)


def test_exact_immediate_replay_returns_the_same_live_row(tmp_path) -> None:
    from maverick.job_queue import JobQueue

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    first = queue.enqueue(
        "run_goal",
        {"goal_id": 7, "options": {"alpha": 1, "beta": 2}},
        dedup_key="delivery:http:req-1",
    )
    # Dict insertion order and synthesized ``time.time()`` differ, but both
    # calls are the same immediate-delivery request.
    second = queue.enqueue(
        "run_goal",
        {"options": {"beta": 2, "alpha": 1}, "goal_id": 7},
        dedup_key="delivery:http:req-1",
    )

    assert second == first
    live = queue.list(status="pending")
    assert [job.id for job in live] == [first]
    assert live[0].dedup_key == "delivery:http:req-1"

    claimed = queue.claim()
    assert claimed is not None and claimed.id == first
    assert queue.enqueue(
        "run_goal",
        {"goal_id": 7, "options": {"alpha": 1, "beta": 2}},
        dedup_key="delivery:http:req-1",
    ) == first


@pytest.mark.parametrize(
    ("kind", "payload", "run_at"),
    [
        ("different_kind", {"goal_id": 7}, 100.0),
        ("run_goal", {"goal_id": 8}, 100.0),
        ("run_goal", {"goal_id": 7}, 101.0),
        ("run_goal", {"goal_id": 7}, None),
    ],
)
def test_live_key_reuse_with_request_mismatch_fails_closed(
    tmp_path,
    kind,
    payload,
    run_at,
) -> None:
    from maverick.job_queue import (
        JobDeduplicationConflict,
        JobQueue,
    )

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    first = queue.enqueue(
        "run_goal",
        {"goal_id": 7},
        run_at=100.0,
        dedup_key="delivery:http:req-mismatch",
    )

    with pytest.raises(JobDeduplicationConflict, match="different"):
        queue.enqueue(
            kind,
            payload,
            run_at=run_at,
            dedup_key="delivery:http:req-mismatch",
        )

    assert [job.id for job in queue.list(status="pending")] == [first]


def test_immediate_and_explicit_schedule_are_distinct_dedup_requests(tmp_path) -> None:
    from maverick.job_queue import JobDeduplicationConflict, JobQueue

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    queue.enqueue("run_goal", {"goal_id": 7}, dedup_key="delivery:schedule-mode")

    with pytest.raises(JobDeduplicationConflict, match="different schedule"):
        queue.enqueue(
            "run_goal",
            {"goal_id": 7},
            run_at=0.0,
            dedup_key="delivery:schedule-mode",
        )


def test_terminal_row_releases_key_for_explicit_retry(tmp_path) -> None:
    from maverick.job_queue import JobQueue

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    first = queue.enqueue(
        "run_goal",
        {"goal_id": 7},
        dedup_key="delivery:http:req-retry",
    )
    assert queue.complete(first)

    second = queue.enqueue(
        "run_goal",
        {"goal_id": 7},
        dedup_key="delivery:http:req-retry",
    )

    assert second != first
    assert queue.get(first).status == "done"
    assert queue.get(second).status == "pending"


def test_worker_payload_progress_does_not_change_original_dedup_request(tmp_path) -> None:
    from maverick.job_queue import JobQueue

    queue = JobQueue(db_path=tmp_path / "jobs.db")
    first = queue.enqueue(
        "run_goal",
        {"goal_id": 7},
        dedup_key="delivery:http:req-progress",
    )
    assert queue.claim().id == first
    assert queue.set_payload(first, {"goal_id": 7, "progress": "dispatched"})

    # Dedup binds the immutable enqueue request, not mutable worker bookkeeping.
    assert queue.enqueue(
        "run_goal",
        {"goal_id": 7},
        dedup_key="delivery:http:req-progress",
    ) == first


def test_partial_unique_index_and_trigger_enforce_live_identity(tmp_path) -> None:
    from maverick.job_queue import JobQueue

    database = tmp_path / "jobs.db"
    queue = JobQueue(db_path=database)
    first = queue.enqueue("a", {}, dedup_key="delivery:immutable")
    queue.complete(first)
    second = queue.enqueue("a", {}, dedup_key="delivery:immutable")
    assert second != first

    with sqlite3.connect(database) as connection:
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' "
            "AND name='idx_jobs_live_dedup'"
        ).fetchone()[0]
        assert "status IN ('pending','running')" in index_sql
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute(
                "UPDATE jobs SET dedup_key='changed' WHERE id=?",
                (second,),
            )

    assert queue.get(second).dedup_key == "delivery:immutable"


def test_additive_migration_preserves_legacy_rows_and_adds_dedup(tmp_path) -> None:
    from maverick.job_queue import JobQueue

    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              kind TEXT NOT NULL,
              payload TEXT NOT NULL,
              run_at REAL NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '',
              created_at REAL NOT NULL,
              updated_at REAL NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO jobs "
            "(kind, payload, run_at, created_at, updated_at) "
            "VALUES ('legacy', '{}', 0, 0, 0)"
        )

    queue = JobQueue(db_path=database)
    new_id = queue.enqueue("new", {}, dedup_key="delivery:migrated")

    assert queue.get(1).dedup_key == ""
    assert queue.get(new_id).dedup_key == "delivery:migrated"
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(jobs)")
        }
        indexes = {
            str(row[1]) for row in connection.execute("PRAGMA index_list(jobs)")
        }
    assert {"dedup_key", "dedup_fingerprint", "dedup_run_at"} <= columns
    assert "idx_jobs_live_dedup" in indexes


def test_concurrent_processes_return_the_exact_winning_live_row(
    tmp_path,
    monkeypatch,
) -> None:
    from maverick.job_queue import JobQueue

    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    database = tmp_path / "jobs.db"
    JobQueue(db_path=database).counts()  # finish additive DDL before contention

    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_concurrent_enqueue,
            args=(str(database), ready, start, results),
        )
        for _ in range(4)
    ]
    try:
        start_spawn_processes(processes, test_file=__file__)
        ready_rows = collect_process_messages(
            ready,
            processes,
            phase="enqueue contenders did not become ready",
        )
        assert {row[0] for row in ready_rows} == {"ready"}, ready_rows
        start.set()
        observed = collect_process_messages(
            results,
            processes,
            phase="enqueue contenders did not report outcomes",
        )
    finally:
        start.set()
        finish_spawn_processes(processes)
        ready.close()
        ready.join_thread()
        results.close()
        results.join_thread()

    assert [process.exitcode for process in processes] == [0, 0, 0, 0]
    assert {row[0] for row in observed} == {"ok"}, observed
    winner_ids = {int(row[1]) for row in observed}
    assert len(winner_ids) == 1
    live = JobQueue(db_path=database).list(status="pending")
    assert len(live) == 1
    assert {live[0].id} == winner_ids


@pytest.fixture
def production(monkeypatch, tmp_path):
    from maverick import tenant_quota_authority

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    # This fixture proves queue deduplication independently of the separately
    # tested mandatory production Postgres quota authority.
    monkeypatch.setattr(tenant_quota_authority, "authority_required", lambda: False)
    return tmp_path


def _production_enqueue(
    queue,
    *,
    suites,
    dedup_key: str,
) -> int:
    from maverick.connections import bind_principal
    from maverick.job_queue import bind_job_authority
    from maverick.paths import reset_tenant, set_tenant

    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites=suites,
        ):
            return queue.enqueue(
                "run_goal",
                {"goal_id": 7},
                dedup_key=dedup_key,
            )
    finally:
        reset_tenant(token)


def test_production_exact_authority_replay_returns_winner_and_mismatch_refuses(
    production,
) -> None:
    from maverick.job_queue import JobDeduplicationConflict, JobQueue

    queue = JobQueue(db_path=production / "authority" / "jobs.db")
    first = _production_enqueue(
        queue,
        suites={"finance"},
        dedup_key="delivery:production:req-1",
    )
    assert _production_enqueue(
        queue,
        suites={"finance"},
        dedup_key="delivery:production:req-1",
    ) == first

    with pytest.raises(JobDeduplicationConflict, match="payload, or authority"):
        _production_enqueue(
            queue,
            suites={"legal"},
            dedup_key="delivery:production:req-1",
        )

    durable = queue.get(first)
    assert durable is not None
    assert durable.authority.grant["allowed_suites"] == ["finance"]
    assert durable.status == "pending"


@pytest.mark.parametrize(
    "dedup_key",
    [" whitespace", "line\nbreak", "é" * 257, 7],
)
def test_production_invalid_dedup_fails_before_filesystem_or_schema(
    production,
    dedup_key,
) -> None:
    from maverick.job_queue import JobDeduplicationError, JobQueue

    database = production / "must-not-exist" / "jobs.db"
    with pytest.raises(JobDeduplicationError, match="dedup_key"):
        JobQueue(db_path=database).enqueue(
            "run_goal",
            {"goal_id": 7},
            dedup_key=dedup_key,
        )

    assert not database.exists()
    assert not database.parent.exists()
