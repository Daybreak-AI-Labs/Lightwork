"""Installation checks and maintenance for administrators (Settings > Maintenance).

Buttons for the operator tasks that were only reachable from a terminal:
the installation health check, the go-live readiness check, the settings-file
check, the license summary, the background work queue (including resuming a
paused schedule), at-rest encryption maintenance and live diagnostics. Every
route calls the same kernel functions the terminal commands call and answers in
plain sentences; the exact technical rows are returned separately so the page
can keep them inside a collapsed "Technical details" section.

Every route requires the global ``admin`` permission (these read and change
installation-wide state); mutations are also same-origin checked.
"""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission

router = APIRouter(prefix="/api/v1/admin/system")
_DOCTOR_LOCK = threading.Lock()


def _admin(request: Request) -> None:
    require_global_permission(request, "admin")


def _mutation(request: Request) -> None:
    from . import app as app_module
    app_module._require_same_origin(request)
    _admin(request)


def _row(key: str, label: str, ok: bool, detail: str, fix_label: str = "",
         fix_href: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "ok": bool(ok), "detail": detail,
            "fix_label": "" if ok else fix_label, "fix_href": "" if ok else fix_href}


# ---- go-live readiness (maverick launch-check) -----------------------------

def _readiness() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        from maverick import spending_limits
        limits = spending_limits.load()
        has_limit = bool(limits.get("workspace") or limits.get("default"))
        if not has_limit:
            from maverick.quotas import _quota_config
            quota = _quota_config()
            has_limit = bool(quota.get("enforce")) and (
                float(quota.get("max_dollars_per_day") or 0) > 0
                or float(quota.get("max_tokens_per_day") or 0) > 0)
        rows.append(_row("spending", "Spending limits", has_limit,
                         "A spending limit is set." if has_limit else "No spending limit is set, so costs are not capped.",
                         "Set spending limits", "/spending-limits"))
    except Exception:  # noqa: BLE001 -- a readiness row never aborts the page
        rows.append(_row("spending", "Spending limits", False,
                         "The spending limits could not be read.", "Open spending limits", "/spending-limits"))
    try:
        from maverick.paths import tenant_by_user_enabled
        on = bool(tenant_by_user_enabled())
        rows.append(_row("separation", "Each person's work kept separate", on,
                         "Each person works in their own space." if on
                         else "Everyone shares one space. Whoever installed Lightwork can change this.",
                         "Manage workspaces", "/tenants"))
    except Exception:  # noqa: BLE001
        rows.append(_row("separation", "Each person's work kept separate", False,
                         "This setting could not be read.", "Manage workspaces", "/tenants"))
    try:
        from maverick.entitlements import _trusted_pubkeys
        keys = _trusted_pubkeys()
        rows.append(_row("license", "License can be checked", bool(keys),
                         "Lightwork can confirm your license is genuine." if keys
                         else "Lightwork has no way to confirm a license yet. Your Lightwork contact can set this up.",
                         "See license", "#maintenance-license"))
    except Exception:  # noqa: BLE001
        rows.append(_row("license", "License can be checked", False,
                         "The license setting could not be read.", "See license", "#maintenance-license"))
    try:
        from maverick.crypto_at_rest import at_rest_enabled
        on = bool(at_rest_enabled())
        rows.append(_row("encryption", "Stored data is encrypted", on,
                         "Saved records are encrypted on disk." if on
                         else "Saved records are not encrypted on disk. Whoever installed Lightwork can turn this on.",
                         "See encryption", "#maintenance-encryption"))
    except Exception:  # noqa: BLE001
        rows.append(_row("encryption", "Stored data is encrypted", False,
                         "The encryption setting could not be read.", "See encryption", "#maintenance-encryption"))
    try:
        from maverick.config import any_provider_configured
        ok = bool(any_provider_configured())
        rows.append(_row("provider", "AI provider connected", ok,
                         "At least one AI provider key is saved." if ok else "No AI provider key is saved yet.",
                         "Add a provider key", "/settings#settings-integrations"))
    except Exception:  # noqa: BLE001
        rows.append(_row("provider", "AI provider connected", False,
                         "The provider settings could not be read.", "Open provider keys",
                         "/settings#settings-integrations"))
    return rows


@router.get("/readiness")
async def readiness(request: Request) -> dict:
    _admin(request)
    rows = await run_in_threadpool(_readiness)
    return {"ready": all(r["ok"] for r in rows), "checks": rows}


# ---- installation health (maverick doctor) ---------------------------------

