"""Buttons for the learning systems that were terminal-only: the rest of
``self-harness`` (meaning-based clash check, transfer, test-case harvest,
export, import and the one-time hold-out record), a before/after preview for
``refine apply/revert``, ``flywheel``, ``factory-learn``, ``self-modify
status/archive/run`` and ``donate status/clear``; plus the background-job
status every long button polls.

Every change and every run that spends model money needs a global
administrator, the rung of the Learning page's switches. Each call goes
through the kernel function the CLI uses, so the kernel's own guards stay in
force (the learning stop, the evaluation spending limit, snapshots, the
hold-out ledger, the self-modification sandbox and its research-only rule).
State changes write one activity-record row naming the person before the
change is made. Long work runs as a background job the page polls.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import learning_jobs as jobs
from .auth import (
    caller_principal,
    has_global_permission,
    has_permission,
    require_global_permission,
    require_permission,
)

router = APIRouter(prefix="/learning-ops", tags=["learning-ops"])
asset_router = APIRouter()

_CORPUS_LIMIT = 5 * 1024 * 1024
_EVIDENCE_LIMIT = 2 * 1024 * 1024
_TEXT = 300
_JOB_ID = re.compile(r"[0-9a-f]{16}\Z")
_PROPOSAL_ID = re.compile(r"[0-9a-f]{16}\Z")
_HALTED = "Learning is stopped for this installation right now, so nothing was changed."
_DGM_SECTION = "Experimental: let Lightwork try improving its own code"


# ---- where a person turns something on or chooses a location ----------------
# Built from the pages' own registries, so a message always names the exact
# place (group, part and label) the person will find.

def setting_place(setting_id: str) -> str:
    """``All settings, <group>, <part>, “<label>”`` for one All settings row."""
    from . import settings_catalog as catalog
    s = catalog.BY_ID[setting_id]
    parts = ["All settings", s.group] + ([s.subgroup] if s.subgroup else []) + [f"“{s.label}”"]
    return ", ".join(parts)


def switch_place(section: str) -> str:
    """A system switch in the Learning page's “Learning systems” list."""
    from .api import _FEATURE_SWITCHES
    return f"“{_FEATURE_SWITCHES[section][0]}” under Learning systems on the Learning page"


def part_place(key: str) -> str:
    """One part's switch under “Turn each part of learning on or off”."""
    from .settings_store import LEARNING_SUBSYSTEMS
    label = next(s["label"] for s in LEARNING_SUBSYSTEMS if s["key"] == key)
    return f"“{label}” under “Turn each part of learning on or off” on the Learning page"


def _modify_blocker(code: object) -> str:
    if code == "self_improvement_disabled":
        return f"Turn on {part_place('self_improvement')} first."
    if code == "editable_surface_missing":
        return f"Choose which files it may practise on in {setting_place('self_modify.editable_paths')}."
    if code == "challenge_corpus_missing":
        return f"Add at least two tests in {setting_place('self_modify.eval_tests')}."
    if code == "attested_evaluator_missing":
        return ("It needs a separate, trusted practice area to test changes in. "
                "Ask whoever installed Lightwork to set one up.")
    if code == "learning_halt_active":
        return "Learning is stopped for this installation right now."
    return "A setting outside this page needs attention. Ask whoever installed Lightwork."


_MODIFY_REFUSALS = (
    ("explicit tenant", "This runs for the whole installation, so it cannot start inside one workspace. "
                        "Switch to the whole installation and try again."),
    ("disabled", f"Turn on the experiment under “{_DGM_SECTION}” on the Learning page first."),
    ("halt", _HALTED),
    ("eval_tests", "Add at least two tests it must pass, in All settings, Memory & learning, "
                   "Research experiments, “Tests each code experiment must pass”."),
    ("objective", "Describe what it should try to improve."),
    ("budget", "The spending limit for this experiment is used up or not set."),
    ("sandbox", "It needs a separate, trusted practice area to test changes in."),
    ("evaluator", "It needs a separate, trusted practice area to test changes in."),
    ("source capture", "Lightwork could not take a clean copy of its own code to practise on."),
    ("ledger", "The record of adopted improvements is not available right now."),
    ("tree", "Lightwork could not find a copy of its own code to practise on."),
)


class ClashIn(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)


