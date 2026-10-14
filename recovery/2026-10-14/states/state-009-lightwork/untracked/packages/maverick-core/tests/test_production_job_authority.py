"""Adverse-path proof for the local production job authority boundary."""
from __future__ import annotations

import json
import sqlite3

import pytest


@pytest.fixture
def production(monkeypatch, tmp_path):
    from maverick import tenant_quota_authority

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    # This module isolates the job-authority contract. Dedicated tenant-quota
    # tests cover production's mandatory Postgres admission boundary.
    monkeypatch.setattr(tenant_quota_authority, "authority_required", lambda: False)
    return tmp_path


def test_missing_authority_and_unreviewed_kind_fail_before_queue_io(production):
    from maverick.job_queue import JobAuthorityError, JobQueue

    database = production / "missing" / "jobs.db"
    queue = JobQueue(db_path=database)

    with pytest.raises(JobAuthorityError, match="reviewed policy"):
        queue.enqueue("caller_plugin_kind", {"owner": "user:mallory"})

    assert not database.exists()
    assert not database.parent.exists()


def test_reviewed_kind_without_trusted_context_fails_before_queue_io(production):
    from maverick.job_queue import JobAuthorityError, JobQueue

    database = production / "missing-context" / "jobs.db"
    queue = JobQueue(db_path=database)

    with pytest.raises(JobAuthorityError, match="trusted authority context"):
        queue.enqueue("run_goal", {"goal_id": 7})

    assert not database.exists()
    assert not database.parent.exists()


def test_caller_payload_cannot_spoof_bound_owner_before_queue_io(production):
    from maverick.connections import bind_principal
    from maverick.job_queue import (
        JobAuthorityError,
        JobQueue,
        bind_job_authority,
    )
    from maverick.paths import reset_tenant, set_tenant

    database = production / "spoof" / "jobs.db"
    queue = JobQueue(db_path=database)
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            with pytest.raises(JobAuthorityError, match="owner conflicts"):
                queue.enqueue(
                    "dashboard_goal_run",
                    {"owner": "user:mallory", "goal_id": 7},
                )
    finally:
        reset_tenant(token)

    assert not database.exists()
    assert not database.parent.exists()


def test_reviewed_job_captures_and_restores_immutable_authority(
    production,
    monkeypatch,
):
    from maverick import killswitch
    from maverick.connections import bind_principal, current_principal
    from maverick.job_queue import JobQueue, bind_job_authority
    from maverick.paths import current_tenant_id, reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick.worker import Worker

    monkeypatch.setattr(registry, "assert_tenant_active", lambda _tenant: None)
    queue = JobQueue(db_path=production / "valid" / "jobs.db")
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            job_id = queue.enqueue(
                "dashboard_goal_run",
                {"goal_id": 7, "user_id": "alice"},
            )
    finally:
        reset_tenant(token)

    queued = queue.get(job_id)
    assert queued is not None
    assert queued.authority is not None
    assert queued.authority.tenant == "acme"
    assert queued.authority.principal == queued.authority.owner == "user:alice"
    assert queued.authority.grant == {
        "allowed_suites": ["finance"],
        "capability": None,
    }
    assert queued.payload["owner"] == "user:alice"
    assert queued.payload["tenant"] == "acme"

    monkeypatch.setattr(killswitch, "critical_check", lambda: None)
    observed = []
    worker = Worker(queue=queue, idle_sleep=0)
    worker.register(
        "dashboard_goal_run",
        lambda job: observed.append(
            (
                current_tenant_id(),
                current_principal(),
                job.authority.grant["allowed_suites"],
            )
        ),
    )

    assert worker.run_once() is True
    assert observed == [("acme", "user:alice", ["finance"])]
    assert queue.get(job_id).status == "done"


def test_cron_successor_inherits_the_same_authority_snapshot(
    production,
    monkeypatch,
):
    from maverick import killswitch
    from maverick.connections import bind_principal
    from maverick.job_queue import JobQueue, bind_job_authority
    from maverick.paths import reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick.worker import Worker

    monkeypatch.setattr(registry, "assert_tenant_active", lambda _tenant: None)
    monkeypatch.setattr(killswitch, "critical_check", lambda: None)
    queue = JobQueue(db_path=production / "cron" / "jobs.db")
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            job_id = queue.enqueue(
                "dashboard_goal_run",
                {"goal_id": 7, "__cron__": "* * * * *"},
                run_at=0,
            )
    finally:
        reset_tenant(token)

    worker = Worker(queue=queue, idle_sleep=0)
    worker.register("dashboard_goal_run", lambda _job: None)
    assert worker.run_once() is True

    completed = queue.get(job_id)
    pending = queue.list(status="pending")
    assert completed is not None and completed.status == "done"
    assert completed.payload["__rearmed__"] is True
    assert len(pending) == 1
    successor = pending[0]
    assert "__rearmed__" not in successor.payload
    assert successor.authority == completed.authority


