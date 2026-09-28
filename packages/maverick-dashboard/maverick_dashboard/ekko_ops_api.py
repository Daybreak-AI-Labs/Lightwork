"""Run an Ekko recorder from the app (``maverick ekko run``).

Two recorders, both the command's own: an uploaded guided activity file read
through :class:`maverick.ekko_daemon.GuidedJsonlObserver` (``--events``), and,
only when Lightwork itself runs on Windows, the active-app recorder
:class:`maverick.ekko_daemon.WindowsForegroundObserver` (``--observer
windows``). Either feeds :class:`maverick.ekko_daemon.EkkoDaemon`, which keeps
every existing check: the device's current enrollment, the administrator's
limits, the exclusive recorder lease and the emergency stop.

Access matches the Ekko page's session controls: ``operate``, the same-origin
guard, and a store scoped to the signed-in person and selected workspace, with
a device chosen inside that scope. Starting a recorder writes the same
required "start authorized" audit row the command writes. Recording runs as a
background job; the page's Pause and Stop buttons end it.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from . import auth

router = APIRouter(tags=["ekko"])
MAX_FILE_CHARS = 1_000_000


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field("local-device", min_length=1, max_length=64)
    events: str = Field(min_length=1, max_length=MAX_FILE_CHARS)
    acknowledge_capture_scope: bool = False


class WatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field("local-device", min_length=1, max_length=64)
    acknowledge_capture_scope: bool = False


def _windows() -> bool:
    return os.name == "nt"


def _prepare(request: Request, device_id: str, acknowledged: bool, observer: str):
    from maverick.audit import EventKind

    from . import ekko_routes as ekko

    ekko._app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    if not acknowledged:
        raise HTTPException(400, "Confirm that you have read what Ekko may and may not record.")
    ekko._require_enabled_policy()
    store = ekko._store(request, device_id)
    policy = ekko._validated_enrolled_policy(store)
    if observer == "guided_jsonl" and policy.capture_level != "guided":
        raise HTTPException(409, "Activity files need detailed recording. An administrator can switch Ekko to "
                                 "detailed recording, then enroll this device again.")
    ekko._audit(EventKind.EKKO_SESSION, required=True, state="start_authorized",
                policy_digest=policy.fingerprint(), observer=observer)
    return store, policy


def _record(store, policy, observer) -> dict:
    from maverick.ekko_daemon import EkkoDaemon

    daemon = EkkoDaemon(store=store, observer=observer, policy=policy)
    try:
        daemon.run(stop_when=lambda: bool(getattr(observer, "exhausted", False)))
    except ValueError as exc:
        line = getattr(observer, "line_number", 0)
        message = (f"Line {line} of the file isn't an activity Ekko can use. Activities recorded before it were kept."
                   if line else "The file couldn't be read as activities.")
        if "exceeds" in str(exc):
            message = f"Line {line} of the file is too long. Activities recorded before it were kept."
        return {"recorded": daemon.accepted_events, "skipped": daemon.blocked_events, "message": message}
    except RuntimeError:
        return {"recorded": daemon.accepted_events, "skipped": daemon.blocked_events,
                "message": "Recording stopped because this device's enrollment or limits changed."}
    finally:
        try:
            daemon.stop()
        finally:
            close = getattr(observer, "close", None)
            if callable(close):
                close()
    return {"recorded": daemon.accepted_events, "skipped": daemon.blocked_events}


@router.get("/api/v1/ekko/recording/options")
def recording_options(request: Request) -> dict:
    """Which recorders this installation can run."""
    auth.require_permission(request, "operate")
    return {"activity_file": True, "windows_active_app": _windows(), "max_file_chars": MAX_FILE_CHARS}


@router.post("/api/v1/ekko/recording/import", status_code=202)
def import_activity_file(request: Request, body: ImportIn) -> dict:
    """Record the activities in an uploaded guided activity file."""
    from maverick.ekko_daemon import GuidedJsonlObserver

    from .ops_jobs import JOBS, Busy, identity

    store, policy = _prepare(request, body.device_id, body.acknowledge_capture_scope, "guided_jsonl")
    observer = GuidedJsonlObserver.from_text(body.events)
    owner, tenant = identity(request)
    try:
        job = JOBS.start(owner=owner, tenant=tenant, kind="ekko-recording",
                         work=lambda: _record(store, policy, observer))
    except Busy as exc:
        observer.close()
        raise HTTPException(429, "A recording is already running. Wait for it to finish.") from exc
    return {"job": job}


@router.post("/api/v1/ekko/recording/watch", status_code=202)
def watch_active_app(request: Request, body: WatchIn) -> dict:
    """Record which allowed app is in front, until Pause or Stop (Windows only)."""
    from .ops_jobs import JOBS, Busy, identity

    if not _windows():
        raise HTTPException(409, "Recording the app in front is only available when Lightwork runs on Windows.")
    store, policy = _prepare(request, body.device_id, body.acknowledge_capture_scope, "windows_foreground")
    try:
        from maverick.ekko_daemon import WindowsForegroundObserver

        observer = WindowsForegroundObserver()
    except (ImportError, OSError, RuntimeError) as exc:
        raise HTTPException(409, "The app recorder isn't available on this computer.") from exc
    owner, tenant = identity(request)
    try:
        job = JOBS.start(owner=owner, tenant=tenant, kind="ekko-recording",
                         work=lambda: _record(store, policy, observer))
    except Busy as exc:
        raise HTTPException(429, "A recording is already running. Stop it first.") from exc
    return {"job": job}


@router.get("/api/v1/ekko/recording/{job_id}")
def recording_status(request: Request, job_id: str) -> dict:
    from .ops_jobs import JOBS, identity

    auth.require_permission(request, "operate")
    owner, tenant = identity(request)
    job = JOBS.get(job_id[:64], owner=owner, tenant=tenant, kind="ekko-recording")
    if job is None:
        raise HTTPException(404, "no such recording")
    return job