class TransferIn(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str | None = Field(default=None, max_length=200)
    retry_judged: bool = False


class HarvestIn(BaseModel):
    mode: Literal["propose", "auto"] = "propose"


class ConfirmIn(BaseModel):
    confirm: bool = False


class JobStopIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: str = Field(pattern=r"^[a-f0-9]{64}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: str = Field(pattern=r"^[a-f0-9]{32}$")
    confirm: Literal[True]


class ModifyRunIn(BaseModel):
    objective: str = Field(min_length=10, max_length=2000)
    cycles: Literal[1, 2, 3, 5] = 1
    confirm: bool = False


# ---- shared helpers ---------------------------------------------------------

def _actor(request: Request) -> str:
    return caller_principal(request) or "local administrator"


def _workspace() -> str:
    from maverick.paths import current_tenant_id
    try:
        return current_tenant_id() or ""
    except Exception:  # noqa: BLE001 -- an unreadable binding is its own scope
        return "?"


def _record(request: Request, action: str, *, installation: bool = True, **detail: Any) -> None:
    """One learning activity-record row naming who acted, written BEFORE the
    change. A refused row stops the change (HTTP 503)."""
    from maverick.audit import AuditRefused, EventKind, audit_event
    try:
        audit_event(EventKind.LEARNING_UPDATE, agent="dashboard", _global=installation,
                    action=action, actor=_actor(request), **detail)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail=(
            "The activity record could not be written, so nothing was started.")) from exc


def _world():
    from ._shared import _world as shared
    try:
        return shared()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Lightwork's records are not available right now.") from exc


def _clip(value: Any, size: int = _TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= size else text[: size - 1] + "…"


def job_problem(exc: Exception) -> str | None:
    """A job failure as one sentence for people, or None for the generic one."""
    from maverick.budget import BudgetExceeded
    from maverick.learning_guard import Halted
    from maverick.provider_cost_cap import ProviderCapExceeded
    if isinstance(exc, Halted):
        return _HALTED
    if isinstance(exc, BudgetExceeded):
        return "It reached its spending limit and stopped. Anything finished before then was kept."
    if isinstance(exc, ProviderCapExceeded):
        return "Your AI provider's spending limit for this period has been reached, so it stopped."
    return None


def start_job(request: Request, kind: str, label: str, fn, *, needs_global: bool = True,
              cancellable: bool = False, on_cancel=None) -> dict:
    try:
        job = jobs.start(kind, label, fn, started_by=_actor(request), workspace=_workspace(),
                         needs_global=needs_global, cancellable=cancellable, on_cancel=on_cancel,
                         problem=job_problem)
    except jobs.JobRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job": job.view()}


def _can_see(request: Request, job: jobs.Job) -> bool:
    if job.needs_global:
        return has_global_permission(request, "admin")
    return has_permission(request, "admin") and job.workspace == _workspace()


def _choice(value, allowed) -> None:
    """A query value must be one the page offered."""
    if value not in allowed:
        raise HTTPException(status_code=422, detail="Choose from the list.")


def _budget(task_class: str):
    """The same config-, dashboard- and organisation-aware spending limit the
    CLI's runs use (``budget_from_config``). An exhausted organisation
    allowance refuses before anything starts, like the task run gate."""
    from maverick.budget import budget_from_config
    try:
        budget = budget_from_config(task_class=task_class)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=(
            "The spending limit for this is not set correctly, so nothing was started.")) from exc
    if getattr(budget, "_tenant_refusal", None):
        raise HTTPException(status_code=409, detail=(
            "Your organisation's spending allowance is used up, so nothing was started."))
    return budget


def _halt_preflight(job: str) -> None:
    from maverick.learning_guard import Halted, check_learning_halt
    try:
        check_learning_halt(job, "dashboard-start")
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc


async def _upload(request: Request, limit: int) -> bytes:
    from . import app as app_module
    app_module._require_same_origin(request)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > limit:
            raise HTTPException(status_code=413, detail="That file is too large.")
    if not data:
        raise HTTPException(status_code=422, detail="Choose a file first.")
    return bytes(data)


# ---- background jobs ----------------------------------------------------------

@router.get("/jobs")
async def list_jobs(request: Request) -> dict:
    require_permission(request, "view")
    return {"jobs": [j.view() for j in jobs.listed() if _can_see(request, j)][:20]}


def _job_or_404(request: Request, job_id: str) -> jobs.Job:
    job = jobs.get(job_id) if _JOB_ID.fullmatch(job_id) else None
    if job is None or not _can_see(request, job):
        raise HTTPException(status_code=404, detail=(
            "The original task result is no longer available. Lightwork may have restarted. This does not mean the work failed; do not repeat it automatically."))
    return job


_LEARNING_KINDS = {"self_harness_run", "self_harness_corpus_quality", "self_harness_clash",
                   "self_harness_transfer", "flywheel", "self_modify_run"}
_JOB_STATES = {"running": "Working", "stopping": "Stopping at the next safe point",
               "done": "Finished", "stopped": "Stopped early", "failed": "Stopped with a problem"}
_RESULT_COUNTS = {"patterns_found": "Patterns found", "ideas_tried": "Ideas tried",
                  "passed_testing": "Passed practice checks", "adopted": "Lessons adopted",
                  "retired": "Lessons retired", "removed": "Cases removed", "tested": "Ideas checked",
                  "tasks": "Completed tasks reviewed", "tried": "Lessons tried"}


def _history_job(request, job_id):
    require_permission(request, "view")
    job = _job_or_404(request, job_id)
    if (job.kind not in _LEARNING_KINDS or job.started_by != _actor(request)
            or job.workspace != _workspace()):
        raise HTTPException(404, "Choose one of your retained learning tasks in this workspace.")
    return job


def _result_counts(result):
    """Expose observations, never retained private lesson or case text."""
    rows = []
    if not isinstance(result, dict):
        return rows
    sources = result.get("reports") or result.get("targets") or [result]
    if not isinstance(sources, list):
        return rows
    from .plain_labels import model_label
    for source in sources[:20]:
        if not isinstance(source, dict):
            continue
        values = {}
        for key, label in _RESULT_COUNTS.items():
            value = source.get(key)
            if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                values[label] = value
        if source.get("paused_for_calibration") is True:
            values["Decision"] = "Paused for its learning checks"
        if source.get("promotion_gate_off") is True:
            values["Activation"] = "Learning permission was off"
        if values:
            rows.append({"label": model_label(source["model"]) if isinstance(source.get("model"), str) else "Recorded observations", "counts": values})
    return rows


def current_learning_job(request, job_id):
    from .draft_api import context_for
    job = _history_job(request, job_id)
    try:
        current = jobs.reviewed_view(job)
    except jobs.JobRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    value = current["job"]
    original = jobs.stop_receipt(job, None, _actor(request), context_for(request))
    result = {"context": context_for(request), "revision": current["revision"],
            "job": {key: value[key] for key in ("id", "label", "started_at", "finished_at", "state", "can_stop", "problem")},
            "status": _JOB_STATES[value["state"]], "observations": _result_counts(value["result"]),
            "original_stop": {"operation": original["operation"]} if original else None,
            "notice": "Recorded observations describe this task's results. Business improvement needs separate evidence."}
    _history_job(request, job_id)
    if result["context"] != context_for(request):
        raise HTTPException(409, "Your account or workspace changed. Read current tasks again.")
    return result


def current_learning_jobs(request):
    require_permission(request, "view")
    rows = [current_learning_job(request, job.id) for job in jobs.listed()
            if job.kind in _LEARNING_KINDS and job.started_by == _actor(request)
            and job.workspace == _workspace() and _can_see(request, job)]
    require_permission(request, "view")
    for row in rows:
        _history_job(request, row["job"]["id"])
    return {"jobs": rows, "notice": "Choose a recent learning task to read its progress." if rows else
                                    "No recent learning tasks are currently available to your account in this workspace."}


def reviewed_learning_stop(request, job_id, body: JobStopIn):
    from maverick.authority_effects import authority_effect_fence

    from .draft_api import context_for
    with authority_effect_fence():
        from .app import _require_same_origin
        _require_same_origin(request)
        job = _history_job(request, job_id)
        if body.context != context_for(request):
            raise HTTPException(409, "Your account or workspace changed. Read the current task again. Nothing was stopped.")
        def before_stop():
            require_permission(request, "view")
            if not _can_see(request, job) or job.started_by != _actor(request) or job.workspace != _workspace():
                raise HTTPException(403, "Your permission or workspace changed. Nothing was stopped.")
            _record(request, "job_stop", installation=job.needs_global, job=job.kind,
                    phase="prepared", operation=body.operation, reviewed_revision=body.revision)
        try:
            receipt = jobs.cancel_reviewed(job, body.revision, body.operation, _actor(request), body.context, before_stop)
        except jobs.JobRefused as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"receipt": {"requested_at": receipt["requested_at"], "message": "The original reviewed stop was requested. Work stops at its next safe point; completed changes remain."},
            "current": current_learning_job(request, job_id)}


