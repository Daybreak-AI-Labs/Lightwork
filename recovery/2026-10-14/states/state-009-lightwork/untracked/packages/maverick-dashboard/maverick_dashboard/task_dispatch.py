"""Production-safe dashboard task dispatch.

Development keeps FastAPI's convenient in-process background runner.  The
reviewed production surface must acknowledge a goal only after its delivery is
durable, so every HTTP producer funnels through the dashboard JobQueue first.
"""
from __future__ import annotations

import logging
import math

from fastapi import BackgroundTasks, HTTPException

log = logging.getLogger(__name__)

_GOAL_DISPATCH_SCHEMA = "lightwork.dashboard-goal-dispatch.v1"
_GOAL_DISPATCH_FIELDS = frozenset(
    {
        "allowed_suites",
        "channel",
        "max_depth",
        "max_dollars",
        "max_wall_seconds",
        "schema",
        "user_id",
    }
)


def _bounded_dispatch_text(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"durable goal dispatch {field} is invalid")
    text = value
    if (
        len(text) > 256
        or (text and (not text.isprintable() or not text.strip()))
        or text != text.strip()
    ):
        raise ValueError(f"durable goal dispatch {field} is invalid")
    return text


def durable_goal_dispatch_payload(
    *,
    max_dollars: float,
    max_wall_seconds: float,
    max_depth: int,
    channel: str | None,
    user_id: str | None,
    allowed_suites: frozenset[str] | set[str] | None,
) -> dict[str, object]:
    """Build the exact private outbox value committed with an API receipt."""
    payload: dict[str, object] = {
        "allowed_suites": (
            None
            if allowed_suites is None
            else sorted(
                {_bounded_dispatch_text(item, "suite") for item in allowed_suites}
            )
        ),
        "channel": _bounded_dispatch_text(channel, "channel"),
        "max_depth": max_depth,
        "max_dollars": max_dollars,
        "max_wall_seconds": max_wall_seconds,
        "schema": _GOAL_DISPATCH_SCHEMA,
        "user_id": _bounded_dispatch_text(user_id, "user_id"),
    }
    # Run the same strict decoder used after storage so malformed values are
    # refused before the world-model transaction is opened.
    _goal_dispatch_kwargs(payload)
    return payload


def _goal_dispatch_kwargs(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _GOAL_DISPATCH_FIELDS:
        raise ValueError("durable goal dispatch schema is not exact")
    if payload.get("schema") != _GOAL_DISPATCH_SCHEMA:
        raise ValueError("durable goal dispatch schema is invalid")
    numeric: dict[str, float] = {}
    for field in ("max_dollars", "max_wall_seconds"):
        raw = payload.get(field)
        if isinstance(raw, bool):
            raise ValueError(f"durable goal dispatch {field} is invalid")
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"durable goal dispatch {field} is invalid") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"durable goal dispatch {field} is invalid")
        numeric[field] = value
    depth = payload.get("max_depth")
    if isinstance(depth, bool) or not isinstance(depth, int) or not 1 <= depth <= 64:
        raise ValueError("durable goal dispatch max_depth is invalid")
    channel = _bounded_dispatch_text(payload.get("channel"), "channel")
    if channel not in {"", "api", "dashboard"}:
        raise ValueError("durable goal dispatch channel is invalid")
    user_id = _bounded_dispatch_text(payload.get("user_id"), "user_id")
    raw_suites = payload.get("allowed_suites")
    if raw_suites is None:
        suites = None
    else:
        if not isinstance(raw_suites, list):
            raise ValueError("durable goal dispatch allowed_suites is invalid")
        suites = [_bounded_dispatch_text(item, "suite") for item in raw_suites]
        if any(not item for item in suites) or suites != sorted(set(suites)):
            raise ValueError("durable goal dispatch allowed_suites is invalid")
    return {
        "allowed_suites": None if suites is None else frozenset(suites),
        "channel": channel or None,
        "max_depth": depth,
        "max_dollars": numeric["max_dollars"],
        "max_wall_seconds": numeric["max_wall_seconds"],
        "user_id": user_id or None,
    }


