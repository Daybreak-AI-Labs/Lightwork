"""Keep kernel stop text out of the customer-facing conversation history."""
from __future__ import annotations

from dataclasses import asdict

from maverick.stop_reasons import stop_payload


def stop_notes(world, turns) -> dict[int, dict]:
    goals = {}
    notes = {}
    for turn in turns:
        if turn.role != "assistant":
            continue
        if turn.goal_id not in goals:
            goals[turn.goal_id] = world.get_goal(turn.goal_id)
        goal = goals[turn.goal_id]
        # A concurrent deletion must not fall back to the unscreened turn text.
        stop = (stop_payload(goal.status, goal.result) if goal is not None
                else stop_payload("failed", None))
        if stop:
            notes[turn.id] = stop
    return notes


def public_turns(world, turns) -> list[dict]:
    notes = stop_notes(world, turns)
    result = []
    for turn in turns:
        public = asdict(turn)
        stop = notes.get(turn.id)
        if stop:
            public["content"] = f"{stop['message']}\n\n{stop['action']}"
            public["stop"] = stop
        result.append(public)
    return result
