"""Buttons for the Safety monitoring page (/immune): the ``maverick immune``
commands that were CLI-only.

Reads need the ``audit`` permission (like the page); anything that plants a
test approval, starts a replay, deletes a recording, signs a record or changes
a setting needs ``admin``. Planted flaws are never returned: whoever plants a
reviewer test can still be measured fairly. Replays and awareness checks run a
real governed goal, so they need an AI provider and run in the background.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_global_permission, require_permission

log = logging.getLogger(__name__)
_MAX_DOC_ITEMS = 5000


class ImmuneSettingsIn(BaseModel):
    canaries: bool | None = None
    twin_record: bool | None = None


class CanaryIn(BaseModel):
    decision_class: str | None = Field(default=None, max_length=200)
    count: int = Field(default=1, ge=1, le=10)


class DrillIn(BaseModel):
    person: str = Field(min_length=1, max_length=300)
    decision_class: str = Field(min_length=1, max_length=200)
    passed: bool


class AwarenessIn(BaseModel):
    goal_ids: list[int] = Field(min_length=1, max_length=10)


_MAX_RULES = 2000
_MAX_FINDINGS = 200


class RulesIn(BaseModel):
    rules: list[dict] = Field(max_length=_MAX_RULES)


class DossierIn(BaseModel):
    dossier: dict


class SealedIn(BaseModel):
    sealed: dict


class ResearchIn(BaseModel):
    dossier: dict
    runs: list[dict] = Field(min_length=1, max_length=50)
    elements: list[str] = Field(default_factory=list, max_length=100)


def _world():
    from ._shared import _world as shared
    try:
        return shared()
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="Lightwork's records are not available right now.") from exc


def _audit(request: Request, action: str, **payload: Any) -> None:
    from maverick.audit import AuditRefused, audit_event
    try:
        audit_event("immune_action", agent="dashboard", action=action,
                    actor=caller_principal(request) or "local", **payload)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail="The activity log refused this change.") from exc


def _ai_ready() -> None:
    try:
        from maverick.config import load_config
        from maverick.operator_preflight import _routed_configuration_missing
        missing = {p: f for p, f in _routed_configuration_missing(load_config()).items() if f}
    except Exception:  # noqa: BLE001
        missing = {"selected": ("configuration",)}
    if missing:
        raise HTTPException(status_code=503, detail="Set up an AI provider in Settings first.")


def people_choices() -> list[dict]:
    """Everyone the roster knows, as ``{value, label}`` choices."""
    try:
        from . import invites, rbac
        from .admin_pages import _invite_labels, _people, _signed_in_principals
        people = _people(rbac.list_users(), _invite_labels(invites.list_invites()),
                         _signed_in_principals())
    except Exception:  # noqa: BLE001 -- an empty picker, not a broken page
        people = []
    return [{"value": p["principal"], "label": p["label"]} for p in people]


def _recorded_goals(world) -> list[dict]:
    from maverick.immune import twin
    folder = twin.recording_path(0).parent
    ids: list[int] = []
    if folder.exists():
        for f in folder.glob("*.jsonl"):
            if re.fullmatch(r"\d+", f.stem):
                ids.append(int(f.stem))
    out = []
    for gid in sorted(ids, reverse=True)[:200]:
        goal = world.get_goal(gid) if world is not None else None
        out.append({"id": gid, "title": (goal.title if goal else f"Task {gid}")[:200],
                    "status": goal.status if goal else ""})
    return out


def _decision_classes(world) -> list[str]:
    from maverick.immune import oversight
    planted = oversight.canary_ids()
    seen: list[str] = []
    for a in world.list_approvals(limit=500):
        if a.id not in planted and a.action and a.action not in seen:
            seen.append(a.action)
    return seen[:100]


def _plain_problem(text: str) -> str:
    text = re.sub(r"[_]+", " ", str(text))
    return text[:1].upper() + text[1:300]


def _replay_in_background(goal_id: int) -> None:
    from maverick.immune import twin
    from maverick.runner import run_goal_in_thread
    from maverick.world_model import close_world_if_owned, open_world
    world = open_world()
    try:
        twin.replay_goal(world, goal_id, lambda gid: run_goal_in_thread(
            gid, **twin.run_kwargs(world.get_goal(gid))))
    except Exception as exc:  # noqa: BLE001 -- the replay ledger carries outcomes
        log.warning("practice re-run of goal %s did not finish (%s)", goal_id, type(exc).__name__)
    finally:
        close_world_if_owned(world)


def _awareness_in_background(goal_ids: list[int]) -> None:
    from maverick.immune import twin
    from maverick.runner import run_goal_in_thread
    from maverick.world_model import close_world_if_owned, open_world
    world = open_world()
    try:
        twin.awareness_probe(world, goal_ids, lambda gid: run_goal_in_thread(
            gid, **twin.run_kwargs(world.get_goal(gid))))
    except Exception as exc:  # noqa: BLE001
        log.warning("awareness check did not finish (%s)", type(exc).__name__)
    finally:
        close_world_if_owned(world)


router = APIRouter(tags=["immune-actions"])

@router.get("/immune/choices")
async def choices(request: Request) -> dict:
    require_permission(request, "audit")
    from maverick.config import get_immune
    world = _world()

    def _read() -> dict:
        settings = get_immune()
        return {"canaries": settings["canaries"], "twin_record": settings["twin_record"],
                "decision_classes": _decision_classes(world),
                "recorded_goals": _recorded_goals(world),
                "people": people_choices()}
    return await run_in_threadpool(_read)

@router.post("/immune/settings")
async def update_settings(request: Request, body: ImmuneSettingsIn) -> dict:
    require_global_permission(request, "admin")
    if body.canaries is None and body.twin_record is None:
        raise HTTPException(status_code=422, detail="Choose a setting to change.")
    from maverick import config

    from . import settings_store
    await run_in_threadpool(settings_store.set_immune_settings,
                            canaries=body.canaries, twin_record=body.twin_record)
    config.reset_config_cache()
    _audit(request, "settings", canaries=body.canaries, twin_record=body.twin_record)
    return config.get_immune()

@router.post("/immune/canaries")
async def plant_canaries(request: Request, body: CanaryIn) -> dict:
    require_permission(request, "admin")
    from maverick.immune import oversight
    if not oversight.canaries_enabled():
        raise HTTPException(status_code=409, detail="Reviewer tests are turned off. Turn them on first.")
    world = _world()
    planted = 0
    try:
        for _ in range(body.count):
            await run_in_threadpool(oversight.generate_canary, world,
                                    decision_class=body.decision_class or None)
            planted += 1
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail="Reviewer tests are turned off. Turn them on first.") from exc
    except LookupError as exc:
        if not planted:
            raise HTTPException(status_code=409, detail=(
                "There are no recent approvals of that kind to base a test on yet.")) from exc
    _audit(request, "plant_reviewer_tests", count=planted)
    # The planted flaw and the approval it hides in stay private.
    return {"planted": planted}

@router.post("/immune/drills")
async def record_drill(request: Request, body: DrillIn) -> dict:
    require_permission(request, "admin")
    from maverick.immune import oversight
    await run_in_threadpool(oversight.record_drill, body.person.strip(),
                            body.decision_class.strip(), body.passed)
    _audit(request, "record_drill", passed=body.passed)
    return {"recorded": True}

@router.post("/immune/twin/{goal_id}/replay", status_code=202)
def replay(request: Request, goal_id: int, bg: BackgroundTasks) -> dict:
    require_permission(request, "admin")
    from maverick.immune import twin
    if not twin.recording_path(goal_id).exists():
        raise HTTPException(status_code=404, detail="That task has no recording to re-run.")
    world = _world()
    goal = world.get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="That task no longer exists.")
    try:
        twin.run_kwargs(goal)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=(
            "This task ran as an automated service, so it cannot be re-run as a person.")) from exc
    _ai_ready()
    _audit(request, "practice_rerun", goal_id=goal_id)
    bg.add_task(_replay_in_background, goal_id)
    return {"started": True}

@router.delete("/immune/twin/{goal_id}")
async def forget(request: Request, goal_id: int) -> dict:
    require_permission(request, "admin")
    from maverick.immune import twin
    deleted = await run_in_threadpool(twin.forget, goal_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="That recording was already deleted.")
    _audit(request, "delete_recording", goal_id=goal_id)
    return {"deleted": True}

@router.post("/immune/awareness", status_code=202)
async def awareness(request: Request, body: AwarenessIn, bg: BackgroundTasks) -> dict:
    require_permission(request, "admin")
    from maverick.immune import twin
    ids = sorted(set(body.goal_ids))
    missing = [g for g in ids if not twin.recording_path(g).exists()]
    if missing:
        raise HTTPException(status_code=404, detail="Some chosen tasks have no recording.")
    _ai_ready()
    _audit(request, "awareness_check", goals=len(ids))
    bg.add_task(_awareness_in_background, ids)
    return {"started": True, "runs": 2 * len(ids)}

@router.post("/immune/rules/check")
async def check_rules(request: Request, body: RulesIn) -> dict:
    require_permission(request, "audit")
    from maverick.immune import policy
    try:
        # Off the event loop: a large rule set is compared pair by pair.
        found = await run_in_threadpool(policy.contradictions, body.rules, limit=_MAX_FINDINGS)
    except (KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=422, detail="That file is not a rule set Lightwork can read.") from exc
    return {"rules": len(body.rules), "contradictions": [
        {"forbidding": str(f), "requiring": str(r),
         "when": ", ".join(f"{_plain_problem(k)} is {v}" for k, v in sorted(w.items()))}
        for f, r, w in found]}

@router.post("/immune/title/check")
async def title_check(request: Request, body: DossierIn) -> dict:
    require_permission(request, "audit")
    from maverick.immune import title
    try:
        problems = await run_in_threadpool(title.problems, body.dossier)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="That file is not a discovery record Lightwork can read.") from exc
    return {"problems": [_plain_problem(p) for p in problems[:200]]}

@router.post("/immune/title/seal")
async def title_seal(request: Request, body: DossierIn) -> dict:
    require_permission(request, "admin")
    from maverick.immune import title
    try:
        sealed = await run_in_threadpool(title.seal, body.dossier)
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="That file is not a discovery record Lightwork can sign.") from exc
    except Exception as exc:  # noqa: BLE001 -- signing identity unavailable
        raise HTTPException(status_code=503, detail="Lightwork's signing key is not available right now.") from exc
    _audit(request, "seal_discovery_record")
    return {"sealed": sealed}

@router.post("/immune/title/verify")
async def title_verify(request: Request, body: SealedIn) -> dict:
    require_permission(request, "audit")
    from maverick.audit.signing import trusted_audit_public_keys
    from maverick.immune import title
    doc = body.sealed
    try:
        key = trusted_audit_public_keys().get(doc["signature"]["key_id"])
        ok = bool(key) and title.verify(doc, key)
    except (KeyError, TypeError, AttributeError, ValueError):
        ok = False
    return {"verified": bool(ok)}

@router.post("/immune/title/research")
async def title_research(request: Request, body: ResearchIn) -> dict:
    require_permission(request, "admin")
    from maverick.immune import title
    try:
        merged = await run_in_threadpool(title.add_research_runs, body.dossier, body.runs,
                                         [e.strip() for e in body.elements if e.strip()])
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=(
            "One of the files is not a research run record Lightwork can use.")) from exc
    added = len(merged.get("gates", [])) - len(body.dossier.get("gates", []) or [])
    _audit(request, "add_research_to_discovery_record", runs=len(body.runs))
    return {"dossier": merged, "added": max(0, added)}




__all__ = ["people_choices", "router"]
