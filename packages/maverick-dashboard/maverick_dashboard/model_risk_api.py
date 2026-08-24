"""Operator API for governed Model Risk & AI Assurance."""
from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from . import model_risk_schemas as schemas
from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/assurance", tags=["model-risk-assurance"])
_GATEWAY_ACTION_LIMITS = {"delivery": 120, "packet_issuance": 12}
_GATEWAY_ACTION_BUCKET_CAP = 10_000
_gateway_action_times: dict[tuple[str, str, str], deque[float]] = {}
_gateway_action_lock = threading.Lock()


def _actor(request: Request) -> str:
    from .api import _request_actor

    return _request_actor(request)


def _require_active_tenant() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using tenant-scoped assurance records",
        )
    return tenant


def _limit_gateway_action(request: Request, action: str) -> None:
    from maverick.paths import current_tenant_id_strict

    actor = _actor(request)
    # The gateway operation itself enforces an explicit tenant.  Keep the
    # limiter safe for disabled/fake modules and return the domain's actionable
    # tenant error instead of pre-empting it here.
    tenant = current_tenant_id_strict() or "<unbound>"
    now = time.monotonic()
    limit = _GATEWAY_ACTION_LIMITS[action]
    key = (tenant, actor, action)
    with _gateway_action_lock:
        if (
            key not in _gateway_action_times
            and len(_gateway_action_times) >= _GATEWAY_ACTION_BUCKET_CAP
        ):
            for candidate, candidate_window in tuple(
                _gateway_action_times.items()
            ):
                while candidate_window and now - candidate_window[0] >= 60:
                    candidate_window.popleft()
                if not candidate_window:
                    _gateway_action_times.pop(candidate, None)
            if len(_gateway_action_times) >= _GATEWAY_ACTION_BUCKET_CAP:
                raise HTTPException(
                    status_code=429,
                    detail="AI evidence gateway rate-limit capacity reached",
                    headers={"Retry-After": "60"},
                )
        window = _gateway_action_times.setdefault(key, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"AI evidence gateway {action} rate limit reached",
                headers={"Retry-After": "60"},
            )
        window.append(now)


def _module(name: str, knob: str):
    module = importlib.import_module(f"maverick.{name}")
    if not module.enabled():
        try:
            from maverick.config import config_source_errors, load_global_config

            errors = config_source_errors(include_tenant=False)
            config = load_global_config()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"{name.replace('_', ' ')} configuration is unavailable",
            ) from exc
        if errors or not isinstance(config, Mapping):
            raise HTTPException(
                status_code=503,
                detail=f"{name.replace('_', ' ')} configuration is unreadable",
            )
        section = config.get(knob)
        if isinstance(section, Mapping) and section.get("enable") is True:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{name.replace('_', ' ')} is enabled but a required "
                    "governance dependency is unavailable"
                ),
            )
        raise HTTPException(
            status_code=403,
            detail=f"{name.replace('_', ' ')} is disabled ([{knob}] enable)",
        )
    return module