def original_learning_stop(request, job_id, operation):
    from .draft_api import context_for
    job = _history_job(request, job_id)
    if not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise HTTPException(404, "Choose the original saved stop request.")
    row = jobs.stop_receipt(job, operation, _actor(request), context_for(request))
    return {"confirmed": row is not None, "current": current_learning_job(request, job_id),
            "message": "The original reviewed stop request is recorded. No stop or work was repeated." if row else
                       "The original stop is unconfirmed. Current progress is shown; its state cannot prove that request succeeded. Nothing was repeated."}


@router.get("/job-history")
async def learning_job_history(request: Request):
    return JSONResponse(await run_in_threadpool(current_learning_jobs, request), headers={"Cache-Control": "private, no-store"})


@router.get("/job-history/{job_id}")
async def learning_job_history_item(request: Request, job_id: str):
    return JSONResponse(await run_in_threadpool(current_learning_job, request, job_id), headers={"Cache-Control": "private, no-store"})


@router.post("/job-history/{job_id}/stop")
async def learning_job_history_stop(request: Request, job_id: str, body: JobStopIn):
    return JSONResponse(await run_in_threadpool(reviewed_learning_stop, request, job_id, body), headers={"Cache-Control": "private, no-store"})


@router.get("/job-history/{job_id}/stop/{operation}")
async def learning_job_original_stop(request: Request, job_id: str, operation: str):
    return JSONResponse(await run_in_threadpool(original_learning_stop, request, job_id, operation), headers={"Cache-Control": "private, no-store"})


