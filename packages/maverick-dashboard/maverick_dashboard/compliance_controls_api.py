"""Buttons behind the /compliance page.

The control-coverage report (``maverick.compliance``) says what is on and what
needs action. This router turns each "what to do" into something a person can
press instead of a config key or a terminal command:

* switch on a protection (signed activity record, AI notice, anonymous logs,
  encryption, strict mode) -- global administrators only, strengthen-only;
* set how long data is kept and apply those rules now;
* find a person's conversations, download everything held about them, or
  erase it (the same kernel paths ``maverick export-user`` / ``erase`` use);
* download the SOC 2 snapshot, AI bill of materials, OSCAL results and the
  Statement of Applicability, run the EU AI Act self-check and search the
  control library.

Every mutation is permission-gated and keeps the kernel's own checks: erasure
still runs the audited, receipt-backed erase command and is verified after.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/api/v1/compliance", tags=["compliance-controls"])

_PERSON_TEXT = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_CHANNEL_TEXT = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_ASSESSMENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
# click echoes to the process streams; erasure output names the person, so it
# is captured instead of reaching the server log. One erasure at a time.
_ERASE_LOCK = threading.Lock()

#: Plain wording per status (the stored values stay the kernel's).
STATUS_TEXT = {
    "active": "On",
    "available": "Available when needed",
    "action_needed": "Needs action",
}

#: The protections a global administrator may switch on from the page.
SWITCHES: dict[str, dict[str, Any]] = {
    "signed_audit": {
        "label": "Turn on signed activity record",
        "confirm": (
            "Sign every entry in the activity record so any later change can be "
            "detected? New entries are signed from the next restart."
        ),
    },
    "ai_disclosure": {
        "label": "Turn on the AI notice",
        "confirm": (
            "Tell people at the start of every chat that they are talking to an "
            "AI assistant?"
        ),
    },
    "anonymous_logs": {
        "label": "Hide personal details in logs",
        "confirm": (
            "Replace email addresses, phone numbers and other personal details "
            "in the activity record with protected codes from now on?"
        ),
    },
    "encryption": {
        "label": "Turn on encryption",
        "confirm": (
            "Encrypt stored conversations, tasks and memory from now on? Data "
            "saved before today stays as it is until it is re-saved."
        ),
    },
    "strict_mode": {
        "label": "Turn on strict mode",
        "confirm": (
            "Turn on strict mode? Risky actions will wait for a person to "
            "approve them, and AI requests will only go to AI models you run "
            "yourself. Work that relies on outside AI services will stop."
        ),
    },
}

#: Plain explanation and the button each control gets. ``action`` kinds:
#: switch (turn a protection on), retention, people, halt, link.
CONTROL_GUIDE: dict[str, dict[str, Any]] = {
    "AI transparency disclosure": {
        "about": "People are told they are talking to an AI at the start of each chat.",
        "action": {"kind": "switch", "key": "ai_disclosure"},
    },
    "Audit logging (record-keeping)": {
        "about": "Lightwork keeps a permanent record of everything it does.",
        "action": {"kind": "link", "href": "/audit", "label": "Open the activity record"},
    },
    "Tamper-evident audit": {
        "about": "Each entry in the activity record is signed, so any later change is detectable.",
        "action": {"kind": "switch", "key": "signed_audit"},
    },
    "Human oversight (consent gating)": {
        "about": "Risky actions wait for a person to approve them before they run.",
        "action": {"kind": "switch", "key": "strict_mode"},
    },
    "Kill switch": {
        "about": "You can stop all running work at once.",
        "action": {"kind": "halt", "label": "Stop all work now"},
    },
    "Data-subject access & portability": {
        "about": "You can give a person a copy of everything Lightwork holds about them.",
        "action": {"kind": "people", "label": "Export a person's data"},
    },
    "Right to erasure": {
        "about": "You can permanently delete everything Lightwork holds about a person.",
        "action": {"kind": "people", "label": "Erase a person's data"},
    },
    "Storage limitation (retention)": {
        "about": "Old records are deleted automatically after the period you choose.",
        "action": {"kind": "retention", "label": "Choose how long data is kept"},
    },
    "Data-egress control": {
        "about": "Your data only goes to AI models you run yourself.",
        "action": {"kind": "switch", "key": "strict_mode"},
    },
    "Secret redaction in logs": {
        "about": "Passwords and keys are always removed from the activity record.",
        "action": None,
    },
    "PII redaction in logs": {
        "about": "Email addresses, phone numbers and similar details are hidden in the activity record.",
        "action": {"kind": "switch", "key": "anonymous_logs"},
    },
    "Encryption at rest": {
        "about": "Stored conversations, tasks and memory are encrypted.",
        "action": {"kind": "switch", "key": "encryption"},
    },
    "Log data minimization (anonymous mode)": {
        "about": "People's identifiers are replaced with protected codes in the activity record.",
        "action": {"kind": "switch", "key": "anonymous_logs"},
    },
    "Governance, accountability & records": {
        "about": "Signed records, approvals and a stop button show who did what and when.",
        "action": {"kind": "link", "href": "/audit", "label": "Open the activity record"},
    },
    "Measurement & monitoring": {
        "about": "Quality checks, spending limits and monitoring are available.",
        "action": None,
    },
    "Consumer notice of AI": {
        "about": "People are told they are talking to an AI at the start of each chat.",
        "action": {"kind": "switch", "key": "ai_disclosure"},
    },
    "Human review of consequential decisions": {
        "about": "A person reviews important decisions before they take effect.",
        "action": {"kind": "switch", "key": "strict_mode"},
    },
    "Automated employment-decision audit trail": {
        "about": (
            "Signed decision records support the yearly independent bias audit, "
            "which your organization arranges itself."
        ),
        "action": {"kind": "link", "href": "/audit", "label": "Open the activity record"},
    },
    "Consumer access & deletion": {
        "about": "You can give a person a copy of their data or delete it.",
        "action": {"kind": "people", "label": "Export or erase a person's data"},
    },
}


def guide_for(check: Any) -> dict[str, Any]:
    """The plain explanation and button for one ``ControlCheck``."""
    entry = CONTROL_GUIDE.get(getattr(check, "control", ""), {})
    action = entry.get("action")
    status = getattr(check, "status", "")
    if action and action.get("kind") == "switch":
        if status == "active":
            action = None
        else:
            action = {**action, **SWITCHES[action["key"]]}
    return {
        "about": entry.get("about", ""),
        "status_text": STATUS_TEXT.get(status, "Unknown"),
        "action": action,
    }


def _settings_for(key: str) -> dict[tuple[str, str], object]:
    if key == "signed_audit":
        return {("audit", "sign"): True}
    if key == "anonymous_logs":
        return {("privacy", "anonymous"): True}
    if key == "encryption":
        return {("encryption", "at_rest"): True}
    if key == "strict_mode":
        return {("enterprise", "mode"): True}
    if key == "ai_disclosure":
        from maverick.compliance import DEFAULT_DISCLOSURE

        return {("compliance", "disclosure_text"): DEFAULT_DISCLOSURE}
    raise HTTPException(status_code=404, detail="That protection is not available here.")


#: Protections that put the deployment into a regulated posture, which makes
#: the dashboard require sign-in. Turning one on without sign-in configured
#: would lock everyone out, so it is refused until sign-in is set up.
_NEEDS_SIGN_IN = frozenset({"strict_mode"})
SIGN_IN_FIRST = (
    "Set up sign-in for Lightwork first. Once this is on, everyone must sign in, "
    "so without sign-in nobody could open the dashboard."
)


def sign_in_configured() -> bool:
    """Whether the dashboard has a sign-in mechanism (token, OIDC, SSO...)."""
    import os

    from . import auth

    return bool(os.environ.get("MAVERICK_DASHBOARD_TOKEN")) or not auth.auth_genuinely_off()


def _status_by_control() -> dict[str, str]:
    from maverick.compliance import compliance_report

    return {c.control: c.status for c in compliance_report()}


_SWITCH_CONTROLS = {
    "signed_audit": "Tamper-evident audit",
    "ai_disclosure": "AI transparency disclosure",
    "anonymous_logs": "PII redaction in logs",
    "encryption": "Encryption at rest",
    "strict_mode": "Data-egress control",
}


@router.post("/switches/{key}")
async def turn_on_protection(request: Request, key: str) -> dict:
    """Switch one protection on (deployment-wide, global admin only)."""
    require_global_permission(request, "admin")
    if key not in SWITCHES:
        raise HTTPException(status_code=404, detail="That protection is not available here.")
    if key in _NEEDS_SIGN_IN and not sign_in_configured():
        raise HTTPException(status_code=409, detail=SIGN_IN_FIRST)
    from . import settings_store

    updates = _settings_for(key)
    try:
        await run_in_threadpool(settings_store.set_compliance_settings, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That setting could not be saved.") from exc
    except settings_store.SecuritySuiteConfigUnavailable as exc:
        raise HTTPException(status_code=503, detail="Settings could not be read.") from exc
    try:
        now_on = (await run_in_threadpool(_status_by_control)).get(_SWITCH_CONTROLS[key]) == "active"
    except Exception:  # pragma: no cover - the save itself succeeded
        now_on = False
    return {
        "saved": True,
        "active": now_on,
        "message": (
            "Turned on."
            if now_on
            else (
                "Saved. It takes effect after Lightwork restarts. If it still shows "
                "as needing action after that, it is being overridden by how "
                "Lightwork was installed; ask the person who installed it."
            )
        ),
    }


# ---- Retention -----------------------------------------------------------

RETENTION_FIELDS: tuple[tuple[str, str], ...] = (
    ("audit_days", "Activity record"),
    ("episodes_days", "Task history"),
    ("events_days", "Task activity details"),
    ("messages_days", "Chat messages"),
    ("usage_days", "Spending records"),
)
RETENTION_CHOICES: tuple[int, ...] = (30, 90, 180, 365, 730, 1095, 2555)


class RetentionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_days: int | None = Field(default=None, ge=1, le=3650)
    episodes_days: int | None = Field(default=None, ge=1, le=3650)
    events_days: int | None = Field(default=None, ge=1, le=3650)
    messages_days: int | None = Field(default=None, ge=1, le=3650)
    usage_days: int | None = Field(default=None, ge=1, le=3650)


def _current_retention() -> dict[str, int | None]:
    from maverick.config import load_config

    section = (load_config() or {}).get("retention") or {}
    out: dict[str, int | None] = {}
    for key, _label in RETENTION_FIELDS:
        value = section.get(key) if isinstance(section, dict) else None
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) else None
    return out


@router.get("/retention")
async def retention_settings(request: Request) -> dict:
    require_permission(request, "view")
    current = await run_in_threadpool(_current_retention)
    return {
        "fields": [
            {"key": key, "label": label, "days": current.get(key)}
            for key, label in RETENTION_FIELDS
        ],
        "choices": list(RETENTION_CHOICES),
    }


@router.post("/retention")
async def save_retention(request: Request, body: RetentionIn) -> dict:
    require_global_permission(request, "admin")
    updates = {
        ("retention", key): value
        for key, value in body.model_dump().items()
        if value is not None
    }
    if not updates:
        raise HTTPException(status_code=422, detail="Choose at least one period.")
    from . import settings_store

    try:
        await run_in_threadpool(settings_store.set_compliance_settings, updates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That setting could not be saved.") from exc
    except settings_store.SecuritySuiteConfigUnavailable as exc:
        raise HTTPException(status_code=503, detail="Settings could not be read.") from exc
    return {"saved": True, "message": "Retention periods saved."}


def _summarize_retention(report: dict) -> dict:
    if report.get("status") == "disabled":
        return {"applied": False, "removed": 0, "message": "No retention periods are set yet. Choose them first."}
    removed = 0

    def walk(value: Any) -> None:
        nonlocal removed
        if isinstance(value, dict):
            for k, v in value.items():
                counted = str(k).startswith(("deleted", "removed"))
                if counted and isinstance(v, int) and not isinstance(v, bool):
                    removed += v
                elif counted and isinstance(v, list):
                    removed += len(v)
                elif k != "marker":
                    walk(v)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(report)
    return {
        "applied": True,
        "removed": removed,
        "message": (
            f"Retention applied. {removed} old record(s) were removed."
            if removed
            else "Retention applied. Nothing was old enough to remove."
        ),
    }


@router.post("/retention/apply")
async def apply_retention(request: Request) -> dict:
    """Run the retention rules now (every workspace; global admin)."""
    require_global_permission(request, "admin")
    from maverick.audit.retention import enforce

    try:
        report = await run_in_threadpool(enforce)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Retention could not be applied.") from exc
    return _summarize_retention(report if isinstance(report, dict) else {})


# ---- People: export / erase ---------------------------------------------


def _world_for_request() -> Any:
    from ._shared import _world

    return _world()


def _channel_label(channel: str) -> str:
    known = {
        "cli": "This computer", "dashboard": "Dashboard", "slack": "Slack",
        "teams": "Microsoft Teams", "telegram": "Telegram", "sms": "Text message",
        "whatsapp": "WhatsApp", "email": "Email", "discord": "Discord",
        "matrix": "Matrix", "signal": "Signal", "webchat": "Website chat",
    }
    return known.get(channel, channel.replace("_", " ").replace("-", " ").capitalize())


def _list_people(limit: int = 500) -> list[dict]:
    world = _world_for_request()
    seen: dict[tuple[str, str], dict] = {}
    for conv in world.list_conversations():
        channel = str(getattr(conv, "channel", "") or "")
        user = str(getattr(conv, "user_id", "") or "")
        if not channel or not user:
            continue
        if channel == "cli" and user.startswith("local:"):
            user = "local"
        key = (channel, user)
        row = seen.get(key)
        last = getattr(conv, "last_seen", None) or 0
        if row is None:
            seen[key] = {
                "channel": channel,
                "user": user,
                "channel_label": _channel_label(channel),
                "person": "You (this computer)" if (channel, user) == ("cli", "local") else user,
                "conversations": 1,
                "last_seen": last,
            }
        else:
            row["conversations"] += 1
            row["last_seen"] = max(row["last_seen"] or 0, last)
    rows = sorted(seen.values(), key=lambda r: -(r["last_seen"] or 0))
    return rows[:limit]


@router.get("/people")
async def list_people(request: Request) -> dict:
    """People Lightwork has talked with in this workspace (for the picker)."""
    require_permission(request, "admin")
    try:
        people = await run_in_threadpool(_list_people)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="People could not be listed.") from exc
    return {"people": people}


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: str = Field(min_length=1, max_length=64)
    user: str = Field(min_length=1, max_length=256)


def _validated(person: PersonIn) -> tuple[str, str]:
    if not _CHANNEL_TEXT.fullmatch(person.channel) or not _PERSON_TEXT.fullmatch(person.user):
        raise HTTPException(status_code=422, detail="Choose a person from the list.")
    return person.channel, person.user


@router.post("/people/export")
async def export_person(request: Request, person: PersonIn) -> Response:
    """Download everything held about one person (right of access)."""
    require_permission(request, "admin")
    channel, user = _validated(person)
    from maverick.dsar import export_subject_data

    world = _world_for_request()
    try:
        bundle = await run_in_threadpool(
            export_subject_data, user, channel=channel, world=world
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Choose a person from the list.") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The export could not be created.") from exc
    body = json.dumps(bundle, indent=2, default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="lightwork-person-data.json"',
            "Cache-Control": "no-store",
        },
    )


def _run_erase(channel: str, user: str) -> dict:
    import click
    from maverick.cli import erase as erase_command
    from maverick.erasure_verify import verify_erasure

    world = _world_for_request()
    before = sum(
        1
        for conv in world.list_conversations(channel)
        if conv.user_id == user
        or (channel == "cli" and user == "local" and str(conv.user_id).startswith("local:"))
    )
    if not before:
        return {"erased": False, "conversations": 0, "clean": False,
                "message": "Nothing was found for this person."}
    out, err = io.StringIO(), io.StringIO()
    with _ERASE_LOCK, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        ctx = click.Context(erase_command, obj={"db": getattr(world, "path", None)})
        with ctx:
            ctx.invoke(erase_command, channel=channel, user=user, yes=True)
    try:
        report = verify_erasure(user, channel=channel, world=world)
    except Exception:  # pragma: no cover - the erase itself committed
        report = {"residual": {}}
    residual = sum(int(v or 0) for v in (report.get("residual") or {}).values())
    if residual:
        message = (
            f"Erased {before} conversation(s), but {residual} item(s) could not be "
            "removed. Try again, or ask your administrator to check the activity record."
        )
    else:
        message = f"Erased {before} conversation(s) and everything linked to them."
    return {"erased": True, "conversations": before, "remaining": residual, "message": message}


@router.post("/people/erase")
async def erase_person(request: Request, person: PersonIn) -> dict:
    """Permanently erase one person's data (right to erasure)."""
    require_permission(request, "admin")
    channel, user = _validated(person)
    try:
        return await run_in_threadpool(_run_erase, channel, user)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The erasure could not be completed.") from exc


