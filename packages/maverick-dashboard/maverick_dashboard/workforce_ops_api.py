"""Workforce and task-page buttons for commands that were terminal-only:
``domains-audit`` (what each specialist can do and what stops it, with the
full document as a download), ``domains-lint`` (check every specialist's
setup, as a background job), ``domains-eval`` (check the specialist test
suite), ``forward`` (tasks coming due) and ``fleet-memory --register`` (add
an outside agent to the shared-memory list).

Reads need ``view`` and follow the page's own scoping: the coming-due list
shows only the tasks the person may see. The downloadable inventory, the
setup check and adding an outside agent need a workspace administrator; the
changes write one activity-record row naming the person first.
"""
from __future__ import annotations

import json
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import goal_owner_filter, require_permission
from .learning_ops_api import _choice, _clip, _world, start_job

router = APIRouter(prefix="/workforce-ops", tags=["workforce-ops"])

_FINDINGS = (
    ("allow_tools is empty", "It has no list of allowed tools, so it could use every tool."),
    ("max_risk is unset", "It has no limit on how risky its actions may be."),
    ("max_risk", "Its risk limit is not a level Lightwork recognises."),
    ("effort", "Its effort level is not a level Lightwork recognises."),
    ("both allowed and denied", "Some tools are both allowed and blocked. Blocked wins."),
    ("persona under", "Its description of who it is, is very short."),
    ("description is empty", "It has no description for the list of specialists."),
    ("no knowledge_sources", "It has no reference sources to base its work on."),
    ("no deny_tools", "It does not explicitly block any tools."),
    ("read-only pack", "It only reads, but does not explicitly block tools that change things."),
    ("output.gate", "Its sign-off rule is lighter than the sign-off on its last step."),
    ("workflow", "A step in its playbook has a problem."),
    ("step", "A step in its playbook has a problem."),
    ("tool", "It lists a tool that is not available, or is riskier than its limit allows."),
)
_VENDORS = {"salesforce": "Salesforce Agentforce", "microsoft": "Microsoft Copilot",
            "google": "Google Gemini", "servicenow": "ServiceNow", "openai": "OpenAI",
            "anthropic": "Anthropic", "open-source": "Open-source agent", "custom": "Built in-house"}


class AgentIn(BaseModel):
    vendor: Literal["salesforce", "microsoft", "google", "servicenow", "openai", "anthropic",
                    "open-source", "custom"]
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    description: str = Field(default="", max_length=200)


def _audit(request: Request, action: str, **detail: Any) -> None:
    from maverick.audit import AuditRefused, EventKind, audit_event

    from .auth import caller_principal
    try:
        audit_event(EventKind.CONTROL_PLANE_ACTION, agent="dashboard", verb=action,
                    actor=caller_principal(request) or "local administrator", ok=True, **detail)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail=(
            "The activity record could not be written, so nothing was done.")) from exc


def _name(pack: str) -> str:
    from .plain_labels import pack_display_name
    return pack_display_name(pack)


def _finding(text: str) -> str:
    low = text.lower()
    return next((plain for needle, plain in _FINDINGS if needle in low), "Another setup problem.")


# ---- what each specialist can do (domains-audit) ----------------------------------

def _audits(suite: str | None):
    from maverick.domain_audit import audit_roster
    audits = audit_roster()
    suites = sorted({a.suite for a in audits if a.suite})
    if suite:
        if suite not in suites:
            raise HTTPException(status_code=422, detail="Choose a department from the list.")
        audits = [a for a in audits if a.suite == suite]
    return audits, suites


def _inventory(suite: str | None) -> dict:
    from maverick.agent_autonomy import levels_enabled
    from maverick.domain_audit import summarize
    audits, suites = _audits(suite)
    s = summarize(audits)
    flagged = [a for a in audits if a.reachable_dangerous and not a.is_builder]
    return {
        "suites": [{"value": x, "label": x.replace("_", " ").capitalize()} for x in suites],
        "specialists": s["packs"], "departments": s["suites"], "builders": s["builders"],
        "can_change_things_while_drafting": s["drafting_agents_reaching_a_mutator"],
        "sign_off_enforced": s["packs_with_human_gate"],
        "sign_off_only_asked": s["packs_with_unenforced_prompt_gate"],
        "extra_refusals": s["packs_with_refusals_beyond_universal"],
        "with_deliverable": s["packs_with_deliverable"],
        "levels_enabled": levels_enabled(),
        "independence": s.get("autonomy_posture", {}),
        "still_supervised": s.get("packs_onboarding", 0),
        "flagged": [{"specialist": _name(a.name),
                     "can_reach": [x.replace("_", " ") for x in a.reachable_dangerous][:10]}
                    for a in flagged][:100],
    }


@router.get("/inventory")
async def inventory(request: Request, suite: str | None = Query(default=None, max_length=80)) -> dict:
    require_permission(request, "view")
    return await run_in_threadpool(_inventory, suite or None)


