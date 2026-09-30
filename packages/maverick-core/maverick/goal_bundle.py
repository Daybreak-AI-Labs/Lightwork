"""A task's full trajectory as one portable bundle (``maverick export``).

The bundle holds the goal record, every event (up to 10,000) and the episode
summaries (up to 200). No prompt content is included unless it was logged to
events. Shared by the command line and the dashboard's download button so
both produce exactly the same file.
"""
from __future__ import annotations

from dataclasses import asdict

MAX_EVENTS = 10_000
MAX_EPISODES = 200


def build_goal_bundle(world, goal_id: int) -> dict | None:
    """The export bundle for ``goal_id``, or ``None`` when there is no such goal."""
    goal = world.get_goal(goal_id)
    if goal is None:
        return None
    events = world.goal_events(goal_id, limit=MAX_EVENTS)
    episodes = world.list_episodes(limit=MAX_EPISODES, goal_id=goal_id)
    return {
        "v": 1,
        "goal": asdict(goal),
        "events": [asdict(e) for e in events],
        "episodes": [asdict(e) for e in episodes],
    }


__all__ = ["MAX_EPISODES", "MAX_EVENTS", "build_goal_bundle"]