@router.get("/jobs/{job_id}")
async def job_status(request: Request, job_id: str) -> dict:
    require_permission(request, "view")
    return {"job": _job_or_404(request, job_id).view()}


@router.post("/jobs/{job_id}/stop")
async def stop_job(request: Request, job_id: str) -> dict:
    require_permission(request, "view")
    job = _job_or_404(request, job_id)
    if not job.cancellable:
        raise HTTPException(status_code=409, detail="This task cannot be stopped part-way. Let it finish.")
    if job.active:
        await run_in_threadpool(lambda: _record(request, "job_stop", installation=job.needs_global,
                                                job=job.kind))
    if not jobs.cancel(job):
        raise HTTPException(status_code=409, detail="This task has already finished. Refresh to see its result.")
    return {"job": job.view()}


# ---- self-coaching (self-harness) -----------------------------------------------

def _fleet() -> list[str]:
    from maverick import self_harness
    from maverick import self_improvement_runner as runner
    return sorted(set(runner.harness_fleet_models()) | set(self_harness.list_learned()))[:50]


def _plan() -> dict:
    from maverick import self_harness
    from maverick.llm import model_for_role

    from .plain_labels import model_label
    st = self_harness.settings()
    cpath = st.get("eval_corpus")
    ledger = str(st.get("holdout_ledger") or "").strip()
    dollars = st.get("eval_budget_dollars")
    try:
        checker = model_label(model_for_role("verifier"))
    except Exception:  # noqa: BLE001 -- no provider configured yet
        checker = ""
    return {
        "enabled": bool(self_harness.enabled()),
        "test_cases_set_up": bool(cpath),
        "careful_testing": bool(st.get("risk_limited")),
        "spending_limit": float(dollars) if isinstance(dollars, (int, float)) and dollars > 0 else None,
        "checker": checker,
        "models": [{"value": m, "label": model_label(m)} for m in _fleet()],
        "hold_out": {"set_up": bool(ledger), "exists": bool(ledger) and Path(ledger).exists()},
        "places": {"test_cases": setting_place("self_harness.eval_corpus"),
                   "hold_out": setting_place("self_harness.holdout_ledger"),
                   "self_coaching": switch_place("self_harness")},
    }


@router.get("/self-harness/plan")
async def harness_plan(request: Request) -> dict:
    require_global_permission(request, "admin")
    try:
        return await run_in_threadpool(_plan)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="Self-coaching details are not available right now.") from exc


def _offered(model_id: str | None) -> str | None:
    if model_id and model_id not in _fleet():
        raise HTTPException(status_code=422, detail="Choose a model from the list.")
    return model_id or None