async def _run_local_goal_in_tenant(
    goal_id: int,
    *,
    tenant: str,
    runner_kwargs: dict,
) -> None:
    from maverick.paths import reset_tenant, set_tenant
    from maverick.runner import run_goal_in_background_async

    if tenant:
        from maverick.tenant.registry import (
            assert_execution_tenant_active,
            principal_for_api_tenant,
        )

        assert_execution_tenant_active(
            tenant,
            principal_id=principal_for_api_tenant(tenant),
        )
    token = set_tenant(tenant or None)
    try:
        await run_goal_in_background_async(goal_id, **runner_kwargs)
    finally:
        reset_tenant(token)


def _mark_dispatch_failure(goal_id: int) -> None:
    """Best-effort typed terminalization after the durable enqueue is refused."""
    try:
        from maverick.failure_taxonomy import (
            FailureKind,
            failure_state,
            persist_goal_state,
        )

        from maverick_dashboard.app import _world

        world = _world()
        goal = world.get_goal(int(goal_id))
        if goal is None or goal.status != "pending":
            return
        state = failure_state(
            FailureKind.PROVIDER_FAILURE,
            "durable dashboard task queue is unavailable",
            origin="runtime",
        )
        persist_goal_state(world, int(goal_id), state)
        world.set_goal_status(
            int(goal_id),
            "blocked",
            result="durable task dispatch unavailable",
        )
    except Exception:
        log.exception("could not terminalize undispatched goal #%s", goal_id)


def dispatch_goal(
    background_tasks: BackgroundTasks,
    goal_id: int,
    *,
    max_dollars: float | None = None,
    max_wall_seconds: float | None = None,
    max_depth: int | None = None,
    conversation_id: int | None = None,
    channel: str | None = None,
    user_id: str | None = None,
    capability=None,
    concurrency_principal: str | None = None,
    allowed_suites: frozenset[str] | set[str] | None = None,
    tenant: str | None = None,
    terminalize_on_failure: bool = True,
) -> int | None:
    """Admit one HTTP-created goal through the deployment contract."""
    from maverick.runner import work_admission

    with work_admission() as accepted:
        if not accepted:
            if terminalize_on_failure:
                _mark_dispatch_failure(goal_id)
            raise HTTPException(
                status_code=503,
                detail="service is draining and cannot accept new work",
                headers={"Retry-After": "1"},
            )
        return _dispatch_goal_admitted(
            background_tasks,
            goal_id,
            max_dollars=max_dollars,
            max_wall_seconds=max_wall_seconds,
            max_depth=max_depth,
            conversation_id=conversation_id,
            channel=channel,
            user_id=user_id,
            capability=capability,
            concurrency_principal=concurrency_principal,
            allowed_suites=allowed_suites,
            tenant=tenant,
            terminalize_on_failure=terminalize_on_failure,
        )


def _dispatch_goal_admitted(
    background_tasks: BackgroundTasks,
    goal_id: int,
    *,
    max_dollars: float | None = None,
    max_wall_seconds: float | None = None,
    max_depth: int | None = None,
    conversation_id: int | None = None,
    channel: str | None = None,
    user_id: str | None = None,
    capability=None,
    concurrency_principal: str | None = None,
    allowed_suites: frozenset[str] | set[str] | None = None,
    tenant: str | None = None,
    terminalize_on_failure: bool = True,
) -> int | None:
    """Dispatch work after its caller has crossed process admission."""
    from maverick.production_scope import production_surface_required

    if production_surface_required():
        from maverick_dashboard.automation_queue import enqueue_goal_run

        try:
            return enqueue_goal_run(
                goal_id,
                max_dollars=max_dollars,
                max_wall_seconds=max_wall_seconds,
                max_depth=max_depth,
                conversation_id=conversation_id,
                channel=channel,
                user_id=user_id,
                capability=capability,
                concurrency_principal=concurrency_principal,
                allowed_suites=allowed_suites,
                tenant=tenant,
            )
        except Exception as exc:
            log.exception("durable dashboard dispatch failed for goal #%s", goal_id)
            if terminalize_on_failure:
                _mark_dispatch_failure(goal_id)
            raise HTTPException(
                status_code=503,
                detail="durable task dispatch is temporarily unavailable",
                headers={"Retry-After": "1"},
            ) from exc

    from maverick.runner import DEFAULT_MAX_DEPTH, run_goal_in_background_async

    runner_kwargs = {
        "max_dollars": max_dollars,
        "max_wall_seconds": max_wall_seconds,
        "max_depth": DEFAULT_MAX_DEPTH if max_depth is None else max_depth,
        "channel": channel,
        "user_id": user_id,
        "conversation_id": conversation_id,
        "capability": capability,
        "concurrency_principal": concurrency_principal,
        "allowed_suites": allowed_suites,
    }
    if tenant is not None:
        background_tasks.add_task(
            _run_local_goal_in_tenant,
            goal_id,
            tenant=str(tenant or ""),
            runner_kwargs=runner_kwargs,
        )
    else:
        background_tasks.add_task(
            run_goal_in_background_async,
            goal_id,
            **runner_kwargs,
        )
    return None