# ---- Downloads and self-checks ----------------------------------------


def _attachment(document: Any, filename: str) -> Response:
    return Response(
        content=json.dumps(document, indent=2, sort_keys=True, default=str),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/downloads/soc2")
async def download_soc2(request: Request) -> Response:
    require_permission(request, "admin")
    from maverick.soc2 import collect_soc2_evidence

    evidence = await run_in_threadpool(collect_soc2_evidence)
    return _attachment(evidence, "lightwork-soc2-snapshot.json")


@router.get("/downloads/ai-bom")
async def download_ai_bom(request: Request) -> Response:
    require_permission(request, "admin")
    from maverick import ai_bom

    try:
        document = await run_in_threadpool(ai_bom.export_ai_bom)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="The AI inventory is not available.") from exc
    return _attachment(document, "lightwork-ai-inventory.cdx.json")


def _assessment_id(value: str) -> str:
    if not _ASSESSMENT_ID.fullmatch(value or ""):
        raise HTTPException(status_code=422, detail="Choose an assessment from the list.")
    return value


@router.get("/downloads/oscal")
async def download_oscal(request: Request, assessment: str) -> Response:
    require_permission(request, "operate")
    from maverick import assessment_exports

    try:
        document = await run_in_threadpool(
            assessment_exports.render_oscal_assessment_results, _assessment_id(assessment)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That assessment no longer exists.") from exc
    return _attachment(document, "lightwork-assessment-results.oscal.json")


@router.get("/downloads/soa")
async def download_soa(request: Request, assessment: str) -> Response:
    require_permission(request, "operate")
    from maverick import assessment_exports

    try:
        soa = await run_in_threadpool(
            assessment_exports.render_statement_of_applicability, _assessment_id(assessment)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="That assessment no longer exists.") from exc
    return Response(
        content=assessment_exports.soa_markdown(soa),
        media_type="text/markdown",
        headers={
            "Content-Disposition": 'attachment; filename="lightwork-statement-of-applicability.md"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/assessments")
async def list_assessments(request: Request) -> dict:
    """Saved assessments for the download pickers (newest first)."""
    require_permission(request, "operate")
    from maverick.assessment import list_saved

    rows = await run_in_threadpool(list_saved)
    return {
        "assessments": [
            {
                "id": row.get("id"),
                "subject": row.get("subject") or "Untitled",
                "type": row.get("type") or "",
                "created_at": row.get("created_at") or 0,
            }
            for row in rows[:200]
        ]
    }


@router.get("/ai-act")
async def ai_act_self_check(request: Request) -> dict:
    """EU AI Act self-check: the live notice status plus the checklists."""
    require_permission(request, "view")
    from maverick.ai_act import assess_ai_act

    report = await run_in_threadpool(assess_ai_act)
    posture = report.get("transparency_obligation_art50") or {}
    checklist = report.get("self_assessment") or {}
    tiers = report.get("obligations_by_tier") or {}
    return {
        "notice_on": bool(posture.get("disclosure_active")),
        "default_level": (
            "Limited risk. An AI assistant that tells people it is an AI is "
            "limited risk, as long as none of the uses below apply."
        ),
        "banned_uses": list(checklist.get("prohibited_art5") or []),
        "high_risk_uses": list(checklist.get("high_risk_annex_iii") or []),
        "duties": [
            {"level": "Limited risk", "text": tiers.get("limited_risk_art50", "")},
            {"level": "High risk", "text": tiers.get("high_risk_annex_iii", "")},
            {"level": "Banned", "text": tiers.get("prohibited_art5", "")},
        ],
        "note": report.get("disclaimer", ""),
    }


@router.get("/control-finder")
async def control_finder(request: Request, q: str = "") -> dict:
    """Controls that address a described risk, with framework references."""
    require_permission(request, "view")
    from maverick.controls import find_controls

    query = (q or "")[:300]
    hits = find_controls(query, limit=8) if query.strip() else []
    return {
        "controls": [
            {
                "title": c.title,
                "area": c.theme.capitalize(),
                "references": [
                    {"framework": fw, "reference": ref} for fw, ref in c.frameworks.items()
                ],
            }
            for c in hits
        ]
    }


__all__ = ["CONTROL_GUIDE", "SIGN_IN_FIRST", "sign_in_configured", "RETENTION_FIELDS", "STATUS_TEXT", "SWITCHES", "guide_for", "router"]
