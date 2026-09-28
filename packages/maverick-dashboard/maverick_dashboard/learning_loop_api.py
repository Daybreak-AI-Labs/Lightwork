"""Buttons for the Learned page's decision points that were CLI-only:
``refine list/show/propose/apply/revert`` and the ``self-harness`` read-outs
(``efficacy``, ``log``, ``preview``, ``conflicts``, ``corpus quality``,
``holdout verify``) plus ``self-harness run``.

Reads need ``view`` (the lessons are already on the page) or ``audit`` (the
learning activity log and the hold-out ledger check read the audit side).
Every change -- suggesting, applying or undoing an instruction change, and
starting a learning pass or a test-case check that spends evaluation budget --
needs a global administrator, the same rung as the other learning buttons.
Each call goes through the kernel function the CLI uses, so the kernel's own
guards (the learning stop, the approval card, snapshots, evaluation budget,
the signed learning audit) stay in force; dashboard-started runs are also
recorded with the person who started them.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_global_permission, require_permission

router = APIRouter(prefix="/learning-loop", tags=["learning-loop"])

_MAX_ROWS = 100
_MAX_TEXT = 300
_PROPOSAL_ID = re.compile(r"[0-9a-f]{16}\Z")
_TARGET_LABELS = {
    "prompt": "Standing instructions",
    "skill": "A named procedure",
    "memory": "Something it remembers",
}
_APPROVAL_LABELS = {
    "pending": "Waiting for approval",
    "approved": "Approved",
    "denied": "Turned down",
    "rejected": "Turned down",
    "expired": "Approval expired",
}
_PHASE_LABELS = {
    "apply": "Learned a new lesson",
    "forget": "Removed a lesson",
    "retire": "Retired a lesson that was no longer confirmed",
    "relapse": "Put a lesson back on trial after recent failures",
}
_HALTED = "Learning is stopped for this installation right now, so nothing was changed."


class ProposeIn(BaseModel):
    target: Literal["prompt", "skill", "memory"] = "prompt"
    title: str = Field(min_length=1, max_length=90)
    failure: str = Field(min_length=1, max_length=2000)
    change: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)


class RunIn(BaseModel):
    scope: Literal["main", "all"] = "main"


class QualityIn(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)


def _clip(value: Any, size: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _audit(request: Request, action: str, **payload: Any) -> None:
    from maverick.audit import AuditRefused, audit_event
    try:
        audit_event("learning_action", agent="dashboard", action=action,
                    actor=caller_principal(request) or "local", **payload)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail="The activity log refused this change.") from exc


def _actor(request: Request) -> str:
    from .api import _request_actor
    return _request_actor(request)


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip()).strip("-._")
    return slug[:96].rstrip("-._")


def _refine_problem(exc: Exception) -> HTTPException:
    """A kernel refusal as one plain sentence (never the kernel's wording)."""
    text = str(exc).lower()
    rules = (
        ("is off", "Suggested instruction changes are turned off. Turn them on in All settings first."),
        ("injection screen", "Part of this text reads like an instruction to ignore Lightwork's rules, "
                             "so it was refused. Reword it and try again."),
        ("queue is full", "Too many suggestions are waiting for a decision. Decide some of them first."),
        ("refinement name", "Give the change a short title using letters and numbers."),
        ("ceiling", "Each box can hold up to 2,000 characters."),
        ("is required", "Fill in every box."),
        ("already applied a refinement", "The approval for this change was already used. "
                                         "Suggest it again to ask for a new approval."),
        ("needs an approved decision", "This change is still waiting for approval. "
                                       "An approver can decide it on the Approvals page."),
        ("needs the id of an approved", "This change has no approval yet. Suggest it again to ask for one."),
        ("no longer matches", "This change or its approval no longer matches. "
                              "Suggest it again and ask for a new approval."),
        ("actor", "Only the person who suggested this change can apply it after it is approved."),
        ("evaluator anchors", "Lightwork's quality checks are not in their released state, "
                              "so no change can be applied right now."),
        ("anchor guard", "Lightwork's quality checks could not run, so no change can be applied right now."),
        ("no longer retained", "The restore point for this change is no longer kept, "
                               "so it cannot be undone automatically."),
        ("snapshot", "Lightwork could not save or restore a restore point, so nothing was changed."),
        ("only a pending proposal", "Only a change that is still waiting can be applied."),
        ("unknown refinement proposal", "That suggestion no longer exists. Refresh the page."),
    )
    for needle, sentence in rules:
        if needle in text:
            return HTTPException(status_code=409, detail=sentence)
    return HTTPException(status_code=409, detail="Lightwork could not do that. Nothing was changed.")


def _approval_state(approval_id: Any) -> str:
    if approval_id in (None, ""):
        return ""
    try:
        from ._shared import _world
        approval = _world().get_approval(int(approval_id))
    except Exception:  # noqa: BLE001 -- the state line is advisory
        return ""
    if approval is None:
        return "Approval no longer on record"
    return _APPROVAL_LABELS.get(str(approval.status), str(approval.status).replace("_", " ").capitalize())


def _proposal_view(row: dict) -> dict:
    status = str(row.get("status") or "")
    approval = _approval_state(row.get("approval_id")) if status == "pending" else ""
    return {
        "id": str(row.get("id") or ""),
        "status": status,
        "target": _TARGET_LABELS.get(str(row.get("target") or ""), "Instructions"),
        "title": _clip(str(row.get("name") or "").replace("-", " ").replace("_", " "), 96),
        "failure": _clip(row.get("failure")),
        "change": _clip(row.get("change"), 600),
        "rationale": _clip(row.get("rationale")),
        "proposed_by": _clip(row.get("proposed_by"), 120),
        "proposed_at": row.get("proposed_at"),
        "applied_at": row.get("applied_at"),
        "reverted_at": row.get("reverted_at"),
        "approval": approval,
        "can_apply": status == "pending" and approval in ("", "Approved"),
        "can_revert": status == "applied",
        "technical": {
            "reference": str(row.get("id") or ""),
            "approval_number": row.get("approval_id"),
            "fingerprint": str(row.get("digest") or "")[:16],
        },
    }


def _refinements_snapshot() -> dict:
    from maverick import harness_refine
    cfg = harness_refine._settings()
    proposals = harness_refine.list_proposals()
    in_force = harness_refine.refinements()
    return {
        "enabled": bool(cfg.get("enable")),
        "require_approval": bool(cfg.get("require_approval", True)),
        "proposals": [_proposal_view(p) for p in proposals[:_MAX_ROWS]],
        "proposals_truncated": len(proposals) > _MAX_ROWS,
        "in_force": [
            {
                "target": _TARGET_LABELS.get(str(r.get("target") or ""), "Instructions"),
                "title": _clip(str(r.get("name") or "").replace("-", " ").replace("_", " "), 96),
                "change": _clip(r.get("change"), 600),
                "applied_at": r.get("applied_at"),
                "applied_by": _clip(r.get("applied_by"), 120),
            }
            for r in in_force[:_MAX_ROWS]
        ],
    }


@router.get("/refinements")
async def refinements(request: Request) -> dict:
    require_permission(request, "view")
    try:
        return await run_in_threadpool(_refinements_snapshot)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="Suggested changes are not available right now.") from exc