def dispatch_idempotent_goal(
    background_tasks: BackgroundTasks,
    goal_id: int,
    payload: object,
) -> int | None:
    """Admit delivery and retirement of one transactional goal outbox."""
    from maverick.runner import work_admission

    with work_admission() as accepted:
        if not accepted:
            raise HTTPException(
                status_code=503,
                detail="service is draining and cannot accept new work",
                headers={"Retry-After": "1"},
            )
        return _dispatch_idempotent_goal_admitted(
            background_tasks,
            goal_id,
            payload,
        )


def _dispatch_idempotent_goal_admitted(
    background_tasks: BackgroundTasks,
    goal_id: int,
    payload: object,
) -> int | None:
    """Deliver and then retire one transactional create-goal outbox.

    Queue insertion precedes the marker.  A crash in that narrow window is
    repaired by the queue's exact deduplication; the reverse order could lose
    the only delivery forever.  The lifecycle lock also makes cancel/resume
    authoritative over a late retry of the original create request.
    """
    from maverick.goal_lifecycle import goal_lifecycle_lock

    from maverick_dashboard.app import _world

    gid = int(goal_id)
    with goal_lifecycle_lock(gid):
        world = _world()
        goal = world.get_goal(gid)
        if goal is None:
            raise HTTPException(
                status_code=503,
                detail="durable task dispatch state is temporarily indeterminate",
                headers={"Retry-After": "1"},
            )
        if goal.status != "pending":
            try:
                world.mark_goal_idempotent_dispatch_delivered(gid)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "durable task dispatch state is temporarily indeterminate"
                    ),
                    headers={"Retry-After": "1"},
                ) from exc
            return None

        job_id = dispatch_goal(
            background_tasks,
            gid,
            terminalize_on_failure=False,
            **_goal_dispatch_kwargs(payload),
        )
        try:
            if not world.mark_goal_idempotent_dispatch_delivered(gid):
                raise RuntimeError("transactional goal outbox is missing")
        except Exception as exc:
            # A production queue row now exists.  Return retryable
            # indeterminacy so the caller can repeat the exact request; queue
            # dedup proves/reuses the same row before this marker is retried.
            log.exception("could not retire goal dispatch outbox #%s", gid)
            raise HTTPException(
                status_code=503,
                detail="durable task dispatch state is temporarily indeterminate",
                headers={"Retry-After": "1"},
            ) from exc
        return job_id


def repair_idempotent_goal_dispatch(goal_id: int, payload: object) -> int:
    """Admit repair of a missing queue row from a committed outbox."""
    from maverick.runner import work_admission

    with work_admission() as accepted:
        if not accepted:
            raise RuntimeError("service is draining and cannot repair goal dispatch")
        return _repair_idempotent_goal_dispatch_admitted(goal_id, payload)


def _repair_idempotent_goal_dispatch_admitted(
    goal_id: int,
    payload: object,
) -> int:
    """Repair a transactional outbox after process admission."""
    from maverick.production_scope import production_surface_required

    if not production_surface_required():
        raise RuntimeError("durable goal dispatch repair is production-only")
    from maverick.goal_lifecycle import goal_lifecycle_lock

    from maverick_dashboard.app import _world
    from maverick_dashboard.automation_queue import enqueue_goal_run

    gid = int(goal_id)
    with goal_lifecycle_lock(gid):
        world = _world()
        goal = world.get_goal(gid)
        if goal is None:
            raise RuntimeError("transactional goal outbox refers to a missing goal")
        if goal.status != "pending":
            world.mark_goal_idempotent_dispatch_delivered(gid)
            return 0
        job_id = enqueue_goal_run(gid, **_goal_dispatch_kwargs(payload))
        if not world.mark_goal_idempotent_dispatch_delivered(gid):
            raise RuntimeError("transactional goal outbox is missing")
        return job_id


__all__ = [
    "dispatch_goal",
    "dispatch_idempotent_goal",
    "durable_goal_dispatch_payload",
    "repair_idempotent_goal_dispatch",
]
