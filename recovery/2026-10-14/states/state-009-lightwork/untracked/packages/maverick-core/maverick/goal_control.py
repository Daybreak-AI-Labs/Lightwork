"""Per-goal cancellation admission and terminal-status fencing.

The global killswitch is intentionally process-wide. Dashboard cancellation is
more precise: it must stop one active goal without halting unrelated tenants or
runs, and a late executor write must never resurrect a cancelled goal as done.
"""
from __future__ import annotations


def check_goal_control(world, goal_id: int | None) -> None:
    """Raise a typed stop when one running goal is cancelled or unreadable.

    Agents call this beside the global killswitch at provider, turn, and tool
    boundaries. A missing/unreadable authoritative row is indeterminate rather
    than permission to continue across another consequential boundary.
    """
    if goal_id is None:
        return
    gid = int(goal_id)
    try:
        goal = world.get_goal(gid)
    except Exception as exc:
        from .failure_taxonomy import FailureKind, FailureSignal, failure_state

        raise FailureSignal(
            failure_state(
                FailureKind.INDETERMINATE_EFFECT,
                "goal cancellation authority is unavailable",
                origin="runtime",
            )
        ) from exc
    if goal is None:
        from .failure_taxonomy import FailureKind, FailureSignal, failure_state

        raise FailureSignal(
            failure_state(
                FailureKind.INDETERMINATE_EFFECT,
                "goal cancellation authority no longer contains this run",
                origin="runtime",
            )
        )
    if goal.status == "cancelled":
        from .killswitch import Halted

        raise Halted("goal was cancelled by an operator", "goal-status")


def set_goal_status_preserving_cancellation(
    world,
    goal_id: int,
    status: str,
    *,
    result: str | None = None,
) -> bool:
    """Publish a status unless an operator cancellation already won.

    The tenant-scoped cross-process lifecycle lock makes the read/transition
    atomic with dashboard cancel/resume. ``False`` means cancellation remains
    authoritative and the caller must not emit a success terminal event.
    """
    from .goal_lifecycle import goal_lifecycle_lock

    gid = int(goal_id)
    with goal_lifecycle_lock(gid):
        current = world.get_goal(gid)
        if current is None:
            raise RuntimeError(f"goal {gid} disappeared during status transition")
        if current.status == "cancelled" and status != "cancelled":
            return False
        world.set_goal_status(gid, status, result=result)
        return True


def set_goal_failure_preserving_cancellation(
    world,
    goal_id: int,
    state,
    status: str,
    *,
    result: str | None = None,
) -> bool:
    """Atomically publish typed state unless operator cancellation already won.

    The execution-state event and goal status must share the lifecycle lock.
    Otherwise a late policy/runtime failure can become the newest typed state
    and resurrect a goal as ``blocked`` after the operator cancelled it.
    ``False`` preserves the cancellation event, status, and result verbatim.
    """
    from .failure_taxonomy import persist_goal_state
    from .goal_lifecycle import goal_lifecycle_lock

    gid = int(goal_id)
    with goal_lifecycle_lock(gid):
        current = world.get_goal(gid)
        if current is None:
            raise RuntimeError(f"goal {gid} disappeared during failure transition")
        if current.status == "cancelled":
            return False
        persist_goal_state(world, gid, state)
        world.set_goal_status(gid, status, result=result)
        return True


__all__ = [
    "check_goal_control",
    "set_goal_failure_preserving_cancellation",
    "set_goal_status_preserving_cancellation",
]
