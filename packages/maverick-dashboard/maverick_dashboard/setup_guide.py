"""The full setup guide (``maverick init``) inside the app.

The terminal setup assistant (``apps/installer-cli/maverick_installer/wizard.py``)
is the source of truth for what setup covers. Its advanced flow walks 42 steps
(``wizard.STEPS``); :data:`STEPS` below names, for each one, where the same
settings are changed in this app. Most live in All settings (the catalog of
product settings, which writes the same keys the assistant writes); the rest
have their own page. A step whose settings are fixed when Lightwork is
installed is marked as such, with the reason. ``tests`` keep this list in step
with the assistant's own.

The page also offers the assistant's headless path (``maverick init
--from-file``): an administrator uploads a prepared settings file, sees whether
it is valid and what the settings check says about it, and installs it; the
previous file is kept as a dated backup copy. It uses the same kernel code as
the command (:mod:`maverick.config_install`).

Global administrators only; installing is same-origin checked, needs an
explicit confirmation and adds one activity-record row.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission

router = APIRouter()
_MAX_FILE = 256 * 1024
_STATIC = Path(__file__).parent / "static"


@router.get("/static/lightwork-setup-guide.js", include_in_schema=False)
async def setup_guide_script() -> FileResponse:
    return FileResponse(_STATIC / "lightwork-setup-guide.js", media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=300"})

# (wizard step id, title, what it covers, config sections it writes, dedicated page or "")
STEPS: tuple[tuple[str, str, str, tuple[str, ...], str], ...] = (
    ("deployment", "Where Lightwork runs", "Desktop, server or cloud. Chosen when Lightwork is installed.",
     ("deployment",), ""),
    ("providers", "AI providers", "Which AI services Lightwork may use, and their keys.", ("providers",),
     "/settings#settings-integrations"),
    ("role_models", "Models", "Which AI model each kind of work uses.", ("models",), "/settings#settings-models"),
    ("channels", "Chat channels", "The chat apps people can give work from.", ("channels",), "/channels"),
    ("safety", "Safety", "How carefully requests and actions are screened.", ("safety",), ""),
    ("signed_skills", "Signed skills", "Whether only signed skills may be added.", ("skills",), ""),
    ("budget", "Spending", "The most one task may spend.", ("budget",), ""),
    ("approvals", "Approvals", "How long a decision can be undone, and the confirmation for risky requests.",
     ("approval",), "/approvals"),
    ("sandbox", "Protected work area", "Where agent commands run and what they may reach.", ("sandbox",), ""),
    ("research_execution", "Research lab", "Whether research experiments may run, and their limits.",
     ("research_execution",), "/research"),
    ("candidate_promotion", "Candidate promotion", "How proposed improvements are reviewed before use.",
     ("candidate_promotion",), ""),
    ("knowledge_access", "Governed knowledge", "Which knowledge agents may use.", ("knowledge_access",), ""),
    ("capabilities", "Capabilities", "Browser, computer and code tools agents may use.", ("capabilities",),
     "/settings#settings-controls"),
    ("self_learning", "Self-learning", "Whether agents learn from past work.", ("self_learning",), ""),
    ("ekko", "Work discovery", "Finding work that could be automated.", ("ekko",), ""),
    ("automation_import", "Automation import", "Bringing in automations from other tools.",
     ("automation_import",), ""),
    ("event_triggers", "Event triggers", "Starting work when something happens elsewhere.",
     ("event_triggers",), ""),
    ("flows", "Workflows", "The workflow engine and its options.", ("flows",), ""),
    ("knowledge", "Knowledge library", "Searchable documents for agents.", ("knowledge",), ""),
    ("oauth_vault", "Account connections", "Keeping signed-in accounts sealed per workspace.", ("oauth",),
     "/connections"),
    ("governed_connectors", "Governed connectors", "Connections to business systems.", ("governed_connectors",),
     ""),
    ("durable", "Durable work", "Resuming long work after a restart.", ("durable",), ""),
    ("journal", "Change journal", "Recording file changes so they can be undone.", ("workspace",), ""),
    ("finance", "Finance", "Finance tools and their limits.", ("finance", "finance_operations"), ""),
    ("value", "Savings assumptions", "Your cost of doing work by hand, for the savings figures.", ("value",),
     "/savings"),
    ("assessments", "Assessments", "Help with assessments and reviews.", ("assessments",), ""),
    ("security_suite", "Security and compliance", "The security and compliance modules.",
     ("governance_agents", "threat_hunt", "env_hunt"), ""),
    ("advanced", "Advanced reasoning", "Planning, checking and other advanced options.",
     ("reflexion", "planning", "verification"), ""),
    ("api_keys", "API access", "Personal and service-account keys for scripts and integrations.",
     ("dashboard",), "/settings#api-keys"),
    ("web_search", "Web search", "Whether agents may search the web.", ("search",), ""),
    ("mcp_servers", "Tool servers", "Outside tool servers agents may use.", ("mcp_servers",), "/mcp"),
    ("plugins", "Add-ons", "Installed add-ons and what they may do.", ("plugins",), "/plugins"),
    ("tool_acl", "Tool permissions", "Which tools each channel may use.", ("tools",), ""),
    ("rate_limits", "Usage limits", "How often each tool may be used.", ("rate_limits",), ""),
    ("retention", "Data retention", "How long records are kept.", ("retention",), ""),
    ("analytics", "Usage analytics", "Whether usage is measured.", ("analytics",), ""),
    ("persona", "Assistant persona", "The assistant's name and style.", ("persona",), ""),
    ("notifications", "Notifications", "Where alerts are sent.", ("notifications",), ""),
    ("deadline_alerts", "Deadline alerts", "Warnings before a data request, incident clock, renewal or approval is due.",
     ("deadline_alerts",), ""),
    ("daily_summary", "Daily summary and chat approvals",
     "Each person's daily summary, and approval cards in Slack.", ("my_work", "notifications"), "/me"),
    ("webhooks", "Webhooks", "Sending events to other apps.", ("webhooks",), "/settings#settings-integrations"),
    ("a2a", "Outside agents", "Letting agents from other systems send work.", ("a2a",), ""),
)
_INSTALL_ONLY = {
    "deployment": "Fixed when Lightwork is installed: it records how this copy was set up.",
}


def _catalog_groups() -> dict[str, str]:
    from . import settings_catalog as catalog
    from .settings_all_page import _slug
    groups: dict[str, str] = {}
    for setting in catalog.SETTINGS:
        groups.setdefault(setting.top, _slug(setting.group))
    return groups


def steps() -> list[dict[str, Any]]:
    groups = _catalog_groups()
    out = []
    for number, (step_id, title, about, sections, page) in enumerate(STEPS, start=1):
        slug = next((groups[s] for s in sections if s in groups), "")
        if page:
            href, label = page, "Open"
        elif slug:
            href, label = "/settings/all#group-" + slug, "Open in All settings"
        else:
            href, label = "", ""
        out.append({"number": number, "id": step_id, "title": title, "about": about, "href": href,
                    "label": label, "in_app": bool(href),
                    "note": _INSTALL_ONLY.get(step_id, "" if href else "Not yet changeable in the app.")})
    return out


@router.get("/start/setup", response_class=HTMLResponse)
async def setup_guide_page(request: Request) -> HTMLResponse:
    require_global_permission(request, "admin")
    from .app import templates
    rows = await run_in_threadpool(steps)
    return templates.TemplateResponse(request, "setup_guide.html", {
        "steps": rows, "in_app": sum(1 for row in rows if row["in_app"]), "total": len(rows)},
        headers={"Cache-Control": "private, no-store"})


# ---- install a prepared settings file (maverick init --from-file) ---------------

async def _file(request: Request) -> bytes:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > _MAX_FILE:
            raise HTTPException(413, "That file is too large to be a settings file.")
    if not raw.strip():
        raise HTTPException(422, "Choose a settings file.")
    return raw


def _check(raw: bytes) -> dict[str, Any]:
    from maverick.config_install import ConfigInstallError, config_findings, parse_config_bytes
    try:
        cfg = parse_config_bytes(raw)
    except ConfigInstallError as exc:
        return {"valid": False, "summary": "This isn't a valid settings file, so it can't be installed.",
                "problems": 0, "technical": [{"level": "error", "text": str(exc)[:300]}]}
    findings = config_findings(cfg)
    errors = [f for f in findings if getattr(f, "severity", "") == "error"]
    summary = ("The file is valid and ready to install." if not findings else
               f"The file can be installed. The settings check found {len(findings)} thing"
               f"{'s' if len(findings) != 1 else ''} to look at"
               + (f", {len(errors)} of which this version will ignore" if errors else "") + ".")
    return {"valid": True, "summary": summary, "problems": len(findings),
            "sections": len([k for k, v in cfg.items() if isinstance(v, dict)]),
            "technical": [{"level": getattr(f, "severity", "info"),
                           "text": f"{getattr(f, 'section', '')}: {getattr(f, 'message', '')}"[:300]}
                          for f in findings][:80]}


@router.post("/api/v1/setup/settings-file/check")
async def check_settings_file(request: Request) -> dict:
    from . import app as app_module
    app_module._require_same_origin(request)
    require_global_permission(request, "admin")
    return await run_in_threadpool(_check, await _file(request))


@router.post("/api/v1/setup/settings-file/install")
async def install_settings_file(request: Request, confirm: bool = False) -> dict:
    from . import app as app_module
    app_module._require_same_origin(request)
    require_global_permission(request, "admin")
    if confirm is not True:
        raise HTTPException(409, "Confirm to install the settings file. Nothing was changed.")
    raw = await _file(request)
    checked = await run_in_threadpool(_check, raw)
    if not checked["valid"]:
        raise HTTPException(422, "This isn't a valid settings file, so it can't be installed.")
    from maverick.config import reset_config_cache
    from maverick.config_install import install_config_bytes

    from .system_checks_api import _record
    await run_in_threadpool(_record, request, "settings_file_installed", sections=checked.get("sections", 0))
    result = await run_in_threadpool(lambda: install_config_bytes(raw, backup=True))
    await run_in_threadpool(reset_config_cache)
    if result["unchanged"]:
        summary = "This settings file is already installed. Nothing changed."
    else:
        summary = ("Installed the settings file. It applies to the next task; some settings need Lightwork "
                   "to restart." + (" The previous file was kept as a backup copy." if result["backup"] else ""))
    return {"installed": not result["unchanged"], "summary": summary}