async def _call(fn: Callable, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such assurance record") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        from maverick.ai_inventory import AIInventoryConfigError
        from maverick.policy_packs import (
            PolicyPackStateError,
            PolicyPackTenantRequired,
            PolicyPackUpdateRefused,
            PolicyPackValidationError,
        )
        from maverick.portfolio_risk import (
            ClosedPortfolioPeriod,
            PortfolioRiskConfigError,
            PortfolioRiskStateError,
        )
        from maverick.privacy_ops import RecordConflict
        from maverick.red_team_assurance import (
            RedTeamDisabled,
            RedTeamStateError,
        )

        if isinstance(exc, RecordConflict):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, RedTeamDisabled):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, RedTeamStateError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, ClosedPortfolioPeriod):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, PortfolioRiskConfigError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, PortfolioRiskStateError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, (PolicyPackValidationError, PolicyPackUpdateRefused)):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if isinstance(exc, PolicyPackTenantRequired):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, PolicyPackStateError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, PermissionError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(exc, AIInventoryConfigError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(
            status_code=503,
            detail=f"assurance operation failed safely ({type(exc).__name__})",
        ) from exc


async def _record_call(fn: Callable, *args, **kwargs) -> dict[str, Any]:
    record = await _call(fn, *args, **kwargs)
    if record is None:
        raise HTTPException(status_code=404, detail="no such assurance record")
    return record


def _safe_vendor_assessment(record: Mapping[str, Any]) -> dict[str, Any]:
    """Project vendor diligence without returning document locators or quotes."""

    answers = record.get("answers") or {}
    sources = record.get("answer_sources") or {}
    safe_answers: dict[str, dict[str, Any]] = {}
    for question_id, value in answers.items():
        if not isinstance(value, Mapping):
            continue
        source = sources.get(question_id)
        safe_answers[str(question_id)] = {
            "answer": value.get("answer"),
            "note": value.get("note", ""),
            "source": (
                source.get("kind") if isinstance(source, Mapping) else "unknown"
            ),
            "confidence": (
                source.get("confidence") if isinstance(source, Mapping) else None
            ),
            "review_required": (
                bool(source.get("review_required"))
                if isinstance(source, Mapping)
                else False
            ),
        }
    result = record.get("result") if isinstance(record.get("result"), Mapping) else {}
    review = record.get("review") if isinstance(record.get("review"), Mapping) else None
    return {
        "id": record.get("id"),
        "vendor": record.get("vendor"),
        "status": record.get("status"),
        "revision": record.get("revision"),
        "created_at": record.get("created_at"),
        "assessment_sha256": record.get("assessment_sha256"),
        "template_digest": record.get("template_digest"),
        "answers": safe_answers,
        "result": {
            key: result.get(key)
            for key in ("risk_rating", "residual_risk", "answered", "total", "findings")
        },
        "document_count": len(record.get("documents") or []),
        "skipped_prior_questions": record.get("skipped_prior_questions") or [],
        "review": (
            {
                key: review.get(key)
                for key in ("decision", "rationale", "reviewer", "reviewed_at")
            }
            if review is not None
            else None
        ),
        "legal_certification": False,
    }


def _safe_document_answers(result: Mapping[str, Any]) -> dict[str, Any]:
    """Do not send connector refs, source text, or even redacted excerpts to UI."""

    answers = result.get("answers") if isinstance(result.get("answers"), Mapping) else {}
    safe_answers: dict[str, dict[str, Any]] = {}
    for question_id, value in answers.items():
        if not isinstance(value, Mapping):
            continue
        safe_answers[str(question_id)] = {
            "answer": value.get("answer"),
            "note": value.get("note", ""),
            "confidence": value.get("confidence"),
            "citation_count": len(value.get("citations") or []),
        }
    evidence = result.get("document_evidence")
    return {
        "answers": safe_answers,
        "unanswered": result.get("unanswered") or [],
        "truncated": bool(result.get("truncated")),
        "review_required": True,
        "document_sha256": (
            evidence.get("document_sha256") if isinstance(evidence, Mapping) else None
        ),
    }


def _safe_drift_baseline(record: Mapping[str, Any]) -> dict[str, Any]:
    """Baselines contain aggregates, but return only their operational metadata."""

    features = record.get("features") if isinstance(record.get("features"), Mapping) else {}
    prediction = record.get("prediction")
    embedding = record.get("embedding")
    performance = record.get("performance")
    return {
        "id": record.get("id"),
        "asset_id": record.get("asset_id"),
        "source_id": record.get("source_id"),
        "artifact_digest": record.get("artifact_digest"),
        "revision": record.get("revision"),
        "status": record.get("status"),
        "payload_sha256": record.get("payload_sha256"),
        "monitored": {
            "feature_names": sorted(str(name) for name in features),
            "prediction": isinstance(prediction, Mapping),
            "embedding": isinstance(embedding, Mapping),
            "performance": isinstance(performance, Mapping),
        },
        "legal_certification": False,
    }


def _safe_drift_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "schema", "asset_id", "artifact_digest", "baseline_id", "run_id",
            "result", "metrics", "evidence_id", "evidence_revision",
            "alert", "legal_certification",
        )
    }



