"""Account-bound in-app pilot planning and observation entry."""
from __future__ import annotations

import hmac
from functools import partial
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config
from maverick.privacy_ops import RecordConflict
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from . import outcome_pilot_sources, outcome_pilots
from .auth import caller_principal, require_global_permission, require_permission
from .draft_api import context_for

router = APIRouter(tags=["outcome-pilots"])


class StrictIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True, allow_inf_nan=False)


class CaseIn(StrictIn):
    kind: Literal["goal", "vendor"]
    reference: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9-]+$")
    label: str = Field(..., min_length=1, max_length=200)


class PlanIn(StrictIn):
    operation_id: str = Field(..., min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(..., min_length=1, max_length=200)
    customer: str = Field(..., min_length=1, max_length=200)
    workflow: str = Field(..., min_length=1, max_length=1000)
    criteria: str = Field(..., min_length=1, max_length=4000)
    cases: list[CaseIn] = Field(..., min_length=4, max_length=100)
    learning_mode: Literal["observational", "exclude_local_learning"] = "observational"
    prior_exposure_statement: str = Field(default="", max_length=2000)


class IndependentReviewIn(StrictIn):
    expected_revision: int = Field(..., ge=1)
    operation_id: str = Field(..., min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    evidence_version: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    decision: Literal["accepted_local_scope", "needs_more_evidence"]
    notes: str = Field(..., min_length=20, max_length=4000)
    confirm: bool


class ObservationIn(StrictIn):
    expected_revision: int = Field(..., ge=1)
    operation_id: str = Field(..., min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    source_version: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    baseline_minutes: float | None = Field(None, ge=0, le=1000000)
    ai_hands_on_minutes: float | None = Field(None, ge=0, le=1000000)
    review_minutes: float | None = Field(None, ge=0, le=1000000)
    rework_minutes: float | None = Field(None, ge=0, le=1000000)
    corrections: int | None = Field(None, ge=0, le=1000000)
    baseline_wall_minutes: float | None = Field(None, ge=0, le=1000000)
    ai_wall_minutes: float | None = Field(None, ge=0, le=1000000)
    baseline_cost: str | None = Field(None, pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
    ai_total_cost: str | None = Field(None, pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
    human_cost: str | None = Field(None, pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
    rework_cost: str | None = Field(None, pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,6})?$")
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    evidence: str = Field(..., min_length=1, max_length=4000)
    note: str = Field(..., min_length=1, max_length=4000)
    criteria_met: bool | None = None

    @model_validator(mode="after")
    def cost_currency(self):
        if any(value is not None for value in (self.baseline_cost, self.ai_total_cost, self.human_cost, self.rework_cost)) and self.currency is None:
            raise ValueError("Currency is required for a reported cost.")
        return self


class PreparedTaskIn(StrictIn):
    operation_id: str = Field(..., min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=4000)
    domain: str = Field(..., min_length=1, max_length=100)


def _context(request, *, mutation=False):
    require_permission(request, "operate")
    if config.config_source_errors(include_tenant=True) or config.get_features().get("outcome_measurement") is not True:
        raise HTTPException(403, "Outcome measurement is disabled in Settings or configuration is unavailable.")
    context = context_for(request)
    header = request.headers.get("X-Lightwork-Pilot-Context")
    if (mutation or header is not None) and not hmac.compare_digest(header or "", context):
        raise HTTPException(409, "The account or workspace changed. Reload the pilot page.")
    return context


async def _call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except HTTPException:
        raise
    except RecordConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, "The pilot or source is unavailable in this account and workspace.") from exc
    except PermissionError as exc:
        raise HTTPException(403, "This source is no longer accessible.") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Pilot records are unavailable. Refresh before retrying; your inputs have been retained.") from exc


def _resolver(request):
    return partial(outcome_pilot_sources.resolve, request)


@router.get("/outcome-pilots")
async def page(request: Request):
    from .app import templates

    context = _context(request)
    return templates.TemplateResponse(request, "outcome_pilots.html", {"pilot_context": context},
                                      headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/outcome-pilots")
async def list_pilots(request: Request, response: Response):
    context = _context(request)
    response.headers["Cache-Control"] = "private, no-store"
    return {**await _call(outcome_pilots.listing, context), "context": context}


@router.post("/api/v1/outcome-pilots")
async def create_pilot(request: Request, response: Response, body: PlanIn):
    context = _context(request, mutation=True)
    response.headers["Cache-Control"] = "private, no-store"
    record = await _call(outcome_pilots.create, body.model_dump(), context=context,
        actor=caller_principal(request) or "local:dashboard", resolve=_resolver(request))
    return {"id": record["id"], "revision": record["revision"], "context": context}


@router.get("/api/v1/outcome-pilots/sources")
async def pilot_sources(request: Request, response: Response):
    context = _context(request)
    response.headers["Cache-Control"] = "private, no-store"
    return {**await _call(outcome_pilot_sources.choices, request), "context": context}


@router.get("/api/v1/outcome-pilots/{pilot_id}")
async def pilot_report(request: Request, response: Response, pilot_id: str):
    from .outcome_pilot_costs_api import report_for

    context = _context(request)
    response.headers["Cache-Control"] = "private, no-store"
    record = await _call(outcome_pilots.get, pilot_id, context)
    return {**await _call(outcome_pilots.report, record, resolve=_resolver(request)),
            "provider_costs": await report_for(request, record), "context": context}


@router.post("/api/v1/outcome-pilots/prepared-tasks")
async def prepare_task(request: Request, response: Response, body: PreparedTaskIn):
    from .app import check_goal_rate_limit

    context = _context(request, mutation=True)
    check_goal_rate_limit(request)
    response.headers["Cache-Control"] = "private, no-store"
    return {**await _call(outcome_pilot_sources.prepare_task, request, body.model_dump(), context), "context": context}


@router.put("/api/v1/outcome-pilots/{pilot_id}/cases/{case_id}")
async def observation(request: Request, response: Response, pilot_id: str, case_id: str, body: ObservationIn):
    context = _context(request, mutation=True)
    response.headers["Cache-Control"] = "private, no-store"
    record = await _call(outcome_pilots.observe, pilot_id, case_id, body.model_dump(), context=context,
        actor=caller_principal(request) or "local:dashboard", resolve=_resolver(request))
    return {"id": record["id"], "revision": record["revision"], "context": context}


@router.get("/api/v1/outcome-pilots/independent-review/{pilot_id}")
async def independent_packet(request: Request, response: Response, pilot_id: str):
    context = _context(request)
    require_global_permission(request, "admin")
    actor = caller_principal(request) or ""
    record = await _call(outcome_pilots.STORE.get, pilot_id)
    if record is None or not actor.startswith("user:") or actor == record["actor"]:
        raise HTTPException(403, "A different named global administrator must review this pilot.")
    for case in record["cases"]:
        await _call(outcome_pilot_sources.resolve, request, case["kind"], case["reference"])
    response.headers["Cache-Control"] = "private, no-store"
    return {**await _call(outcome_pilots.report, record, resolve=_resolver(request)), "context": context}


@router.post("/api/v1/outcome-pilots/independent-review/{pilot_id}")
async def independent_decision(request: Request, response: Response, pilot_id: str, body: IndependentReviewIn):
    context = _context(request, mutation=True)
    require_global_permission(request, "admin")
    record = await _call(outcome_pilots.independent_review, pilot_id, body.model_dump(),
                         actor=caller_principal(request) or "", resolve=_resolver(request))
    response.headers["Cache-Control"] = "private, no-store"
    return {"id": record["id"], "revision": record["revision"], "context": context}
