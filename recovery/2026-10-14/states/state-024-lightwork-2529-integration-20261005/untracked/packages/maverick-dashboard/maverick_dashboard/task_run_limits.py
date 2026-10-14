"""Plain-language, task-local run limits for the Chat composer."""
from __future__ import annotations

import math
import re

from fastapi import HTTPException

MAX_TASK_SPEND_DOLLARS = 100.0
MAX_TASK_MINUTES = 24 * 60
MAX_TASK_DEPTH = 10


def depth_ceiling() -> int:
    """Return the current workspace ceiling, bounded by the public task API."""
    from maverick.env_setting import env_or_config_int

    try:
        return max(1, min(MAX_TASK_DEPTH, env_or_config_int(
            "MAVERICK_DEFAULT_MAX_DEPTH", "swarm", "default_max_depth", 3)))
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Task limits are unavailable. Ask your workspace manager to review Settings.") from exc


def _number(raw: str, *, label: str, maximum: float) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None
    if not re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", value):
        raise HTTPException(422, f"Enter {label} as a non-negative amount with up to two decimal places.")
    result = float(value)
    if not math.isfinite(result) or result > maximum:
        raise HTTPException(422, f"{label.capitalize()} cannot be more than {maximum:g}.")
    return result


def read_chat_task_limits(*, max_dollars: str = "", max_wall_minutes: str = "", max_depth: str = "") -> dict:
    """Validate the optional Chat overrides and apply current workspace policy."""
    from maverick.runner import requested_goal_dollars, requested_goal_wall_seconds

    dollars = _number(max_dollars, label="maximum spend", maximum=MAX_TASK_SPEND_DOLLARS)
    minutes_text = (max_wall_minutes or "").strip()
    if minutes_text:
        if not re.fullmatch(r"\d{1,4}", minutes_text):
            raise HTTPException(422, "Enter the time limit as a whole number of minutes.")
        minutes = int(minutes_text)
        if not 1 <= minutes <= MAX_TASK_MINUTES:
            raise HTTPException(422, f"The time limit must be between 1 and {MAX_TASK_MINUTES} minutes.")
        wall_seconds = float(minutes * 60)
    else:
        wall_seconds = None

    depth_text = (max_depth or "").strip()
    if depth_text:
        if not re.fullmatch(r"\d{1,2}", depth_text):
            raise HTTPException(422, "Choose one of the listed amounts of extra help.")
        depth = int(depth_text)
        if not 1 <= depth <= depth_ceiling():
            raise HTTPException(422, "That amount of extra help is above the current workspace limit. Refresh Chat and try again.")
    else:
        depth = None

    try:
        effective_dollars = requested_goal_dollars(dollars) if dollars is not None else None
        effective_seconds = requested_goal_wall_seconds(wall_seconds) if wall_seconds is not None else None
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Task limits are unavailable. Ask your workspace manager to review Settings.") from exc

    if dollars is not None and effective_dollars != dollars:
        raise HTTPException(422, "That spending limit is above the current workspace limit. Review Settings or choose a lower amount.")
    if wall_seconds is not None and effective_seconds != wall_seconds:
        raise HTTPException(422, "That time limit is above the current workspace limit. Review Settings or choose a shorter time.")
    return {"max_dollars": effective_dollars, "max_wall_seconds": effective_seconds, "max_depth": depth}


def guard_chat_task_limits(limits: dict) -> None:
    """Fail closed when an operator tightens a ceiling after task admission."""
    from maverick.runner import (
        configured_goal_ceiling_dollars,
        configured_goal_ceiling_wall_seconds,
    )

    dollars = limits.get("max_dollars")
    seconds = limits.get("max_wall_seconds")
    depth = limits.get("max_depth")
    try:
        dollar_ceiling = configured_goal_ceiling_dollars()
        seconds_ceiling = configured_goal_ceiling_wall_seconds()
        current_depth = depth_ceiling()
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Task limits are unavailable. Ask your workspace manager to review Settings.") from exc
    if dollars is not None and dollar_ceiling is not None and dollars > dollar_ceiling:
        raise HTTPException(409, "The workspace spending limit changed. Review this task’s limits before starting it.")
    if seconds is not None and seconds_ceiling is not None and seconds > seconds_ceiling:
        raise HTTPException(409, "The workspace time limit changed. Review this task’s limits before starting it.")
    if depth is not None and depth > current_depth:
        raise HTTPException(409, "The workspace limit on extra help changed. Refresh Chat before starting this task.")


def limits_from_receipt(receipt: dict) -> tuple[dict, dict]:
    """Read only a complete, bounded set of task limits from a saved receipt."""
    if not isinstance(receipt, dict):
        raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    detail = receipt.get("detail", {})
    if not isinstance(detail, dict):
        raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    limits = {"max_dollars": None, "max_wall_seconds": None, "max_depth": None}
    raw = detail.get("task_limits")
    if raw is None:
        return detail, limits
    if not isinstance(raw, dict) or set(raw) != set(limits):
        raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    for key in ("max_dollars", "max_wall_seconds"):
        value = raw[key]
        upper = MAX_TASK_SPEND_DOLLARS if key == "max_dollars" else MAX_TASK_MINUTES * 60
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or not math.isfinite(value) or not 0 <= value <= upper):
            raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    if raw["max_wall_seconds"] is not None and raw["max_wall_seconds"] < 60:
        raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    depth = raw["max_depth"]
    if depth is not None and (isinstance(depth, bool) or not isinstance(depth, int)
                              or not 1 <= depth <= MAX_TASK_DEPTH):
        raise HTTPException(409, "This task’s limits cannot be verified. Start a new task and review its limits.")
    return detail, raw