@router.post("/self-harness/clash-check")
async def clash_check(request: Request, body: ClashIn) -> dict:
    """``self-harness conflicts --semantic``: the checker model judges each
    possible pair by meaning. Stopping lowers its spending limit so the
    remaining pairs fall back to the wording check."""
    require_global_permission(request, "admin")
    from maverick import self_harness
    model = await run_in_threadpool(_offered, body.model_id)
    budget = _budget("self_harness")
    await run_in_threadpool(lambda: _record(request, "self_harness_semantic_conflicts", model=model or ""))

    def _run(job: jobs.Job) -> dict:
        from maverick.llm import LLM, model_for_role
        classifier = self_harness.llm_conflict_classifier(LLM(model_for_role("verifier")), budget=budget)
        pairs = self_harness.detect_store_conflicts(model_id=model, classifier_fn=classifier)
        return {"pairs": [{"model": m, "first": _clip(a), "second": _clip(b)} for m, a, b in pairs[:100]],
                "spent": round(float(budget.dollars), 4)}
    return start_job(request, "self_harness_clash", "Checking lessons for clashes by meaning", _run,
                     cancellable=True, on_cancel=jobs.stop_budget(budget))


@router.post("/self-harness/transfer")
async def transfer(request: Request, body: TransferIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import self_harness
    from maverick import self_improvement_runner as runner
    if not self_harness.enabled():
        raise HTTPException(status_code=409, detail=f"Turn on {switch_place('self_harness')} first.")
    if not self_harness.settings().get("eval_corpus"):
        raise HTTPException(status_code=409, detail=(
            "Trying lessons on other models needs test cases. Set them up first."))
    fleet = await run_in_threadpool(_fleet)
    if body.source not in fleet or (body.target and body.target not in fleet):
        raise HTTPException(status_code=422, detail="Choose models from the list.")
    targets = [body.target] if body.target else [m for m in runner.harness_fleet_models() if m != body.source]
    targets = [t for t in targets if t != body.source]
    if not targets:
        raise HTTPException(status_code=409, detail="There is no other model to try these lessons on.")
    _halt_preflight("self-harness")
    await run_in_threadpool(lambda: _record(request, "self_harness_transfer", source=body.source,
                                            targets=targets, retry_judged=body.retry_judged))

    def _run(job: jobs.Job) -> dict:
        report = runner.run_self_harness_transfer(body.source, targets=targets, force=body.retry_judged,
                                                  raise_errors=True)
        return {"targets": [{"model": t, "tried": len(r.get("attempted") or []),
                             "on_trial": [_clip(x) for x in (r.get("promoted") or [])[:20]]}
                            for t, r in sorted(report.items())][:20]}
    return start_job(request, "self_harness_transfer", "Trying proven lessons on other models", _run)


def _corpus_path() -> str:
    from maverick import self_harness
    cpath = self_harness.settings().get("eval_corpus")
    if not cpath:
        raise HTTPException(status_code=409, detail=(
            "Test cases are not set up yet. Choose where to keep them in "
            f"{setting_place('self_harness.eval_corpus')}."))
    return str(cpath)


@router.post("/self-harness/test-cases/harvest")
async def harvest(request: Request, body: HarvestIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import self_improvement_runner as runner
    await run_in_threadpool(_corpus_path)
    world = _world()
    await run_in_threadpool(lambda: _record(request, "self_harness_corpus_harvest", mode=body.mode))
    try:
        n = await run_in_threadpool(lambda: runner.run_corpus_harvest(world, mode=body.mode, raise_errors=True))
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail=(
            "Lightwork could not look through past work right now. Nothing was changed.")) from exc
    return {"found": int(n), "mode": body.mode}


@router.get("/self-harness/test-cases/export")
async def export_cases(request: Request) -> Response:
    require_global_permission(request, "admin")
    from maverick import self_harness_eval as ev
    cpath = await run_in_threadpool(_corpus_path)
    raw = await run_in_threadpool(ev._load_raw, cpath)
    cases = sum(len(v) for v in raw.values() if isinstance(v, list))
    await run_in_threadpool(lambda: _record(request, "self_harness_corpus_export", cases=cases))
    return Response(json.dumps(raw, indent=2, sort_keys=True), media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="Lightwork-test-cases.json"',
        "Cache-Control": "private, no-store", "X-Lightwork-Cases": str(cases)})


def _parse_cases(raw: bytes) -> dict:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="That file is not a test-case file Lightwork can read.") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="That file is not a test-case file Lightwork can read.")
    return data


