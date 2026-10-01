"""Ekko Work Discovery dashboard and owner-scoped human API.

This is deliberately *not* a collector ingress.  Endpoint collectors write to
the local tenant store through the core daemon; exposing a half-secure remote
token surface here would weaken the boundary.  Every resource lookup below is
constructed from the request-pinned tenant and authenticated caller, while a
bounded device id selects only a device inside that scope.
"""
from __future__ import annotations

import os
import re
import secrets
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from . import auth

router = APIRouter()

_DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_OPPORTUNITY_RE = re.compile(r"^[0-9a-f]{24}$")
_DEFAULT_DEVICE = "local-device"


class _StrictInput(BaseModel):
    """Reject injected ownership or tenancy fields instead of ignoring them."""

    model_config = ConfigDict(extra="forbid")


class EkkoControlIn(_StrictInput):
    enabled: bool


class DeviceIn(_StrictInput):
    device_id: str = Field(_DEFAULT_DEVICE, min_length=1, max_length=64)


class StartSessionIn(DeviceIn):
    acknowledge_capture_scope: bool = False


class HandoffIn(DeviceIn):
    kind: Literal["flow", "agent"] = "flow"


class EnrollIn(DeviceIn):
    apps: list[str] = Field(min_length=1, max_length=64)
    days: Literal[7, 14, 30, 60, 90] = 30
    acknowledge_capture_scope: bool = False


#: Readable names for Ekko's canonical application IDs.
_APP_NAMES = {
    "browser": "Web browser", "chrome": "Google Chrome", "edge": "Microsoft Edge",
    "firefox": "Firefox", "safari": "Safari", "spreadsheet": "Spreadsheet app",
    "excel": "Microsoft Excel", "google_sheets": "Google Sheets",
    "presentation": "Presentation app", "powerpoint": "Microsoft PowerPoint",
    "google_slides": "Google Slides", "document": "Document editor",
    "word": "Microsoft Word", "google_docs": "Google Docs", "file_manager": "File manager",
    "file_explorer": "File Explorer", "finder": "Finder", "analytics": "Analytics app",
    "power_bi": "Power BI", "tableau": "Tableau", "database": "Database tool",
    "crm": "Customer records (CRM)", "salesforce": "Salesforce", "erp": "Business system (ERP)",
    "sap": "SAP", "email": "Email", "outlook": "Microsoft Outlook", "gmail": "Gmail",
    "chat": "Chat", "teams": "Microsoft Teams", "slack": "Slack", "lightwork": "Lightwork",
}


def _app_name(app: str) -> str:
    return _APP_NAMES.get(app, app.replace("_", " ").capitalize())


def _app():
    # Included while maverick_dashboard.app is importing; resolve lazily.
    from . import app as app_module

    return app_module


def _device_id(value: str) -> str:
    value = str(value or "").strip()
    if not _DEVICE_RE.fullmatch(value):
        raise HTTPException(status_code=422, detail="Device names can use letters, numbers, dots, dashes, colons and underscores.")
    return value


def _session_id(value: str) -> str:
    value = str(value or "").strip()
    if not _SESSION_RE.fullmatch(value):
        # Resource ids are owner-scoped; malformed and foreign ids are both
        # non-existent to the caller.
        raise HTTPException(status_code=404, detail="no such Ekko session")
    return value


def _owner(request: Request) -> str:
    # Auth-off is bound to the current OS security context, matching the local
    # CLI. Authenticated deployments remain pinned to the verified principal.
    from maverick.work_discovery_identity import local_os_principal

    return auth.caller_principal(request) or local_os_principal()


def _store(request: Request, device_id: str):
    from maverick.paths import current_tenant_id
    from maverick.work_discovery_store import WorkDiscoveryStore

    return WorkDiscoveryStore(
        _owner(request),
        _device_id(device_id),
        tenant=current_tenant_id(),
    )


def _public_enrollment(value) -> dict | None:
    if value is None:
        return None
    raw = value.to_dict() if hasattr(value, "to_dict") else dict(value)
    # A policy digest is an internal binding value, not dashboard data.
    return {
        key: raw[key]
        for key in ("active", "enrolled_at", "updated_at", "expires_at")
        if key in raw
    }


def _public_session(value) -> dict | None:
    if value is None:
        return None
    raw = value.to_dict() if hasattr(value, "to_dict") else dict(value)
    return {
        key: raw[key]
        for key in (
            "session_id",
            "state",
            "started_at",
            "updated_at",
            "ended_at",
            "last_sequence",
        )
        if key in raw
    }


