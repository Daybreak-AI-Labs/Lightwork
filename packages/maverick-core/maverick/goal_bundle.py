"""A task's full trajectory as one portable bundle (``maverick export``).

The bundle holds the goal record, every event (up to 10,000) and the episode
summaries (up to 200). No prompt content is included unless it was logged to
events. Shared by the command line and the dashboard's download button so
both produce exactly the same file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

MAX_EVENTS = 10_000
MAX_EPISODES = 200


class IncompleteGoalBundle(ValueError):
    """Refuse an export that cannot truthfully represent the entire history."""


def build_goal_bundle(world, goal_id: int) -> dict | None:
    """The export bundle for ``goal_id``, or ``None`` when there is no such goal."""
    goal = world.get_goal(goal_id)
    if goal is None:
        return None
    events = world.goal_events(goal_id, limit=MAX_EVENTS + 1)
    episodes = world.list_episodes(limit=MAX_EPISODES + 1, goal_id=goal_id)
    if len(events) > MAX_EVENTS or len(episodes) > MAX_EPISODES:
        raise IncompleteGoalBundle(
            "This task exceeds the supported archive size. No partial history was downloaded. "
            "Use workspace backup to preserve the complete record."
        )
    refreshed = world.get_goal(goal_id)
    if refreshed is None or asdict(refreshed) != asdict(goal):
        raise IncompleteGoalBundle("The task changed while preparing the archive. Retry after the task settles.")
    bundle = {
        "v": 1,
        "goal": asdict(goal),
        "events": [asdict(e) for e in events],
        "episodes": [asdict(e) for e in episodes],
    }
    content = json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str).encode()
    bundle["manifest"] = {
        "schema": "lightwork.goal-archive.v1", "complete_within_export_limits": True,
        "event_count": len(events), "episode_count": len(episodes),
        "sha256": hashlib.sha256(content).hexdigest(),
        "scope": "Goal record, recorded events and episode summaries only. Attachments and external systems are not included.",
        "resumable": False, "redacted": False,
    }
    return bundle


__all__ = ["IncompleteGoalBundle", "MAX_EPISODES", "MAX_EVENTS", "build_goal_bundle"]