@router.get("/model-risk/inventory")
async def model_risk_inventory(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {"assets": await _call(officer.list_inventory)}


@router.post("/model-risk/observations")
async def observe_model_risk_asset(
    request: Request,
    body: schemas.ModelRiskObservationIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        officer.observe_asset,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.get("/model-risk/declarations")
async def model_risk_declarations(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {"declarations": await _call(officer.list_declarations)}


@router.post("/model-risk/declarations")
async def declare_model_risk_asset(
    request: Request,
    body: schemas.ModelRiskDeclarationIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    payload = body.model_dump()
    asset_id = payload.pop("asset_id")
    return await _call(
        officer.declare_asset,
        asset_id,
        **payload,
        actor=_actor(request),
    )


@router.patch("/model-risk/declarations/{asset_id}")
async def update_model_risk_declaration(
    request: Request,
    asset_id: str,
    body: schemas.ModelRiskDeclarationUpdateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    payload = body.model_dump(exclude_unset=True)
    expected_revision = payload.pop("expected_revision")
    return await _record_call(
        officer.update_declaration,
        asset_id,
        expected_revision=expected_revision,
        actor=_actor(request),
        **payload,
    )


@router.post("/model-risk/declarations/{asset_id}/review")
async def review_model_risk_declaration(
    request: Request,
    asset_id: str,
    body: schemas.ModelRiskDeclarationReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _record_call(
        officer.review_declaration,
        asset_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/model-risk/evidence")
async def model_risk_evidence(
    request: Request,
    asset_id: str = "",
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {
        "evidence": await _call(
            officer.list_evidence,
            asset_id=asset_id or None,
        )
    }


@router.post("/model-risk/evidence")
async def record_model_risk_evidence(
    request: Request,
    body: schemas.ModelRiskEvidenceIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    payload = body.model_dump()
    asset_id = payload.pop("asset_id")
    return await _call(
        officer.record_evidence,
        asset_id,
        **payload,
        actor=_actor(request),
    )


@router.post("/model-risk/evidence/training-receipts")
async def record_verified_training_receipt_evidence(
    request: Request,
    body: schemas.ModelRiskTrainingReceiptEvidenceIn,
) -> dict[str, Any]:
    """Register only the commitment from a verified tenant-private receipt."""
    require_permission(request, "operate")
    request_actor = _actor(request)
    if body.actor != request_actor:
        raise HTTPException(
            status_code=403,
            detail="actor must match the authenticated caller",
        )
    officer = _module("model_risk_assurance", "model_risk_assurance")
    payload = body.model_dump()
    asset_id = payload.pop("asset_id")
    return await _call(
        officer.record_verified_training_receipt_evidence,
        asset_id,
        **payload,
    )


@router.post("/model-risk/evidence/{evidence_id}/review")
async def review_model_risk_evidence(
    request: Request,
    evidence_id: str,
    body: schemas.EvidenceReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _record_call(
        officer.review_evidence,
        evidence_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/model-risk/findings")
async def model_risk_findings(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {"findings": await _call(officer.findings)}


# AI vendor risk -------------------------------------------------------------


@router.get("/ai-vendor/assessments")
async def list_ai_vendor_assessments(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    return await _call(vendor.list_vendor_assessments, limit=limit)


@router.get("/ai-vendor/assessments/{record_id}")
async def get_ai_vendor_assessment(
    request: Request,
    record_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    record = await _record_call(vendor.get_vendor_assessment, record_id)
    return _safe_vendor_assessment(record)


@router.get("/ai-vendor/prior-answer-plan")
async def ai_vendor_prior_answer_plan(
    request: Request,
    vendor_name: str = Query(..., min_length=1, max_length=300),
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    plan = await _call(vendor.prior_answer_plan, vendor_name)
    return {
        "vendor_key": plan.get("vendor_key"),
        "prior_record_id": plan.get("prior_record_id"),
        "skipped_question_ids": sorted((plan.get("skipped") or {}).keys()),
        "ask": plan.get("ask") or [],
        "review_required": True,
    }


async def _extract_vendor_documents(
    request: Request,
    documents: list[schemas.VendorDocumentReferenceIn],
) -> list[dict[str, Any]]:
    """Use authenticated connector credentials only; never accept document bytes."""

    if not documents:
        return []
    from .api import _document_source_principal

    vendor = importlib.import_module("maverick.ai_vendor_risk")
    principal = _document_source_principal(request)
    extracted: list[dict[str, Any]] = []
    for document in documents:
        extracted.append(await _call(
            vendor.extract_answers_from_connected_document,
            document.source,
            document.document_id,
            ref=document.ref,
            document_name=document.document_name,
            principal=principal,
            allow_ambient_credentials=False,
        ))
    return extracted


@router.post("/ai-vendor/documents/extract")
async def extract_ai_vendor_document_answers(
    request: Request,
    body: schemas.VendorDocumentReferenceIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    extracted = await _extract_vendor_documents(request, [body])
    return _safe_document_answers(extracted[0])


@router.post("/ai-vendor/assessments", status_code=201)
async def create_ai_vendor_assessment(
    request: Request,
    body: schemas.VendorAssessmentDraftIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    document_results = await _extract_vendor_documents(request, body.documents)
    record = await _call(
        vendor.draft_vendor_assessment,
        body.vendor,
        answers=body.answers,
        document_results=document_results,
        actor=_actor(request),
        use_prior=body.use_prior,
    )
    return _safe_vendor_assessment(record)


@router.post("/ai-vendor/assessments/{record_id}/review")
async def review_ai_vendor_assessment(
    request: Request,
    record_id: str,
    body: schemas.VendorAssessmentReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    record = await _record_call(
        vendor.review_vendor_assessment,
        record_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )
    return _safe_vendor_assessment(record)


@router.post("/ai-vendor/assessments/{record_id}/project")
async def project_ai_vendor_assessment(
    request: Request,
    record_id: str,
    body: schemas.VendorAssessmentProjectionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    vendor = importlib.import_module("maverick.ai_vendor_risk")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        vendor.project_to_model_risk,
        record_id,
        body.asset_id,
        actor=_actor(request),
        valid_until=body.valid_until,
        officer=officer.ModelRiskAssuranceOfficer(),
    )


# Drift monitoring -----------------------------------------------------------


@router.post("/drift/baselines", status_code=201)
async def capture_drift_baseline(
    request: Request,
    body: schemas.DriftBaselineIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    drift = _module("drift_monitoring", "drift_monitoring")
    payload = body.model_dump()
    asset_id = payload.pop("asset_id")
    record = await _call(
        drift.capture_baseline,
        asset_id,
        actor=_actor(request),
        **payload,
    )
    return _safe_drift_baseline(record)


@router.get("/drift/baselines/{baseline_id}")
async def get_drift_baseline(
    request: Request,
    baseline_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    drift = _module("drift_monitoring", "drift_monitoring")
    record = await _record_call(drift.DriftMonitoringService().get_baseline, baseline_id)
    return _safe_drift_baseline(record)


@router.post("/drift/baselines/{baseline_id}/evaluations", status_code=201)
async def evaluate_drift_baseline(
    request: Request,
    baseline_id: str,
    body: schemas.DriftEvaluationIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    drift = _module("drift_monitoring", "drift_monitoring")
    report = await _call(
        drift.evaluate,
        baseline_id,
        actor=_actor(request),
        **body.model_dump(),
    )
    return _safe_drift_report(report)


@router.get("/drift/evaluations")
async def list_drift_evaluations(
    request: Request,
    asset_id: str = Query(default="", max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """List bounded, already-safe MRA drift evidence; no raw samples exist here."""

    require_permission(request, "view")
    _require_active_tenant()
    officer = _module("model_risk_assurance", "model_risk_assurance")
    evidence = await _call(officer.list_evidence, asset_id=asset_id or None)
    items = [
        {
            key: row.get(key)
            for key in (
                "id", "asset_id", "source_id", "result", "scope_digest",
                "artifact_digest", "summary", "metrics", "observed_at",
                "valid_until", "revision",
            )
        }
        for row in evidence
        if row.get("evidence_kind") == "drift_monitoring"
    ]
    return {
        "items": items[:limit],
        "count": min(len(items), limit),
        "truncated": len(items) > limit,
        "limit": limit,
    }


# Agent and MCP topology -----------------------------------------------------


@router.get("/agent-topology/agents/{agent_name}")
async def get_agent_topology(
    request: Request,
    agent_name: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    topology = _module("agent_topology", "agent_topology")
    return await _call(topology.agent_view, agent_name)


@router.get("/agent-topology/blast-radius/{kind}/{name}")
async def get_agent_blast_radius(
    request: Request,
    kind: str,
    name: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    topology = _module("agent_topology", "agent_topology")
    return await _call(topology.blast_radius, kind, name)


@router.post("/agent-topology/mcp/sync")
async def sync_agent_topology_mcp_inventory(request: Request) -> dict[str, Any]:
    require_global_permission(request, "admin")
    topology = _module("agent_topology", "agent_topology")
    return await _call(topology.sync_mcp_inventory, actor=_actor(request))


@router.get("/red-team/suite")
async def red_team_starter_suite(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _call(manager.starter_suite_manifest)


@router.get("/red-team/campaigns")
async def red_team_campaigns(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "operate")
    manager = _module("red_team_assurance", "red_team_assurance")
    page = await _call(manager.list_campaigns, limit=limit)
    return {
        "campaigns": page["items"],
        "count": page["count"],
        "truncated": page["truncated"],
        "limit": page["limit"],
    }


@router.get("/red-team/campaigns/{campaign_id}")
async def red_team_campaign(
    request: Request,
    campaign_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(manager.get_campaign, campaign_id)


@router.post("/red-team/campaigns", status_code=201)
async def create_red_team_campaign(
    request: Request,
    body: schemas.RedTeamCampaignIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _call(
        manager.create_campaign,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post("/red-team/campaigns/{campaign_id}/finalize")
async def finalize_red_team_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.RedTeamFinalizeIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(
        manager.finalize_campaign,
        campaign_id,
        actor=_actor(request),
        force_incomplete=body.force_incomplete,
    )


@router.post("/red-team/campaigns/{campaign_id}/cancel")
async def cancel_red_team_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.RedTeamCancelIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(
        manager.cancel_campaign,
        campaign_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        rationale=body.rationale,
    )


@router.get("/red-team/schedules")
async def red_team_schedules(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "operate")
    manager = _module("red_team_assurance", "red_team_assurance")
    page = await _call(manager.list_schedules, limit=limit)
    return {
        "schedules": page["items"],
        "count": page["count"],
        "truncated": page["truncated"],
        "limit": page["limit"],
    }


@router.get("/red-team/schedules/{schedule_id}")
async def red_team_schedule(
    request: Request,
    schedule_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(manager.get_schedule, schedule_id)


@router.post("/red-team/schedules", status_code=201)
async def create_red_team_schedule(
    request: Request,
    body: schemas.RedTeamScheduleIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _call(
        manager.create_schedule,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post("/red-team/schedules/{schedule_id}/cancel")
async def cancel_red_team_schedule(
    request: Request,
    schedule_id: str,
    body: schemas.RedTeamCancelIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(
        manager.cancel_schedule,
        schedule_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        rationale=body.rationale,
    )


@router.post("/red-team/schedules/run-due")
async def run_due_red_team_schedules(
    request: Request,
    body: schemas.RedTeamRunDueIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _call(
        manager.run_due_schedules,
        actor=_actor(request),
        limit=body.limit,
    )


@router.get("/portfolio-risk/report")
async def portfolio_risk_report(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _call(portfolio.current_report)


@router.get("/portfolio-risk/snapshots")
async def portfolio_risk_snapshots(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return {"snapshots": await _call(portfolio.list_snapshots)}


@router.get("/portfolio-risk/snapshots/{period_id}")
async def portfolio_risk_snapshot(
    request: Request,
    period_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _record_call(portfolio.get_snapshot, period_id)


@router.get("/portfolio-risk/trend")
async def portfolio_risk_trend(
    request: Request,
    contributor_limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Prior-quarter comparison with contributor deltas and drill-through ids."""
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _call(
        portfolio.portfolio_trend, contributor_limit=contributor_limit
    )


@router.get("/portfolio-risk/snapshots/{period_id}/contributors/{contributor_id}")
async def portfolio_risk_contributor(
    request: Request,
    period_id: str,
    contributor_id: str,
) -> dict[str, Any]:
    """Content-free contributor identity for click-through from the trend."""
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _record_call(
        portfolio.get_contributor_drillthrough, period_id, contributor_id
    )


@router.post("/portfolio-risk/snapshots")
async def refresh_portfolio_risk_snapshot(
    request: Request,
    body: schemas.PortfolioSnapshotRefreshIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _record_call(
        portfolio.refresh_current_snapshot,
        actor=_actor(request),
        expected_revision=body.expected_revision,
        period_id=body.period_id,
    )


@router.get("/ai-inventory")
async def ai_inventory_overview(
    request: Request,
    connector: str = Query(default="", max_length=64),
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    inventory = _module("ai_inventory", "ai_inventory")
    available = await _call(inventory.available_connectors)
    if connector and connector not in available:
        raise HTTPException(status_code=422, detail="unknown AI inventory connector")
    config = await _call(inventory.get_ai_inventory_config)
    selected = connector or None
    assets, runs = await asyncio.gather(
        _call(inventory.list_discovered_assets, connector=selected),
        _call(inventory.list_discovery_runs, connector=selected, limit=100),
    )
    configured = list(config.get("connectors") or [])
    statuses: dict[str, Any] = {}
    for name in configured:
        statuses[name] = await _call(inventory.connector_status, name)
    limits = config.get("limits")
    return {
        "available_connectors": available,
        "configured_connectors": configured,
        "limits": {
            "page_cap": getattr(limits, "page_cap", None),
            "record_cap": getattr(limits, "record_cap", None),
            "retry_attempts": getattr(limits, "retry_attempts", None),
            "timeout_seconds": getattr(limits, "timeout_seconds", None),
            "missing_grace_runs": config.get("missing_grace_runs"),
        },
        "statuses": statuses,
        "assets": assets,
        "runs": runs,
    }


@router.post("/ai-inventory/connectors/{connector}/sync")
async def sync_ai_inventory_connector(
    request: Request,
    connector: str,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    inventory = _module("ai_inventory", "ai_inventory")
    available = await _call(inventory.available_connectors)
    if connector not in available:
        raise HTTPException(status_code=422, detail="unknown AI inventory connector")
    return await _call(
        inventory.sync_connector,
        connector,
        actor=_actor(request),
    )


@router.get("/policy-packs")
async def policy_pack_overview(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    packs = _module("policy_packs", "policy_packs")
    bundled, catalog, channels, subscriptions = await asyncio.gather(
        _call(packs.bundled_summaries),
        _call(packs.list_catalog),
        _call(packs.list_channels),
        _call(packs.list_subscriptions),
    )
    return {
        "bundled": bundled,
        "catalog": catalog,
        "channels": channels,
        "subscriptions": subscriptions,
        "certification": False,
        "legal_advice": False,
    }


@router.get("/policy-packs/{pack_id}")
async def policy_pack_detail(
    request: Request,
    pack_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    packs = _module("policy_packs", "policy_packs")
    active, subscription = await asyncio.gather(
        _call(packs.get_channel_summary, pack_id),
        _call(packs.get_subscription_summary, pack_id),
    )
    if active is None and subscription is None:
        bundled = await _call(packs.bundled_summaries)
        if pack_id not in bundled:
            raise HTTPException(status_code=404, detail="no such policy pack")
    return {
        "pack_id": pack_id,
        "active_channel": active,
        "subscription": subscription,
        "certification": False,
        "legal_advice": False,
    }


@router.post("/policy-packs/bundled/{pack_id}/stage", status_code=201)
async def stage_bundled_policy_pack(
    request: Request,
    pack_id: str,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    packs = _module("policy_packs", "policy_packs")
    staged = await _call(packs.stage_bundled, pack_id, actor=_actor(request))
    return await _record_call(packs.get_catalog_summary, staged["id"])


@router.post("/policy-packs/catalog/{catalog_entry_id}/activate")
async def activate_policy_pack(
    request: Request,
    catalog_entry_id: str,
    body: schemas.PolicyPackActivateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    packs = _module("policy_packs", "policy_packs")
    activated = await _record_call(
        packs.activate_pack,
        catalog_entry_id,
        actor=_actor(request),
        expected_revision=body.expected_revision,
        admin=True,
    )
    return await _record_call(packs.get_channel_summary, activated["pack_id"])


@router.post("/policy-packs/{pack_id}/subscribe", status_code=201)
async def subscribe_policy_pack(
    request: Request,
    pack_id: str,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    packs = _module("policy_packs", "policy_packs")
    subscribed = await _call(packs.subscribe, pack_id, actor=_actor(request))
    return await _record_call(
        packs.get_subscription_summary,
        subscribed["pack_id"],
    )


@router.post("/policy-packs/subscriptions/{subscription_id}/refresh")
async def refresh_policy_pack_subscription(
    request: Request,
    subscription_id: str,
    body: schemas.PolicyPackRefreshIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    packs = _module("policy_packs", "policy_packs")
    refreshed = await _record_call(
        packs.refresh_subscription,
        subscription_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )
    return await _record_call(
        packs.get_subscription_summary,
        refreshed["pack_id"],
    )


@router.post("/model-risk/findings/{finding_id}/risk-acceptance")
async def accept_model_risk(
    request: Request,
    finding_id: str,
    body: schemas.ModelRiskAcceptanceIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _record_call(
        officer.accept_risk,
        finding_id,
        finding_sha256=body.finding_sha256,
        rationale=body.rationale,
        reviewer=_actor(request),
        expires_at=body.expires_at,
        expected_revision=body.expected_revision,
    )


@router.get("/model-risk/incidents")
async def model_risk_incidents(
    request: Request,
    asset_id: str = "",
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {
        "incidents": await _call(
            officer.list_incidents,
            asset_id=asset_id or None,
        )
    }


@router.post("/model-risk/incidents")
async def record_model_risk_incident(
    request: Request,
    body: schemas.ModelRiskIncidentIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    payload = body.model_dump()
    asset_id = payload.pop("asset_id")
    return await _call(
        officer.record_incident,
        asset_id,
        **payload,
        actor=_actor(request),
    )


@router.patch("/model-risk/incidents/{incident_id}")
async def update_model_risk_incident(
    request: Request,
    incident_id: str,
    body: schemas.ModelRiskIncidentUpdateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _record_call(
        officer.update_incident,
        incident_id,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post("/model-risk/promotion-authorizations")
async def authorize_model_risk_promotion(
    request: Request,
    body: schemas.ModelRiskPromotionAuthorizationIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        officer.authorize_promotion_candidate,
        **body.model_dump(),
        reviewer=_actor(request),
    )


@router.post("/model-risk/promotion-authorizations/revoke")
async def revoke_model_risk_promotion(
    request: Request,
    body: schemas.ModelRiskPromotionRevocationIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        officer.revoke_promotion_candidate,
        **body.model_dump(),
        reviewer=_actor(request),
    )


@router.get("/model-risk/deployments")
async def model_risk_deployments(
    request: Request,
    asset_id: str = "",
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return {
        "deployments": await _call(
            officer.list_deployments,
            asset_id=asset_id or None,
        )
    }


@router.post("/model-risk/deployments")
async def record_model_risk_deployment(
    request: Request,
    body: schemas.ModelRiskDeploymentIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        officer.record_deployment,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.get("/model-risk/evidence-pack")
async def model_risk_evidence_pack(
    request: Request,
    asset_id: str = "",
) -> dict[str, Any]:
    require_permission(request, "operate")
    officer = _module("model_risk_assurance", "model_risk_assurance")
    return await _call(
        officer.render_assurance_pack,
        actor=_actor(request),
        asset_id=asset_id or None,
    )


# AI Evidence-Ready Gateway -------------------------------------------------


@router.get("/gateway/summary")
async def evidence_gateway_summary(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _call(gateway.summary)


@router.get("/gateway/policies")
async def evidence_gateway_policies(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return {"policies": await _call(gateway.list_policies)}


@router.get("/gateway/policies/{policy_id}")
async def evidence_gateway_policy(
    request: Request,
    policy_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _record_call(gateway.get_policy, policy_id)


@router.put("/gateway/policies/{policy_id}")
async def upsert_evidence_gateway_policy(
    request: Request,
    policy_id: str,
    body: schemas.EvidenceGatewayPolicyUpsertIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    current = await _call(gateway.get_policy, policy_id)
    payload = body.model_dump(
        exclude_unset=isinstance(current, dict),
    )
    if isinstance(current, dict):
        settings = current.get("settings")
        if not isinstance(settings, dict):
            raise HTTPException(
                status_code=503,
                detail="current evidence policy is malformed",
            )
        for field in (
            "disclosure_text",
            "require_interaction_disclosure",
            "require_machine_readable_marking",
            "require_visible_marking",
            "supported_modalities",
            "machine_marker",
            "visible_marker",
        ):
            payload.setdefault(field, settings[field])
        for field in ("model_sha256", "context_sha256", "metadata"):
            payload.setdefault(field, current[field])
    return await _call(
        gateway.upsert_policy,
        policy_id,
        **payload,
        actor=_actor(request),
    )


@router.post("/gateway/deliver")
async def deliver_evidence_ready_text(
    request: Request,
    body: schemas.EvidenceGatewayDeliveryIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _limit_gateway_action(request, "delivery")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    payload = body.model_dump()
    generated_text = payload.pop("generated_text")
    return await _call(
        gateway.deliver_text,
        generated_text,
        **payload,
        actor=_actor(request),
    )


@router.get("/gateway/receipts")
async def evidence_gateway_receipts(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None, min_length=16, max_length=2048),
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    page = await _call(
        gateway.list_interaction_receipts_page,
        limit=limit,
        cursor=cursor,
    )
    return {
        "receipts": page["items"],
        "next_cursor": page["next_cursor"],
        "snapshot_total": page["snapshot_total"],
        "count": page["count"],
        "missing_governed_count": page["missing_governed_count"],
    }


@router.get("/gateway/receipts/{receipt_id}")
async def evidence_gateway_receipt(
    request: Request,
    receipt_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _record_call(gateway.get_interaction_receipt, receipt_id)


@router.post("/gateway/receipts/{receipt_id}/verify")
async def verify_evidence_gateway_receipt(
    request: Request,
    receipt_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    receipt = await _record_call(gateway.get_interaction_receipt, receipt_id)
    valid = await _call(gateway.verify_interaction_receipt, receipt)
    return {"receipt_id": receipt_id, "valid": bool(valid)}


@router.get("/gateway/regulatory-impacts")
async def evidence_gateway_regulatory_impacts(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None, max_length=2_048),
    status: str | None = Query(
        None,
        pattern="^(pending_review|accepted_refreshing|accepted|dismissed)$",
    ),
    pending_first: bool = Query(True),
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    page = await _call(
        gateway.list_regulatory_impacts_page,
        limit=limit,
        cursor=cursor,
        status=status,
        pending_first=pending_first,
    )
    return {
        "regulatory_impacts": page["items"],
        "next_cursor": page["next_cursor"],
        "snapshot_total": page["snapshot_total"],
        "count": page["count"],
        "status": page["status"],
        "pending_first": page["pending_first"],
    }


@router.get("/gateway/regulatory-impacts/{impact_id}")
async def evidence_gateway_regulatory_impact(
    request: Request,
    impact_id: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _record_call(gateway.get_regulatory_impact, impact_id)


@router.post("/gateway/regulatory-impacts")
async def record_evidence_gateway_regulatory_impact(
    request: Request,
    body: schemas.EvidenceGatewayRegulatoryImpactIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    payload = body.model_dump()
    alert_id = payload.pop("alert_id")
    return await _call(
        gateway.record_regulatory_impact,
        alert_id,
        **payload,
        actor=_actor(request),
    )


@router.post("/gateway/regulatory-impacts/{impact_id}/review")
async def review_evidence_gateway_regulatory_impact(
    request: Request,
    impact_id: str,
    body: schemas.EvidenceGatewayImpactReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _record_call(
        gateway.review_regulatory_impact,
        impact_id,
        decision=body.decision,
        reviewer=_actor(request),
        rationale=body.rationale,
        expected_revision=body.expected_revision,
    )


@router.post("/gateway/assurance-packet")
async def download_evidence_gateway_assurance_packet(
    request: Request,
    profile: str = Query(
        "combined",
        pattern="^(article50_gpai|nist_ai_rmf|federal_vendor|combined)$",
    ),
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=1_024,
    ),
) -> Response:
    # Rendering appends a signed attestation to the packet ledger, so this is
    # an explicit privileged mutation rather than a cacheable/read-only GET.
    require_global_permission(request, "admin")
    _limit_gateway_action(request, "packet_issuance")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    issued = await _call(
        gateway.issue_assurance_packet,
        profile=profile,
        actor=_actor(request),
        idempotency_key=idempotency_key,
    )
    packet = issued["packet"]
    try:
        payload = json.dumps(
            packet,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="assurance packet was not valid JSON",
        ) from exc
    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Idempotent-Replay": (
                "true" if issued["idempotent_replay"] else "false"
            ),
            "Content-Disposition": (
                'attachment; filename="lightwork-ai-assurance-packet.json"'
            ),
        },
    )


@router.post("/gateway/assurance-packet/verify")
async def verify_evidence_gateway_assurance_packet(
    request: Request,
    body: schemas.EvidenceGatewayPacketVerifyIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    valid = await _call(gateway.verify_assurance_packet, body.packet)
    return {"valid": bool(valid)}


@router.post("/gateway/demo-seed")
async def seed_evidence_gateway_demo(request: Request) -> dict[str, Any]:
    require_global_permission(request, "admin")
    gateway = _module("ai_evidence_gateway", "evidence_gateway")
    return await _call(gateway.seed_demo, actor=_actor(request))