def _public_collector(value) -> dict:
    raw = dict(value or {})
    state = raw.get("state")
    if state not in {"live", "waiting", "stale"}:
        state = "waiting"
    return {
        key: raw[key]
        for key in ("session_id", "heartbeat_at", "expires_at")
        if key in raw
    } | {"state": state}


def _status_data(request: Request, device_id: str) -> dict:
    from maverick.config import get_ekko

    device = _device_id(device_id)
    policy = get_ekko()
    store = _store(request, device)
    # A read-only page visit must not enroll anything or even create the local
    # SQLite file. The store is opened only after a collector/enrollment has
    # established durable state.
    state = (
        store.status()
        if store.path.exists()
        else {
            "enrollment": None,
            "session": None,
            "event_count": 0,
            "collector": {"state": "waiting", "session_id": None},
        }
    )
    scoped_policy = None
    if state.get("enrollment") is not None:
        try:
            scoped_policy = store.get_policy()
        except Exception as exc:
            from maverick.work_discovery_store import WorkDiscoveryStoreError

            if isinstance(exc, WorkDiscoveryStoreError):
                raise HTTPException(
                    status_code=503,
                    detail="Ekko enrollment integrity could not be verified",
                ) from exc
            raise
    visible_policy = (
        scoped_policy.to_dict()
        if scoped_policy is not None
        else policy
    )
    blocked = set(policy.get("blocked_apps") or [])
    return {
        "enabled": bool(policy.get("enable", False)),
        "control_managed": "MAVERICK_EKKO" in os.environ,
        # What a person may choose from when enrolling: the admin ceiling,
        # minus anything the blocklist always wins over.
        "app_choices": [
            {"value": app, "label": _app_name(app)}
            for app in (policy.get("allowed_apps") or []) if app not in blocked
        ],
        "app_labels": {
            app: _app_name(app)
            for app in {*(visible_policy.get("allowed_apps") or []),
                        *(visible_policy.get("blocked_apps") or [])}
        },
        "enrollment_days": int(policy.get("enrollment_days", 30)),
        "can_control": auth.has_global_permission(request, "admin"),
        "device_id": device,
        "policy_scope": "enrollment" if scoped_policy is not None else "deployment_ceiling",
        "capture_level": visible_policy.get("capture_level", "application_metadata"),
        "allowed_apps": list(visible_policy.get("allowed_apps") or []),
        "blocked_apps": list(visible_policy.get("blocked_apps") or []),
        "retention_days": int(visible_policy.get("retention_days", 14)),
        "provider_egress": bool(visible_policy.get("provider_egress", False)),
        "enrollment": _public_enrollment(state.get("enrollment")),
        "session": _public_session(state.get("session")),
        "collector": _public_collector(state.get("collector")),
        "collector_state": _public_collector(state.get("collector"))["state"],
        "event_count": int(state.get("event_count", 0)),
    }


def _require_enabled_policy() -> dict:
    from maverick.config import get_ekko

    policy = get_ekko()
    if not bool(policy.get("enable", False)):
        raise HTTPException(
            status_code=409,
            detail="Ekko is turned off. An administrator needs to turn it on first.",
        )
    if not policy.get("allowed_apps"):
        raise HTTPException(
            status_code=409,
            detail="No apps are allowed yet. Your administrator needs to choose which apps Ekko may notice.",
        )
    return policy


def _active_enrollment(store):
    enrollment = store.get_enrollment()
    if enrollment is None or not bool(enrollment.active):
        raise HTTPException(
            status_code=409,
            detail=(
                "This device is not enrolled for you yet. Enroll it first, "
                "then start watching."
            ),
        )
    return enrollment


def _validated_enrolled_policy(store):
    from maverick.work_discovery_store import WorkDiscoveryStoreError

    _active_enrollment(store)
    try:
        policy = store.get_policy()
    except WorkDiscoveryStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail="Ekko enrollment integrity could not be verified",
        ) from exc
    if policy is None:
        raise HTTPException(
            status_code=409,
            detail="Your enrollment for this device has ended. Enroll it again to continue.",
        )
    try:
        from maverick.config import validate_ekko_policy_ceiling

        validate_ekko_policy_ceiling(policy)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail="Your administrator changed what Ekko may notice. Enroll this device again to continue.",
        ) from exc
    return policy