def _plain_keys(preview: dict) -> dict:
    from .plain_labels import model_label
    preview["keys"] = [dict(k, label=model_label(k["key"])) for k in preview["keys"][:50]]
    return preview


@router.post("/self-harness/test-cases/import/preview")
async def import_preview(request: Request, replace: bool = Query(default=False)) -> dict:
    require_global_permission(request, "admin")
    from maverick import self_harness_eval as ev
    cpath = await run_in_threadpool(_corpus_path)
    data = _parse_cases(await _upload(request, _CORPUS_LIMIT))
    try:
        preview = await run_in_threadpool(lambda: ev.preview_corpus_import(cpath, data, replace=replace))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That file is not a test-case file Lightwork can read.") from exc
    return _plain_keys(preview)


@router.post("/self-harness/test-cases/import")
async def import_cases(request: Request, replace: bool = Query(default=False),
                       confirm: bool = Query(default=False)) -> dict:
    require_global_permission(request, "admin")
    if not confirm:
        raise HTTPException(status_code=422, detail="Confirm the change first.")
    from maverick import self_harness_eval as ev
    cpath = await run_in_threadpool(_corpus_path)
    data = _parse_cases(await _upload(request, _CORPUS_LIMIT))
    await run_in_threadpool(lambda: _record(request, "self_harness_corpus_import", replace=replace))
    try:
        n = await run_in_threadpool(lambda: ev.import_corpus(cpath, data, replace=replace))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That file is not a test-case file Lightwork can read.") from exc
    return {"cases": int(n), "replaced": replace}


@router.post("/self-harness/hold-out/set-up")
async def set_up_hold_out(request: Request, body: ConfirmIn) -> dict:
    """``self-harness holdout provision``: create the configured hold-out
    record exactly once. An existing record is never replaced."""
    require_global_permission(request, "admin")
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm the change first.")
    from maverick.config import get_self_harness
    from maverick.self_harness_holdout import HoldoutLedgerError, HoldoutQueryLedger
    target = str(get_self_harness().get("holdout_ledger") or "").strip()
    if not target:
        raise HTTPException(status_code=409, detail=(
            f"Choose where to keep the hold-out record first, in {setting_place('self_harness.holdout_ledger')}."))
    if Path(target).exists():
        raise HTTPException(status_code=409, detail="The hold-out record is already set up. It is never replaced.")
    await run_in_threadpool(lambda: _record(request, "self_harness_holdout_provision"))

    def _make() -> str:
        return str(HoldoutQueryLedger.provision(Path(target)).verify().ledger_id)
    try:
        ledger_id = await run_in_threadpool(_make)
    except (HoldoutLedgerError, OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=(
            "The hold-out record could not be created. It may already exist, or the folder "
            "cannot be written.")) from exc
    return {"created": True, "record_number": ledger_id}


# ---- suggested instruction changes: before/after preview ---------------------------

def _refine_preview(proposal_id: str) -> dict:
    from maverick import harness_refine

    from .learning_loop_api import _refine_problem
    try:
        review = harness_refine.change_review(proposal_id)
    except harness_refine.RefineError as exc:
        if "unknown refinement proposal" in str(exc):
            raise HTTPException(404, "That suggestion no longer exists. Refresh the page.") from exc
        raise _refine_problem(exc) from exc
    row = review["proposal"]
    changes = [{"title": _clip(str((change["after"] or change["before"]).get("name") or "").replace("-", " "), 96),
                "before": (change["before"] or {}).get("change") or "",
                "after": (change["after"] or {}).get("change") or ""}
               for change in review["changes"]]
    selected = next((change for change in review["changes"]
                     if change["key"] == harness_refine._key(row["target"], row["name"])), {})
    return {"title": _clip(str(row.get("name") or "").replace("-", " ").replace("_", " "), 96),
            "revision": review["revision"], "status": row.get("status"),
            "now": (selected.get("before") or {}).get("change") or "",
            "after": (selected.get("after") or {}).get("change") or "",
            "changes": changes,
            "undo_also_removes": [change["title"] for change in changes if change["before"] and not change["after"]]
            if row.get("status") == "applied" else []}


@router.get("/refinements/{proposal_id}/preview")
async def refine_preview(request: Request, proposal_id: str) -> dict:
    require_global_permission(request, "admin")
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        raise HTTPException(status_code=404, detail="That suggestion no longer exists. Refresh the page.")
    return await run_in_threadpool(_refine_preview, proposal_id)