def test_explicit_capability_is_validated_snapshotted_and_restored(production):
    from maverick.capability import Capability
    from maverick.connections import bind_principal
    from maverick.job_queue import JobQueue, bind_job_authority
    from maverick.paths import reset_tenant, set_tenant
    from maverick.worker import _run_identity_kwargs

    capability = Capability(
        principal="agent:analyst",
        allow_tools=frozenset({"read_file"}),
        deny_tools=frozenset({"shell"}),
        max_risk="low",
        allow_paths=frozenset({"/reports/**"}),
        allow_hosts=frozenset({"api.example.test"}),
    )
    queue = JobQueue(db_path=production / "capability" / "jobs.db")
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
            capability=capability,
        ):
            job_id = queue.enqueue("run_goal", {"goal_id": 7})
    finally:
        reset_tenant(token)

    job = queue.get(job_id)
    assert job.authority.grant["capability"]["principal"] == "agent:analyst"
    restored = _run_identity_kwargs(job)["capability"]
    assert restored == capability


def test_payload_update_cannot_mutate_durable_authority(production):
    from maverick.connections import bind_principal
    from maverick.job_queue import (
        JobAuthorityError,
        JobQueue,
        bind_job_authority,
    )
    from maverick.paths import reset_tenant, set_tenant

    queue = JobQueue(db_path=production / "immutable" / "jobs.db")
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            job_id = queue.enqueue("run_goal", {"goal_id": 7})
    finally:
        reset_tenant(token)

    payload = dict(queue.get(job_id).payload)
    payload["owner"] = "user:mallory"
    with pytest.raises(JobAuthorityError, match="owner conflicts"):
        queue.set_payload(job_id, payload)

    durable = queue.get(job_id)
    assert durable.payload["owner"] == "user:alice"
    assert durable.authority.owner == "user:alice"

    payload = dict(durable.payload)
    payload["allowed_suites"] = ["finance", "legal"]
    with pytest.raises(JobAuthorityError, match="allowed_suites conflicts"):
        queue.set_payload(job_id, payload)

    durable = queue.get(job_id)
    assert durable.payload["allowed_suites"] == ["finance"]
    assert durable.authority.grant["allowed_suites"] == ["finance"]

    payload = dict(durable.payload)
    payload["goal_id"] = 8
    with pytest.raises(JobAuthorityError, match="protected fields are immutable"):
        queue.set_payload(job_id, payload)

    assert queue.get(job_id).payload["goal_id"] == 7


def test_builtin_goal_cannot_cross_its_captured_owner(
    production,
    monkeypatch,
):
    from maverick import killswitch, runner
    from maverick.connections import bind_principal
    from maverick.job_queue import JobQueue, bind_job_authority
    from maverick.paths import reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick.worker import Worker
    from maverick.world_model import close_world_if_owned, open_world

    monkeypatch.setattr(registry, "assert_tenant_active", lambda _tenant: None)
    monkeypatch.setattr(killswitch, "critical_check", lambda: None)
    invoked = []
    monkeypatch.setattr(
        runner,
        "run_goal_in_thread",
        lambda *_args, **_kwargs: invoked.append(True),
    )
    queue = JobQueue(db_path=production / "cross-owner" / "jobs.db")
    token = set_tenant("acme")
    try:
        world = open_world()
        try:
            goal_id = world.create_goal("victim", "must not run", owner="user:bob")
        finally:
            close_world_if_owned(world)
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            job_id = queue.enqueue("run_goal", {"goal_id": goal_id})
    finally:
        reset_tenant(token)

    assert Worker(queue=queue, idle_sleep=0).run_once() is True
    job = queue.get(job_id)
    assert invoked == []
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.last_error == "production job authority was refused before execution"