def _bound(request: Request, fn):
    """Run ``fn`` with the caller bound as the kernel's current identity."""
    from maverick import connections
    principal = caller_principal(request)

    def call():
        with connections.bind_principal(principal):
            return fn()
    return call


@router.post("/refinements")
async def propose_refinement(request: Request, body: ProposeIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import harness_refine
    name = _slug(body.title)
    if not name:
        raise HTTPException(status_code=422, detail="Give the change a short title using letters and numbers.")
    actor = _actor(request)
    observation = {"failure": body.failure, "target": body.target, "name": name,
                   "change": body.change, "rationale": body.rationale}
    try:
        out = await run_in_threadpool(_bound(
            request, lambda: harness_refine.propose(observation, proposed_by=actor)))
    except harness_refine.RefineError as exc:
        raise _refine_problem(exc) from exc
    view = _proposal_view(out)
    return {"proposal": view, "needs_approval": bool(out.get("approval_id"))}


@router.post("/refinements/{proposal_id}/apply")
async def apply_refinement(request: Request, proposal_id: str) -> dict:
    require_global_permission(request, "admin")
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        raise HTTPException(status_code=404, detail="That suggestion no longer exists. Refresh the page.")
    from maverick import harness_refine
    from maverick.learning_guard import Halted
    actor = _actor(request)
    try:
        out = await run_in_threadpool(_bound(
            request, lambda: harness_refine.apply(proposal_id, applied_by=actor)))
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc
    except harness_refine.RefineError as exc:
        raise _refine_problem(exc) from exc
    return {"proposal": _proposal_view(out)}


@router.post("/refinements/{proposal_id}/revert")
async def revert_refinement(request: Request, proposal_id: str) -> dict:
    require_global_permission(request, "admin")
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        raise HTTPException(status_code=404, detail="That suggestion no longer exists. Refresh the page.")
    from maverick import harness_refine
    actor = _actor(request)
    try:
        undone = await run_in_threadpool(_bound(
            request, lambda: harness_refine.revert(proposal_id, reverted_by=actor)))
    except harness_refine.RefineError as exc:
        raise _refine_problem(exc) from exc
    if not undone:
        raise HTTPException(status_code=409, detail="There is nothing to undo for this change.")
    return {"reverted": True}


# ---- self-harness read-outs ---------------------------------------------

def _known_models() -> list[str]:
    from maverick import self_harness
    models = set(self_harness.list_learned().keys())
    try:
        from maverick.llm import model_for_role
        models.add(model_for_role("orchestrator"))
    except Exception:  # noqa: BLE001 -- no provider configured yet
        pass
    return sorted(m for m in models if m)[:50]


def _model_choice(model_id: str | None) -> str | None:
    """The chosen model, only when it is one the page offered."""
    if not model_id:
        return None
    if model_id not in _known_models():
        raise HTTPException(status_code=422, detail="Choose a model from the list.")
    return model_id


@router.get("/models")
async def models(request: Request) -> dict:
    require_permission(request, "view")
    from maverick import self_harness

    from .plain_labels import model_label
    names = await run_in_threadpool(_known_models)
    return {"enabled": bool(self_harness.enabled()),
            "models": [{"value": m, "label": model_label(m)} for m in names]}


@router.get("/efficacy")
async def efficacy(request: Request, model_id: str = Query(min_length=1, max_length=200)) -> dict:
    require_permission(request, "view")
    from maverick import self_harness
    model = await run_in_threadpool(_model_choice, model_id)
    rows = await run_in_threadpool(self_harness.line_efficacy, model)
    return {"lessons": [
        {"text": _clip(r.get("text")), "department": (r.get("domain") or ""),
         "went_well": int(r.get("success") or 0), "did_not": int(r.get("failure") or 0),
         "rate": r.get("rate"), "recent_rate": r.get("recent_rate")}
        for r in rows[:200]
    ], "truncated": len(rows) > 200}


@router.get("/conflicts")
async def conflicts(request: Request, model_id: str | None = Query(default=None, max_length=200)) -> dict:
    require_permission(request, "view")
    from maverick import self_harness
    model = await run_in_threadpool(_model_choice, model_id)
    # The lexical check only: the meaning-based check calls an AI model and
    # stays a deliberate terminal choice.
    pairs = await run_in_threadpool(self_harness.detect_store_conflicts, model)
    return {"pairs": [{"model": m, "first": _clip(a), "second": _clip(b)}
                      for m, a, b in pairs[:_MAX_ROWS]], "truncated": len(pairs) > _MAX_ROWS}


def _log_rows(limit: int) -> list[dict]:
    from maverick.audit import EventKind, default_audit_log
    rows: list[dict] = []
    for ev in default_audit_log().tail(2000):
        if ev.get("kind") == EventKind.LEARNING_UPDATE and ev.get("agent") == "self_harness":
            rows.append(ev)
    rows = rows[-limit:]
    rows.reverse()
    return [{"when": ev.get("ts") if isinstance(ev.get("ts"), (int, float)) else None,
             "what": _PHASE_LABELS.get(str(ev.get("phase") or "apply"),
                                       str(ev.get("phase") or "").replace("_", " ").capitalize()),
             "model": str(ev.get("model_id") or ""),
             "lesson": "" if ev.get("line") in (None, "*") else _clip(ev.get("line"))}
            for ev in rows]


@router.get("/log")
async def learning_log(request: Request, limit: int = Query(default=30, ge=1, le=_MAX_ROWS)) -> dict:
    require_permission(request, "audit")
    try:
        events = await run_in_threadpool(_log_rows, limit)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The learning activity is not available right now.") from exc
    return {"events": events}


def _preview(model_id: str | None, min_support: int) -> dict:
    from maverick import reflexion, self_harness
    if not self_harness.enabled():
        return {"enabled": False, "weaknesses": [], "scanned": 0, "eligible": 0}
    if not model_id:
        from maverick.llm import model_for_role
        model_id = model_for_role("orchestrator")
    records = [r.to_dict() for r in reflexion.list_recent(limit=500)]
    eligible = self_harness.count_eligible(records, model_id=model_id)
    sigs = self_harness.mine_failures(records, model_id=model_id, min_support=min_support)
    out = []
    for sig in sigs[:50]:
        proposal = self_harness.propose_addendum(sig)
        out.append({"pattern": _clip(sig.signature), "times": int(sig.support),
                    "kind": str(sig.failure_class or "").replace("_", " "),
                    "would_add": _clip(proposal.addendum_line) if proposal else ""})
    return {"enabled": True, "model": model_id, "weaknesses": out,
            "scanned": len(records), "eligible": eligible}


@router.get("/preview")
async def preview(request: Request, model_id: str | None = Query(default=None, max_length=200),
                  min_support: Literal[2, 3, 5] = 3) -> dict:
    require_permission(request, "audit")
    model = await run_in_threadpool(_model_choice, model_id)
    try:
        return await run_in_threadpool(_preview, model, min_support)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The preview is not available right now.") from exc


def _holdout() -> dict:
    from maverick.config import get_self_harness
    configured = str(get_self_harness().get("holdout_ledger") or "").strip()
    if not configured:
        return {"configured": False}
    from pathlib import Path

    from maverick.self_harness_holdout import HoldoutLedgerError, HoldoutQueryLedger
    try:
        status = HoldoutQueryLedger(Path(configured)).verify()
    except (HoldoutLedgerError, OSError, ValueError):
        return {"configured": True, "verified": False}
    return {"configured": True, "verified": True, "events": int(status.ledger_events),
            "checks_used": int(status.queries),
            "technical": {"ledger": str(status.ledger_id),
                          "latest_fingerprint": str(status.ledger_tip_sha256 or "")[:16]}}


@router.get("/holdout")
async def holdout(request: Request) -> dict:
    require_permission(request, "audit")
    try:
        return await run_in_threadpool(_holdout)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The hold-out check is not available right now.") from exc


# ---- changes that spend evaluation budget ----------------------------------

def _report_view(report: Any, retired: int) -> dict:
    return {"model": str(getattr(report, "model_id", "") or ""),
            "patterns_found": int(report.mined), "ideas_tried": int(report.proposed),
            "passed_testing": int(report.validated), "adopted": int(report.promoted),
            "retired": int(retired or 0),
            "paused_for_calibration": bool(report.frozen),
            "promotion_gate_off": not bool(report.gate_enabled),
            "new_lessons": [_clip(ln) for ln in list(report.applied_lines)[:20]],
            "back_on_trial": [_clip(ln) for ln in list(getattr(report, "relapsed", []) or [])[:20]]}


@router.post("/run")
async def run_now(request: Request, body: RunIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import self_harness
    from maverick import self_improvement_runner as runner
    from maverick.learning_guard import Halted, check_learning_halt
    if not self_harness.enabled():
        raise HTTPException(status_code=409, detail=(
            "Self-coaching is off. Turn it on on the Learning page first."))

    def _run() -> list[dict]:
        check_learning_halt("self-harness", "dashboard-start")
        if body.scope == "all":
            results = runner.run_self_harness_all_models()
            return [_report_view(rep, ret) for _m, (rep, ret) in sorted(results.items())][:20]
        report, retired = runner.run_self_harness_cycle()
        return [_report_view(report, retired)]
    _audit(request, "self_harness_run", scope=body.scope)
    try:
        reports = await run_in_threadpool(_run)
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc
    return {"reports": reports}


@router.post("/corpus-quality")
async def corpus_quality(request: Request, body: QualityIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import self_harness
    from maverick import self_improvement_runner as runner
    if not self_harness.settings().get("eval_corpus"):
        return {"configured": False, "cases": []}
    model = await run_in_threadpool(_model_choice, body.model_id)
    _audit(request, "self_harness_corpus_quality", model=model or "")
    try:
        rows = await run_in_threadpool(lambda: runner.run_corpus_quality(key=model, samples=2,
                                                                         raise_errors=True))
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail=(
            "The test cases could not be checked right now. Nothing was changed.")) from exc
    return {"configured": True, "cases": [
        {"task": _clip(str(r.get("goal") or "").splitlines()[0] if r.get("goal") else ""),
         "useful": bool(r.get("discriminative")), "baseline_rate": r.get("baseline_rate"),
         "age_days": r.get("age_days")}
        for r in rows[:200]
    ]}


__all__ = ["router"]
