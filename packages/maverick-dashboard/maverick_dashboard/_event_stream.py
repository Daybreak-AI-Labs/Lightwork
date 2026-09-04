"""Shared durable replay rules; wire formats remain transport-specific."""
from __future__ import annotations

TERMINAL_RUN_STATUSES = frozenset({"done", "completed", "failed", "cancelled", "blocked", "error"})


def replay_cursor(since: object = 0, last_event_id: object = None) -> int:
    """Honor reconnect progress without inventing IDs outside SQLite's range."""
    cursor = 0
    for value in (since, last_event_id):
        try:
            cursor = max(cursor, min(int(value), 2**63 - 1))
        except (TypeError, ValueError, OverflowError):
            pass
    return cursor


def event_page(world, goal_id: int, since: int, limit: int):
    """Read one bounded page with an observed indication of remaining replay."""
    limit = max(1, min(limit, 500))
    events = world.goal_events(goal_id, since_id=since, limit=limit + 1)
    return events[:limit], len(events) > limit
