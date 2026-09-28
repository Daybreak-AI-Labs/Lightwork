"""Get two viewpoints: a governed two-sided debate on a question.

People reach ``maverick debate`` here: one side argues for, one argues against,
and an impartial judge weighs them. It is admitted exactly like a task start --
emergency stop, the person's quota and spending limits, the workspace's
allowance -- and runs on a budget built from the configured caps, never above
``MAX_DOLLARS``. Every model call still goes through ``Budget.reserve`` and the
provider, organization and spending-limit admission inside ``LLM``; the spend
is settled to the usage ledger afterwards the way a task's is. The model comes
from the configured ``analyst`` role, never a fixed name. Nothing else is
created or changed; each run is recorded in the activity log.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ._shared import require_provider_or_400
from .auth import caller_principal, execution_user_id_from_request, require_permission

log = logging.getLogger(__name__)
router = APIRouter(tags=["viewpoints"])

MAX_DOLLARS = 1.0
MAX_TURN_CHARS = 4000
SIDES = (("Proponent", "for", "the answer is YES / the proposal is sound"),
         ("Skeptic", "against", "the answer is NO / the proposal is flawed"))


class ViewpointsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=3, max_length=2000)
    rounds: int = Field(default=2, ge=1, le=3)


class _Refused(Exception):
    """Not admitted; the message is written for people."""


def _audit(kind: str, actor: str, **data) -> None:
    from maverick.audit import audit_event
    audit_event(kind, agent="dashboard", actor=actor, **data)


def _admit(principal: str):
    """The checks a task start makes, then a capped budget. Returns the budget."""
    from maverick import killswitch, quotas, spending_limits
    from maverick.budget import budget_from_config, resolve_budget_caps
    from maverick.paths import current_tenant_id
    from maverick.tenant.registry import assert_tenant_active

    try:
        killswitch.check()
    except killswitch.Halted as exc:
        raise _Refused("Lightwork is paused by an emergency stop. An administrator can resume it.") from exc
    try:
        if quotas.quotas_enforced() and quotas.over_quota(principal):
            raise _Refused("You have used your spending allowance for today. Try again tomorrow or ask an administrator.")
        reason = spending_limits.start_refusal(principal)
        assert_tenant_active(current_tenant_id())
    except _Refused:
        raise
    except Exception as exc:
        raise _Refused("Spending limits can't be checked right now, so nothing was started. Try again in a moment.") from exc
    if reason:
        raise _Refused(reason)
    try:
        configured = resolve_budget_caps().get("max_dollars")
        cap = MAX_DOLLARS if configured is None else min(MAX_DOLLARS, float(configured))
        budget = budget_from_config(max_dollars=cap)
    except ValueError as exc:
        raise _Refused("The spending caps for AI work can't be read right now, so nothing was started.") from exc
    if getattr(budget, "_tenant_refusal", None):
        raise _Refused("This workspace has used its spending allowance, so nothing was started. An administrator can raise it.")
    return budget


def _plain_turn(text: str) -> str:
    text = (text or "").strip()
    if not text or text.startswith("(turn failed"):
        return "This side could not answer this time."
    return text[:MAX_TURN_CHARS]


def _run(question: str, rounds: int, principal: str, actor: str) -> dict:
    from maverick.budget import BudgetExceeded
    from maverick.debate import DebateParticipant, run_debate
    from maverick.llm import LLM, model_for_role
    from maverick.orchestrator import _QuotaUsageSettlement
    from maverick.spending_limits import SpendingLimitReached
    from maverick.tenant.provider_admission import run_scope

    budget = _admit(principal)
    settlement = _QuotaUsageSettlement(principal=principal, budget=budget, domain=None)
    try:
        # Recorded before any spend; a deployment that cannot record refuses.
        _audit("viewpoints_run", actor, phase="start", rounds=rounds,
               question_chars=len(question), max_dollars=budget.max_dollars)
    except Exception as exc:
        settlement.record()
        raise _Refused("The activity log couldn't record this request, so nothing was started.") from exc
    llm = LLM(model=model_for_role("analyst"))
    participants = [DebateParticipant(name=name, persona=persona, llm_complete=llm.complete)
                    for name, _, persona in SIDES]
    outcome = "failed"
    try:
        with run_scope(budget, principal):
            result = run_debate(question, participants, judge_complete=llm.complete,
                                rounds=rounds, budget=budget)
        outcome = "finished"
    except SpendingLimitReached as exc:
        outcome = "stopped"
        raise _Refused(str(exc)) from exc
    except BudgetExceeded as exc:
        outcome = "stopped"
        raise _Refused(f"This stopped when it reached the limit for one request (at most ${budget.max_dollars:,.2f}). "
                       "Try a shorter question or fewer exchanges.") from exc
    finally:
        settlement.record()
        try:
            _audit("viewpoints_run", actor, phase="end", outcome=outcome,
                   dollars=round(float(budget.dollars), 4))
        except Exception:  # spend already happened; never hide the result
            log.warning("viewpoints: completion audit row not written", exc_info=True)
    side = {name: key for name, key, _ in SIDES}
    reason = (result.judge_reason or "").strip()
    if not reason or reason == "judge JSON parse failed":
        reason = "The judge's answer couldn't be read, so neither side is treated as stronger."
    return {
        "ran": True,
        "rounds": result.rounds_completed,
        "turns": [{"side": side.get(turn.speaker, "for"), "text": _plain_turn(turn.text)}
                  for turn in result.transcript[: rounds * len(SIDES)]],
        "verdict": side.get(result.winner, "draw"),
        "reason": reason[:1000],
        "key_point": (result.key_argument or "").strip()[:1000],
        "cost": round(float(result.total_dollars), 4),
    }


@router.post("/api/v1/viewpoints")
async def get_viewpoints(request: Request, body: ViewpointsIn) -> dict:
    """Run one two-sided debate for the signed-in person and return it."""
    from maverick.orchestrator import _execution_principal

    from .app import _require_same_origin, check_goal_rate_limit

    require_permission(request, "operate")
    _require_same_origin(request)
    check_goal_rate_limit(request, source="viewpoints")
    question = " ".join(body.question.split())
    if len(question) < 3:
        raise HTTPException(422, "Type a question or decision to weigh up.")
    try:
        require_provider_or_400(role="analyst")
    except HTTPException as exc:
        raise HTTPException(400, "AI isn't connected yet. Finish connecting AI in setup, then try again.") from exc
    principal = _execution_principal(user_id=execution_user_id_from_request(request), capability=None)
    actor = caller_principal(request) or principal
    try:
        return await run_in_threadpool(_run, question, body.rounds, principal, actor)
    except _Refused as exc:
        return {"ran": False, "message": str(exc)}
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("viewpoints: debate failed", exc_info=True)
        raise HTTPException(503, "The viewpoints couldn't be finished. Try again in a moment.") from exc
