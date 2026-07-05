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


def _fire_template(world, trig, tpl, events, owner, fire, summary) -> None:
    """Render the bound template per event (event fields fill DECLARED params)
    and fire a goal. Mutates ``summary``."""
    from maverick_dashboard import trigger_events_store
    name = trig["name"]
    declared = set(tpl.params)
    for event in events:
        params = dict(trig.get("params") or {})
        for k, v in event.items():      # event fields fill DECLARED params only
            if k in declared:
                params[str(k)] = str(v)[:2000]
        try:
            title, description = tpl.render(**params)
        except ValueError as e:         # a required param the event didn't supply
            summary["notes"].append(f"skipped an event: {e}")
            trigger_events_store.record(name, owner, trigger_events_store.EVENT_SKIPPED, note=str(e))
            continue
        goal_id = world.create_goal(title[:200], description[:8000], owner=owner)
        world.record_goal_origin(goal_id, "event", name)
        fire(goal_id)
        summary["fired"].append(goal_id)


def _event_key(name: str, event: dict) -> str:
    """A stable idempotency key for an event: the trigger name + the event's own
    id (or a hash of its fields), so a re-delivered event doesn't fire twice."""
    import hashlib
    import json as _json
    eid = event.get("id") or event.get("guid") or event.get("uid")
    if eid is None:
        # A dedup fingerprint, not a security digest -> usedforsecurity=False.
        digest = hashlib.sha1(_json.dumps(event, sort_keys=True, default=str).encode(),
                              usedforsecurity=False)
        eid = digest.hexdigest()[:16]
    return f"{name}:{eid}"


def _fire_flow(trig, events, owner, fire_flow, summary) -> None:
    """Start a flow run per event, with the event's fields as the run's data
    (the trigger's ``params`` are defaults). Each fire carries an idempotency key
    so a re-delivered event is deduplicated. Mutates ``summary``; run ids land in
    ``summary['fired_flows']``."""
    flow_id = trig["flow"]
    for event in events:
        data = dict(trig.get("params") or {})
        for k, v in event.items():   # a flow takes ALL event fields as data (no declared set)
            data[str(k)] = v if isinstance(v, (int, float, bool)) else str(v)[:2000]
        run_id = fire_flow(flow_id, data, owner, _event_key(trig["name"], event),
                           f"event:{trig['name']}")
        if run_id:
            summary["fired_flows"].append(run_id)


def _deliver(world, trig, res, owner, fire, fire_flow, summary) -> bool:
    """Dispatch a trigger's new events to its target (bound flow, else template).
    Returns False when nothing was delivered and the caller must NOT advance the
    cursor past these events (missing target, or no flow dispatcher wired);
    otherwise True. The missing-target cases advance the cursor themselves so a
    permanent backlog can't wedge the trigger."""
    from maverick.templates import load_template

    from maverick_dashboard import event_triggers_store, trigger_events_store
    name = trig["name"]
    if trig.get("flow"):
        from maverick.flow import store as flow_store
        if flow_store.load_flow(trig["flow"]) is None:
            summary["notes"].append(f"flow unavailable: {trig['flow']}")
            event_triggers_store.set_cursor(name, res.cursor)   # don't wedge on backlog
            trigger_events_store.record(name, owner, trigger_events_store.TEMPLATE_UNAVAILABLE,
                                        note=f"flow {trig['flow']} not found")
            return False
        if fire_flow is None:
            # No flow dispatcher in this context (e.g. a caller that only wires
            # template fires). Leave the cursor so these events retry once a
            # dispatcher is available rather than silently advancing past them.
            summary["notes"].append("flow firing unavailable in this context")
            return False
        _fire_flow(trig, res.events, owner, fire_flow, summary)
        return True
    try:
        tpl = load_template(trig["template"])
    except (ValueError, FileNotFoundError) as e:
        summary["notes"].append(f"template unavailable: {e}")
        event_triggers_store.set_cursor(name, res.cursor)
        trigger_events_store.record(name, owner, trigger_events_store.TEMPLATE_UNAVAILABLE, note=str(e))
        return False
    _fire_template(world, trig, tpl, res.events, owner, fire, summary)
    return True


def poll_and_fire(world: Any, triggers: list[dict], *, principal: str = "",
                  fire: Callable[[int], None],
                  fire_flow: Callable[..., Any] | None = None) -> list[dict]:
    """Poll each trigger and fire its target (template->goal, or flow) per new
    event. Returns a summary per trigger: ``{name, new_events, fired: [goal_id...],
    fired_flows: [run_id...], notes, poll_error}``. Never raises for a single bad
    trigger -- it's recorded in that trigger's notes + ``poll_error`` and the batch
    continues. ``fire_flow(flow_id, data, owner) -> run_id`` starts a flow run."""
    from maverick.automation_events import EventSourceError, poll_source
    from maverick.paths import reset_tenant, set_tenant

    from maverick_dashboard import event_triggers_store, trigger_events_store

    results: list[dict] = []
    for trig in triggers:
        # Attribute the fired goal to the trigger's OWN owner -- so the
        # background tick (which has no request principal and drains every
        # owner's triggers) doesn't create unowned goals. On the on-demand path
        # the trigger's owner is the caller, so this matches ``principal``.
        owner = trig.get("owner") or principal
        name = trig["name"]
        summary: dict = {"name": name, "new_events": 0, "fired": [], "fired_flows": [],
                         "notes": [], "poll_error": False}
        results.append(summary)   # append the ref now; it's mutated in place below
        # Bind the poll (incl. any OAuth vault read) to the tenant that created
        # the trigger, so a polled source never reads a cross-tenant token.
        # Single-tenant deployments store "" and run under the active tenant.
        tok = set_tenant(trig["tenant"]) if trig.get("tenant") else None
        try:
            try:
                res = poll_source(trig["source"], trig["config"], trig["cursor"])
            except EventSourceError as e:
                summary["poll_error"] = True
                summary["notes"].append(f"poll failed: {e}")
                trigger_events_store.record(name, owner, trigger_events_store.POLL_ERROR, note=str(e))
                continue
            summary["new_events"] = len(res.events)
            if not _deliver(world, trig, res, owner, fire, fire_flow, summary):
                continue
            # Only touch the store when the cursor actually moved -- an idle poll
            # (no new items) shouldn't rewrite the whole trigger file every tick.
            if res.cursor != trig["cursor"]:
                event_triggers_store.set_cursor(name, res.cursor)
            if summary["fired"] or summary["fired_flows"]:
                trigger_events_store.record(name, owner, trigger_events_store.FIRED,
                                            new_events=summary["new_events"],
                                            fired=summary["fired"],
                                            fired_flows=summary["fired_flows"])
        except Exception as e:  # one trigger's unexpected error must not abort the batch
            summary["poll_error"] = True
            summary["notes"].append(f"trigger error: {type(e).__name__}: {e}")
            trigger_events_store.record(name, owner, trigger_events_store.POLL_ERROR,
                                        note=f"{type(e).__name__}: {e}")
        finally:
            if tok is not None:
                reset_tenant(tok)
    return results
