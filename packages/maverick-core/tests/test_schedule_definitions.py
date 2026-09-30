"""Atomic schedule management invariants; no worker or provider is invoked."""
import pytest

from maverick.job_queue import JobQueue
from maverick.schedule_definitions import change_definition, list_definitions


def test_running_occurrence_does_not_hide_definition_and_pause_keeps_execution(tmp_path):
    queue = JobQueue(tmp_path / "jobs.db")
    first = queue.enqueue("noop", {"__cron__": "* * * * *", "owner": "user:a"}, run_at=100)
    running = queue.claim(now=100)
    definition = list_definitions(queue, owner="user:a")[0]
    assert definition["running"] and definition["id"] == first
    change_definition(queue, definition["definition_id"], expected_revision=definition["revision"],
                      state="paused", owner="user:a", actor="user:a")
    assert queue.get(first).status == "running"
    queue.rearm(first, expected_attempts=running.attempts, now=100)
    assert queue.list(status="pending") == []
    assert list_definitions(JobQueue(queue.db_path), owner="user:a")[0]["state"] == "paused"


def test_stale_edit_cannot_overwrite_or_mutate_admitted_payload(tmp_path):
    queue = JobQueue(tmp_path / "jobs.db")
    first = queue.enqueue("noop", {"__cron__": "* * * * *", "title": "Original", "owner": "user:a"}, run_at=100)
    queue.claim(now=100)
    definition = list_definitions(queue)[0]
    changed = change_definition(queue, definition["definition_id"], expected_revision=1,
                                state="active", title="Future", owner="user:a", actor="user:a")
    assert queue.get(first).payload["title"] == "Original"
    assert changed["title"] == "Future" and changed["revision"] == 2
    with pytest.raises(ValueError, match="changed"):
        change_definition(queue, definition["definition_id"], expected_revision=1,
                          state="retired", owner="user:a", actor="user:a")
    with pytest.raises(KeyError):
        change_definition(queue, definition["definition_id"], expected_revision=2,
                          state="retired", owner="user:other", actor="user:other")


def test_resume_schedules_only_future_and_retains_cancelled_occurrences(tmp_path, monkeypatch):
    monkeypatch.setattr("maverick.schedule_definitions.time.time", lambda: 1000)
    queue = JobQueue(tmp_path / "jobs.db")
    first = queue.enqueue("noop", {"__cron__": "* * * * *"}, run_at=100)
    definition = list_definitions(queue)[0]
    paused = change_definition(queue, definition["definition_id"], expected_revision=1, state="paused", actor="local")
    assert queue.get(first).status == "cancelled"
    resumed = change_definition(queue, definition["definition_id"], expected_revision=paused["revision"], state="active", actor="local")
    assert resumed["next_run"] > 1000
    assert queue.claim(now=1000) is None
    assert len(resumed["history"]) == 2


def test_definition_identity_and_occurrence_cursor_survive_history_purge(tmp_path):
    queue = JobQueue(tmp_path / "jobs.db")
    queue.enqueue("noop", {"__cron__": "* * * * *"}, run_at=100)
    initial = list_definitions(queue)[0]
    paused = change_definition(queue, initial["definition_id"], expected_revision=1, state="paused", actor="local")
    with queue._conn() as connection:
        connection.execute("DELETE FROM jobs")
    resumed = change_definition(queue, initial["definition_id"], expected_revision=paused["revision"], state="active", actor="local")
    assert resumed["definition_id"] == initial["definition_id"]
    job = queue.get(resumed["id"])
    assert job.occurrence == 1


def test_recovery_rejects_changed_evidence_without_replaying_history(tmp_path):
    from maverick.schedule_definitions import recovery_evidence

    queue = JobQueue(tmp_path / "jobs.db")
    first = queue.enqueue("noop", {"__cron__": "* * * * *", "owner": "user:a"}, run_at=100)
    with queue._conn() as connection:
        connection.execute("UPDATE jobs SET status='failed',attempts=1,recurrence_reconcile_required=1 WHERE id=?", (first,))
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (first,)).fetchone()
        reviewed = recovery_evidence(connection, row)
        connection.execute("UPDATE jobs SET last_error='new evidence' WHERE id=?", (first,))
    with pytest.raises(ValueError, match="evidence changed"):
        queue.reconcile_recurrence(first, expected_attempts=1, expected_evidence=reviewed,
                                   rearm=True, confirm=True, actor="user:a", rationale="Reviewed old worker shutdown")
    assert queue.get(first).status == "failed"
    assert queue.list(status="pending") == []
    with queue._conn() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (first,)).fetchone()
        reviewed = recovery_evidence(connection, row)
    child = queue.reconcile_recurrence(first, expected_attempts=1, expected_evidence=reviewed,
                                       rearm=True, confirm=True, actor="user:a", rationale="Reviewed old worker shutdown")
    assert queue.get(first).status == "failed" and queue.get(child).attempts == 0
