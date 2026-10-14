"""Shared admission for paid AI workflow-authoring requests.

Workflow drafts are not saved or published by these calls, but each one still
spends provider money. Keep their start checks, hard cap, current dispatch
checks, quota settlement, and activity record aligned with other in-product AI
operations.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from maverick.config import DEFAULT_FLOW_AUTHORING_MAX_DOLLARS

log = logging.getLogger(__name__)

MAX_DOLLARS = DEFAULT_FLOW_AUTHORING_MAX_DOLLARS


class WorkflowAuthoringRefused(RuntimeError):
    """The request could not safely be admitted or continued."""


def _dispatch_guard(principal: str, budget, refusal: dict[str, str]) -> None:
    """Recheck mutable spend authority immediately before each provider send."""
    from maverick import killswitch, quotas, spending_limits
    from maverick.budget import BudgetExceeded
    from maverick.paths import current_tenant_id
    from maverick.tenant.registry import assert_tenant_active

    try:
        killswitch.check()
    except killswitch.Halted:
        refusal["message"] = "Lightwork is paused by an emergency stop. An administrator can resume it."
        raise
    try:
        if quotas.quotas_enforced() and quotas.over_quota(principal):
            refusal["message"] = "You have used your spending allowance for today. Try again tomorrow or ask an administrator."
            raise RuntimeError("quota reached")
        reason = spending_limits.start_refusal(principal)
        if reason:
            refusal["message"] = str(reason)
            raise RuntimeError("spending limit reached")
        assert_tenant_active(current_tenant_id())
        budget.check()
    except BudgetExceeded:
        refusal["message"] = f"This stopped at the limit for one request (at most ${budget.max_dollars:,.2f})."
        raise
    except Exception:
        if not refusal.get("message"):
            refusal["message"] = "Lightwork could not confirm the current spending and workspace limits, so no AI request was sent."
        raise


def run_authoring_ai(
    principal: str,
    actor: str,
    action: str,
    work: Callable[[Callable[..., Any], Any], Any],
) -> Any:
    """Run one workflow-authoring completion under normal product admission.

    ``work`` receives a guarded, role-resolved completion function and the
    admitted budget. It must make at most one model call. A failed admission or
    dispatch is returned as a plain product refusal; provider failures remain
    exceptions for the API's existing safe error handling.
    """
    from maverick import killswitch
    from maverick.audit import audit_event
    from maverick.budget import BudgetExceeded
    from maverick.llm import LLM, ProviderDispatchDenied, model_for_role
    from maverick.orchestrator import _QuotaUsageSettlement
    from maverick.spending_limits import SpendingLimitReached
    from maverick.tenant.provider_admission import run_scope

    from .viewpoints_api import _admit, _Refused

    try:
        from maverick.config import get_flows

        max_dollars = float(get_flows().get("authoring_max_dollars", MAX_DOLLARS))
        if not math.isfinite(max_dollars) or max_dollars < 0:
            raise ValueError("invalid workflow authoring limit")
        if max_dollars == 0:
            raise WorkflowAuthoringRefused(
                "AI workflow writing is paused. An administrator can raise its limit in Settings."
            )
        budget = _admit(principal, max_dollars)
    except WorkflowAuthoringRefused:
        raise
    except _Refused as exc:
        raise WorkflowAuthoringRefused(str(exc)) from exc
    except Exception as exc:
        raise WorkflowAuthoringRefused(
            "The workflow writing limit could not be checked, so no AI request was sent."
        ) from exc

    settlement = _QuotaUsageSettlement(principal=principal, budget=budget, domain=None)
    refusal: dict[str, str] = {}
    llm = LLM(model=model_for_role("orchestrator"))
    completion_started = False

    def complete(*args, **kwargs):
        nonlocal completion_started
        if completion_started:
            raise WorkflowAuthoringRefused(
                "This workflow-writing request has reached its one AI step. Review the draft and start a new request for another change."
            )
        # Mark before dispatch so a timeout or provider error cannot trigger a
        # second paid request from a retrying caller.
        completion_started = True
        kwargs.update(
            budget=budget,
            dispatch_guard=lambda: _dispatch_guard(principal, budget, refusal),
            max_physical_attempts=1,
            _no_failover=True,
        )
        return llm.complete(*args, **kwargs)

    outcome = "failed"
    try:
        try:
            audit_event(
                "workflow_authoring_ai", agent="dashboard", actor=actor,
                phase="start", action=action, max_dollars=budget.max_dollars,
            )
        except Exception as exc:
            raise WorkflowAuthoringRefused(
                "The activity log could not record this request, so no AI work was started."
            ) from exc
        with run_scope(budget, principal):
            result = work(complete, budget)
        outcome = "finished"
        return result
    except ProviderDispatchDenied as exc:
        outcome = "stopped"
        raise WorkflowAuthoringRefused(
            refusal.get("message")
            or "Lightwork could not confirm current permission to send this AI request. Nothing was changed."
        ) from exc
    except (SpendingLimitReached, BudgetExceeded, killswitch.Halted) as exc:
        outcome = "stopped"
        message = refusal.get("message")
        if not message:
            if isinstance(exc, BudgetExceeded):
                message = f"This stopped at the limit for one request (at most ${budget.max_dollars:,.2f})."
            elif isinstance(exc, SpendingLimitReached):
                message = str(exc)
            else:
                message = "Lightwork is paused by an emergency stop. An administrator can resume it."
        raise WorkflowAuthoringRefused(message) from exc
    finally:
        settlement.record()
        try:
            audit_event(
                "workflow_authoring_ai", agent="dashboard", actor=actor,
                phase="end", action=action, outcome=outcome,
                dollars=round(float(budget.dollars), 4),
            )
        except Exception:
            # Provider work may already have happened; its response must not be
            # hidden by an audit-view failure. The missing completion record is
            # visible to the operator through the existing activity record.
            log.warning("workflow authoring completion audit row was not written", exc_info=True)
