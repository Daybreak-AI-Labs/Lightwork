"""Buttons for the Learned and Learning pages: the learning commands that were
CLI-only (``self-harness forget/retire/canary``, ``dream``, ``dream --rollback``,
``hindsight``, ``prove-learning``).

Every change is a deployment-wide learning action, so it needs a global
administrator -- the same rung as the Learning page's switches and the corpus
review. Each call goes through the kernel function the CLI uses, so the
kernel's own guards (the learning stop, audit rows, snapshots) stay in force.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission

router = APIRouter(tags=["learning-actions"])
_SNAPSHOT = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")


class GuidanceLineIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)
    line: str = Field(min_length=1, max_length=4000)
    domain: str | None = Field(default=None, max_length=200)


class ModelIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)


class RetireIn(BaseModel):
    older_than_days: Literal[30, 90, 180, 365]


class DreamIn(BaseModel):
    preview: bool = True


class RollbackIn(BaseModel):
    snapshot: str = Field(min_length=1, max_length=80)


class HindsightIn(BaseModel):
    snapshot: str | None = Field(default=None, max_length=80)
    all_tasks: bool = False
    record: bool = False


class ProveIn(BaseModel):
    baseline: list[float] = Field(min_length=1, max_length=10000)
    treatment: list[float] = Field(min_length=1, max_length=10000)


def _world():
    from ._shared import _world as shared
    try:
        return shared()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="Lightwork's records are not available right now.") from exc


def snapshot_label(name: str) -> str:
    """``20260927T101500Z`` -> ``27 Sep 2026, 10:15 UTC``; anything else as-is."""
    digits = re.sub(r"\D", "", name)
    for fmt, size in (("%Y%m%d%H%M%S", 14), ("%Y%m%d%H%M", 12)):
        if len(digits) >= size:
            try:
                when = _dt.datetime.strptime(digits[:size], fmt)
                return when.strftime("%d %b %Y, %H:%M UTC").lstrip("0")
            except ValueError:
                continue
    return name


def _record(request: Request, action: str, **detail) -> None:
    """One learning activity-record row naming the administrator who acted.

    The kernel's own learning rows do not know who pressed the button; this
    one does. Raises HTTP 503 when the activity record refuses the row (the
    change has already been made, so the page says so)."""
    from maverick.audit import AuditRefused, EventKind, audit_event

    from . import auth
    try:
        audit_event(EventKind.LEARNING_UPDATE, agent="dashboard", _global=True,
                    action=action, actor=auth.caller_principal(request) or "local administrator",
                    **detail)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail=(
            "The change was made, but it could not be added to the activity record.")) from exc


def _halted(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=(
        "Learning is stopped for this installation right now, so nothing was changed."))


@router.post("/learned/guidance/remove")
async def remove_guidance(request: Request, body: GuidanceLineIn) -> dict:
    require_global_permission(request, "admin")
    from maverick.self_harness import forget_addendum
    removed = await run_in_threadpool(forget_addendum, body.model_id, line=body.line,
                                      domain=(body.domain or None))
    if not removed:
        raise HTTPException(status_code=404, detail="That guidance was already removed.")
    return {"removed": True}


@router.post("/learned/guidance/retire")
async def retire_guidance(request: Request, body: RetireIn) -> dict:
    require_global_permission(request, "admin")
    from maverick.self_harness import retire_stale
    n = await run_in_threadpool(lambda: retire_stale(older_than_days=float(body.older_than_days)))
    return {"retired": int(n)}


@router.post("/learned/guidance/probation-review")
async def review_probation(request: Request, body: ModelIn) -> dict:
    require_global_permission(request, "admin")
    from maverick.self_harness import review_canaries
    res = await run_in_threadpool(review_canaries, body.model_id)
    return {"kept": len(res.get("graduated") or []), "removed": len(res.get("demoted") or [])}


@router.get("/learning/snapshots")
async def snapshots(request: Request) -> dict:
    require_global_permission(request, "admin")
    from maverick import dreaming
    names = await run_in_threadpool(dreaming.list_snapshots)
    return {"snapshots": [{"value": n, "label": snapshot_label(n)} for n in reversed(names)]}


def _dream(preview: bool) -> dict:
    from maverick import dreaming
    from maverick.learning_guard import check_learning_halt
    world = _world()
    if preview:
        report = dreaming.dream_cycle_dry(world)
    else:
        check_learning_halt("dreaming", "dashboard_start")
        cfg = dreaming.settings()
        if cfg.get("snapshots", True):
            dreaming.snapshot_learning_state(keep_last=int(cfg.get("snapshot_keep_last", 5)))
        report = dreaming.dream_cycle(world)
    return {"preview": preview, "tasks_reviewed": report.goals_replayed,
            "failures_reviewed": report.failures_replayed,
            "lessons_written": report.insights_written,
            "skills_learned": report.skills_distilled,
            "skills_retired": report.skills_retired + report.skills_quarantined,
            "notes_tidied": report.reflexions_pruned + report.facts_pruned
            + report.insights_expired + report.insights_retired,
            "paused": bool(report.learning_frozen)}


@router.post("/learning/dream")
async def run_dream(request: Request, body: DreamIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import dreaming
    from maverick.learning_guard import Halted
    if not body.preview and not dreaming.enabled():
        raise HTTPException(status_code=409, detail=(
            "Turn on Dreaming under Learning systems first."))
    try:
        result = await run_in_threadpool(_dream, body.preview)
    except Halted as exc:
        raise _halted(exc) from exc
    if not body.preview:
        await run_in_threadpool(
            lambda: _record(request, "dream_run", **{k: v for k, v in result.items()
                                                     if k != "preview"}))
    return result


@router.post("/learning/rollback")
async def rollback(request: Request, body: RollbackIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import dreaming
    if body.snapshot != "latest" and not _SNAPSHOT.fullmatch(body.snapshot):
        raise HTTPException(status_code=422, detail="Choose a saved point from the list.")
    names = await run_in_threadpool(dreaming.list_snapshots)
    if body.snapshot != "latest" and body.snapshot not in names:
        raise HTTPException(status_code=404, detail="That saved point no longer exists.")
    try:
        restored = await run_in_threadpool(dreaming.rollback_learning_state, body.snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="That saved point no longer exists.") from exc
    except Exception as exc:  # noqa: BLE001 -- earlier stores may already have been restored
        raise HTTPException(status_code=503, detail=(
            "Lightwork could not fully restore that saved point. Some learned information "
            "may have changed. Review the Learned and Learning pages before trying again.")) from exc
    if not restored:
        raise HTTPException(status_code=404, detail="There is no saved point to go back to yet.")
    await run_in_threadpool(lambda: _record(request, "rollback", snapshot=body.snapshot,
                                            restored=[str(n) for n in restored]))
    return {"restored": len(restored)}


@router.post("/learning/hindsight")
async def hindsight(request: Request, body: HindsightIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import dreaming
    from maverick import hindsight as _h
    names = await run_in_threadpool(dreaming.list_snapshots)
    if not names:
        raise HTTPException(status_code=409, detail=(
            "There is no saved point to compare with yet. Run a learning cycle first."))
    chosen = body.snapshot or names[-1]
    if chosen not in names:
        raise HTTPException(status_code=404, detail="That saved point no longer exists.")
    world = _world()

    def _run():
        report = _h.replay(world, before=dreaming.snapshots_dir() / chosen, after=None,
                           limit=100, status=None if body.all_tasks else "blocked")
        if body.record:
            _h.write_ledger(report, before_label=chosen)
        return report
    report = await run_in_threadpool(_run)
    return {"compared_with": snapshot_label(chosen), "tasks": report.n_goals,
            "covered_before": report.covered_before, "covered_now": report.covered_now,
            "gained": list(report.gained)[:50], "lost": list(report.regressed)[:50],
            "recorded": bool(body.record)}


@router.post("/learning/prove")
async def prove(request: Request, body: ProveIn) -> dict:
    require_global_permission(request, "admin")
    from maverick.learning_proof import paired_lift
    if len(body.baseline) != len(body.treatment):
        raise HTTPException(status_code=422, detail=(
            "Both result sets must cover the same tasks, in the same order."))
    if any(not 0.0 <= x <= 1.0 for x in (*body.baseline, *body.treatment)):
        raise HTTPException(status_code=422, detail="Scores must be between 0 and 1.")
    try:
        r = paired_lift(body.baseline, body.treatment)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Those results cannot be compared.") from exc
    verdict = ("improved" if r.improved else
               "worse" if (r.significant and r.delta < 0) else "no_clear_change")
    return {"verdict": verdict, "tasks": r.n, "before": round(r.baseline_mean, 4),
            "after": round(r.treatment_mean, 4), "change": round(r.delta, 4),
            "better": r.wins, "worse": r.losses, "same": r.ties}


__all__ = ["router", "snapshot_label"]
