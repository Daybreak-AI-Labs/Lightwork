"""Adversarial recovery proof for consequential authority audit outboxes."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request, Response

_PRINCIPAL = "user:alice"
_TENANT = "acme"
_SIGNOFF_KEY = "authority-recovery-signoff-key-0001"


def _request(path: str, *, idempotency_key: str | None = None) -> Request:
    headers = [(b"origin", b"http://testserver")]
    if idempotency_key is not None:
        headers.append((b"idempotency-key", idempotency_key.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


@pytest.fixture
def authority_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)

    from maverick import production_scope
    from maverick.paths import reset_tenant, set_tenant
    from maverick.world_model import WorldModel
    from maverick_dashboard import api, automation_queue
    from maverick_dashboard import app as dashboard_app

    tenant_token = set_tenant(_TENANT)
    world = WorldModel(tmp_path / "world.db")
    monkeypatch.setattr(
        production_scope,
        "production_surface_required",
        lambda: True,
    )
    monkeypatch.setattr(api, "_world", lambda: world)
    monkeypatch.setattr(dashboard_app, "_world", lambda: world)
    monkeypatch.setattr(api, "require_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "assert_goal_access", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _PRINCIPAL)
    monkeypatch.setattr(api, "_supervisor", lambda _request: _PRINCIPAL)

    try:
        yield api, dashboard_app, automation_queue, world
    finally:
        world.close()
        reset_tenant(tenant_token)


def _done_goal(world, *, result: str = "reviewed deliverable") -> tuple[int, float]:
    goal_id = world.create_goal(
        "review forecast",
        "",
        owner=_PRINCIPAL,
        domain="finance_cash13w",
    )
    world.set_goal_status(goal_id, "done", result=result)
    return goal_id, world.get_goal(goal_id).updated_at


def _stage_signoff(
    world,
    *,
    key: str = _SIGNOFF_KEY,
    result: str = "reviewed deliverable",
):
    from maverick.api_idempotency import request_sha256
    from maverick.signoff_authority import policy_binding

    goal_id, version = _done_goal(world, result=result)
    binding = policy_binding("finance_cash13w", "review")
    note = "reviewed"
    normalized = {
        "decision": "approved",
        "deliverable_updated_at": float(version),
        "goal_domain": binding.goal_domain,
        "goal_id": int(goal_id),
        "governing_gate": binding.governing_gate,
        "note": note,
        "policy_sha256": binding.policy_sha256,
    }
    claim = world.decide_signoff_idempotent(
        goal_id,
        "approved",
        decided_by=_PRINCIPAL,
        note=note,
        expected_updated_at=version,
        goal_domain=binding.goal_domain,
        gate=binding.governing_gate,
        policy_sha256=binding.policy_sha256,
        operation="POST /api/v1/goals/{goal_id}/signoff",
        principal_id=_PRINCIPAL,
        idempotency_key=key,
        request_sha256=request_sha256(normalized),
        note_sha256=request_sha256({"note": note}),
    )
    return goal_id, version, claim


def _healthy_report():
    from maverick import production_observability as assurance

    signal = assurance.AssuranceSignal(
        assurance.KMS_KEY_CUSTODY,
        True,
        1,
        1,
        "verified",
    )
    return assurance.AssuranceReport(True, True, (signal,))


def test_abandoned_approval_is_finalized_by_background_sweep_without_resubmit(
    authority_env,
    monkeypatch,
):
    api, _dashboard_app, automation_queue, world = authority_env
    from maverick import audit

    approval_id = world.create_approval("wire funds", risk="critical")
    attempts: list[str] = []

    def unavailable(_kind, **payload):
        attempts.append(payload["approval_event_id"])
        return False

    monkeypatch.setattr(audit, "record", unavailable)
    response = asyncio.run(
        api.approve_approval(
            _request(f"/api/v1/approvals/{approval_id}/approve"),
            approval_id,
        )
    )
    assert response.status_code == 202
    decision_id = response.headers["X-Lightwork-Decision-Id"]
    assert attempts == [decision_id]
    assert world.get_approval(approval_id).status == "pending"

    monkeypatch.setattr(
        world,
        "decide_approval_audited",
        lambda *_args, **_kwargs: pytest.fail("client decision was resubmitted"),
    )
    delivered: list[str] = []
    monkeypatch.setattr(
        audit,
        "record",
        lambda _kind, **payload: delivered.append(
            payload["approval_event_id"]
        )
        or True,
    )
    monkeypatch.setattr(
        automation_queue,
        "_run_for_active_tenants",
        lambda _operation, fn: (fn(), True)[1],
    )

    automation_queue._handle_authority_audit_sweep()
    assert world.get_approval(approval_id).status == "approved"
    assert delivered == [decision_id]
    assert world.pending_approval_audit_events(approval_id=approval_id) == []

    automation_queue._handle_authority_audit_sweep()
    assert delivered == [decision_id]


def test_abandoned_signoff_is_finalized_by_startup_recovery_without_resubmit(
    authority_env,
    monkeypatch,
):
    api, dashboard_app, automation_queue, world = authority_env
    from maverick import audit
    from maverick_dashboard.api_schemas import SignoffIn

    goal_id, version = _done_goal(world)
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: False)
    with pytest.raises(HTTPException) as pending:
        asyncio.run(
            api.post_signoff(
                _request(
                    f"/api/v1/goals/{goal_id}/signoff",
                    idempotency_key=_SIGNOFF_KEY,
                ),
                goal_id,
                SignoffIn(
                    decision="approved",
                    expected_updated_at=version,
                    note="reviewed",
                ),
                Response(),
            )
        )
    assert pending.value.status_code == 503
    decision_id = pending.value.headers["X-Lightwork-Decision-Id"]
    assert world.signoff_for(goal_id) is None

    monkeypatch.setattr(
        world,
        "decide_signoff_idempotent",
        lambda *_args, **_kwargs: pytest.fail("client sign-off was resubmitted"),
    )
    delivered: list[str] = []
    monkeypatch.setattr(
        audit,
        "record",
        lambda _kind, **payload: delivered.append(payload["signoff_event_id"])
        or True,
    )
    monkeypatch.setattr(
        dashboard_app,
        "_startup_recovery_tenants",
        lambda _queue: [_TENANT],
    )

    recovered = dashboard_app._reconcile_known_tenant_authority_audits(
        automation_queue
    )
    assert recovered == {
        "approval_delivered": 0,
        "signoff_effective": 1,
        "signoff_stale": 0,
        "pending": 0,
        "failed": 0,
    }
    assert delivered == [decision_id]
    assert world.signoff_for(goal_id)["decision"] == "approved"

    repeated = dashboard_app._reconcile_known_tenant_authority_audits(
        automation_queue
    )
    assert repeated == {
        "approval_delivered": 0,
        "signoff_effective": 0,
        "signoff_stale": 0,
        "pending": 0,
        "failed": 0,
    }
    assert delivered == [decision_id]


@pytest.mark.parametrize("drift", ["deliverable", "policy"])
def test_signoff_recovery_retires_deliverable_or_policy_drift_as_stale(
    authority_env,
    monkeypatch,
    drift,
):
    _api, _dashboard_app, _automation_queue, world = authority_env
    import maverick.signoff_authority as authority
    from maverick import audit
    from maverick_dashboard.signoff_delivery import deliver_signoff_audit_event

    goal_id, _version, claim = _stage_signoff(world)
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: False)
    assert deliver_signoff_audit_event(world, claim.event) == "pending"

    if drift == "deliverable":
        world.set_goal_status(goal_id, "done", result="replacement deliverable")
    else:
        changed = authority.policy_binding("finance_cash13w", "approval")
        monkeypatch.setattr(
            authority,
            "current_signoff_policy_binding",
            lambda _domain: changed,
        )
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)

    assert deliver_signoff_audit_event(world, claim.event) == "stale"
    assert world.get_signoff_audit_event(claim.event.event_id).state == "STALE"
    assert world.signoff_for(goal_id) is None


def test_oldest_unavailable_authority_event_blocks_merged_outbox_overtaking(
    authority_env,
    monkeypatch,
):
    _api, _dashboard_app, _automation_queue, world = authority_env
    from maverick import audit
    from maverick.audit import EventKind
    from maverick_dashboard.authority_audit_delivery import (
        reconcile_pending_authority_audits,
    )

    approval_id = world.create_approval("oldest authority", risk="critical")
    approval = world.decide_approval_audited(
        approval_id,
        "approved",
        decided_by=_PRINCIPAL,
    )
    goal_id, _version, signoff = _stage_signoff(
        world,
        key="authority-recovery-signoff-key-0002",
    )
    with world._writing() as connection:
        connection.execute(
            "UPDATE approval_audit_outbox SET created_at = 10 WHERE event_id = ?",
            (approval.event_id,),
        )

    attempted: list[str] = []

    def oldest_unavailable(kind, **payload):
        attempted.append(str(kind))
        return kind != EventKind.APPROVAL_DECISION

    monkeypatch.setattr(audit, "record", oldest_unavailable)
    first = reconcile_pending_authority_audits(world)
    assert first == {
        "approval_delivered": 0,
        "signoff_effective": 0,
        "signoff_stale": 0,
        "pending": 1,
    }
    assert attempted == [str(EventKind.APPROVAL_DECISION)]
    assert world.get_signoff_audit_event(signoff.event.event_id).state == (
        "PENDING_AUDIT"
    )
    assert world.signoff_for(goal_id) is None

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    second = reconcile_pending_authority_audits(world)
    assert second == {
        "approval_delivered": 1,
        "signoff_effective": 1,
        "signoff_stale": 0,
        "pending": 0,
    }
    assert world.get_approval(approval_id).status == "approved"
    assert world.signoff_for(goal_id)["decision"] == "approved"


def test_authority_sweep_isolates_tenant_failure_and_visits_healthy_tenant(
    authority_env,
    monkeypatch,
):
    _api, _dashboard_app, automation_queue, _world = authority_env
    from maverick import paths
    from maverick.paths import current_tenant_id
    from maverick.tenant import registry
    from maverick_dashboard import authority_audit_delivery

    monkeypatch.setattr(automation_queue, "_configured_tenant_floor", lambda: "")
    monkeypatch.setattr(paths, "list_bound_tenant_namespaces", list)
    monkeypatch.setattr(
        registry,
        "list_tenants",
        lambda: [
            SimpleNamespace(id="broken", active=True),
            SimpleNamespace(id="healthy", active=True),
        ],
    )
    monkeypatch.setattr(
        registry,
        "principal_for_api_tenant",
        lambda tenant: f"user:{tenant}" if tenant else None,
    )
    monkeypatch.setattr(
        registry,
        "assert_execution_tenant_active",
        lambda *_args, **_kwargs: None,
    )

    visited: list[str] = []

    def reconcile(_world):
        tenant = current_tenant_id() or ""
        visited.append(tenant)
        if tenant == "broken":
            raise RuntimeError("tenant-local database unavailable")
        return {
            "approval_delivered": 0,
            "signoff_effective": 0,
            "signoff_stale": 0,
            "pending": 0,
        }

    monkeypatch.setattr(
        authority_audit_delivery,
        "reconcile_pending_authority_audits",
        reconcile,
    )
    automation_queue._handle_authority_audit_sweep()

    assert visited == ["", "broken", "healthy"]
    assert current_tenant_id() == _TENANT


def test_production_reconcile_seeds_fixed_system_authority(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)

    from maverick import automation_events, data_engine, dreaming, flow, production_scope
    from maverick.connections import bind_principal
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import automation_queue

    monkeypatch.setattr(
        production_scope,
        "production_surface_required",
        lambda: True,
    )
    monkeypatch.setattr(automation_events, "enabled", lambda: False)
    monkeypatch.setattr(data_engine, "enabled", lambda: False)
    monkeypatch.setattr(dreaming, "enabled", lambda: False)
    monkeypatch.setattr(flow, "enabled", lambda: False)
    monkeypatch.setattr(automation_queue, "reconcile_dashboard_tasks", lambda: None)
    monkeypatch.setattr(automation_queue, "_queue", None)
    monkeypatch.setattr(automation_queue, "_worker", None)
    monkeypatch.setattr(automation_queue, "_worker_threads", [])

    token = set_tenant("attacker-controlled-ambient-tenant")
    try:
        with bind_principal("user:ambient-attacker"):
            automation_queue.reconcile()
            automation_queue.reconcile()
    finally:
        reset_tenant(token)

    pending = [
        job
        for job in automation_queue.queue().list(status="pending", limit=100)
        if job.kind == automation_queue.AUTHORITY_AUDIT_SWEEP_KIND
    ]
    assert len(pending) == 1
    job = pending[0]
    assert job.authority is not None
    assert job.authority.mode == "system"
    assert job.authority.tenant == "control-plane:dashboard"
    assert job.authority.principal == "service:dashboard-automation"
    assert job.authority.owner == "service:dashboard-automation"
    assert job.authority.grant == {
        "allowed_suites": [],
        "capability": None,
    }
    assert job.payload == {
        "__cron__": "* * * * *",
        "allowed_suites": [],
        "capability": None,
        "owner": "service:dashboard-automation",
        "principal_id": "service:dashboard-automation",
        "tenant": "control-plane:dashboard",
    }


def test_pending_authority_backlog_fails_readiness_and_publishes_metric_until_recovery(
    authority_env,
    monkeypatch,
):
    _api, _dashboard_app, automation_queue, world = authority_env
    from maverick import agent_trust, audit, client, production_observability, shield_policy
    from maverick_dashboard import (
        health_routes,
        service_credentials,
        tenant_quota_health,
    )
    from maverick_dashboard.authority_audit_delivery import (
        current_authority_audit_backlog,
        reconcile_pending_authority_audits,
    )

    approval_id = world.create_approval("publish backlog", risk="critical")
    world.decide_approval_audited(
        approval_id,
        "approved",
        decided_by=_PRINCIPAL,
    )
    _stage_signoff(
        world,
        key="authority-recovery-signoff-key-0003",
    )

    monkeypatch.setattr(
        production_observability,
        "production_assurance_required",
        lambda: True,
    )
    monkeypatch.setattr(
        production_observability,
        "collect_assurance",
        lambda _world: _healthy_report(),
    )
    monkeypatch.setattr(client, "client_binding_enforced", lambda: False)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (False, {}))
    monkeypatch.setattr(
        service_credentials,
        "require_metrics_service_credential",
        lambda _request: None,
    )
    monkeypatch.setattr(
        automation_queue,
        "_run_for_active_tenants",
        lambda _operation, fn: (fn(), True)[1],
    )
    # This test isolates the authority-audit outbox gate.  Production also
    # requires the independent quota-authority gate, so supply an explicitly
    # healthy result instead of disabling that fail-closed requirement.
    monkeypatch.setattr(
        tenant_quota_health,
        "collect_tenant_quota_statuses",
        lambda _world_provider: (True, []),
    )

    ready_before, checks_before = health_routes.readiness_deep_checks(
        lambda: world
    )
    assert ready_before is False
    assert checks_before["production_authority_audit_backlog"] == (
        "fail: pending_or_unknown=2"
    )
    assert checks_before["production_tenant_quota_authority"] == "ok"
    metrics_before = asyncio.run(
        health_routes.metrics_response(
            _request("/metrics"),
            world_provider=lambda: world,
            owner_filter=lambda _request: None,
        )
    ).body.decode()
    assert "maverick_authority_audit_backlog 2" in metrics_before

    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    assert reconcile_pending_authority_audits(world) == {
        "approval_delivered": 1,
        "signoff_effective": 1,
        "signoff_stale": 0,
        "pending": 0,
    }
    assert current_authority_audit_backlog(world) == 0
    assert world.pending_approval_audit_events(approval_id=approval_id) == []
    assert world.pending_signoff_audit_events() == []

    ready_after, checks_after = health_routes.readiness_deep_checks(lambda: world)
    assert ready_after is True
    assert checks_after["production_authority_audit_backlog"] == "ok"
    assert checks_after["production_tenant_quota_authority"] == "ok"
    metrics_after = asyncio.run(
        health_routes.metrics_response(
            _request("/metrics"),
            world_provider=lambda: world,
            owner_filter=lambda _request: None,
        )
    ).body.decode()
    assert "maverick_authority_audit_backlog 0" in metrics_after