def _audit(kind: str, *, required: bool = False, **payload) -> bool:
    """Write a content-free lifecycle audit event.

    Pause, stop, and erase remain available during an audit outage; making a
    safety stop depend on the logging sink would be the less safe failure mode.
    Starting or resuming invasive observation is different: those authorization
    events fail closed when the tenant audit ledger is unavailable.
    """
    from maverick.audit import AuditRefused, audit_event

    try:
        persisted = audit_event(kind, agent="dashboard", **payload)
    except AuditRefused as exc:
        if required:
            raise HTTPException(
                status_code=503,
                detail="Ekko authorization audit was refused",
            ) from exc
        # Pause/stop/erase already completed before their evidence call. Keep
        # the safety result, but never return a false successful audit status.
        raise
    if required and not persisted:
        raise HTTPException(
            status_code=503,
            detail="Ekko authorization audit could not be persisted",
        )
    return persisted


def _discover(store, policy: dict):
    # Core bounds both the input event count and returned candidates.
    return store.discover(
        min_occurrences=max(2, int(policy.get("min_occurrences", 3))),
        min_distinct_days=max(2, int(policy.get("min_distinct_days", 2))),
    )


def _candidate(store, policy: dict, opportunity_id: str):
    if not _OPPORTUNITY_RE.fullmatch(str(opportunity_id or "")):
        raise HTTPException(status_code=404, detail="no such Ekko opportunity")
    for candidate in _discover(store, policy):
        if candidate.opportunity_id == opportunity_id:
            return candidate
    raise HTTPException(status_code=404, detail="no such Ekko opportunity")


def _handoff_brief(candidate, kind: str) -> str:
    steps = [
        f"{index}. {step.action} {step.object_type} in {step.app}"
        for index, step in enumerate(candidate.pattern, 1)
    ]
    artifact = "workflow" if kind == "flow" else "agent playbook"
    return "\n".join(
        [
            f"Draft an unsaved {artifact} for: {candidate.title}",
            "",
            "Evidence from approved, content-free application metadata:",
            *steps,
            "",
            (
                f"Observed {candidate.occurrences} times across "
                f"{candidate.distinct_days} distinct days; confidence "
                f"{candidate.confidence:.0%}; estimated "
                f"{candidate.estimated_minutes_per_run:.1f} minutes per run; "
                f"risk signal {candidate.risk}."
            ),
            "",
            "Preserve human review before saving, scheduling, running, or delivering. ",
            "Use only tenant-approved connectors and tools. If a required connector is ",
            "missing, mark it as a capability gap; do not substitute browser or computer ",
            "control and do not invent access to files, accounts, or source content.",
        ]
    )[:8000]


