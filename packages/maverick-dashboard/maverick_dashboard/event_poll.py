"""Request-less poll-and-fire for event triggers.

Shared by the HTTP endpoint (``POST /api/v1/event-triggers/poll``) and the
background automation scheduler (app lifespan). Each polls its sources, fires
the bound template per new event -- the event's fields filling the template's
declared params -- and advances the cursor at most once. ``fire(goal_id)``
schedules the actual run: a FastAPI BackgroundTask in the request path, a daemon
thread in the scheduler.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


def poll_and_fire(world: Any, triggers: list[dict], *, principal: str = "",
                  fire: Callable[[int], None]) -> list[dict]:
    """Poll each trigger and fire the template per new event. Returns a summary
    per trigger: ``{name, new_events, fired: [goal_id...], notes}``. Never raises
    for a single bad source/template -- it is recorded in that trigger's notes."""
    from maverick.automation_events import EventSourceError, poll_source
    from maverick.templates import load_template

    from maverick_dashboard import event_triggers_store

    results: list[dict] = []
    for trig in triggers:
        summary: dict = {"name": trig["name"], "new_events": 0, "fired": [], "notes": []}
        try:
            res = poll_source(trig["source"], trig["config"], trig["cursor"])
        except EventSourceError as e:
            summary["notes"].append(f"poll failed: {e}")
            results.append(summary)
            continue
        try:
            tpl = load_template(trig["template"])
        except (ValueError, FileNotFoundError) as e:
            # A deleted/renamed template shouldn't wedge the trigger on its
            # backlog forever: note it and advance the cursor past what we saw.
            summary["notes"].append(f"template unavailable: {e}")
            event_triggers_store.set_cursor(trig["name"], res.cursor)
            results.append(summary)
            continue
        declared = set(tpl.params)
        summary["new_events"] = len(res.events)
        for event in res.events:
            params = dict(trig.get("params") or {})
            for k, v in event.items():          # event fields fill DECLARED params only
                if k in declared:
                    params[str(k)] = str(v)[:2000]
            try:
                title, description = tpl.render(**params)
            except ValueError as e:             # a required param the event didn't supply
                summary["notes"].append(f"skipped an event: {e}")
                continue
            goal_id = world.create_goal(title[:200], description[:8000], owner=principal)
            world.record_goal_origin(goal_id, "event", trig["name"])
            fire(goal_id)
            summary["fired"].append(goal_id)
        event_triggers_store.set_cursor(trig["name"], res.cursor)
        results.append(summary)
    return results
