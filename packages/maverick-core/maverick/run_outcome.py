"""Typed execution control outcomes; display text never grants replay authority."""
from __future__ import annotations

import asyncio


class GoalCancelled(asyncio.CancelledError):
    """The goal owner requested cancellation of this owned execution."""


class GoalAdmissionRefused(str):
    """String-compatible blocked status issued before orchestration starts.

    This certificate concerns this invocation only, not prior attempts of an
    existing goal. The flow binding may use it because it just created the
    child goal. Persisting/displaying the string must not preserve replay
    authority: a reconstructed ordinary ``"blocked"`` is not this type.
    """

    def __new__(cls, goal_id: int, *, stop_kind: str, detail: str):
        value = super().__new__(cls, "blocked")
        value.goal_id = goal_id
        value.stop_kind = stop_kind
        value.detail = detail
        return value


def cancellation_message(error: BaseException) -> str:
    if getattr(error, "tool_execution_unknown", False):
        return (
            "Cancelled. An in-flight tool may have committed an external effect; "
            "review its outcome before retrying the work."
        )
    return "Cancelled by the goal owner." if isinstance(error, GoalCancelled) else "cancelled"