def _doctor() -> dict[str, Any]:
    from maverick import health
    rows: list[dict[str, str]] = []

    def collect(marker: str, label: str, detail: str = "", fix: str = "") -> None:
        if marker == health.RED:
            health._FAILURES.append(label)
            state = "problem"
        elif marker == health.YELLOW:
            state = "attention"
        else:
            state = "ok"
        rows.append({"state": state, "label": str(label), "detail": str(detail), "fix": str(fix)})

    # diagnose() prints through health._row; collect the rows instead. One run
    # at a time: the collector is swapped in for the duration of the check.
    with _DOCTOR_LOCK:
        original = health._row
        health._row = collect
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                failures = health.diagnose()
        finally:
            health._row = original
    problems = sum(1 for r in rows if r["state"] == "problem")
    attention = sum(1 for r in rows if r["state"] == "attention")
    if failures or problems:
        summary = (f"{problems} problem{'s' if problems != 1 else ''} found. "
                   "Share the technical details with whoever looks after this installation.")
    elif attention:
        summary = f"Lightwork is working. {attention} item{'s' if attention != 1 else ''} could be improved."
    else:
        summary = "Lightwork is installed correctly and working."
    return {"healthy": not (failures or problems), "problems": problems,
            "attention": attention, "checked": len(rows), "summary": summary,
            "technical": rows}


@router.post("/health-check")
async def health_check(request: Request) -> dict:
    _mutation(request)  # runs live checks (it may contact your AI provider)
    return await run_in_threadpool(_doctor)


# ---- settings file check (maverick config-lint) ----------------------------

def _config_check() -> dict[str, Any]:
    from maverick.config import config_path
    path = config_path()
    if not path.exists():
        return {"file_present": False, "errors": 0, "warnings": 0,
                "summary": "Lightwork is using its built-in settings. There is nothing to check.",
                "technical": []}
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # 3.10
            import tomli as tomllib
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 -- unreadable file is itself the finding
        return {"file_present": True, "errors": 1, "warnings": 0,
                "summary": "The installation settings file could not be read, so its settings are being ignored. "
                           "Whoever installed Lightwork needs to repair it.",
                "technical": [{"level": "error", "text": "settings file is not valid"}]}
    from maverick.config_lint import lint_config
    findings = lint_config(raw)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity != "error"]
    if errors:
        summary = (f"{len(errors)} setting{'s' if len(errors) != 1 else ''} in the installation settings file "
                   "cannot be used. Share the technical details with whoever installed Lightwork.")
    elif warnings:
        summary = (f"The installation settings work. {len(warnings)} "
                   f"suggestion{'s' if len(warnings) != 1 else ''} to tidy up.")
    else:
        summary = "The installation settings file has no problems."
    technical = [{"level": f.severity,
                  "text": f"[{f.section}]{(' ' + f.key) if f.key else ''}: {f.message}"}
                 for f in findings]
    return {"file_present": True, "errors": len(errors), "warnings": len(warnings),
            "summary": summary, "technical": technical}


@router.get("/settings-check")
async def settings_check(request: Request) -> dict:
    _admin(request)
    return await run_in_threadpool(_config_check)


# ---- license (maverick license show) ---------------------------------------

_LICENSE_STATUS = {
    "licensed": ("Active", "Your license is active."),
    "grace": ("Renewal due", "Your license has expired but still works for a short time. Renew it soon."),
    "expired": ("Expired", "Your license has expired. Paid features are off; everything else still works."),
    "invalid": ("Not accepted", "The license file could not be confirmed. Paid features are off."),
    "unverified": ("Not confirmed", "The license could not be confirmed yet. Paid features are off."),
    "unlicensed": ("No license", "No license is installed. Lightwork's standard features all work."),
}
_FEATURE_NAMES = {
    "external_agents": "Outside agents",
    "fleet_governance": "Agent fleet governance",
    "fleet_memory": "Shared fleet memory",
    "siem_export": "Security log export",
    "advanced_evolve": "Advanced self-improvement",
    "custom_pack_factory": "Custom specialist teams",
    "multi_tenant": "Multiple workspaces",
}


def _license() -> dict[str, Any]:
    from maverick.entitlements import GATED_FEATURES, load_entitlements
    ent = load_entitlements()
    label, sentence = _LICENSE_STATUS.get(ent.status, ("Unknown", "The license state is unknown."))
    return {
        "status": ent.status,
        "status_label": label,
        "summary": sentence,
        "plan": (ent.tier or "basic").capitalize(),
        "customer": ent.customer or "",
        "expires": ent.expires_at.date().isoformat() if ent.expires_at else "",
        "add_ons": [s.replace("_", " ").capitalize() for s in ent.suites],
        "features": [{"label": _FEATURE_NAMES.get(f, f.replace("_", " ").capitalize()),
                      "included": bool(ent.allows(f))} for f in sorted(GATED_FEATURES)],
    }


@router.get("/license")
async def license_summary(request: Request) -> dict:
    _admin(request)
    return await run_in_threadpool(_license)


