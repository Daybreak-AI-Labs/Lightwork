"""Process-wide shutdown admission at the dashboard boundary."""
from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException


def test_draining_withdraws_readiness_immediately():
    from maverick import runner
    from maverick_dashboard import health_routes

    runner.begin_draining()
    ready, checks = health_routes.readiness_deep_checks()

    assert ready is False
    assert checks["runner_admission"] == "fail: process is draining"


def test_dashboard_dispatch_refuses_before_durable_enqueue_during_drain(
    monkeypatch,
):
    from maverick import production_scope, runner
    from maverick_dashboard import automation_queue, task_dispatch

    terminalized: list[int] = []
    monkeypatch.setattr(production_scope, "production_surface_required", lambda: True)
    monkeypatch.setattr(
        automation_queue,
        "enqueue_goal_run",
        lambda *_args, **_kwargs: pytest.fail("draining dispatch reached enqueue"),
    )
    monkeypatch.setattr(
        task_dispatch,
        "_mark_dispatch_failure",
        terminalized.append,
    )
    runner.begin_draining()

    with pytest.raises(HTTPException) as raised:
        task_dispatch.dispatch_goal(BackgroundTasks(), 41)

    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "1"}
    assert terminalized == [41]


def test_idempotent_outbox_is_left_retryable_when_shutdown_wins_admission():
    from maverick import runner
    from maverick_dashboard import task_dispatch

    runner.begin_draining()

    with pytest.raises(HTTPException) as raised:
        task_dispatch.dispatch_idempotent_goal(
            BackgroundTasks(),
            99,
            {"not": "decoded because admission closes first"},
        )

    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "1"}