# ---- improvement flywheel --------------------------------------------------------

@router.post("/flywheel/turn")
async def turn_flywheel(request: Request) -> dict:
    require_global_permission(request, "admin")
    from maverick import data_engine
    if not data_engine.enabled():
        raise HTTPException(status_code=409, detail=f"Turn on {part_place('data_engine')} first.")
    _halt_preflight("flywheel")
    await run_in_threadpool(lambda: _record(request, "flywheel_turn"))

    def _run(job: jobs.Job) -> dict:
        from maverick.flywheel import maybe_run
        rep = maybe_run()
        return {"acted": bool(rep.acted), "tasks": int(rep.n_episodes),
                "avoid": [_clip(g.action, 120) for g in rep.guardrails[:10]],
                "habits": [_clip(m.action, 120) for m in rep.memories[:10]],
                "ideas": [{"instead_of": _clip(h.baseline_action, 120), "try": _clip(h.candidate_action, 120)}
                          for h in rep.hypotheses[:10]],
                "counts": {"avoid": len(rep.guardrails), "habits": len(rep.memories),
                           "ideas": len(rep.hypotheses)}}
    return start_job(request, "flywheel", "Turning the improvement flywheel", _run)


# ---- improving how new agents are built (factory-learn) ----------------------------

def _factory_on() -> None:
    from maverick import factory_learning
    if not factory_learning.enabled():
        raise HTTPException(status_code=409, detail=(
            f"Turn on {part_place('factory_learning')} first."))


@router.get("/factory/preview")
async def factory_preview(request: Request, min_support: int = 3) -> dict:
    require_global_permission(request, "admin")
    from maverick import factory_learning
    _choice(min_support, (2, 3, 5))
    _factory_on()
    _halt_preflight("factory-learning")
    rows = await run_in_threadpool(lambda: factory_learning.mine_corrections(min_support=min_support))
    return {"corrections": [{"guidance": _clip(c.guidance, 400), "seen_in": int(c.support),
                             "area": _clip(str(c.scope).replace("_", " "), 80)} for c in rows[:50]]}


@router.post("/factory/promote")
async def factory_promote(request: Request, min_support: int = 3,
                          confirm: bool = Query(default=False)) -> dict:
    require_global_permission(request, "admin")
    _choice(min_support, (2, 3, 5))
    if not confirm:
        raise HTTPException(status_code=422, detail="Confirm the change first.")
    from maverick import factory_learning
    from maverick.learning_guard import Halted
    _factory_on()
    raw = await _upload(request, _EVIDENCE_LIMIT)
    _halt_preflight("factory-learning")
    await run_in_threadpool(lambda: _record(request, "factory_learning_promote", min_support=min_support))

    def _do() -> list:
        with tempfile.TemporaryDirectory(prefix="lw-factory-") as folder:
            path = Path(folder) / "evidence.json"
            path.write_bytes(raw)
            scorer = factory_learning.load_measured_evidence(path)
        return factory_learning.review_and_promote(min_support=min_support, scorer=scorer)
    try:
        promoted = await run_in_threadpool(_do)
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=(
            "That results file cannot be used: it must be a complete, unchanged version 2 results file.")) from exc
    return {"adopted": len(promoted or [])}


# ---- experimental self-modification (self-modify) ------------------------------------

def _modify_status() -> dict:
    from maverick import self_improvement as si
    from maverick import self_modify as sm
    from maverick.budget import budget_from_config
    from maverick.self_modify_loop import earned_surface
    from maverick.self_modify_runner import build_policy, load_archive
    status = sm.production_status()
    archive = load_archive()
    try:
        ledger = getattr(si.shared(), "ledger", None)
    except Exception:  # noqa: BLE001
        ledger = None
    tier, surface = earned_surface(archive, build_policy(), ledger=ledger)
    try:
        limit = budget_from_config(task_class="self_modify").max_dollars
    except Exception:  # noqa: BLE001 -- shown as "not set"
        limit = None
    return {"state": status["state"], "requested": bool(status["requested"]),
            "running_allowed": bool(status["effective"]),
            "blockers": [_modify_blocker(b.get("code")) for b in status["blockers"]][:10],
            "level": str(getattr(tier, "name", "") or "").replace("_", " "),
            "areas": len(surface.editable_globs),
            "results": len(archive.candidates),
            "spending_limit": float(limit) if isinstance(limit, (int, float)) else None,
            "technical": {"areas": [str(g) for g in surface.editable_globs][:40]}}