# ---- background work queue (maverick queue status/failed/reconcile) --------

_QUEUE_STATES = {"pending": "Waiting", "running": "Running", "done": "Finished", "failed": "Stopped"}


def _job_kind(kind: str) -> str:
    return {"run_goal": "Run a task", "run_flow": "Run a workflow"}.get(
        kind, kind.replace("_", " ").replace(".", " ").capitalize())


def _queue() -> dict[str, Any]:
    from maverick.job_queue import JobQueue
    q = JobQueue()
    counts = q.counts()
    failed = q.list(status="failed", limit=25)
    return {
        "counts": [{"state": _QUEUE_STATES.get(k, k.capitalize()), "count": int(v)}
                   for k, v in sorted(counts.items())],
        "stopped": [{
            "id": j.id,
            "attempts": int(j.attempts or 0),
            "what": _job_kind(j.kind),
            "when": j.updated_at or j.created_at,
            "needs_decision": bool(j.recurrence_reconcile_required),
            "technical": (j.last_error or "").strip()[:300],
        } for j in failed],
    }


@router.get("/queue")
async def queue_summary(request: Request) -> dict:
    _admin(request)
    return await run_in_threadpool(_queue)


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempts: int = Field(ge=0, le=10_000)


@router.post("/queue/{job_id}/resume")
async def queue_resume(job_id: int, body: ResumeIn, request: Request) -> dict:
    """Let a paused recurring schedule run again from its next occurrence.

    The run that could not be confirmed stays stopped and is never repeated;
    ``attempts`` is the value the page was showing, so a job that changed
    since is refused rather than resumed blind."""
    _mutation(request)
    from maverick.job_queue import JobQueue

    def _do() -> int:
        return JobQueue().reconcile_recurrence(
            job_id, expected_attempts=body.attempts, rearm=True, confirm=True)
    try:
        next_id = await run_in_threadpool(_do)
    except ValueError as exc:
        raise HTTPException(409, "This schedule changed since the list was loaded. Refresh and try again.") from exc
    return {"resumed": True, "next": next_id}


# ---- encryption (maverick encryption migrate / rotate) ---------------------

def _world_db():
    from maverick import world_model
    return world_model.DEFAULT_DB


def _encryption() -> dict[str, Any]:
    from maverick.crypto_at_rest import at_rest_enabled
    on = bool(at_rest_enabled())
    unprotected = None
    if on:
        try:
            from maverick.encryption_migrate import migrate_world_db
            db = _world_db()
            unprotected = sum(migrate_world_db(db, dry_run=True).values()) if db.exists() else 0
        except Exception:  # noqa: BLE001 -- status read is best effort
            unprotected = None
    return {"enabled": on, "older_unprotected": unprotected}


@router.get("/encryption")
async def encryption_status(request: Request) -> dict:
    _admin(request)
    return await run_in_threadpool(_encryption)


@router.post("/encryption/protect-older")
async def encryption_protect_older(request: Request) -> dict:
    _mutation(request)
    from maverick.crypto_at_rest import EncryptionUnavailable
    from maverick.encryption_migrate import migrate_world_db

    def _do() -> int:
        db = _world_db()
        return sum(migrate_world_db(db, dry_run=False, backup=False).values()) if db.exists() else 0
    try:
        count = await run_in_threadpool(_do)
    except EncryptionUnavailable as exc:
        raise HTTPException(503, "Encryption is not set up on this installation, so nothing was changed.") from exc
    return {"protected": count}


@router.post("/encryption/new-key")
async def encryption_new_key(request: Request) -> dict:
    _mutation(request)
    from maverick.crypto_at_rest import EncryptionUnavailable, rotate_at_rest_key
    try:
        await run_in_threadpool(rotate_at_rest_key)
    except EncryptionUnavailable as exc:
        raise HTTPException(503, "Encryption is not set up on this installation, so no new key was made.") from exc
    return {"rotated": True}


# ---- live diagnostics (maverick diag circuits / ratelimits) ----------------

def _diagnostics() -> dict[str, Any]:
    from maverick.circuit_breaker import snapshot
    from maverick.rate_limit_predictor import report
    paused = [s for s in snapshot() if s.get("state") in {"open", "half_open", "half-open"}]
    return {
        "paused_services": [{"name": str(s.get("key") or "").split(":")[0].capitalize() or "A service",
                             "state": "Paused after repeated errors" if s.get("state") == "open"
                             else "Trying again"} for s in paused],
        "recent_calls": [{"provider": str(r.get("provider") or "").capitalize(),
                          "calls": int(r.get("recorded") or 0)} for r in report()],
    }


@router.get("/diagnostics")
async def diagnostics(request: Request) -> dict:
    _admin(request)
    return await run_in_threadpool(_diagnostics)