@router.get("/inventory/download")
async def inventory_download(request: Request, suite: str | None = Query(default=None, max_length=80)) -> Response:
    require_permission(request, "admin")
    from maverick.domain_audit import to_json
    audits, _suites = await run_in_threadpool(_audits, suite or None)
    await run_in_threadpool(lambda: _audit(request, "specialist_inventory_download",
                                           suite=suite or "", specialists=len(audits)))
    body = json.dumps(to_json(audits), indent=2)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-specialist-inventory.json"',
        "Cache-Control": "private, no-store"})


# ---- check every specialist's setup (domains-lint) -----------------------------------

@router.post("/setup-check")
async def setup_check(request: Request) -> dict:
    require_permission(request, "admin")

    def _run(job) -> dict:
        from maverick.domain import available_domains, lint_profile
        domains = available_domains()
        rows, n_err, n_warn = [], 0, 0
        for name in sorted(domains):
            if job.cancelled:
                break
            errors, warnings = lint_profile(domains[name])
            n_err += len(errors)
            n_warn += len(warnings)
            if errors or warnings:
                rows.append({"specialist": _name(name), "must_fix": sorted({_finding(e) for e in errors}),
                             "could_improve": sorted({_finding(w) for w in warnings}),
                             "technical": [_clip(x, 240) for x in (errors + warnings)[:10]]})
        rows.sort(key=lambda r: (-len(r["must_fix"]), r["specialist"]))
        return {"specialists": len(domains), "must_fix": n_err, "could_improve": n_warn,
                "findings": rows[:300], "more": max(0, len(rows) - 300)}
    return start_job(request, "specialist_setup_check", "Checking every specialist's setup", _run,
                     needs_global=False, cancellable=True)


# ---- specialist test suite (domains-eval --check) ----------------------------------

def _test_suite() -> dict:
    from maverick.domain_eval import GOLDEN_CASES, check_suite
    problems = check_suite()
    cases = []
    for c in GOLDEN_CASES[:200]:
        checks = []
        if c.expect_includes:
            checks.append("mentions " + ", ".join(c.expect_includes))
        if c.expect_excludes:
            checks.append("never mentions " + ", ".join(c.expect_excludes))
        if c.expect_refusal:
            checks.append("refuses")
        if c.expect_citation:
            checks.append("cites a source")
        cases.append({"specialist": _name(c.domain), "task": _clip(c.task, 200), "expects": checks})
    return {"cases": len(GOLDEN_CASES), "specialists": len({c.domain for c in GOLDEN_CASES}),
            "problems": len(problems), "technical": [_clip(p, 240) for p in problems[:50]],
            "list": cases}


@router.get("/test-suite")
async def test_suite(request: Request) -> dict:
    require_permission(request, "view")
    return await run_in_threadpool(_test_suite)


# ---- tasks coming due (forward) -----------------------------------------------------

@router.get("/upcoming")
async def upcoming(request: Request, days: int = 30) -> dict:
    require_permission(request, "view")
    _choice(days, (7, 14, 30, 60, 90))
    from maverick.forward_ledger import upcoming as ledger
    world = _world()
    owner = goal_owner_filter(request)
    now = time.time()
    rows = await run_in_threadpool(lambda: ledger(world, days=days, now=now, owner=owner))
    return {"days": days, "tasks": [
        {"task": int(r.goal.id), "title": _clip(r.goal.title, 200),
         "department": (r.goal.domain or "").replace("_", " "), "due": r.deadline,
         "overdue": r.deadline < now, "questions": int(r.open_questions)}
        for r in rows[:200]]}


# ---- outside agents sharing memory (fleet-memory --register) --------------------------

@router.get("/outside-agents/vendors")
async def vendors(request: Request) -> dict:
    require_permission(request, "view")
    return {"vendors": [{"value": k, "label": v} for k, v in _VENDORS.items()]}


@router.post("/outside-agents")
async def add_outside_agent(request: Request, body: AgentIn) -> dict:
    require_permission(request, "admin")
    from maverick import fleet_memory
    if not fleet_memory.enabled():
        from .learning_ops_api import switch_place
        raise HTTPException(status_code=409, detail=f"Turn on {switch_place('fleet_memory')} first.")
    await run_in_threadpool(lambda: _audit(request, "fleet_memory_register", vendor=body.vendor,
                                           outside_agent=body.name))
    ok = await run_in_threadpool(lambda: fleet_memory.register_agent(
        body.name, body.vendor, description=" ".join(body.description.split())))
    if not ok:
        raise HTTPException(status_code=409, detail=(
            "That agent name is already used by another company's agent, or the list could not be saved."))
    return {"added": True, "agent": body.name, "vendor": _VENDORS[body.vendor]}


__all__ = ["router"]
