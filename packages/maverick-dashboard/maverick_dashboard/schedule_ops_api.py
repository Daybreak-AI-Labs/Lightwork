"""Create schedules from a plain timetable instead of a typed cron line.

``maverick schedule add`` in the app. The page sends choices (every day,
weekday, week or month at a time, in a chosen time zone); the server turns
them into the cron line with :func:`maverick.scheduler.timetable_cron` and
checks it with :func:`maverick.scheduler.next_run` before anything is queued.
A custom cron line is still accepted, but only from the page's Technical
details and only after the same server check.

Saved workflows and described tasks go through the existing
``POST /api/v1/schedules`` handler (owner stamping, department limits,
template rendering). Re-running an earlier task queues the built-in
``run_goal`` job exactly like ``maverick schedule add <cron> run_goal``, after
the per-task access check. Every schedule writes one audit row first.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .auth import (
    assert_goal_access,
    caller_principal,
    caller_suites,
    execution_user_id_from_request,
    goal_owner_filter,
    require_permission,
)

router = APIRouter(tags=["schedules"])
MAX_ZONE = 64


@lru_cache(maxsize=1)
def _zones() -> tuple[str, ...]:
    try:
        from zoneinfo import available_timezones

        zones = {z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "posix/", "right/"))}
    except Exception:  # pragma: no cover - platform without tz data
        zones = set()
    return tuple(sorted(zones | {"UTC"}))


def valid_zone(zone: str) -> bool:
    """Whether ``zone`` is a known time zone name (never guessed or defaulted)."""
    return isinstance(zone, str) and 0 < len(zone) <= MAX_ZONE and zone in _zones()


class TimetableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repeat: Literal["hourly", "daily", "weekdays", "weekly", "monthly"] = "daily"
    at: str = Field("09:00", pattern=r"^\d{2}:\d{2}$")
    days: list[int] = Field(default_factory=list, max_length=7)
    day_of_month: int = Field(1, ge=1, le=28)
    every_hours: int = Field(1, ge=1, le=12)
    zone: str = Field("UTC", min_length=1, max_length=MAX_ZONE)
    # Technical details only: a custom five-part timetable, checked on the server.
    advanced: str | None = Field(None, max_length=120)


class PreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timetable: TimetableIn


class ScheduleAddIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: Literal["workflow", "task", "rerun"]
    template: str | None = Field(None, max_length=120)
    params: dict[str, str] | None = None
    text: str | None = Field(None, max_length=16_000)
    title: str | None = Field(None, max_length=200)
    goal_id: int | None = Field(None, ge=1)
    timetable: TimetableIn


def _cron(timetable: TimetableIn) -> tuple[str, str, list[float]]:
    """(cron, zone, next three runs) or a plain 400."""
    from maverick.scheduler import CronError, timetable_cron, upcoming_runs

    if not valid_zone(timetable.zone):
        raise HTTPException(400, "Choose a time zone from the list.")
    try:
        if timetable.advanced and timetable.advanced.strip():
            cron = " ".join(timetable.advanced.split())
        else:
            cron = timetable_cron(timetable.repeat, at=timetable.at, days=timetable.days,
                                  day_of_month=timetable.day_of_month, every_hours=timetable.every_hours)
        runs = upcoming_runs(cron, tz=timetable.zone, count=3)
    except (CronError, ValueError) as exc:
        raise HTTPException(400, "That timetable isn't valid. Check the days and time, or clear the custom "
                                 "timetable in Technical details.") from exc
    return cron, timetable.zone, runs


def _audit(request: Request, **data) -> None:
    from maverick.audit import AuditRefused, audit_event

    try:
        audit_event("schedule_created", agent="dashboard", actor=caller_principal(request) or "local", **data)
    except AuditRefused as exc:
        raise HTTPException(503, "The activity log couldn't record this schedule, so it wasn't created.") from exc


@router.get("/api/v1/schedules/options")
def schedule_options(request: Request) -> dict:
    """What a person can schedule and the time zones to choose from."""
    from maverick.config import get_features

    from .app import _world, template_market_entries

    require_permission(request, "operate")
    try:
        workflows = [{"name": e["name"], "title": e.get("title") or e["name"], "params": list(e.get("params") or [])}
                     for e in template_market_entries()][:200]
    except Exception:  # an unreadable template folder still lets people schedule tasks
        workflows = []
    try:
        goals = _world().list_goals(owner=goal_owner_filter(request), limit=50)
        tasks = [{"id": g.id, "title": (g.title or "Untitled task")[:200]} for g in goals]
    except Exception:
        tasks = []
    return {"enabled": bool(get_features().get("scheduling", True)), "zones": list(_zones()),
            "workflows": workflows, "tasks": tasks}


@router.post("/api/v1/schedules/preview")
def schedule_preview(request: Request, body: PreviewIn) -> dict:
    """The next three run times for a timetable. Nothing is saved."""
    require_permission(request, "operate")
    cron, zone, runs = _cron(body.timetable)
    return {"cron": cron, "zone": zone, "next_runs": runs}


@router.post("/api/v1/schedules/add", status_code=201)
async def schedule_add(request: Request, body: ScheduleAddIn) -> dict:
    """Arm a recurring schedule from plain choices."""
    from .api import _require_scheduling, create_schedule
    from .api_schemas import ScheduleIn
    from .app import _require_same_origin, _world

    require_permission(request, "operate")
    _require_same_origin(request)
    _require_scheduling()
    cron, zone, _ = _cron(body.timetable)
    if body.what != "rerun":
        if body.what == "workflow" and not (body.template or "").strip():
            raise HTTPException(400, "Choose a saved workflow to schedule.")
        if body.what == "task" and not (body.text or "").strip():
            raise HTTPException(400, "Describe the task to run on this schedule.")
        _audit(request, what=body.what, cron=cron, zone=zone)
        payload = ScheduleIn(cron=cron, tz=zone, title=body.title,
                             template=(body.template or None) if body.what == "workflow" else None,
                             params=body.params if body.what == "workflow" else None,
                             text=body.text if body.what == "task" else None)
        return (await create_schedule(request, payload)).model_dump()
    if body.goal_id is None:
        raise HTTPException(400, "Choose the earlier task to run again.")
    goal = _world().get_goal(body.goal_id)
    if goal is None:
        raise HTTPException(404, "no such goal")
    assert_goal_access(request, goal)
    from maverick.job_queue import JobQueue
    from maverick.scheduler import schedule_cron

    schedule_id = uuid4().hex
    title = (body.title or goal.title or "Run an earlier task again")[:200]
    job_payload = {"goal_id": int(body.goal_id), "title": title, "__cron__": cron, "__tz__": zone,
                   "schedule_id": schedule_id}
    owner = caller_principal(request) or ""
    if owner:
        job_payload["owner"] = owner
    suites = caller_suites(request)
    if suites is not None:
        job_payload["allowed_suites"] = sorted(suites)
    user_id = execution_user_id_from_request(request)
    if user_id:
        job_payload["channel"] = "api"
        job_payload["user_id"] = user_id
    _audit(request, what="rerun", goal=int(body.goal_id), cron=cron, zone=zone)
    job_id, run_at = schedule_cron(JobQueue(), cron, "run_goal", job_payload)
    return {"id": job_id, "cron": cron, "kind": "run_goal", "title": title, "next_run": run_at,
            "schedule_id": schedule_id, "zone": zone}


class ScheduleChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    state: Literal["active", "paused", "retired"]
    title: str | None = Field(None, min_length=1, max_length=200)
    timetable: TimetableIn | None = None
    confirm: bool = False


class RecurrenceRecoveryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_attempts: int = Field(ge=0, strict=True)
    expected_evidence: str = Field(pattern=r"^[a-f0-9]{64}$")
    context: str = Field(pattern=r"^[a-f0-9]{64}$")
    successor_id: int | None = Field(None, ge=1, strict=True)
    rearm: bool = False
    rationale: str = Field(min_length=5, max_length=1000)
    confirm: bool = False


@router.post("/api/v1/schedules/definitions/{identity}")
def change_schedule(request: Request, identity: str, body: ScheduleChangeIn) -> dict:
    from maverick.job_queue import JobQueue
    from maverick.schedule_definitions import change_definition

    from .api import _require_scheduling
    from .app import _require_same_origin

    require_permission(request, "operate")
    _require_same_origin(request)
    _require_scheduling()
    if not body.confirm:
        raise HTTPException(422, "Review the effect on future runs and confirm the change.")
    changes = {}
    if body.timetable:
        cron, zone, _ = _cron(body.timetable)
        changes.update(cron=cron, zone=zone)
    try:
        return change_definition(JobQueue(), identity, expected_revision=body.expected_revision,
                                 state=body.state, title=body.title, owner=goal_owner_filter(request),
                                 actor=caller_principal(request) or "local", **changes)
    except KeyError as exc:
        raise HTTPException(404, "Schedule is unavailable.") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/api/v1/schedules/recovery/{job_id}")
def recurrence_recovery_review(request: Request, job_id: int) -> dict:
    from maverick.job_queue import JobQueue
    from maverick.schedule_definitions import recovery_evidence

    from .draft_api import context_for
    from .queue_status_api import redacted_error

    require_permission(request, "admin")
    queue = JobQueue()
    with queue._conn() as connection:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or not row["recurrence_reconcile_required"]:
            raise HTTPException(409, "This occurrence no longer needs recurrence recovery. Refresh schedules.")
        candidates = connection.execute(
            "SELECT id,status,scheduled_at FROM jobs WHERE id>? AND kind=? AND recurrence_payload=? ORDER BY id",
            (job_id, row["kind"], row["recurrence_payload"]),
        ).fetchall()
        evidence = recovery_evidence(connection, row)
        return {"job_id": job_id, "attempts": row["attempts"], "context": context_for(request),
                "expected_evidence": evidence, "scheduled_at": row["scheduled_at"],
                "status": row["status"], "problem": redacted_error(row["last_error"]),
                "external_outcome": "unknown", "successors": [dict(item) for item in candidates],
                "consequence": "Only future recurrence is repaired. The historical action is never replayed. Confirm old workers are stopped before continuing."}


@router.post("/api/v1/schedules/recovery/{job_id}")
def recurrence_recovery_apply(request: Request, job_id: int, body: RecurrenceRecoveryIn) -> dict:
    import hmac

    from maverick.job_queue import JobQueue

    from .app import _require_same_origin
    from .draft_api import context_for

    require_permission(request, "admin")
    _require_same_origin(request)
    if not hmac.compare_digest(body.context, context_for(request)):
        raise HTTPException(409, "Account or workspace changed. Review recovery again.")
    try:
        successor = JobQueue().reconcile_recurrence(job_id, expected_attempts=body.expected_attempts,
            successor_id=body.successor_id, rearm=body.rearm, confirm=body.confirm,
            expected_evidence=body.expected_evidence, actor=caller_principal(request) or "local",
            rationale=body.rationale)
    except (ValueError, KeyError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"repaired": True, "job_id": job_id, "successor_id": successor,
            "historical_replayed": False, "external_outcome": "unknown"}


@router.get("/api/v1/schedules/definitions/{identity}/history")
def schedule_history(request: Request, identity: str) -> dict:
    from maverick.job_queue import JobQueue
    from maverick.schedule_definitions import list_definitions

    require_permission(request, "view")
    queue = JobQueue()
    selected = next((item for item in list_definitions(queue, owner=goal_owner_filter(request)) if item["definition_id"] == identity), None)
    if selected is None:
        raise HTTPException(404, "Schedule is unavailable.")
    with queue._conn() as connection:
        versions = connection.execute(
            "SELECT revision,state,actor,rationale,created_at FROM schedule_definition_versions WHERE id=? ORDER BY revision",
            (identity,),
        ).fetchall()
    return {"schedule": selected, "versions": [dict(item) for item in versions],
            "historical_execution_replay": "never performed by schedule management"}
