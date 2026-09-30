"""Typed native connection diagnostics and durable schedule controls for Copilot."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException
from maverick.governed_operations import digest
from pydantic import Field

from .auth import caller_principal, goal_owner_filter, require_permission
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class ConnectionInput(StrictInput):
    name: str = Field(min_length=1, max_length=64)


def connection_snapshot(request, selection):
    from .api import _require_connections
    from .connection_dependencies import owned_connections

    require_permission(request, "operate")
    _require_connections()
    rows = owned_connections(request)
    return Snapshot(digest(rows), "Connections", "Review readiness and affected work for one owned account.",
        fields=[{"name": "name", "label": "Saved connection", "kind": "select", "required": True,
                 "choices": [{"value": row["name"], "label": row["name"] + " / " + row["connector"]} for row in rows]}],
        blocked="No owned connection is available in this workspace." if not rows else "")


def connection_prepare(request, selection, arguments):
    from .connection_dependencies import impact

    value = impact(request, arguments["name"])
    return Preview("Review the current connection and its scoped dependencies. No provider call or credential change occurs.",
        native={"name": value["name"], "revision": value["revision"], "impact_version": value["impact_version"], "context": value["context"]})


def connection_execute(request, selection, arguments, native, operation):
    from .connection_dependencies import require_review

    value = require_review(request, native["name"], native["revision"], native["impact_version"], expected_context=native["context"])
    return Outcome("confirmed", "Current scoped diagnostics reviewed. Authentication and exact task access remain separate checks.", value)


class ScheduleInput(StrictInput):
    identity: str = Field(min_length=1, max_length=128)
    state: Literal["active", "paused", "retired"]


def _schedules(request):
    from maverick.job_queue import JobQueue
    from maverick.schedule_definitions import list_definitions

    from .api import _require_scheduling

    require_permission(request, "operate")
    _require_scheduling()
    return list_definitions(JobQueue(), owner=goal_owner_filter(request))


def schedule_snapshot(request, selection):
    rows = _schedules(request)
    return Snapshot(digest(rows), "Automations", "Review future schedule admission and durable history.",
        details={"schedules": rows}, fields=[
            {"name": "identity", "label": "Schedule", "kind": "select", "required": True,
             "choices": [{"value": row["definition_id"], "label": row["title"] + " / " + row["state"]} for row in rows]},
            {"name": "state", "label": "Future runs", "kind": "select", "required": True,
             "choices": [{"value": value, "label": label} for value, label in
                         (("active", "Activate future runs"), ("paused", "Pause future runs"), ("retired", "Retire and retain history"))]}],
        blocked="No durable schedule is available in this workspace." if not rows else "")


def schedule_prepare(request, selection, arguments):
    row = next((item for item in _schedules(request) if item["definition_id"] == arguments["identity"]), None)
    if row is None:
        raise HTTPException(404, "Schedule is unavailable.")
    return Preview("Change future schedule admission. Already admitted attempts are not cancelled or replayed.",
        changes=[{"label": row["title"], "before": row["state"], "after": arguments["state"]}],
        native={"identity": row["definition_id"], "revision": row["revision"], "state": arguments["state"]})


def schedule_recover(request, selection, arguments, native, operation):
    from maverick.job_queue import JobQueue

    _schedules(request)
    with JobQueue()._conn() as connection:
        row = connection.execute("SELECT revision FROM schedule_definition_versions WHERE id=? AND rationale=? AND actor=?",
            (native["identity"], "Copilot reviewed schedule change " + operation, caller_principal(request) or "local")).fetchone()
    if row:
        return Outcome("replayed", "This exact reviewed schedule change was already recorded. No work was dispatched again.", {"revision": row["revision"]})
    return None


def schedule_execute(request, selection, arguments, native, operation):
    from maverick.job_queue import JobQueue
    from maverick.schedule_definitions import change_definition

    from .app import _require_same_origin

    _require_same_origin(request)
    _schedules(request)
    recovered = schedule_recover(request, selection, arguments, native, operation)
    if recovered is not None:
        return recovered
    try:
        result = change_definition(JobQueue(), native["identity"], expected_revision=native["revision"],
            state=native["state"], owner=goal_owner_filter(request), actor=caller_principal(request) or "local",
            rationale="Copilot reviewed schedule change " + operation)
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, "The schedule changed. Refresh native status before reviewing again.") from exc
    return Outcome("confirmed", "Future schedule admission changed. Existing attempts and history were retained.", result)


class RecoveryInput(StrictInput):
    job_id: int = Field(ge=1)


def recovery_snapshot(request, selection):
    from maverick.job_queue import JobQueue

    require_permission(request, "admin")
    with JobQueue()._conn() as connection:
        rows = [dict(row) for row in connection.execute("SELECT id,attempts,status,updated_at FROM jobs WHERE recurrence_reconcile_required=1 ORDER BY id LIMIT 100")]
    return Snapshot(digest(rows), "Automations recovery", "Review a parked occurrence before repairing future recurrence.",
        fields=[{"name": "job_id", "label": "Parked occurrence", "kind": "select", "required": True,
                 "choices": [{"value": row["id"], "label": str(row["id"]) + " / " + row["status"]} for row in rows]}],
        blocked="No parked occurrence needs review." if not rows else "")


def recovery_prepare(request, selection, arguments):
    from .schedule_ops_api import recurrence_recovery_review

    value = recurrence_recovery_review(request, arguments["job_id"])
    return Preview("Inspect the exact recovery evidence. Future rearming or successor selection requires native administrator review.", native=value)


def recovery_execute(request, selection, arguments, native, operation):
    from .schedule_ops_api import recurrence_recovery_review

    value = recurrence_recovery_review(request, arguments["job_id"])
    if value["expected_evidence"] != native["expected_evidence"]:
        raise HTTPException(409, "Recovery evidence changed. Refresh Automations.")
    return Outcome("confirmed", "Recovery evidence reviewed. Historical outcomes remain unknown; no occurrence was replayed.", value)


def entries():
    return (
        Adapter("connections.review", "Review connection diagnostics", "connections", "/connections", ("/connections", "/automations"), ConnectionInput, connection_snapshot, connection_prepare, connection_execute, aliases=("check my connection",)),
        Adapter("schedules.change", "Change future schedule runs", "schedules", "/automations", ("/automations",), ScheduleInput, schedule_snapshot, schedule_prepare, schedule_execute, schedule_recover, aliases=("pause my schedule",)),
        Adapter("schedules.recovery", "Review parked schedule recovery", "schedules", "/automations", ("/automations",), RecoveryInput, recovery_snapshot, recovery_prepare, recovery_execute),
    )
