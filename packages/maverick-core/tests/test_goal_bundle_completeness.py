"""Archive overflow must never become an apparently complete download."""
from dataclasses import dataclass

import pytest
from maverick.goal_bundle import MAX_EPISODES, MAX_EVENTS, IncompleteGoalBundle, build_goal_bundle


@dataclass
class Row:
    id: int


class ArchiveWorld:
    def __init__(self, events=0, episodes=0):
        self.events, self.episodes = events, episodes

    def get_goal(self, goal_id):
        return Row(goal_id)

    def goal_events(self, goal_id, limit):
        return [Row(i) for i in range(min(limit, self.events))]

    def list_episodes(self, limit, goal_id):
        return [Row(i) for i in range(min(limit, self.episodes))]


@pytest.mark.parametrize("events,episodes", [(MAX_EVENTS + 1, 0), (0, MAX_EPISODES + 1)])
def test_oversized_archive_visibly_refuses(events, episodes):
    with pytest.raises(IncompleteGoalBundle, match="No partial history"):
        build_goal_bundle(ArchiveWorld(events, episodes), 7)


def test_archive_manifest_records_explicit_scope():
    bundle = build_goal_bundle(ArchiveWorld(4, 2), 7)
    assert bundle["manifest"]["event_count"] == 4
    assert bundle["manifest"]["episode_count"] == 2
    assert bundle["manifest"]["redacted"] is False
    assert bundle["manifest"]["resumable"] is False
    assert len(bundle["manifest"]["sha256"]) == 64