@router.get("/ekko", response_class=HTMLResponse)
async def ekko_page(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> HTMLResponse:
    auth.require_permission(request, "operate")
    status = _status_data(request, device_id)
    return _app().templates.TemplateResponse(
        request,
        "ekko.html",
        {"ekko": status},
    )


@router.get("/api/v1/ekko/status")
async def ekko_status(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    auth.require_permission(request, "operate")
    return _status_data(request, device_id)


@router.put("/api/v1/ekko/control")
def ekko_control(request: Request, payload: EkkoControlIn) -> dict:
    _app()._require_same_origin(request)
    # The dashboard overlay is deployment-global, so a tenant-local admin must
    # not change it for other clients.
    auth.require_global_permission(request, "admin")
    from . import settings_store

    try:
        status = settings_store.set_ekko(
            payload.enabled,
            actor=auth.caller_principal(request) or "local",
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=409,
            detail="This setting is controlled by your installation's configuration, so it cannot be changed here.",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The setting could not be saved. Try again.") from exc
    return {"ekko": status}


@router.post("/api/v1/ekko/enroll")
def ekko_enroll(request: Request, payload: EnrollIn) -> dict:
    """Enroll this person's device for the chosen apps, for a set number of
    days -- the dashboard form of ``maverick ekko enroll``. Enrollment only
    records consent under the administrator's app ceiling; it never starts
    observing (that stays a separate, confirmed step)."""
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    if not payload.acknowledge_capture_scope:
        raise HTTPException(
            status_code=400,
            detail="Confirm that you have read what Ekko may and may not record.",
        )
    _require_enabled_policy()
    import time

    from maverick.config import get_ekko_policy

    try:
        policy = get_ekko_policy(allowed_apps=payload.apps)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Choose only from the apps your administrator allows.",
        ) from exc
    store = _store(request, payload.device_id)
    expires_at = time.time() + payload.days * 86_400
    from maverick.audit import EventKind

    _audit(
        EventKind.EKKO_CONSENT_CHANGED,
        required=True,
        state="enroll_authorized",
        policy_digest=policy.fingerprint(),
        expires_at=expires_at,
    )
    try:
        enrollment = store.enroll(policy, expires_at=expires_at)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(
            status_code=409,
            detail="This device could not be enrolled. Check that it is not already being watched, then try again.",
        ) from exc
    _audit(
        EventKind.EKKO_CONSENT_CHANGED,
        state="enrolled",
        policy_digest=policy.fingerprint(),
        expires_at=enrollment.expires_at,
    )
    return {"enrollment": _public_enrollment(enrollment),
            "status": _status_data(request, payload.device_id)}


@router.post("/api/v1/ekko/revoke")
def ekko_revoke(request: Request, payload: DeviceIn) -> dict:
    """Withdraw consent for this device (``maverick ekko revoke``): active
    observation stops at once. Recorded evidence stays until erased."""
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    store = _store(request, payload.device_id)
    enrollment = store.revoke_enrollment() if store.path.exists() else None
    from maverick.audit import EventKind

    _audit(EventKind.EKKO_CONSENT_CHANGED, state="revoked")
    return {"revoked": enrollment is not None,
            "status": _status_data(request, payload.device_id)}


@router.get("/api/v1/ekko/sessions")
def ekko_sessions(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    auth.require_permission(request, "operate")
    store = _store(request, device_id)
    return {
        "device_id": _device_id(device_id),
        "sessions": [
            _public_session(session) for session in store.list_sessions(limit=100)
        ] if store.path.exists() else [],
    }


@router.post("/api/v1/ekko/sessions", status_code=201)
def ekko_start_session(request: Request, payload: StartSessionIn) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    if not payload.acknowledge_capture_scope:
        raise HTTPException(
            status_code=400,
            detail="Confirm that you have read what Ekko may and may not record.",
        )
    _require_enabled_policy()
    store = _store(request, payload.device_id)
    enrolled_policy = _validated_enrolled_policy(store)
    active = {
        session.state.value
        for session in store.list_sessions(limit=100)
        if session.state.value in {"created", "running", "paused"}
    }
    if active:
        raise HTTPException(
            status_code=409,
            detail="This device is already being watched. Stop or pause that first.",
        )
    from maverick.audit import EventKind

    _audit(
        EventKind.EKKO_SESSION,
        required=True,
        state="start_authorized",
        policy_digest=enrolled_policy.fingerprint(),
    )
    from maverick.work_discovery_store import WorkDiscoveryStoreError

    try:
        session = store.create_session(policy=enrolled_policy)
    except (ValueError, WorkDiscoveryStoreError) as exc:
        raise HTTPException(status_code=409, detail="Watching could not start. Refresh the page and try again.") from exc
    _audit(EventKind.EKKO_SESSION, session_id=session.session_id, state="running")
    return {"session": _public_session(session), "unsaved": True}


def _transition(request: Request, device_id: str, session_id: str, target: str) -> dict:
    if target == "running":
        _require_enabled_policy()
    store = _store(request, device_id)
    if target == "running":
        _validated_enrolled_policy(store)
    sid = _session_id(session_id)
    if store.get_session(sid) is None:
        raise HTTPException(status_code=404, detail="no such Ekko session")
    from maverick.audit import EventKind
    from maverick.work_discovery import SessionState
    from maverick.work_discovery_store import WorkDiscoveryStoreError

    if target == SessionState.RUNNING.value:
        _audit(
            EventKind.EKKO_SESSION,
            required=True,
            session_id=sid,
            state="resume_authorized",
        )

    try:
        session = store.transition_session(sid, SessionState(target))
    except WorkDiscoveryStoreError as exc:
        raise HTTPException(status_code=409, detail="That change could not be made. Refresh the page and try again.") from exc
    _audit(EventKind.EKKO_SESSION, session_id=sid, state=target)
    return {"session": _public_session(session)}


@router.post("/api/v1/ekko/sessions/{session_id}/pause")
async def ekko_pause_session(
    request: Request, session_id: str, payload: DeviceIn,
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    return _transition(request, payload.device_id, session_id, "paused")


@router.post("/api/v1/ekko/sessions/{session_id}/resume")
async def ekko_resume_session(
    request: Request, session_id: str, payload: DeviceIn,
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    return _transition(request, payload.device_id, session_id, "running")


@router.post("/api/v1/ekko/sessions/{session_id}/stop")
async def ekko_stop_session(
    request: Request, session_id: str, payload: DeviceIn,
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    return _transition(request, payload.device_id, session_id, "stopped")


def _candidate_response(store, policy: dict) -> dict:
    candidates = _discover(store, policy) if store.path.exists() else []
    return {
        "candidates": [candidate.to_dict() for candidate in candidates],
        "count": len(candidates),
        "preview_only": True,
    }


@router.get("/api/v1/ekko/candidates")
def ekko_candidates(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    auth.require_permission(request, "operate")
    from maverick.config import get_ekko

    return _candidate_response(_store(request, device_id), get_ekko())


@router.post("/api/v1/ekko/candidates/mine")
def ekko_mine_candidates(request: Request, payload: DeviceIn) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    from maverick.audit import EventKind
    from maverick.config import get_ekko

    response = _candidate_response(_store(request, payload.device_id), get_ekko())
    _audit(EventKind.EKKO_MINING_RUN, candidate_count=response["count"])
    return response


@router.post("/api/v1/ekko/candidates/{opportunity_id}/handoff")
def ekko_candidate_handoff(
    request: Request, opportunity_id: str, payload: HandoffIn,
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    from maverick.audit import EventKind
    from maverick.config import get_ekko
    from maverick.work_discovery import build_draft_bundle

    from .in_app_routes import _draft_context

    store = _store(request, payload.device_id)
    candidate = _candidate(store, get_ekko(), opportunity_id)
    # Give the workflow editor the exact provider-free, validated graph. Agent
    # handoffs still use a bounded brief. Neither path saves or activates work.
    bundle = build_draft_bundle(candidate, owner=_owner(request))
    brief = _handoff_brief(candidate, payload.kind)
    prepared_flow = bundle.flow.to_dict() if payload.kind == "flow" else None
    if prepared_flow is not None:
        prepared_flow.pop("owner", None)
        prepared_flow["id"] = f"{prepared_flow['id']}-{secrets.token_hex(4)}"
        prepared_flow["version"] = 0  # the editor still needs an explicit save
    context = _draft_context(request)
    _audit(
        EventKind.EKKO_EXPORT,
        opportunity_id=candidate.opportunity_id,
        artifact_kind=payload.kind,
        unsaved=True,
    )
    return {
        "kind": payload.kind,
        "brief": brief,
        "context": context,
        "opportunity_id": candidate.opportunity_id,
        "redirect": "/flows/designer" if payload.kind == "flow" else "/workflow-builder",
        "unsaved": bool(bundle.unsaved),
        "requires_human_approval": bool(bundle.requires_human_approval),
        **({"flow": prepared_flow} if prepared_flow is not None else {}),
    }


@router.delete("/api/v1/ekko/sessions/{session_id}")
def ekko_erase_session(
    request: Request,
    session_id: str,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    store = _store(request, device_id)
    sid = _session_id(session_id)
    if store.get_session(sid) is None:
        raise HTTPException(status_code=404, detail="no such Ekko session")
    erased = store.erase(session_id=sid)
    from maverick.audit import EventKind

    _audit(EventKind.EKKO_ERASE, scope="session", **erased)
    return {"erased": erased}


@router.delete("/api/v1/ekko/data")
def ekko_erase_data(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    erased = _store(request, device_id).erase()
    from maverick.audit import EventKind

    _audit(EventKind.EKKO_ERASE, scope="device", **erased)
    return {"erased": erased}


@router.delete("/api/v1/ekko/device")
def ekko_forget_device(
    request: Request,
    device_id: str = Query(_DEFAULT_DEVICE, min_length=1, max_length=64),
) -> dict:
    _app()._require_same_origin(request)
    auth.require_permission(request, "operate")
    forgotten = _store(request, device_id).forget_device()
    from maverick.audit import EventKind

    _audit(EventKind.EKKO_ERASE, scope="forgot_device", **forgotten)
    return {"forgotten": forgotten}


__all__ = ["router"]
