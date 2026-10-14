"""Adverse lifecycle tests for transactional API goal delivery."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import BackgroundTasks, Response
from starlette.requests import Request

_OWNER = "user:alice"
_TENANT = "api:user:alice"


def _request(path: str, key: str = "delivery-lifecycle-request") -> Request:
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
def isolated_delivery(tmp_path, monkeypatch):
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
    # Keep quota admission active while substituting the hermetic SQLite world
    # for the live Postgres dependency covered by guarded integration tests.
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


def _create_pending_outbox(world, key: str):
    from maverick.api_idempotency import request_sha256
    from maverick_dashboard.task_dispatch import durable_goal_dispatch_payload

    normalized = {
        "description": f"recover {key}",
        "domain": "",
        "max_depth": 2,
        "max_dollars": 1.0,
        "max_wall_seconds": 30.0,
        "title": f"outbox {key}",
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
            max_dollars=1.0,
            max_wall_seconds=30.0,
            max_depth=2,
            channel="api",
            user_id="alice",
            allowed_suites=frozenset({"finance"}),
        ),
    )


def _wire_goal_api(monkeypatch, dashboard_app, api, *, suites):
    monkeypatch.setattr(api, "require_permission", lambda *_args: None)
    monkeypatch.setattr(api, "require_provider_or_400", lambda: None)
    monkeypatch.setattr(api, "assert_goal_access", lambda *_args: None)
    monkeypatch.setattr(api, "caller_principal", lambda _request: _OWNER)
    monkeypatch.setattr(api, "caller_suites", lambda _request: frozenset(suites))
    monkeypatch.setattr(
        api,
        "execution_user_id_from_request",
        lambda _request: "alice",
    )
    monkeypatch.setattr(api, "_ground_outcome", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        dashboard_app,
        "check_goal_rate_limit",
        lambda *_args, **_kwargs: None,
    )


def test_successful_durable_enqueue_marks_outbox_delivered(isolated_delivery):
    dashboard_app, aq = isolated_delivery
    from maverick_dashboard.task_dispatch import dispatch_idempotent_goal

    world = dashboard_app._world()
    claim = _create_pending_outbox(world, "delivery-marker-request")

    job_id = dispatch_idempotent_goal(
        BackgroundTasks(),
        claim.goal_id,
        claim.dispatch,
    )

    assert job_id is not None
    assert len(aq._goal_jobs(claim.goal_id, _TENANT, live_only=True)) == 1
    assert world.get_goal_idempotent_dispatch(claim.goal_id) is None
    replay = _create_pending_outbox(world, "delivery-marker-request")
    assert replay.replayed is True
    assert replay.delivery_pending is False
    assert replay.dispatch is None


def test_cancel_resume_then_original_retry_cannot_restore_create_delivery(
    isolated_delivery,
    monkeypatch,
):
    dashboard_app, aq = isolated_delivery
    from maverick_dashboard import api

    _wire_goal_api(
        monkeypatch,
        dashboard_app,
        api,
        suites={"finance", "legal"},
    )
    request = _request("/api/v1/goals", "cancel-resume-original-request")
    payload = api.GoalIn(
        title="cancel then resume",
        description="the create delivery must stay retired",
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
    )

    first = asyncio.run(
        api.create_goal(request, payload, BackgroundTasks(), Response())
    )
    original = aq._goal_jobs(first.id, _TENANT, live_only=True)
    assert len(original) == 1
    assert original[0].payload["allowed_suites"] == ["finance", "legal"]

    # Authority can legitimately narrow between the original request and an
    # operator-controlled resume. A stale create outbox must not reintroduce
    # the older delivery merely because its payload differs from the resume.
    monkeypatch.setattr(
        api,
        "caller_suites",
        lambda _request: frozenset({"finance"}),
    )
    asyncio.run(api.cancel_goal(request, first.id))
    assert aq._goal_jobs(first.id, _TENANT, live_only=True) == []

    asyncio.run(api.resume_goal(first.id, request, BackgroundTasks()))
    resumed = aq._goal_jobs(first.id, _TENANT, live_only=True)
    assert len(resumed) == 1
    assert resumed[0].payload["allowed_suites"] == ["finance"]

    replay_response = Response()
    replay = asyncio.run(
        api.create_goal(request, payload, BackgroundTasks(), replay_response)
    )

    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    after_replay = aq._goal_jobs(first.id, _TENANT, live_only=True)
    assert [job.id for job in after_replay] == [resumed[0].id]
    assert [job.payload["allowed_suites"] for job in after_replay] == [["finance"]]
    assert dashboard_app._world().get_goal_idempotent_dispatch(first.id) is None


def test_startup_quarantines_one_poisoned_outbox_and_continues_repairs(
    isolated_delivery,
):
    dashboard_app, aq = isolated_delivery
    world = dashboard_app._world()
    poisoned = _create_pending_outbox(world, "poisoned-startup-request")
    healthy = _create_pending_outbox(world, "healthy-startup-request")
    secret_ciphertext = "kms://private/customer/key-version-7"
    with world._writing() as conn:
        conn.execute(
            "UPDATE api_idempotency SET result_json = ? WHERE goal_id = ?",
            (secret_ciphertext, poisoned.goal_id),
        )

    asyncio.run(dashboard_app._reclaim_orphans())

    bad_goal = world.get_goal(poisoned.goal_id)
    assert bad_goal is not None
    assert bad_goal.status == "blocked"
    assert secret_ciphertext not in str(bad_goal.result or "")
    assert "customer" not in str(bad_goal.result or "")
    assert aq._goal_jobs(poisoned.goal_id, _TENANT, live_only=True) == []

    healthy_jobs = aq._goal_jobs(healthy.goal_id, _TENANT, live_only=True)
    assert len(healthy_jobs) == 1
    assert healthy_jobs[0].payload["goal_id"] == healthy.goal_id
    assert world.get_goal(healthy.goal_id).status == "pending"
    assert world.get_goal_idempotent_dispatch(healthy.goal_id) is None

    # A second startup pass is quiet: the quarantined row is no longer a
    # candidate and the healthy row already owns one live durable delivery.
    asyncio.run(dashboard_app._reclaim_orphans())
    assert [
        job.id
        for job in aq._goal_jobs(healthy.goal_id, _TENANT, live_only=True)
    ] == [healthy_jobs[0].id]


class _AdmissionProbe(RuntimeError):
    """Raised only when a mutable admission dependency is consulted."""


def test_goal_exact_replay_bypasses_provider_rate_and_mutable_template(
    isolated_delivery,
    monkeypatch,
):
    dashboard_app, _aq = isolated_delivery
    from maverick import templates as template_store
    from maverick_dashboard import api

    _wire_goal_api(
        monkeypatch,
        dashboard_app,
        api,
        suites={"finance"},
    )
    calls = {"provider": 0, "rate": 0, "template": 0}
    blocked = {"provider": False, "rate": False, "template": False}

    def provider_gate() -> None:
        calls["provider"] += 1
        if blocked["provider"]:
            raise _AdmissionProbe("provider admission")

    def rate_gate(*_args, **_kwargs) -> None:
        calls["rate"] += 1
        if blocked["rate"]:
            raise _AdmissionProbe("shared rate limit")

    class Template:
        def render(self, **params):
            assert params == {"customer": "Acme"}
            return "Rendered goal", "Rendered for Acme"

    def load_template(_name: str):
        calls["template"] += 1
        if blocked["template"]:
            raise _AdmissionProbe("mutable template")
        return Template()

    monkeypatch.setattr(api, "require_provider_or_400", provider_gate)
    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", rate_gate)
    monkeypatch.setattr(template_store, "load_template", load_template)
    payload = api.GoalIn(
        title="client template request",
        description="",
        template="mutable-goal-template",
        params={"customer": "Acme"},
        max_dollars=1.0,
        max_wall_seconds=30.0,
        max_depth=2,
    )

    first = asyncio.run(
        api.create_goal(
            _request("/api/v1/goals", "goal-admission-first"),
            payload,
            BackgroundTasks(),
            Response(),
        )
    )
    assert calls == {"provider": 1, "rate": 1, "template": 1}

    # All three dependencies can change after commit. The exact receipt must
    # remain replayable without consulting any of them.
    blocked.update(provider=True, rate=True, template=True)
    replay_response = Response()
    replay = asyncio.run(
        api.create_goal(
            _request("/api/v1/goals", "goal-admission-first"),
            payload,
            BackgroundTasks(),
            replay_response,
        )
    )
    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert calls == {"provider": 1, "rate": 1, "template": 1}

    # A fresh key has no committed receipt and still traverses each mutable
    # gate in order. Distinct keys keep the probes independent.
    with pytest.raises(_AdmissionProbe, match="provider admission"):
        asyncio.run(
            api.create_goal(
                _request("/api/v1/goals", "goal-admission-provider"),
                payload,
                BackgroundTasks(),
                Response(),
            )
        )
    assert calls == {"provider": 2, "rate": 1, "template": 1}

    blocked["provider"] = False
    with pytest.raises(_AdmissionProbe, match="shared rate limit"):
        asyncio.run(
            api.create_goal(
                _request("/api/v1/goals", "goal-admission-rate"),
                payload,
                BackgroundTasks(),
                Response(),
            )
        )
    assert calls == {"provider": 3, "rate": 2, "template": 1}

    blocked["rate"] = False
    with pytest.raises(_AdmissionProbe, match="mutable template"):
        asyncio.run(
            api.create_goal(
                _request("/api/v1/goals", "goal-admission-template"),
                payload,
                BackgroundTasks(),
                Response(),
            )
        )
    assert calls == {"provider": 4, "rate": 3, "template": 2}


def test_compose_exact_replay_bypasses_provider_and_shared_rate_limit(
    isolated_delivery,
    monkeypatch,
):
    dashboard_app, _aq = isolated_delivery
    from maverick_dashboard import api

    _wire_goal_api(
        monkeypatch,
        dashboard_app,
        api,
        suites={"finance"},
    )
    calls = {"provider": 0, "rate": 0}
    blocked = {"provider": False, "rate": False}

    def provider_gate() -> None:
        calls["provider"] += 1
        if blocked["provider"]:
            raise _AdmissionProbe("provider admission")

    def rate_gate(*_args, **_kwargs) -> None:
        calls["rate"] += 1
        if blocked["rate"]:
            raise _AdmissionProbe("shared rate limit")

    monkeypatch.setattr(api, "require_provider_or_400", provider_gate)
    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", rate_gate)
    payload = api.ComposeIn(
        title="compose replay first",
        steps=["collect", "review"],
        budget_dollars=1.0,
    )

    first = asyncio.run(
        api.compose_goal(
            _request("/api/v1/goals/compose", "compose-admission-first"),
            payload,
            BackgroundTasks(),
            Response(),
        )
    )
    assert calls == {"provider": 1, "rate": 1}

    blocked.update(provider=True, rate=True)
    replay_response = Response()
    replay = asyncio.run(
        api.compose_goal(
            _request("/api/v1/goals/compose", "compose-admission-first"),
            payload,
            BackgroundTasks(),
            replay_response,
        )
    )
    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert calls == {"provider": 1, "rate": 1}

    with pytest.raises(_AdmissionProbe, match="provider admission"):
        asyncio.run(
            api.compose_goal(
                _request(
                    "/api/v1/goals/compose",
                    "compose-admission-provider",
                ),
                api.ComposeIn(title="fresh compose provider"),
                BackgroundTasks(),
                Response(),
            )
        )
    assert calls == {"provider": 2, "rate": 1}

    blocked["provider"] = False
    with pytest.raises(_AdmissionProbe, match="shared rate limit"):
        asyncio.run(
            api.compose_goal(
                _request("/api/v1/goals/compose", "compose-admission-rate"),
                api.ComposeIn(title="fresh compose rate"),
                BackgroundTasks(),
                Response(),
            )
        )
    assert calls == {"provider": 3, "rate": 2}


def test_child_exact_replay_bypasses_shared_rate_limit(
    isolated_delivery,
    monkeypatch,
):
    dashboard_app, _aq = isolated_delivery
    from maverick_dashboard import api

    _wire_goal_api(
        monkeypatch,
        dashboard_app,
        api,
        suites={"finance"},
    )
    world = dashboard_app._world()
    parent_id = world.create_goal("parent", "structural parent", owner=_OWNER)
    calls = 0
    blocked = False

    def rate_gate(*_args, **_kwargs) -> None:
        nonlocal calls
        calls += 1
        if blocked:
            raise _AdmissionProbe("shared rate limit")

    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", rate_gate)
    payload = api.ChildIn(title="child replay first", description="details")
    path = f"/api/v1/goals/{parent_id}/children"

    first = asyncio.run(
        api.create_child_goal(
            _request(path, "child-admission-first"),
            parent_id,
            payload,
            Response(),
        )
    )
    assert calls == 1

    blocked = True
    replay_response = Response()
    replay = asyncio.run(
        api.create_child_goal(
            _request(path, "child-admission-first"),
            parent_id,
            payload,
            replay_response,
        )
    )
    assert replay == first
    assert replay_response.headers["Idempotency-Replayed"] == "true"
    assert calls == 1

    with pytest.raises(_AdmissionProbe, match="shared rate limit"):
        asyncio.run(
            api.create_child_goal(
                _request(path, "child-admission-rate"),
                parent_id,
                api.ChildIn(title="fresh child", description="details"),
                Response(),
            )
        )
    assert calls == 2


def test_chat_exact_replay_bypasses_provider_and_shared_rate_limit(
    isolated_delivery,
    monkeypatch,
):
    dashboard_app, _aq = isolated_delivery
    calls = {"provider": 0, "rate": 0}
    blocked = {"provider": False, "rate": False}

    monkeypatch.setattr(
        dashboard_app,
        "_require_same_origin",
        lambda _request: None,
    )
    monkeypatch.setattr(
        dashboard_app,
        "require_permission",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        dashboard_app,
        "caller_principal",
        lambda _request: _OWNER,
    )
    monkeypatch.setattr(
        dashboard_app,
        "caller_suites",
        lambda _request: frozenset({"finance"}),
    )
    monkeypatch.setattr(
        dashboard_app,
        "execution_user_id_from_request",
        lambda _request: "alice",
    )

    def provider_gate() -> None:
        calls["provider"] += 1
        if blocked["provider"]:
            raise _AdmissionProbe("provider admission")

    def rate_gate(*_args, **_kwargs) -> None:
        calls["rate"] += 1
        if blocked["rate"]:
            raise _AdmissionProbe("shared rate limit")

    monkeypatch.setattr(
        dashboard_app,
        "require_provider_or_400",
        provider_gate,
    )
    monkeypatch.setattr(dashboard_app, "check_goal_rate_limit", rate_gate)
    request = _request("/chat/send")

    first = asyncio.run(
        dashboard_app.chat_send(
            request,
            BackgroundTasks(),
            "chat replay first",
            "details",
            "chat-admission-first",
            [],
        )
    )
    assert calls == {"provider": 1, "rate": 1}

    blocked.update(provider=True, rate=True)
    replay = asyncio.run(
        dashboard_app.chat_send(
            request,
            BackgroundTasks(),
            "chat replay first",
            "details",
            "chat-admission-first",
            [],
        )
    )
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.headers["location"] == first.headers["location"]
    assert calls == {"provider": 1, "rate": 1}

    with pytest.raises(_AdmissionProbe, match="provider admission"):
        asyncio.run(
            dashboard_app.chat_send(
                request,
                BackgroundTasks(),
                "fresh chat provider",
                "details",
                "chat-admission-provider",
                [],
            )
        )
    assert calls == {"provider": 2, "rate": 1}

    blocked["provider"] = False
    with pytest.raises(_AdmissionProbe, match="shared rate limit"):
        asyncio.run(
            dashboard_app.chat_send(
                request,
                BackgroundTasks(),
                "fresh chat rate",
                "details",
                "chat-admission-rate",
                [],
            )
        )
    assert calls == {"provider": 3, "rate": 2}
