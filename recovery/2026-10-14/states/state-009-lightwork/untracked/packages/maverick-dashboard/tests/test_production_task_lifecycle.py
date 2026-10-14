"""Adverse-path proof for production dashboard tasks and live cursors."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from fastapi import BackgroundTasks, HTTPException, Response
from fastapi.testclient import TestClient
from starlette.requests import Request

_OWNER = "user:alice"
_TENANT = "api:user:alice"


def _request(path: str = "/api/v1/goals/1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"origin", b"http://testserver")],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def _idempotency_request(path: str, key: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (b"origin", b"http://testserver"),
                (b"idempotency-key", key.encode("ascii")),
            ],
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import (
        killswitch,
        production_scope,
        tenant_quota_authority,
        world_model,
    )
    from maverick.connections import bind_principal
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import app as dashboard_app
    from maverick_dashboard import auth
    from maverick_dashboard import automation_queue as aq

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    # These lifecycle tests use the hermetic SQLite world instead of a live
    # Postgres service, but still exercise required quota admission. Supply the
    # complete policy and narrow only the quota module's backend selector.
    monkeypatch.setattr(tenant_quota_authority, "_production_required", lambda: False)
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_ENFORCE", "1")
    monkeypatch.setenv("MAVERICK_TENANT_QUOTA_POLICY_GENERATION", "1")
    monkeypatch.setenv(
        "MAVERICK_TENANT_QUOTA_LIMITS_JSON",
        json.dumps({
            "default": {
                "concurrent_runs": 100,
                "queued_jobs": 100,
                "daily_tokens": 1_000_000,
                "daily_spend_dollars": 10_000,
                "tool_calls": 1_000_000,
                "storage_bytes": 1_000_000_000,
            }
        }),
    )
    # RUN-16 exercises the fail-closed fleet HALT authority separately. These
    # tests isolate queue/cursor lifecycle and do not provision that backend.
    monkeypatch.setattr(killswitch, "critical_check", lambda: None)
    dashboard_app._world_cache.clear()
    monkeypatch.setattr(aq, "_queue", None)
    monkeypatch.setattr(aq, "_worker", None)
    monkeypatch.setattr(aq, "_worker_threads", [])
    monkeypatch.setattr(
        auth,
        "stored_automation_identity",
        lambda owner: (
            owner,
            owner.removeprefix("user:") or None,
            frozenset({"finance", "legal"}),
        ),
    )
    tenant_token = set_tenant(_TENANT)
    principal_context = bind_principal(_OWNER)
    principal_context.__enter__()
    try:
        yield dashboard_app, aq
    finally:
        principal_context.__exit__(None, None, None)
        reset_tenant(tenant_token)
        dashboard_app._world_cache.clear()


def _create_goal(world, title: str, description: str, *, owner: str = _OWNER) -> int:
    return int(world.create_goal(title, description, owner=owner))


def _create_idempotent_goal(world, *, key: str = "production-goal-request-1"):
    from maverick.api_idempotency import request_sha256
    from maverick_dashboard.task_dispatch import durable_goal_dispatch_payload

    normalized = {
        "description": "survive the dispatch boundary",
        "domain": "",
        "max_depth": 2,
        "max_dollars": 1.0,
        "max_wall_seconds": 30.0,
        "title": "transactional outbox",
    }
    return world.create_goal_idempotent(
        normalized["title"],
        normalized["description"],
        owner=_OWNER,
        operation="POST /api/v1/goals",
        principal_id=_OWNER,
        idempotency_key=key,
        request_sha256=request_sha256(normalized),
        dispatch_payload=durable_goal_dispatch_payload(
            max_dollars=normalized["max_dollars"],
            max_wall_seconds=normalized["max_wall_seconds"],
            max_depth=normalized["max_depth"],
            channel="api",
            user_id="alice",
            allowed_suites=frozenset({"finance"}),
        ),
    )


def _enqueue_schedule(aq, payload: dict, *, run_at=None) -> int:
    body = dict(payload)
    body.setdefault("owner", _OWNER)
    body.setdefault("allowed_suites", ["finance", "legal"])
    return aq._enqueue_reviewed_job(
        aq.DASHBOARD_SCHEDULE_KIND,
        body,
        run_at=run_at,
        owner=str(body["owner"]),
        allowed_suites=body["allowed_suites"],
    )


def _worker(aq):
    from maverick.worker import Worker

    worker = Worker(queue=aq.queue())
    worker.register(aq.DASHBOARD_GOAL_RUN_KIND, aq._handle_dashboard_goal_run)
    worker.register(aq.DASHBOARD_SCHEDULE_KIND, aq._handle_dashboard_schedule)
    worker.register_terminal_failure(
        aq.DASHBOARD_GOAL_RUN_KIND,
        aq._handle_dashboard_terminal_failure,
    )
    worker.register_terminal_failure(
        aq.DASHBOARD_SCHEDULE_KIND,
        aq._handle_dashboard_terminal_failure,
    )
    return worker


def test_production_dispatch_is_durable_before_ack(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard.task_dispatch import dispatch_goal

    world = dashboard_app._world()
    goal_id = _create_goal(world, "durable", "run after restart")
    background = BackgroundTasks()

    job_id = dispatch_goal(
        background,
        goal_id,
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        allowed_suites=frozenset({"finance"}),
    )

    assert background.tasks == []
    job = aq.queue().get(job_id)
    assert job is not None and job.status == "pending"
    assert job.kind == aq.DASHBOARD_GOAL_RUN_KIND
    assert job.payload["goal_id"] == goal_id
    assert job.payload["allowed_suites"] == ["finance"]
    assert job.authority is not None
    assert job.authority.tenant == _TENANT
    assert job.authority.owner == job.authority.principal == _OWNER


def test_live_goal_delivery_refuses_mismatched_retry_authority_or_budget(
    isolated,
) -> None:
    dashboard_app, aq = isolated
    from maverick.job_queue import JobDeduplicationConflict

    world = dashboard_app._world()
    goal_id = _create_goal(world, "immutable delivery", "bind exact dispatch")
    first = aq.enqueue_goal_run(
        goal_id,
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        allowed_suites={"finance"},
    )
    assert aq.enqueue_goal_run(
        goal_id,
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        allowed_suites={"finance"},
    ) == first

    with pytest.raises(JobDeduplicationConflict):
        aq.enqueue_goal_run(
            goal_id,
            max_dollars=2.0,
            max_wall_seconds=30.0,
            max_depth=2,
            allowed_suites={"finance"},
        )
    with pytest.raises(JobDeduplicationConflict):
        aq.enqueue_goal_run(
            goal_id,
            max_dollars=1.0,
            max_wall_seconds=30.0,
            max_depth=2,
            allowed_suites={"legal"},
        )

    live = aq._goal_jobs(goal_id, _TENANT, live_only=True)
    assert [job.id for job in live] == [first]
    assert live[0].payload["max_dollars"] == 1.0
    assert live[0].payload["allowed_suites"] == ["finance"]


def test_live_exact_replay_survives_current_grant_narrowing(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick import runner
    from maverick_dashboard import auth

    world = dashboard_app._world()
    goal_id = _create_goal(world, "stable replay", "narrow only at execution")
    first = aq.enqueue_goal_run(
        goal_id,
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        allowed_suites={"finance", "legal"},
    )

    monkeypatch.setattr(
        auth,
        "stored_automation_identity",
        lambda _owner: ("user:alice", "alice", frozenset({"finance"})),
    )
    assert aq.enqueue_goal_run(
        goal_id,
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        allowed_suites={"finance", "legal"},
    ) == first

    durable = aq.queue().get(first)
    assert durable is not None and durable.authority is not None
    assert durable.payload["allowed_suites"] == ["finance", "legal"]
    assert durable.authority.grant["allowed_suites"] == ["finance", "legal"]

    seen = {}

    def fake_run(gid: int, **kwargs) -> str:
        seen.update(kwargs)
        dashboard_app._world().set_goal_status(gid, "done")
        return "done"

    monkeypatch.setattr(runner, "run_goal_in_thread", fake_run)
    assert _worker(aq).run_once() is True
    assert seen["allowed_suites"] == frozenset({"finance"})


def test_ownerless_dashboard_goal_is_refused_before_queue_io(isolated) -> None:
    dashboard_app, aq = isolated

    world = dashboard_app._world()
    goal_id = world.create_goal("ownerless", "must not execute", owner="")

    with pytest.raises(RuntimeError, match="authenticated owner"):
        aq.enqueue_goal_run(goal_id)

    assert not aq._db_path().exists()


def test_direct_payload_identity_is_not_queue_authority(isolated) -> None:
    _dashboard_app, aq = isolated
    from maverick.job_queue import JobAuthorityError

    with pytest.raises(JobAuthorityError, match="trusted authority context"):
        aq.queue().enqueue(
            aq.DASHBOARD_GOAL_RUN_KIND,
            {
                "schema": "lightwork.dashboard-goal-task.v1",
                "goal_id": 7,
                "tenant": _TENANT,
                "owner": _OWNER,
                "principal_id": _OWNER,
                "allowed_suites": ["finance"],
            },
        )

    assert not aq._db_path().exists()


def test_system_job_uses_fixed_control_plane_authority(isolated) -> None:
    _dashboard_app, aq = isolated
    from maverick.connections import current_principal
    from maverick.paths import current_tenant_id
    from maverick.worker import Worker

    with aq._reviewed_job_authority(owner="", system=True):
        job_id = aq.queue().enqueue(aq.FLOW_SWEEP_KIND, {})

    queued = aq.queue().get(job_id)
    assert queued is not None
    assert queued.authority is not None
    assert queued.authority.mode == "system"
    assert queued.authority.tenant == "control-plane:dashboard"
    assert queued.authority.owner == "service:dashboard-automation"

    observed = []
    worker = Worker(queue=aq.queue(), idle_sleep=0)
    worker.register(
        aq.FLOW_SWEEP_KIND,
        lambda _job: observed.append((current_tenant_id(), current_principal())),
    )
    assert worker.run_once() is True
    assert observed == [(None, "service:dashboard-automation")]
    assert current_tenant_id() == _TENANT


def test_start_quarantines_ownerless_legacy_backlog_before_threads(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick import flow, production_scope

    world = dashboard_app._world()
    goal_id = world.create_goal("legacy", "no inherited authority", owner="")
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: False)
    job_id = aq.queue().enqueue(
        aq.DASHBOARD_GOAL_RUN_KIND,
        {
            "schema": "lightwork.dashboard-goal-task.v1",
            "goal_id": goal_id,
            "tenant": _TENANT,
        },
    )
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(flow, "enabled", lambda: False)
    monkeypatch.setattr(aq, "_worker_count", lambda: 0)

    assert aq.start() is True
    assert aq._worker_threads == []
    assert aq.queue().get(job_id).status == "failed"
    assert world.get_goal(goal_id).status == "blocked"
    aq.stop()


def test_pending_delivery_runs_after_worker_recreation(isolated, monkeypatch) -> None:
    dashboard_app, aq = isolated
    from maverick import runner

    world = dashboard_app._world()
    goal_id = _create_goal(world, "restart", "survive process restart")
    job_id = aq.enqueue_goal_run(goal_id)

    def fake_run(gid: int, **_kwargs) -> str:
        dashboard_app._world().set_goal_status(gid, "done", result="finished")
        return "done"

    monkeypatch.setattr(runner, "run_goal_in_thread", fake_run)
    assert _worker(aq).run_once() is True
    assert aq.queue().get(job_id).status == "done"
    assert world.get_goal(goal_id).status == "done"


def test_goal_delivery_cannot_cross_its_captured_owner(isolated) -> None:
    dashboard_app, aq = isolated

    world = dashboard_app._world()
    goal_id = _create_goal(
        world,
        "other owner",
        "must remain untouched",
        owner="user:bob",
    )
    job_id = aq._enqueue_reviewed_job(
        aq.DASHBOARD_GOAL_RUN_KIND,
        {
            "schema": "lightwork.dashboard-goal-task.v1",
            "goal_id": goal_id,
        },
        owner=_OWNER,
        allowed_suites={"finance"},
    )

    assert _worker(aq).run_once() is True
    assert aq.queue().get(job_id).status == "failed"
    assert world.get_goal(goal_id).status == "pending"


def test_worker_loss_is_quarantined_without_replay(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state

    world = dashboard_app._world()
    goal_id = _create_goal(world, "lost", "may have effects")
    job_id = aq.enqueue_goal_run(goal_id)
    claimed = aq.queue().claim()
    assert claimed is not None and claimed.id == job_id
    world.set_goal_status(goal_id, "active")

    counts = aq.reconcile_goal_jobs()

    assert counts["worker_lost"] == 1
    assert aq.queue().get(job_id).status == "failed"
    assert world.get_goal(goal_id).status == "blocked"
    assert load_goal_state(world, goal_id).kind is FailureKind.INDETERMINATE_EFFECT


def test_failed_preexecution_delivery_gets_typed_terminal_state(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state

    world = dashboard_app._world()
    goal_id = _create_goal(world, "unavailable", "never started")
    job_id = aq.enqueue_goal_run(goal_id)
    claimed = aq.queue().claim()
    assert claimed is not None
    assert aq.queue().fail(
        job_id,
        "worker unavailable",
        retry_after=None,
        max_attempts=0,
        expected_attempts=claimed.attempts,
    )

    counts = aq.reconcile_goal_jobs()

    assert counts["delivery_failed"] == 1
    assert world.get_goal(goal_id).status == "blocked"
    assert load_goal_state(world, goal_id).kind is FailureKind.PROVIDER_FAILURE


def test_identity_retry_exhaustion_terminalizes_suspended_tenant_goal(
    isolated, monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state
    from maverick.paths import reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick.worker import Worker

    tenant = "suspended-customer"
    token = set_tenant(tenant)
    try:
        world = dashboard_app._world()
        goal_id = _create_goal(world, "suspended", "must never execute")
        job_id = aq.enqueue_goal_run(goal_id, tenant=tenant)
    finally:
        reset_tenant(token)

    def suspended(_tenant: str) -> None:
        raise registry.TenantSuspended("suspended")

    monkeypatch.setattr(registry, "assert_tenant_active", suspended)
    worker = Worker(queue=aq.queue(), max_attempts=1, retry_after=0)
    worker.register(aq.DASHBOARD_GOAL_RUN_KIND, aq._handle_dashboard_goal_run)
    worker.register_terminal_failure(
        aq.DASHBOARD_GOAL_RUN_KIND,
        aq._handle_dashboard_terminal_failure,
    )

    assert worker.run_once() is True
    assert aq.queue().get(job_id).status == "failed"
    token = set_tenant(tenant)
    try:
        repaired_world = dashboard_app._world()
        assert repaired_world.get_goal(goal_id).status == "blocked"
        assert load_goal_state(repaired_world, goal_id).kind is FailureKind.PROVIDER_FAILURE
    finally:
        reset_tenant(token)


def test_startup_distinguishes_queued_backlog_from_dispatch_crash(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state

    world = dashboard_app._world()
    orphan_id = _create_goal(world, "orphan", "no delivery row")
    queued_id = _create_goal(world, "queued", "has delivery row")
    aq.enqueue_goal_run(queued_id)

    asyncio.run(dashboard_app._reclaim_orphans())

    assert world.get_goal(orphan_id).status == "blocked"
    assert load_goal_state(world, orphan_id).kind is FailureKind.PROVIDER_FAILURE
    assert world.get_goal(queued_id).status == "pending"
    assert aq.goal_run_job_status(queued_id) == ("pending",)


def test_durable_dispatch_authority_text_is_exact_and_never_normalized():
    from maverick_dashboard.task_dispatch import durable_goal_dispatch_payload

    exact = durable_goal_dispatch_payload(
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
        channel="api",
        user_id="Alice",
        allowed_suites=frozenset({"finance"}),
    )
    assert exact["user_id"] == "Alice"

    for field, value in (
        ("user_id", " Alice"),
        ("user_id", 7),
        ("allowed_suites", frozenset({" finance"})),
    ):
        kwargs = {
            "max_dollars": 1.0,
            "max_wall_seconds": 30.0,
            "max_depth": 2,
            "channel": "api",
            "user_id": "Alice",
            "allowed_suites": frozenset({"finance"}),
        }
        kwargs[field] = value
        with pytest.raises(ValueError, match="durable goal dispatch"):
            durable_goal_dispatch_payload(**kwargs)


def test_idempotent_dispatch_failure_preserves_pending_outbox_for_exact_replay(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard.task_dispatch import dispatch_idempotent_goal

    world = dashboard_app._world()
    claim = _create_idempotent_goal(world)
    real_enqueue = aq.enqueue_goal_run

    def refuse(*_args, **_kwargs):
        raise OSError("simulated queue outage")

    monkeypatch.setattr(aq, "enqueue_goal_run", refuse)
    with pytest.raises(HTTPException) as exc:
        dispatch_idempotent_goal(
            BackgroundTasks(),
            claim.goal_id,
            claim.dispatch,
        )

    assert exc.value.status_code == 503
    assert world.get_goal(claim.goal_id).status == "pending"
    assert world.get_goal_idempotent_dispatch(claim.goal_id) == claim.dispatch

    monkeypatch.setattr(aq, "enqueue_goal_run", real_enqueue)
    first = dispatch_idempotent_goal(
        BackgroundTasks(),
        claim.goal_id,
        claim.dispatch,
    )
    second = dispatch_idempotent_goal(
        BackgroundTasks(),
        claim.goal_id,
        claim.dispatch,
    )

    assert second == first
    live = aq._goal_jobs(claim.goal_id, _TENANT, live_only=True)
    assert [job.id for job in live] == [first]


def test_startup_repairs_transactional_goal_outbox_once(isolated) -> None:
    dashboard_app, aq = isolated

    world = dashboard_app._world()
    claim = _create_idempotent_goal(world, key="startup-repair-request")
    assert aq.goal_run_job_status(claim.goal_id) == ()

    asyncio.run(dashboard_app._reclaim_orphans())
    first = aq._goal_jobs(claim.goal_id, _TENANT, live_only=True)

    assert len(first) == 1
    assert world.get_goal(claim.goal_id).status == "pending"
    assert first[0].payload["goal_id"] == claim.goal_id
    assert first[0].payload["allowed_suites"] == ["finance"]

    asyncio.run(dashboard_app._reclaim_orphans())
    second = aq._goal_jobs(claim.goal_id, _TENANT, live_only=True)
    assert [job.id for job in second] == [first[0].id]


def test_startup_repairs_every_bound_principal_tenant(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick import entitlements
    from maverick.api_idempotency import request_sha256
    from maverick.connections import bind_principal
    from maverick.paths import reset_tenant, set_tenant
    from maverick.tenant import registry
    from maverick_dashboard.task_dispatch import durable_goal_dispatch_payload

    # A business-tenant roster must not make exact per-user API namespaces
    # disappear from startup recovery merely because they are intentionally
    # not provisioned as business tenants.
    monkeypatch.setattr(registry, "_production_required", lambda: False)
    monkeypatch.setattr(entitlements, "require", lambda _feature: True)
    registry.create_tenant("managed-business")

    created = []
    for tenant, owner, user_id in (
        ("api:user:alice", "user:alice", "alice"),
        ("api:user:bob", "user:bob", "bob"),
    ):
        token = set_tenant(tenant)
        with bind_principal(owner):
            try:
                world = dashboard_app._world()
                normalized = {
                    "description": f"recover {owner}",
                    "domain": "",
                    "max_depth": 2,
                    "max_dollars": 1.0,
                    "max_wall_seconds": 30.0,
                    "title": f"outbox {owner}",
                }
                claim = world.create_goal_idempotent(
                    normalized["title"],
                    normalized["description"],
                    owner=owner,
                    operation="POST /api/v1/goals",
                    principal_id=owner,
                    idempotency_key=f"startup-repair-{user_id}",
                    request_sha256=request_sha256(normalized),
                    dispatch_payload=durable_goal_dispatch_payload(
                        max_dollars=1.0,
                        max_wall_seconds=30.0,
                        max_depth=2,
                        channel="api",
                        user_id=user_id,
                        allowed_suites=frozenset({"finance"}),
                    ),
                )
                created.append((tenant, world, claim.goal_id))
            finally:
                reset_tenant(token)

    asyncio.run(dashboard_app._reclaim_orphans())

    for tenant, world, goal_id in created:
        jobs = aq._goal_jobs(goal_id, tenant, live_only=True)
        assert len(jobs) == 1
        assert jobs[0].authority is not None
        assert jobs[0].authority.tenant == tenant
        assert world.get_goal(goal_id).status == "pending"


def test_dispatch_refusal_is_503_and_typed(isolated, monkeypatch) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state
    from maverick_dashboard.task_dispatch import dispatch_goal

    world = dashboard_app._world()
    goal_id = _create_goal(world, "refused", "queue down")
    monkeypatch.setattr(
        aq,
        "enqueue_goal_run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk down")),
    )

    with pytest.raises(HTTPException) as exc:
        dispatch_goal(BackgroundTasks(), goal_id)

    assert exc.value.status_code == 503
    assert world.get_goal(goal_id).status == "blocked"
    assert load_goal_state(world, goal_id).kind is FailureKind.PROVIDER_FAILURE


def test_completed_exact_api_replay_does_not_create_new_delivery(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard import api

    monkeypatch.setattr(api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(api, "require_provider_or_400", lambda: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _OWNER)
    monkeypatch.setattr(
        api,
        "caller_suites",
        lambda _request: frozenset({"finance"}),
    )
    monkeypatch.setattr(
        api,
        "execution_user_id_from_request",
        lambda _request: "alice",
    )
    monkeypatch.setattr(
        dashboard_app,
        "check_goal_rate_limit",
        lambda *_args, **_kwargs: None,
    )

    headers = [
        (b"origin", b"http://testserver"),
        (b"idempotency-key", b"completed-goal-replay"),
    ]
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/goals",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )
    payload = api.GoalIn(
        title="completed replay",
        description="must not be redispatched",
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
    )
    first_response = Response()
    first = asyncio.run(
        api.create_goal(request, payload, BackgroundTasks(), first_response)
    )
    deliveries = aq._goal_jobs(first.id, _TENANT, live_only=True)
    assert len(deliveries) == 1
    assert aq.queue().complete(deliveries[0].id)
    dashboard_app._world().set_goal_status(first.id, "done", result="finished")

    replay_response = Response()
    replay = asyncio.run(
        api.create_goal(request, payload, BackgroundTasks(), replay_response)
    )

    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert aq._goal_jobs(first.id, _TENANT, live_only=True) == []
    all_deliveries = aq._goal_jobs(first.id, _TENANT, live_only=False)
    assert [job.id for job in all_deliveries] == [deliveries[0].id]


def test_production_compose_exact_replay_and_mismatch_are_durable(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard import api

    monkeypatch.setattr(api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(api, "require_provider_or_400", lambda: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _OWNER)
    monkeypatch.setattr(
        api,
        "caller_suites",
        lambda _request: frozenset({"finance"}),
    )
    monkeypatch.setattr(
        api,
        "execution_user_id_from_request",
        lambda _request: "alice",
    )
    monkeypatch.setattr(
        dashboard_app,
        "check_goal_rate_limit",
        lambda *_args, **_kwargs: None,
    )

    request = _idempotency_request(
        "/api/v1/goals/compose",
        "compose-production-replay",
    )
    payload = api.ComposeIn(
        title="compose once",
        steps=["collect", "review"],
        budget_dollars=1.0,
    )
    first_response = Response()
    first = asyncio.run(
        api.compose_goal(request, payload, BackgroundTasks(), first_response)
    )
    replay_response = Response()
    replay = asyncio.run(
        api.compose_goal(request, payload, BackgroundTasks(), replay_response)
    )

    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    live = aq._goal_jobs(first.id, _TENANT, live_only=True)
    assert len(live) == 1
    assert len(dashboard_app._world().list_goals(limit=100)) == 1

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            api.compose_goal(
                request,
                api.ComposeIn(title="different compose"),
                BackgroundTasks(),
                Response(),
            )
        )
    assert exc.value.status_code == 409
    assert len(dashboard_app._world().list_goals(limit=100)) == 1


def test_production_child_exact_replay_and_mismatch_are_structural_only(
    isolated,
    monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard import api

    monkeypatch.setattr(api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(api, "assert_goal_access", lambda *_args: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _OWNER)
    monkeypatch.setattr(
        dashboard_app,
        "check_goal_rate_limit",
        lambda *_args, **_kwargs: None,
    )

    world = dashboard_app._world()
    parent_id = _create_goal(world, "parent", "structural parent")
    request = _idempotency_request(
        f"/api/v1/goals/{parent_id}/children",
        "child-production-replay",
    )
    payload = api.ChildIn(title="one child", description="details")
    first_response = Response()
    first = asyncio.run(
        api.create_child_goal(request, parent_id, payload, first_response)
    )
    replay_response = Response()
    replay = asyncio.run(
        api.create_child_goal(request, parent_id, payload, replay_response)
    )

    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert world.get_goal(first.id).parent_id == parent_id
    assert len(world.list_goals(limit=100)) == 2
    assert aq.goal_run_job_status(first.id) == ()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            api.create_child_goal(
                request,
                parent_id,
                api.ChildIn(title="different child", description="details"),
                Response(),
            )
        )
    assert exc.value.status_code == 409
    assert len(world.list_goals(limit=100)) == 2


def test_cancel_removes_delivery_and_resume_creates_a_new_one(
    isolated, monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state
    from maverick_dashboard import api

    monkeypatch.setattr(api, "_ground_outcome", lambda *_args, **_kwargs: None)
    world = dashboard_app._world()
    goal_id = _create_goal(world, "control", "cancel and resume")
    first_job = aq.enqueue_goal_run(goal_id)

    asyncio.run(api.cancel_goal(_request(), goal_id))

    assert aq.queue().get(first_job) is None
    assert world.get_goal(goal_id).status == "cancelled"
    assert load_goal_state(world, goal_id).kind is FailureKind.CANCELLATION

    asyncio.run(api.resume_goal(goal_id, _request(), BackgroundTasks()))

    assert world.get_goal(goal_id).status == "pending"
    live = aq._goal_jobs(goal_id, _TENANT, live_only=True)
    assert len(live) == 1 and live[0].id != first_job


def test_resume_refuses_while_previous_worker_still_owns_delivery(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick_dashboard import api

    world = dashboard_app._world()
    goal_id = _create_goal(world, "still stopping", "do not overlap")
    aq.enqueue_goal_run(goal_id)
    assert aq.queue().claim() is not None
    world.set_goal_status(goal_id, "cancelled")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api.resume_goal(goal_id, _request(), BackgroundTasks()))

    assert exc.value.status_code == 409
    assert world.get_goal(goal_id).status == "cancelled"


def test_execution_revalidates_and_narrows_stored_suites(
    isolated, monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick import runner
    from maverick_dashboard import auth

    world = dashboard_app._world()
    goal_id = _create_goal(world, "authority", "narrow me")
    aq.enqueue_goal_run(goal_id, allowed_suites={"finance", "legal"})
    seen = {}

    monkeypatch.setattr(
        auth,
        "stored_automation_identity",
        lambda _owner: ("user:alice", "alice", frozenset({"finance"})),
    )

    def fake_run(gid: int, **kwargs) -> str:
        seen.update(kwargs)
        dashboard_app._world().set_goal_status(gid, "done")
        return "done"

    monkeypatch.setattr(runner, "run_goal_in_thread", fake_run)
    assert _worker(aq).run_once() is True
    assert seen["allowed_suites"] == frozenset({"finance"})
    assert seen["user_id"] == "alice"


def test_flow_execution_keeps_queue_grant_as_an_additional_floor(isolated) -> None:
    _dashboard_app, aq = isolated
    from types import SimpleNamespace

    run = SimpleNamespace(allowed_suites=["finance", "legal"])
    job = SimpleNamespace(
        authority=SimpleNamespace(
            grant={"allowed_suites": ["finance"], "capability": None}
        )
    )

    execution_view = aq._flow_run_with_job_suite_floor(run, job)

    assert execution_view is not run
    assert run.allowed_suites == ["finance", "legal"]
    assert execution_view.allowed_suites == ["finance"]


def test_scheduled_occurrence_persists_goal_identity_before_execution(
    isolated, monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick import runner

    payload = aq.schedule_task_payload(
        {
            "text": "produce the report",
            "title": "report",
            "schedule_id": "schedule-one",
        }
    )
    job_id = _enqueue_schedule(aq, payload)

    def fake_run(goal_id: int, **_kwargs) -> str:
        dashboard_app._world().set_goal_status(goal_id, "done", result="ready")
        return "done"

    monkeypatch.setattr(runner, "run_goal_in_thread", fake_run)
    assert _worker(aq).run_once() is True

    job = aq.queue().get(job_id)
    assert job is not None and job.status == "done"
    assert job.payload["delivery_claimed"] is True
    goal_id = int(job.payload["goal_id"])
    assert dashboard_app._world().get_goal(goal_id).status == "done"
    origins = dashboard_app._world().goals_for_origin("schedule", "schedule-one")
    assert [goal.id for goal in origins] == [goal_id]


def test_schedule_worker_loss_quarantines_known_goal(isolated) -> None:
    dashboard_app, aq = isolated
    from maverick.failure_taxonomy import FailureKind, load_goal_state

    world = dashboard_app._world()
    goal_id = _create_goal(world, "scheduled", "possibly started")
    world.set_goal_status(goal_id, "active")
    payload = aq.schedule_task_payload(
        {
            "text": "possibly started",
            "title": "scheduled",
            "schedule_id": "schedule-two",
        }
    )
    payload.update({"delivery_claimed": True, "goal_id": goal_id})
    job_id = _enqueue_schedule(aq, payload)
    assert aq.queue().claim() is not None

    counts = aq.reconcile_schedule_jobs()

    assert counts["worker_lost"] == 1
    assert aq.queue().get(job_id).status == "failed"
    assert world.get_goal(goal_id).status == "blocked"
    assert load_goal_state(world, goal_id).kind is FailureKind.INDETERMINATE_EFFECT


def test_schedule_creation_claim_without_goal_id_never_replays(isolated) -> None:
    dashboard_app, aq = isolated
    payload = aq.schedule_task_payload(
        {
            "text": "do not duplicate",
            "title": "claimed",
            "schedule_id": "schedule-three",
        }
    )
    payload["delivery_claimed"] = True
    job_id = _enqueue_schedule(aq, payload)

    assert _worker(aq).run_once() is True

    assert aq.queue().get(job_id).status == "failed"
    assert dashboard_app._world().list_goals(limit=10) == []


def test_schedule_owner_revocation_cancels_rearmed_successor(
    isolated, monkeypatch,
) -> None:
    dashboard_app, aq = isolated
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import auth

    tenant_token = set_tenant("api:user:revoked")
    try:
        payload = aq.schedule_task_payload(
            {
                "text": "must not run",
                "title": "revoked",
                "schedule_id": "revoked-owner",
                "owner": "user:revoked",
                "__cron__": "* * * * *",
            }
        )
        job_id = _enqueue_schedule(aq, payload, run_at=0)
    finally:
        reset_tenant(tenant_token)

    def revoked(_owner: str):
        raise auth.AutomationAuthorizationError("revoked")

    monkeypatch.setattr(auth, "stored_automation_identity", revoked)
    assert _worker(aq).run_once() is True

    assert aq.queue().get(job_id).status == "failed"
    assert not any(
        job.kind == aq.DASHBOARD_SCHEDULE_KIND
        for job in aq.queue().list(status="pending", limit=100)
    )
    assert dashboard_app._world().goals_for_origin(
        "schedule", "revoked-owner"
    ) == []


@pytest.mark.parametrize(
    ("since", "last_event_id", "expected"),
    [(0, None, 0), (2, "7", 7), (9, "3", 9)],
)
def test_numeric_cursor_is_monotonic(since, last_event_id, expected) -> None:
    from maverick_dashboard.stream_cursor import numeric_cursor

    assert numeric_cursor(since, last_event_id) == expected


@pytest.mark.parametrize("value", ["bad", "-1", str(1 << 63)])
def test_numeric_cursor_rejects_ambiguous_reconnect(value: str) -> None:
    from maverick_dashboard.stream_cursor import numeric_cursor

    with pytest.raises(ValueError):
        numeric_cursor(0, value)


def test_flow_authority_loss_maps_to_retryable_503(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick.flow import store
    from maverick_dashboard.app import app

    monkeypatch.setattr(
        store,
        "load_run",
        lambda _run_id: (_ for _ in ()).throw(
            store.FlowAuthorityUnavailable("database unavailable")
        ),
    )
    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/v1/flows/runs/authority-loss"
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert response.json()["code"] == "flow_authority_unavailable"


def test_v1_goal_stream_resumes_from_durable_header(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    dashboard_app._world_cache.clear()
    world = dashboard_app._world()
    goal_id = world.create_goal("stream", "resume")
    world.append_event(goal_id, "one", "status", "already delivered")
    cursor = world.append_event(goal_id, "two", "status", "resume here")
    world.append_event(goal_id, "three", "status", "new event")
    world.set_goal_status(goal_id, "done")

    response = TestClient(dashboard_app.app).get(
        f"/api/v1/goals/{goal_id}/events/stream",
        headers={"Last-Event-ID": str(cursor)},
    )

    assert response.status_code == 200
    assert "new event" in response.text
    assert "already delivered" not in response.text
    assert "resume here" not in response.text
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_flow_stream_uses_restart_stable_snapshot_cursor(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick.flow import store
    from maverick_dashboard.app import app

    store.save_run(
        store.FlowRun(
            run_id="cursor-run",
            flow_id="demo",
            status="completed",
            data={"result": "ok"},
        )
    )
    client = TestClient(app)
    first = client.get("/api/v1/flows/runs/cursor-run/events")

    assert first.status_code == 200
    match = re.search(r"^id: ([0-9a-f]{64})$", first.text, flags=re.MULTILINE)
    assert match is not None
    cursor = match.group(1)

    resumed = client.get(
        "/api/v1/flows/runs/cursor-run/events",
        headers={"Last-Event-ID": cursor},
    )
    assert resumed.status_code == 200
    assert "event: terminal" in resumed.text
    assert f"id: {cursor}" in resumed.text

    invalid = client.get(
        "/api/v1/flows/runs/cursor-run/events",
        headers={"Last-Event-ID": "process-local-counter"},
    )
    assert invalid.status_code == 400


def test_halt_status_authority_outage_is_retryable_503(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import killswitch
    from maverick_dashboard.app import app

    def unavailable():
        raise killswitch.HaltAuthorityUnavailable("database unavailable")

    monkeypatch.setattr(killswitch, "fleet_halt_status", unavailable)
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/halt")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


def test_halt_arm_authority_outage_reports_local_stop(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import killswitch
    from maverick_dashboard.app import app

    def unavailable(*_args, **_kwargs):
        raise killswitch.HaltAuthorityUnavailable("audit unavailable")

    monkeypatch.setattr(killswitch, "arm_fleet_halt", unavailable)
    response = TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Origin": "http://testserver"},
    ).post("/api/v1/halt", json={"reason": "stop now"})

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert "local safety stop remains armed" in response.json()["detail"]


def test_halt_clear_requires_generation_and_surfaces_conflict(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import killswitch
    from maverick_dashboard.app import app

    def conflict(**_kwargs):
        raise killswitch.HaltConflictError("new generation")

    monkeypatch.setattr(killswitch, "clear_fleet_halt", conflict)
    client = TestClient(app, headers={"Origin": "http://testserver"})

    assert client.delete("/api/v1/halt").status_code == 422
    response = client.delete("/api/v1/halt?expected_generation=3")
    assert response.status_code == 409
    assert "refresh" in response.json()["detail"]