@router.get("/self-modify")
async def modify_status(request: Request) -> dict:
    require_global_permission(request, "admin")
    try:
        return await run_in_threadpool(_modify_status)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The experiment's status is not available right now.") from exc


@router.get("/self-modify/results")
async def modify_results(request: Request, limit: int = Query(default=20, ge=1, le=100)) -> dict:
    require_global_permission(request, "admin")
    from maverick.self_modify_runner import load_archive
    archive = await run_in_threadpool(load_archive)
    rows = sorted(archive.candidates, key=lambda c: c.created_at, reverse=True)[:limit]
    return {"results": [{"when": c.created_at, "round": int(c.generation), "score": round(float(c.score), 3),
                         "before": round(float(c.baseline_score), 3), "summary": _clip(c.summary, 400),
                         "undone": bool(c.rolled_back), "widens": bool(c.capability_widens)}
                        for c in rows]}


def _modify_reason(reason: str) -> str:
    text = (reason or "").lower()
    for needle, sentence in _MODIFY_REFUSALS:
        if needle in text:
            return sentence
    return "The experiment could not run. Nothing was changed. Try again."


@router.post("/self-modify/run")
async def modify_run(request: Request, body: ModifyRunIn) -> dict:
    """``self-modify run``: research cycles that propose, test in the practice
    area and keep results for review. The runner never adopts code; every
    refusal it makes is shown, never worked around."""
    require_global_permission(request, "admin")
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm the change first.")
    from maverick.self_modify_runner import run
    budget = _budget("self_modify")
    objective = " ".join(body.objective.split())
    await run_in_threadpool(lambda: _record(request, "self_modify_run", cycles=body.cycles,
                                            objective=objective[:300]))

    def _run(job: jobs.Job) -> dict:
        summary = run(cycles=body.cycles, objective=objective, budget=budget)
        out = {"ran": bool(summary.ran), "tested": int(summary.evaluated),
               "spent": round(float(budget.dollars), 4),
               "rounds": [{"ideas": int(r.proposed), "tested": int(r.evaluated),
                           "note": _clip(r.reason, 200)} for r in summary.reports[:20]]}
        if not summary.ran:
            out["problem"] = _modify_reason(summary.reason)
        return out
    return start_job(request, "self_modify_run", "Practising changes to its own code", _run,
                     cancellable=True, on_cancel=jobs.stop_budget(budget))


# ---- donated task outlines (donate) --------------------------------------------------

@router.get("/donations")
async def donations(request: Request) -> dict:
    require_permission(request, "view")
    from maverick.donation import _donations_enabled, _text_donations_enabled, list_pending

    def _read() -> dict:
        pending = list_pending()
        sizes = []
        for p in pending[:10]:
            try:
                sizes.append(int(p.stat().st_size))
            except OSError:
                sizes.append(0)
        return {"sharing": bool(_donations_enabled()), "with_text": bool(_text_donations_enabled()),
                "waiting": len(pending), "sizes": sizes, "can_clear": has_permission(request, "admin")}
    return await run_in_threadpool(_read)


@router.post("/donations/clear")
async def clear_donations(request: Request, body: ConfirmIn) -> dict:
    require_permission(request, "admin")
    if not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm the change first.")
    from maverick.donation import clear_outbox, list_pending
    waiting = len(await run_in_threadpool(list_pending))
    if not waiting:
        return {"cleared": 0}
    await run_in_threadpool(lambda: _record(request, "donations_clear", installation=False, waiting=waiting))
    return {"cleared": int(await run_in_threadpool(clear_outbox))}


# ---- page scripts ---------------------------------------------------------------------

_SCRIPTS = ("lightwork-ops-jobs.js", "lightwork-learning-ops.js", "lightwork-learned-ops.js",
            "lightwork-workforce-ops.js")


def _script(name: str):
    async def serve() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / name,
                            media_type="application/javascript; charset=utf-8",
                            headers={"Cache-Control": "no-store, private, max-age=0",
                                     "X-Content-Type-Options": "nosniff"})
    serve.__name__ = "asset_" + name.replace("-", "_").replace(".", "_")
    return serve


for _name in _SCRIPTS:
    asset_router.add_api_route("/static/" + _name, _script(_name), methods=["GET"], include_in_schema=False)


__all__ = ["asset_router", "job_problem", "router", "start_job"]