def test_claim_rejects_legacy_row_without_consuming_attempt(production, monkeypatch):
    from maverick.job_queue import JobAuthorityError, JobQueue
    from maverick.production_scope import production_surface_required

    database = production / "legacy" / "jobs.db"
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "0")
    queue = JobQueue(db_path=database)
    job_id = queue.enqueue("dashboard_goal_run", {"goal_id": 7})
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    assert production_surface_required() is True

    with pytest.raises(JobAuthorityError, match="snapshot is missing"):
        queue.claim()

    job = queue.get(job_id)
    assert job.status == "pending"
    assert job.attempts == 0


def test_claim_rejects_payload_identity_tamper_without_consuming_attempt(production):
    from maverick.connections import bind_principal
    from maverick.job_queue import (
        JobAuthorityError,
        JobQueue,
        bind_job_authority,
    )
    from maverick.paths import reset_tenant, set_tenant

    database = production / "tampered" / "jobs.db"
    queue = JobQueue(db_path=database)
    token = set_tenant("acme")
    try:
        with bind_principal("user:alice"), bind_job_authority(
            owner="user:alice",
            allowed_suites={"finance"},
        ):
            job_id = queue.enqueue("run_goal", {"goal_id": 7})
    finally:
        reset_tenant(token)

    with sqlite3.connect(database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM jobs WHERE id=?", (job_id,)
            ).fetchone()[0]
        )
        payload["owner"] = "user:mallory"
        connection.execute(
            "UPDATE jobs SET payload=? WHERE id=?",
            (json.dumps(payload), job_id),
        )

    with pytest.raises(JobAuthorityError, match="owner conflicts"):
        queue.claim()

    job = queue.get(job_id)
    assert job.status == "pending"
    assert job.attempts == 0


def test_startup_quarantine_terminalizes_legacy_and_custom_backlog(
    production,
    monkeypatch,
):
    from maverick.job_queue import JobQueue

    database = production / "quarantine" / "jobs.db"
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "0")
    queue = JobQueue(db_path=database)
    legacy_id = queue.enqueue("dashboard_goal_run", {"goal_id": 7})
    custom_id = queue.enqueue("plugin_job", {"tenant": "acme"})
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")

    assert queue.quarantine_invalid_production_jobs() == [legacy_id, custom_id]
    assert queue.get(legacy_id).status == "failed"
    assert queue.get(custom_id).status == "failed"
    assert queue.claim() is None


def test_startup_quarantine_migrates_a_pre_authority_database(
    production,
) -> None:
    from maverick.job_queue import JobQueue

    database = production / "old-schema" / "jobs.db"
    database.parent.mkdir(parents=True)
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
            "VALUES ('run_goal', '{\"goal_id\":7}', 0, 0, 0)"
        )

    queue = JobQueue(db_path=database)
    assert queue.quarantine_invalid_production_jobs() == [1]
    assert queue.get(1).status == "failed"
    with sqlite3.connect(database) as connection:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(jobs)")
        }
    assert {
        "authority_schema",
        "authority_tenant",
        "authority_principal",
        "authority_owner",
        "authority_mode",
        "authority_grant",
    } <= columns


def test_system_authority_cannot_mint_a_principal_only_kind(production):
    from maverick.connections import bind_principal
    from maverick.job_queue import (
        JobAuthorityError,
        JobQueue,
        bind_job_authority,
    )

    database = production / "system-spoof" / "jobs.db"
    queue = JobQueue(db_path=database)
    with bind_principal("service:dashboard-automation"), bind_job_authority(
        owner="service:dashboard-automation",
        allowed_suites=(),
        mode="system",
        tenant="control-plane:dashboard",
        principal="service:dashboard-automation",
    ):
        with pytest.raises(JobAuthorityError, match="requires principal authority"):
            queue.enqueue("dashboard_goal_run", {"goal_id": 7})

    assert not database.exists()


def test_reviewed_system_job_captures_fixed_service_authority(production):
    from maverick.connections import bind_principal
    from maverick.job_queue import JobQueue, bind_job_authority

    queue = JobQueue(db_path=production / "system" / "jobs.db")
    service = "service:dashboard-automation"
    with bind_principal(service), bind_job_authority(
        owner=service,
        allowed_suites=(),
        mode="system",
        tenant="control-plane:dashboard",
        principal=service,
    ):
        job_id = queue.enqueue("flow_delay_sweep", {})

    job = queue.get(job_id)
    assert job is not None
    assert job.authority is not None
    assert job.authority.mode == "system"
    assert job.authority.tenant == "control-plane:dashboard"
    assert job.authority.owner == job.authority.principal == service
    assert job.authority.grant == {"allowed_suites": [], "capability": None}
