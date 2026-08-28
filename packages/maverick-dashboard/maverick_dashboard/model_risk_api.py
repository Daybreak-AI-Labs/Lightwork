"""Operator API for governed Model Risk & AI Assurance."""
from __future__ import annotations

import asyncio
import importlib
import json
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool

from . import model_risk_schemas as schemas
from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/assurance", tags=["model-risk-assurance"])
_GATEWAY_ACTION_LIMITS = {"delivery": 120, "packet_issuance": 12}
_GATEWAY_ACTION_BUCKET_CAP = 10_000
_MEDIA_PROVENANCE_UPLOAD_CAP = 64 * 1024 * 1024
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


def _human_principal(request: Request) -> str:
    """Require an authenticated end-user for externally consequential records."""

    from .auth import caller_principal

    principal = caller_principal(request)
    if not principal or not principal.startswith("user:"):
        raise HTTPException(
            status_code=403,
            detail="an authenticated human user is required for this decision",
        )
    return principal


def _policy_pack_evidence_resolver(
    *,
    actor: str,
) -> Callable[[str], Mapping[str, Any] | None]:
    """Resolve exact approved evidence through a narrowly validated adapter.

    The first adapter deliberately supports only Model Risk evaluation records.
    Other evidence kinds require their own semantic normalizer; relabeling a
    generic record as policy, incident, or legal evidence would be unsafe.
    """

    from maverick import evidence_graph, model_risk_assurance

    def resolve(evidence_id: str) -> Mapping[str, Any] | None:
        evidence_graph.project_model_risk_evidence(evidence_id, actor=actor)
        record = model_risk_assurance.get_evidence(evidence_id)
        if not isinstance(record, Mapping):
            return None
        if record.get("evidence_kind") != "evaluation":
            raise ValueError(
                "this evidence kind has no governed policy-pack semantic adapter"
            )
        observed_at = record.get("observed_at")
        try:
            observed_iso = datetime.fromtimestamp(
                float(observed_at),
                tz=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, TypeError, ValueError) as exc:
            raise ValueError("governed evidence observed_at is invalid") from exc
        return {
            "id": record.get("id"),
            "revision": record.get("revision"),
            "content_sha256": record.get("payload_sha256"),
            "evidence_kind": "evaluation",
            "schema": "lightwork.evaluation-evidence.v1",
            "observed_at": observed_iso,
        }

    return resolve


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


def _raise_ai_cost_collector_http_error(exc: Exception) -> None:
    from maverick.ai_cost_collectors import (
        AICostCollectorConfigError,
        AICostCollectorProtocolError,
        AICostCollectorTransportError,
        AICostCollectorUnsupportedError,
    )

    if isinstance(exc, AICostCollectorUnsupportedError):
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if isinstance(exc, AICostCollectorConfigError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(
        exc,
        (AICostCollectorProtocolError, AICostCollectorTransportError),
    ):
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _raise_identity_governance_http_error(exc: Exception) -> None:
    from maverick.ai_identity_governance_connectors import (
        IdentityGovernanceConfigError,
        IdentityGovernanceLimitError,
        IdentityGovernanceProtocolError,
        IdentityGovernanceTransportError,
        IdentityGovernanceUnsupportedError,
    )

    if isinstance(exc, IdentityGovernanceUnsupportedError):
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    if isinstance(exc, IdentityGovernanceConfigError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            IdentityGovernanceLimitError,
            IdentityGovernanceProtocolError,
            IdentityGovernanceTransportError,
        ),
    ):
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _raise_policy_and_dynamic_risk_http_error(exc: Exception) -> None:
    from maverick.ai_bank_mrm import (
        AIBankMRMApprovalError,
        AIBankMRMAssetError,
        AIBankMRMConfigError,
        AIBankMRMEvidenceError,
        AIBankMRMIdempotencyConflict,
        AIBankMRMStaleError,
        AIBankMRMStateError,
    )
    from maverick.ai_benchmark_exchange import (
        AIBenchmarkExchangeConfigError,
        AIBenchmarkExchangeConsentError,
        AIBenchmarkExchangeEvidenceError,
        AIBenchmarkExchangeIdempotencyConflict,
        AIBenchmarkExchangeStaleError,
        AIBenchmarkExchangeStateError,
        AIBenchmarkExchangeVerificationError,
    )
    from maverick.ai_conformity_workflow import (
        AIConformityWorkflowAssetError,
        AIConformityWorkflowConfigError,
        AIConformityWorkflowIdempotencyConflict,
        AIConformityWorkflowIncomplete,
        AIConformityWorkflowStateError,
    )
    from maverick.ai_discovery_sensors import (
        AIDiscoverySensorsApprovalError,
        AIDiscoverySensorsBindingError,
        AIDiscoverySensorsConfigError,
        AIDiscoverySensorsIdempotencyConflict,
        AIDiscoverySensorsStateError,
    )
    from maverick.ai_dynamic_risk import (
        DynamicRiskAssetError,
        DynamicRiskConfigError,
        DynamicRiskIdempotencyConflict,
        DynamicRiskStateError,
    )
    from maverick.ai_explainability import (
        ExplainabilityAssetError,
        ExplainabilityConfigError,
        ExplainabilityIdempotencyConflict,
        ExplainabilityStateError,
    )
    from maverick.ai_insurability import (
        AIInsurabilityApprovalError,
        AIInsurabilityAssetError,
        AIInsurabilityConfigError,
        AIInsurabilityEvidenceError,
        AIInsurabilityIdempotencyConflict,
        AIInsurabilityStaleError,
        AIInsurabilityStateError,
    )
    from maverick.ai_media_provenance import (
        MediaProvenanceBindingError,
        MediaProvenanceConfigError,
        MediaProvenanceIdempotencyConflict,
        MediaProvenanceStateError,
        MediaProvenanceUnavailable,
    )
    from maverick.ai_policy_management import (
        AIPolicyManagementConfigError,
        AIPolicyManagementStateError,
    )
    from maverick.ai_regulated_verticals import (
        AIRegulatedVerticalsApprovalError,
        AIRegulatedVerticalsAssetError,
        AIRegulatedVerticalsConfigError,
        AIRegulatedVerticalsEvidenceError,
        AIRegulatedVerticalsIdempotencyConflict,
        AIRegulatedVerticalsStaleError,
        AIRegulatedVerticalsStateError,
        AIRegulatedVerticalsTemplateError,
    )
    from maverick.ai_runtime_guardrails import (
        RuntimeGuardrailsConfigError,
        RuntimeGuardrailsError,
        RuntimeGuardrailsTenantError,
    )

    if isinstance(
        exc,
        (
            DynamicRiskConfigError,
            AIBankMRMConfigError,
            AIBenchmarkExchangeConfigError,
            AIConformityWorkflowConfigError,
            AIDiscoverySensorsConfigError,
            AIPolicyManagementConfigError,
            ExplainabilityConfigError,
            AIInsurabilityConfigError,
            MediaProvenanceConfigError,
            AIRegulatedVerticalsConfigError,
            RuntimeGuardrailsConfigError,
        ),
    ):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, RuntimeGuardrailsTenantError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, AIBenchmarkExchangeVerificationError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, MediaProvenanceUnavailable):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            DynamicRiskAssetError,
            DynamicRiskIdempotencyConflict,
            AIBankMRMApprovalError,
            AIBankMRMAssetError,
            AIBankMRMEvidenceError,
            AIBankMRMIdempotencyConflict,
            AIBankMRMStaleError,
            AIBankMRMStateError,
            AIBenchmarkExchangeConsentError,
            AIBenchmarkExchangeEvidenceError,
            AIBenchmarkExchangeIdempotencyConflict,
            AIBenchmarkExchangeStaleError,
            AIBenchmarkExchangeStateError,
            AIConformityWorkflowAssetError,
            AIConformityWorkflowIdempotencyConflict,
            AIConformityWorkflowIncomplete,
            AIConformityWorkflowStateError,
            AIDiscoverySensorsApprovalError,
            AIDiscoverySensorsBindingError,
            AIDiscoverySensorsIdempotencyConflict,
            AIDiscoverySensorsStateError,
            ExplainabilityAssetError,
            ExplainabilityIdempotencyConflict,
            AIInsurabilityApprovalError,
            AIInsurabilityAssetError,
            AIInsurabilityEvidenceError,
            AIInsurabilityIdempotencyConflict,
            AIInsurabilityStaleError,
            AIInsurabilityStateError,
            MediaProvenanceBindingError,
            MediaProvenanceIdempotencyConflict,
            MediaProvenanceStateError,
            AIRegulatedVerticalsApprovalError,
            AIRegulatedVerticalsAssetError,
            AIRegulatedVerticalsEvidenceError,
            AIRegulatedVerticalsIdempotencyConflict,
            AIRegulatedVerticalsStaleError,
            AIRegulatedVerticalsStateError,
            AIRegulatedVerticalsTemplateError,
        ),
    ):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(
        exc,
        (
            DynamicRiskStateError,
            AIPolicyManagementStateError,
            ExplainabilityStateError,
        ),
    ):
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if isinstance(exc, RuntimeGuardrailsError):
        raise HTTPException(status_code=503, detail=str(exc)) from exc


async def _call(fn: Callable, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="no such assurance record") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        from maverick.ai_business_intake import (
            AIBusinessIntakeConfigError,
            AIBusinessIntakeStateError,
        )
        from maverick.ai_cost_roi import (
            AICostConfigError,
            AICostIdempotencyConflict,
            AICostProtocolError,
            AICostStateError,
        )
        from maverick.ai_inventory import AIInventoryConfigError
        from maverick.ai_literacy import (
            AILiteracyIdempotencyConflict,
            AILiteracyStateError,
        )
        from maverick.ai_regulatory_feed import (
            AIRegulatoryFeedConfigError,
            AIRegulatoryFeedStateError,
        )
        from maverick.eu_ai_incidents import (
            Article73ConfigurationError,
            Article73StateError,
        )
        from maverick.grc_outbound_sync import (
            GRCOutboundConfigError,
            GRCOutboundConflict,
            GRCOutboundDeliveryError,
            GRCOutboundStateError,
        )
        from maverick.grc_vendor_transports import GRCVendorConfigError
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
        _raise_ai_cost_collector_http_error(exc)
        _raise_identity_governance_http_error(exc)
        _raise_policy_and_dynamic_risk_http_error(exc)
        if isinstance(
            exc,
            (
                AIBusinessIntakeConfigError,
                AICostConfigError,
                Article73ConfigurationError,
                GRCVendorConfigError,
            ),
        ):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, GRCOutboundConfigError):
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if isinstance(
            exc,
            (
                AILiteracyIdempotencyConflict,
                AILiteracyStateError,
                AIBusinessIntakeStateError,
                AICostIdempotencyConflict,
                AICostProtocolError,
                AICostStateError,
                Article73StateError,
                GRCOutboundConflict,
                GRCOutboundDeliveryError,
                GRCOutboundStateError,
            ),
        ):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, AIRegulatoryFeedConfigError):
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if isinstance(exc, AIRegulatoryFeedStateError):
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
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
    return await _call(vendor.list_vendor_assessments, limit=limit)


@router.get("/ai-vendor/assessments/{record_id}")
async def get_ai_vendor_assessment(
    request: Request,
    record_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
    record = await _record_call(vendor.get_vendor_assessment, record_id)
    return _safe_vendor_assessment(record)


@router.get("/ai-vendor/prior-answer-plan")
async def ai_vendor_prior_answer_plan(
    request: Request,
    vendor_name: str = Query(..., min_length=1, max_length=300),
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
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

    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
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
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
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
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
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
    vendor = _module("ai_vendor_risk", "ai_vendor_risk")
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
    limit: int = Query(100, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _call(manager.list_schedules, limit=limit)


@router.get("/red-team/schedules/{schedule_id}")
async def red_team_schedule(
    request: Request,
    schedule_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
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


@router.post("/red-team/schedules/{schedule_id}/materialize")
async def materialize_red_team_schedule(
    request: Request,
    schedule_id: str,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    manager = _module("red_team_assurance", "red_team_assurance")
    return await _record_call(
        manager.materialize_due_schedule,
        schedule_id,
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


@router.get("/portfolio-risk/trend")
async def portfolio_risk_trend(
    request: Request,
    contributor_limit: int = 100,
) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _call(
        portfolio.portfolio_trend,
        contributor_limit=contributor_limit,
    )


@router.get(
    "/portfolio-risk/snapshots/{period_id}/contributors/{contributor_id}"
)
async def portfolio_risk_contributor(
    request: Request,
    period_id: str,
    contributor_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _record_call(
        portfolio.get_contributor_drillthrough,
        period_id,
        contributor_id,
    )


@router.get("/portfolio-risk/snapshots/{period_id}")
async def portfolio_risk_snapshot(
    request: Request,
    period_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    portfolio = _module("portfolio_risk", "portfolio_risk")
    return await _record_call(portfolio.get_snapshot, period_id)


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


# Explainable dynamic AI risk ----------------------------------------------


def _dynamic_risk_human(risk, request: Request):
    return risk.RiskActor(risk.ActorKind.HUMAN, _human_principal(request))


@router.get("/dynamic-risk/assets/{asset_id}")
async def get_dynamic_risk_score(
    request: Request,
    asset_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    return await _record_call(risk.default_service.current_score, asset_id)


@router.get("/dynamic-risk/assets/{asset_id}/signals")
async def list_dynamic_risk_signals(
    request: Request,
    asset_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    items = await _call(risk.default_service.list_signals, asset_id)
    return {"signals": items, "count": len(items)}


@router.get("/dynamic-risk/signals/{signal_id}")
async def get_dynamic_risk_signal(
    request: Request,
    signal_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    return await _record_call(risk.default_service.get_signal, signal_id)


@router.post("/dynamic-risk/signals", status_code=201)
async def ingest_dynamic_risk_signal(
    request: Request,
    body: schemas.AIDynamicRiskSignalIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    signal = risk.RiskSignal(**body.model_dump())
    return await _call(
        risk.default_service.ingest_signal,
        signal,
        actor=_dynamic_risk_human(risk, request),
    )


@router.post("/dynamic-risk/signals/{signal_id}/resolve")
async def resolve_dynamic_risk_signal(
    request: Request,
    signal_id: str,
    body: schemas.AIDynamicRiskResolveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    return await _call(
        risk.default_service.resolve_signal,
        signal_id,
        expected_revision=body.expected_revision,
        resolution_code=body.resolution_code,
        resolution_evidence_digest=body.resolution_evidence_digest,
        actor=_dynamic_risk_human(risk, request),
    )


@router.post("/dynamic-risk/signals/{signal_id}/supersede")
async def supersede_dynamic_risk_signal(
    request: Request,
    signal_id: str,
    body: schemas.AIDynamicRiskSupersedeIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    return await _call(
        risk.default_service.supersede_signal,
        signal_id,
        superseding_signal_id=body.superseding_signal_id,
        expected_revision=body.expected_revision,
        actor=_dynamic_risk_human(risk, request),
    )


@router.post("/dynamic-risk/assets/{asset_id}/score")
async def score_dynamic_risk_asset(
    request: Request,
    asset_id: str,
    body: schemas.AIDynamicRiskScoreIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    risk = _module("ai_dynamic_risk", "ai_dynamic_risk")
    context = risk.RiskContext(
        asset_criticality=body.asset_criticality,
        eu_risk_tier=body.eu_risk_tier,
        sensitive_data=body.sensitive_data,
        external_exposure=body.external_exposure,
    )
    return await _call(
        risk.default_service.score_asset,
        asset_id,
        context=context,
        expected_revision=body.expected_revision,
        actor=_dynamic_risk_human(risk, request),
    )


# Transient runtime policy enforcement ------------------------------------


async def _runtime_authority(runtime, body: schemas.AIRuntimeGuardrailIn):
    """Resolve exact asset and reviewed policy facts from Model Risk authority."""

    assurance = _module("model_risk_assurance", "model_risk_assurance")
    observation = await _call(assurance.get_observation, body.asset_id)
    if not isinstance(observation, Mapping):
        raise HTTPException(
            status_code=409,
            detail="runtime asset binding is stale or unavailable",
        )
    claimed_digest = body.asset_version_digest.lower().removeprefix("sha256:")
    if (
        observation.get("revision") != body.asset_revision
        or observation.get("version_digest") != claimed_digest
    ):
        raise HTTPException(
            status_code=409,
            detail="runtime asset binding is stale or unavailable",
        )
    declaration = await _call(assurance.get_declaration, body.asset_id)
    if (
        not isinstance(declaration, Mapping)
        or declaration.get("status") != "approved"
        or declaration.get("observed_version_digest") != claimed_digest
    ):
        raise HTTPException(
            status_code=409,
            detail="an approved current asset declaration is required",
        )
    risk_context = declaration.get("risk_context") or {}
    identity = observation.get("identity") or {}
    if not isinstance(risk_context, Mapping) or not isinstance(identity, Mapping):
        raise HTTPException(
            status_code=503,
            detail="runtime asset policy authority is malformed",
        )

    risk_tier = declaration.get("risk_tier")
    if risk_tier not in {"unclassified", "low", "medium", "high", "critical"}:
        raise HTTPException(
            status_code=503,
            detail="runtime asset policy authority is malformed",
        )
    facts: dict[str, Any] = {"risk_tier": risk_tier}
    for field in (
        "jurisdictions",
        "data_classes",
        "deployment_types",
        "tags",
        "use_case_tags",
    ):
        value = risk_context.get(field, [])
        if (
            not isinstance(value, list)
            or len(value) > 64
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 128
                or any(ord(char) < 32 or ord(char) == 127 for char in item)
                for item in value
            )
        ):
            raise HTTPException(
                status_code=503,
                detail="runtime asset policy authority is malformed",
            )
        facts[field] = tuple(value)
    asset_type = identity.get("asset_type")
    if not isinstance(asset_type, str) or not asset_type:
        raise HTTPException(
            status_code=503,
            detail="runtime asset policy authority is malformed",
        )
    facts["asset_types"] = (asset_type,)
    binding = runtime.RuntimeAssetBinding(
        asset_id=body.asset_id,
        asset_revision=body.asset_revision,
        asset_version_digest=claimed_digest,
    )
    try:
        policy_context = runtime.RuntimePolicyContext(**facts)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="runtime asset policy authority is malformed",
        ) from exc
    return binding, policy_context


async def _record_runtime_dynamic_risk(runtime, result) -> dict[str, Any]:
    """Record non-allow runtime findings when the risk plane is explicitly on."""

    if result.decision == "allow":
        return {"status": "not_applicable"}
    try:
        from maverick import ai_dynamic_risk as risk

        if not risk.enabled():
            return {"status": "disabled"}
        normalized = runtime.dynamic_risk_receipt(result)
        signal = risk.signal_from_runtime_receipt(normalized)
        saved = await _call(
            risk.default_service.ingest_signal,
            signal,
            actor=risk.RiskActor(
                risk.ActorKind.SYSTEM,
                "system:runtime-guardrails",
            ),
        )
        return {
            "status": "recorded",
            "signal_id": saved.get("signal_id") or saved.get("id"),
            "signal_revision": saved.get("revision"),
        }
    except HTTPException:
        return {"status": "unavailable"}
    except Exception:
        return {"status": "unavailable"}


@router.post("/runtime-guardrails/evaluate")
async def evaluate_runtime_guardrails(
    request: Request,
    body: schemas.AIRuntimeGuardrailIn,
) -> dict[str, Any]:
    """Evaluate transient content and return only redacted content plus receipts."""

    require_permission(request, "operate")
    _require_active_tenant()
    runtime = _module("ai_runtime_guardrails", "ai_runtime_guardrails")
    binding, policy_context = await _runtime_authority(runtime, body)
    result = await _call(
        runtime.default_service.evaluate,
        runtime.RuntimeGuardrailInput(
            stage=body.stage,
            content=body.content,
            asset=binding,
            policy_context=policy_context,
            action_name=body.action_name,
            action_arguments=body.action_arguments,
            known_prompt=body.known_prompt,
        ),
    )
    risk_signal = await _record_runtime_dynamic_risk(runtime, result)
    return {
        "decision": result.decision,
        "receipt": result.receipt,
        "redacted_content": result.redacted_content,
        "dynamic_risk_signal": risk_signal,
        "raw_content_persisted": False,
    }


# Governed digest-only AI explainability ----------------------------------


async def _configured_explainability():
    explainability = _module("ai_explainability", "ai_explainability")
    service = await _call(explainability.configured_explainability)
    return explainability, service


def _explainability_human(explainability, request: Request):
    return explainability.ExplanationActor(
        explainability.ActorKind.HUMAN,
        _human_principal(request),
    )


@router.get("/ai-explainability/explanations")
async def list_ai_explanations(
    request: Request,
    asset_id: str | None = Query(default=None, max_length=36),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _explainability, service = await _configured_explainability()
    items = await _call(service.list_explanations, asset_id)
    return {"explanations": items, "count": len(items)}


@router.get("/ai-explainability/explanations/{explanation_id}")
async def get_ai_explanation(
    request: Request,
    explanation_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _explainability, service = await _configured_explainability()
    return await _record_call(service.get_explanation, explanation_id)


@router.post("/ai-explainability/explanations", status_code=201)
async def ingest_ai_explanation(
    request: Request,
    body: schemas.AIExplanationReceiptIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    explainability, service = await _configured_explainability()
    return await _call(
        service.ingest_receipt,
        body.model_dump(by_alias=True),
        actor=_explainability_human(explainability, request),
    )


@router.post("/ai-explainability/explanations/{explanation_id}/review")
async def review_ai_explanation(
    request: Request,
    explanation_id: str,
    body: schemas.AIExplanationReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    explainability, service = await _configured_explainability()
    return await _call(
        service.review_explanation,
        explanation_id,
        expected_revision=body.expected_revision,
        outcome=body.outcome,
        evidence_sha256=body.evidence_sha256,
        actor=_explainability_human(explainability, request),
    )


# Independent insurance-assurance evidence profiles -----------------------


async def _configured_insurability():
    insurability = _module("ai_insurability", "ai_insurability")
    service = await _call(insurability.configured_insurability)
    return insurability, service


@router.get("/ai-insurability/profiles")
async def list_ai_insurability_profiles(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    items = await _call(service.list_profiles, limit=limit)
    return {"profiles": items, "count": len(items)}


@router.get("/ai-insurability/profiles/{profile_id}")
async def get_ai_insurability_profile(
    request: Request,
    profile_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    return await _record_call(service.get_profile, profile_id)


@router.post("/ai-insurability/profiles", status_code=201)
async def create_ai_insurability_profile(
    request: Request,
    body: schemas.AIInsurabilityCreateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    return await _call(
        service.create_profile,
        body.profile_id,
        asset_id=body.asset_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-insurability/profiles/{profile_id}/versions")
async def version_ai_insurability_profile(
    request: Request,
    profile_id: str,
    body: schemas.AIInsurabilityVersionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    return await _call(
        service.add_version,
        profile_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-insurability/profiles/{profile_id}/approve")
async def approve_ai_insurability_profile(
    request: Request,
    profile_id: str,
    body: schemas.AIInsurabilityApproveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    return await _call(
        service.approve_profile,
        profile_id,
        body.version,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
    )


@router.get("/ai-insurability/profiles/{profile_id}/export")
async def export_ai_insurability_profile(
    request: Request,
    profile_id: str,
    version: int | None = Query(default=None, ge=1, le=50),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _insurability, service = await _configured_insurability()
    return await _call(service.export_profile, profile_id, version=version)


# EU AI Act conformity-readiness workflow ---------------------------------


async def _configured_conformity_workflow():
    conformity = _module("ai_conformity_workflow", "ai_conformity_workflow")
    service = await _call(conformity.configured_ai_conformity_workflow)
    return conformity, service


def _conformity_actor(conformity, request: Request):
    return conformity.HumanActor(_human_principal(request))


def _conformity_citations(conformity, rows) -> tuple:
    return tuple(
        conformity.LegalCitation(item.provision, item.official_url)
        for item in rows
    )


@router.get("/ai-conformity/cases")
async def list_ai_conformity_cases(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _conformity, service = await _configured_conformity_workflow()
    items = await _call(service.list_case_projections, limit=limit)
    return {"cases": items, "count": len(items)}


@router.get("/ai-conformity/cases/{case_id}")
async def get_ai_conformity_case(
    request: Request,
    case_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _conformity, service = await _configured_conformity_workflow()
    return await _record_call(service.project_case, case_id)


@router.post("/ai-conformity/cases", status_code=201)
async def create_ai_conformity_case(
    request: Request,
    body: schemas.AIConformityCreateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    conformity, service = await _configured_conformity_workflow()
    return await _call(
        service.create_case,
        asset_id=body.asset_id,
        economic_operator_role=body.economic_operator_role,
        risk_tier=body.risk_tier,
        act_applicability=body.act_applicability,
        legal_basis=_conformity_citations(conformity, body.legal_basis),
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


@router.post("/ai-conformity/cases/{case_id}/evidence", status_code=201)
async def bind_ai_conformity_evidence(
    request: Request,
    case_id: str,
    body: schemas.AIConformityEvidenceIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    conformity, service = await _configured_conformity_workflow()
    return await _call(
        service.bind_evidence,
        case_id,
        evidence=conformity.EvidenceReference(
            body.evidence_type,
            body.source_ref,
            body.sha256,
        ),
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


@router.post("/ai-conformity/cases/{case_id}/fria-determination")
async def set_ai_conformity_fria_determination(
    request: Request,
    case_id: str,
    body: schemas.AIConformityDeterminationIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    conformity, service = await _configured_conformity_workflow()
    return await _call(
        service.set_fria_determination,
        case_id,
        outcome=body.outcome,
        rationale_sha256=body.rationale_sha256,
        citations=_conformity_citations(conformity, body.citations),
        expected_revision=body.expected_revision,
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


@router.post("/ai-conformity/cases/{case_id}/article-43-route")
async def set_ai_conformity_article_43_route(
    request: Request,
    case_id: str,
    body: schemas.AIConformityArticle43RouteIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    conformity, service = await _configured_conformity_workflow()
    return await _call(
        service.set_article_43_route,
        case_id,
        route=body.route,
        rationale_sha256=body.rationale_sha256,
        citations=_conformity_citations(conformity, body.citations),
        expected_revision=body.expected_revision,
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


@router.post("/ai-conformity/cases/{case_id}/checklist")
async def set_ai_conformity_checklist_entry(
    request: Request,
    case_id: str,
    body: schemas.AIConformityChecklistEntryIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    conformity, service = await _configured_conformity_workflow()
    entry = conformity.ChecklistEntry(
        status=body.status,
        evidence_binding_ids=tuple(body.evidence_binding_ids),
        rationale_sha256=body.rationale_sha256,
        citations=_conformity_citations(conformity, body.citations),
    )
    return await _call(
        service.set_checklist_entry,
        case_id,
        domain=body.domain,
        section=body.section,
        entry=entry,
        expected_revision=body.expected_revision,
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


async def _ai_conformity_action(
    request: Request,
    case_id: str,
    body: schemas.AIConformityActionIn,
    *,
    action: str,
) -> dict[str, Any]:
    conformity, service = await _configured_conformity_workflow()
    method = service.submit_case if action == "submit" else service.approve_case
    return await _call(
        method,
        case_id,
        expected_revision=body.expected_revision,
        operation_id=body.operation_id,
        actor=_conformity_actor(conformity, request),
    )


@router.post("/ai-conformity/cases/{case_id}/submit")
async def submit_ai_conformity_case(
    request: Request,
    case_id: str,
    body: schemas.AIConformityActionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    return await _ai_conformity_action(
        request,
        case_id,
        body,
        action="submit",
    )


@router.post("/ai-conformity/cases/{case_id}/approve")
async def approve_ai_conformity_case(
    request: Request,
    case_id: str,
    body: schemas.AIConformityActionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    return await _ai_conformity_action(
        request,
        case_id,
        body,
        action="approve",
    )


@router.get("/ai-conformity/cases/{case_id}/export")
async def export_ai_conformity_case(
    request: Request,
    case_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _conformity, service = await _configured_conformity_workflow()
    return await _call(service.export_package, case_id)


# Regulated-vertical evidence packages ------------------------------------


async def _configured_regulated_verticals():
    regulated = _module("ai_regulated_verticals", "ai_regulated_verticals")
    service = await _call(regulated.configured_regulated_verticals)
    return regulated, service


@router.get("/ai-regulated-verticals/packages")
async def list_ai_regulated_vertical_packages(
    request: Request,
    limit: int = Query(default=100, ge=1, le=250),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    items = await _call(service.list_packages, limit=limit)
    return {"packages": items, "count": len(items)}


@router.get("/ai-regulated-verticals/packages/{package_id}")
async def get_ai_regulated_vertical_package(
    request: Request,
    package_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    return await _record_call(service.get_package, package_id)


@router.post("/ai-regulated-verticals/packages", status_code=201)
async def create_ai_regulated_vertical_package(
    request: Request,
    body: schemas.AIRegulatedVerticalCreateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    return await _call(
        service.create_package,
        body.package_id,
        package_type=body.package_type,
        asset_id=body.asset_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-regulated-verticals/packages/{package_id}/versions")
async def version_ai_regulated_vertical_package(
    request: Request,
    package_id: str,
    body: schemas.AIRegulatedVerticalVersionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    return await _call(
        service.add_version,
        package_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-regulated-verticals/packages/{package_id}/approve")
async def approve_ai_regulated_vertical_package(
    request: Request,
    package_id: str,
    body: schemas.AIRegulatedVerticalApproveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    return await _call(
        service.approve_package,
        package_id,
        body.version,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
    )


@router.get("/ai-regulated-verticals/packages/{package_id}/export")
async def export_ai_regulated_vertical_package(
    request: Request,
    package_id: str,
    version: int | None = Query(default=None, ge=1, le=50),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _regulated, service = await _configured_regulated_verticals()
    return await _call(service.export_package, package_id, version=version)


# Bank model-risk-management evidence packages ----------------------------


async def _configured_bank_mrm():
    bank_mrm = _module("ai_bank_mrm", "ai_bank_mrm")
    service = await _call(bank_mrm.configured_bank_mrm)
    return bank_mrm, service


@router.get("/ai-bank-mrm/packages")
async def list_ai_bank_mrm_packages(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    items = await _call(service.list_packages, limit=limit)
    return {"packages": items, "count": len(items)}


@router.get("/ai-bank-mrm/packages/{package_id}")
async def get_ai_bank_mrm_package(
    request: Request,
    package_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    return await _record_call(service.get_package, package_id)


@router.post("/ai-bank-mrm/packages", status_code=201)
async def create_ai_bank_mrm_package(
    request: Request,
    body: schemas.AIBankMRMCreateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    return await _call(
        service.create_package,
        body.package_id,
        asset_id=body.asset_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-bank-mrm/packages/{package_id}/versions")
async def version_ai_bank_mrm_package(
    request: Request,
    package_id: str,
    body: schemas.AIBankMRMVersionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    return await _call(
        service.add_version,
        package_id,
        content=body.content,
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-bank-mrm/packages/{package_id}/approve")
async def approve_ai_bank_mrm_package(
    request: Request,
    package_id: str,
    body: schemas.AIBankMRMApproveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    return await _call(
        service.approve_package,
        package_id,
        body.version,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
    )


@router.get("/ai-bank-mrm/packages/{package_id}/export")
async def export_ai_bank_mrm_package(
    request: Request,
    package_id: str,
    version: int | None = Query(default=None, ge=1, le=50),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _bank_mrm, service = await _configured_bank_mrm()
    return await _call(service.export_package, package_id, version=version)


# Privacy-bounded benchmark exchange --------------------------------------


async def _configured_benchmark_exchange():
    benchmark = _module("ai_benchmark_exchange", "ai_benchmark_exchange")
    service = await _call(benchmark.configured_benchmark_exchange)
    return benchmark, service


@router.get("/ai-benchmark/contributions")
async def list_ai_benchmark_contributions(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    items = await _call(service.list_contributions, limit=limit)
    return {"contributions": items, "count": len(items)}


@router.get("/ai-benchmark/contributions/{contribution_id}")
async def get_ai_benchmark_contribution(
    request: Request,
    contribution_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _record_call(service.get_contribution, contribution_id)


@router.post("/ai-benchmark/contributions", status_code=201)
async def create_ai_benchmark_contribution(
    request: Request,
    body: schemas.AIBenchmarkContributionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _call(
        service.create_contribution,
        body.contribution_id,
        metric_code=body.metric_code,
        numerator=body.numerator,
        denominator=body.denominator,
        period=body.period.model_dump(),
        cohort=body.cohort.model_dump(),
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        voluntary_opt_in=body.voluntary_opt_in,
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-benchmark/contributions/{contribution_id}/versions")
async def version_ai_benchmark_contribution(
    request: Request,
    contribution_id: str,
    body: schemas.AIBenchmarkContributionVersionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _call(
        service.add_version,
        contribution_id,
        metric_code=body.metric_code,
        numerator=body.numerator,
        denominator=body.denominator,
        period=body.period.model_dump(),
        cohort=body.cohort.model_dump(),
        evidence_bindings=[item.model_dump() for item in body.evidence_bindings],
        voluntary_opt_in=body.voluntary_opt_in,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        actor=_human_principal(request),
    )


@router.post("/ai-benchmark/contributions/{contribution_id}/approve")
async def approve_ai_benchmark_contribution(
    request: Request,
    contribution_id: str,
    body: schemas.AIBenchmarkContributionApproveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _call(
        service.approve_contribution,
        contribution_id,
        body.version,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
    )


@router.get("/ai-benchmark/contributions/{contribution_id}/export")
async def export_ai_benchmark_contribution(
    request: Request,
    contribution_id: str,
    version: int | None = Query(default=None, ge=1, le=50),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _call(service.export_contribution, contribution_id, version=version)


@router.get("/ai-benchmark/cohort-reports")
async def list_ai_benchmark_cohort_reports(
    request: Request,
    limit: int = Query(default=100, ge=1, le=100),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    items = await _call(service.list_cohort_reports, limit=limit)
    return {"reports": items, "count": len(items)}


@router.get("/ai-benchmark/cohort-reports/{report_id}")
async def get_ai_benchmark_cohort_report(
    request: Request,
    report_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _record_call(service.get_cohort_report, report_id)


@router.post("/ai-benchmark/cohort-reports", status_code=201)
async def ingest_ai_benchmark_cohort_report(
    request: Request,
    body: schemas.AIBenchmarkCohortReportIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _benchmark, service = await _configured_benchmark_exchange()
    return await _call(
        service.ingest_cohort_report,
        body.model_dump(by_alias=True),
        actor=_human_principal(request),
    )


# Privacy-safe discovery sensor control plane ------------------------------


async def _configured_discovery_sensors():
    discovery = _module("ai_discovery_sensors", "ai_discovery_sensors")
    service = await _call(discovery.default_service)
    return discovery, service


def _discovery_actor(discovery, request: Request):
    return discovery.SensorActor(_human_principal(request))


@router.get("/ai-discovery-sensors/sensors")
async def list_ai_discovery_sensors(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _discovery, service = await _configured_discovery_sensors()
    items = await _call(service.list_sensors, limit=limit)
    return {"sensors": items, "count": len(items)}


@router.get("/ai-discovery-sensors/sensors/{sensor_id}")
async def get_ai_discovery_sensor(
    request: Request,
    sensor_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _discovery, service = await _configured_discovery_sensors()
    return await _record_call(service.get_sensor, sensor_id)


@router.post("/ai-discovery-sensors/sensors", status_code=201)
async def register_ai_discovery_sensor(
    request: Request,
    body: schemas.AIDiscoverySensorRegisterIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    return await _call(
        service.register_sensor,
        sensor_name=body.sensor_name,
        sensor_type=body.sensor_type,
        config_sha256=body.config_sha256,
        actor=_discovery_actor(discovery, request),
    )


@router.post("/ai-discovery-sensors/sensors/{sensor_id}/approve")
async def approve_ai_discovery_sensor(
    request: Request,
    sensor_id: str,
    body: schemas.AIDiscoverySensorApprovalIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    return await _call(
        service.approve_sensor,
        sensor_id,
        expected_revision=body.expected_revision,
        actor=_discovery_actor(discovery, request),
    )


@router.post("/ai-discovery-sensors/sensors/{sensor_id}/configuration")
async def configure_ai_discovery_sensor(
    request: Request,
    sensor_id: str,
    body: schemas.AIDiscoverySensorConfigurationIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    return await _call(
        service.prepare_sensor_configuration,
        sensor_id,
        config_sha256=body.config_sha256,
        expected_revision=body.expected_revision,
        actor=_discovery_actor(discovery, request),
    )


@router.post("/ai-discovery-sensors/sensors/{sensor_id}/revocation")
async def revoke_ai_discovery_sensor(
    request: Request,
    sensor_id: str,
    body: schemas.AIDiscoverySensorRevocationIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    return await _call(
        service.request_sensor_revocation,
        sensor_id,
        reason_code=body.reason_code,
        expected_revision=body.expected_revision,
        actor=_discovery_actor(discovery, request),
    )


@router.post("/ai-discovery-sensors/sensors/{sensor_id}/revocation/approve")
async def approve_ai_discovery_sensor_revocation(
    request: Request,
    sensor_id: str,
    body: schemas.AIDiscoverySensorApprovalIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    return await _call(
        service.approve_sensor_revocation,
        sensor_id,
        expected_revision=body.expected_revision,
        actor=_discovery_actor(discovery, request),
    )


@router.post("/ai-discovery-sensors/sensors/{sensor_id}/observations", status_code=201)
async def ingest_ai_discovery_observation(
    request: Request,
    sensor_id: str,
    body: schemas.AIDiscoveryObservationIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    discovery, service = await _configured_discovery_sensors()
    observation = discovery.NormalizedDiscoveryObservation(
        event_sha256=body.event_sha256,
        observed_at=body.observed_at,
        ai_service_code=body.ai_service_code,
        ai_product_code=body.ai_product_code,
        provider_code=body.provider_code,
        domain_category=body.domain_category,
        deployment_type=body.deployment_type,
        environment_code=body.environment_code,
        context_codes=tuple(body.context_codes),
    )
    return await _call(
        service.ingest_observation,
        sensor_id,
        sensor_revision=body.sensor_revision,
        config_sha256=body.config_sha256,
        observation=observation,
    )


@router.get("/ai-discovery-sensors/observations")
async def list_ai_discovery_observations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _discovery, service = await _configured_discovery_sensors()
    items = await _call(service.list_observations, limit=limit)
    return {"observations": items, "count": len(items)}


@router.get("/ai-discovery-sensors/observations/{observation_id}")
async def get_ai_discovery_observation(
    request: Request,
    observation_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _discovery, service = await _configured_discovery_sensors()
    return await _record_call(service.get_observation, observation_id)


@router.get("/ai-discovery-sensors/findings")
async def aggregate_ai_discovery_findings(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _discovery, service = await _configured_discovery_sensors()
    return await _call(service.aggregate_findings)


# Digest-only C2PA media provenance ---------------------------------------


async def _configured_media_provenance():
    provenance = _module("ai_media_provenance", "ai_media_provenance")
    service = await _call(provenance.configured_media_provenance)
    return provenance, service


def _media_provenance_actor(provenance, request: Request):
    return provenance.MediaProvenanceActor(
        "human",
        _human_principal(request),
    )


async def _read_media_upload(file: UploadFile) -> bytes:
    try:
        payload = await file.read(_MEDIA_PROVENANCE_UPLOAD_CAP + 1)
    finally:
        await file.close()
    if not payload:
        raise HTTPException(status_code=422, detail="media upload is empty")
    if len(payload) > _MEDIA_PROVENANCE_UPLOAD_CAP:
        raise HTTPException(
            status_code=413,
            detail="media upload exceeds the 64 MiB bound",
        )
    return payload


@router.get("/ai-media-provenance/capabilities")
async def get_ai_media_provenance_capabilities(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _provenance, service = await _configured_media_provenance()
    return await _call(service.capability_status)


@router.get("/ai-media-provenance/records")
async def list_ai_media_provenance_records(
    request: Request,
    asset_id: str | None = None,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _provenance, service = await _configured_media_provenance()
    items = await _call(service.list_records, asset_id=asset_id)
    return {"records": items, "count": len(items)}


@router.get("/ai-media-provenance/records/{provenance_id}")
async def get_ai_media_provenance_record(
    request: Request,
    provenance_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _provenance, service = await _configured_media_provenance()
    return await _record_call(service.get_record, provenance_id)


@router.get("/ai-media-provenance/records/{provenance_id}/export")
async def export_ai_media_provenance_record(
    request: Request,
    provenance_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _provenance, service = await _configured_media_provenance()
    return await _call(service.export_record, provenance_id)


@router.post("/ai-media-provenance/receipts/verification", status_code=201)
async def ingest_ai_media_verification_receipt(
    request: Request,
    body: schemas.AIMediaProvenanceReceiptIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    return await _call(
        service.ingest_verification_receipt,
        body.model_dump(by_alias=True),
        actor=_media_provenance_actor(provenance, request),
    )


@router.post("/ai-media-provenance/receipts/signing", status_code=201)
async def ingest_ai_media_signing_receipt(
    request: Request,
    body: schemas.AIMediaProvenanceReceiptIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    return await _call(
        service.ingest_signing_receipt,
        body.model_dump(by_alias=True),
        actor=_media_provenance_actor(provenance, request),
    )


@router.post("/ai-media-provenance/verify", status_code=201)
async def verify_ai_media_provenance(
    request: Request,
    file: UploadFile = File(...),
    media_kind: str = Form(...),
    asset_id: str = Form(...),
    deployment_id: str = Form(...),
    context_id: str = Form(...),
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    payload = await _read_media_upload(file)
    return await _call(
        service.verify_media,
        payload,
        media_kind=media_kind,
        asset_id=asset_id,
        deployment_id=deployment_id,
        context_id=context_id,
        actor=_media_provenance_actor(provenance, request),
    )


@router.post("/ai-media-provenance/sign")
async def sign_ai_media_provenance(
    request: Request,
    file: UploadFile = File(...),
    media_kind: str = Form(...),
    asset_id: str = Form(...),
    deployment_id: str = Form(...),
    context_id: str = Form(...),
    assertion_codes: str = Form(...),
) -> Response:
    require_global_permission(request, "admin")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    payload = await _read_media_upload(file)
    assertions = [value.strip() for value in assertion_codes.split(",") if value.strip()]
    result = await _call(
        service.sign_media,
        payload,
        media_kind=media_kind,
        asset_id=asset_id,
        deployment_id=deployment_id,
        context_id=context_id,
        assertion_codes=assertions,
        actor=_media_provenance_actor(provenance, request),
    )
    return Response(
        content=result.media,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": "attachment; filename=lightwork-c2pa-signed-media.bin",
            "X-Lightwork-Provenance-Id": result.record["provenance_id"],
            "X-Lightwork-Content-SHA256": result.record["content_sha256"],
        },
    )


@router.post("/ai-media-provenance/records/{provenance_id}/release")
async def prepare_ai_media_provenance_release(
    request: Request,
    provenance_id: str,
    body: schemas.AIMediaProvenanceReleasePrepareIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    return await _call(
        service.prepare_release_record,
        provenance_id,
        expected_revision=body.expected_revision,
        purpose_code=body.purpose_code,
        evidence_sha256=body.evidence_sha256,
        actor=_media_provenance_actor(provenance, request),
    )


@router.post("/ai-media-provenance/records/{provenance_id}/review")
async def review_ai_media_provenance_release(
    request: Request,
    provenance_id: str,
    body: schemas.AIMediaProvenanceReleaseReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    provenance, service = await _configured_media_provenance()
    return await _call(
        service.review_release_record,
        provenance_id,
        expected_revision=body.expected_revision,
        outcome=body.outcome,
        evidence_sha256=body.evidence_sha256,
        actor=_media_provenance_actor(provenance, request),
    )


# Read-only identity and data-governance connectors -----------------------


def _identity_governance_section() -> Mapping[str, Any]:
    from maverick import ai_identity_governance_connectors as connectors
    from maverick.config import config_source_errors, load_config

    errors = config_source_errors()
    config = load_config()
    if errors or not isinstance(config, Mapping):
        raise HTTPException(
            status_code=503,
            detail="identity-governance connector configuration is unreadable",
        )
    section = config.get("ai_identity_governance_connectors")
    try:
        active = connectors.enabled(section)
    except connectors.IdentityGovernanceConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if not active:
        raise HTTPException(
            status_code=403,
            detail=(
                "identity-governance connectors are disabled "
                "([ai_identity_governance_connectors] enable)"
            ),
        )
    assert isinstance(section, Mapping)
    return section


@router.get("/identity-governance/connectors")
async def get_identity_governance_connectors(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    from . import identity_governance_runtime

    section = _identity_governance_section()
    return await _call(identity_governance_runtime.connector_status, section)


@router.post("/identity-governance/connectors/{provider}/collect")
async def collect_identity_governance_connector(
    request: Request,
    provider: str,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _human_principal(request)
    from . import identity_governance_runtime

    section = _identity_governance_section()
    return await _call(
        identity_governance_runtime.collect_connector,
        section,
        provider,
    )


# Operational AI policy management ----------------------------------------


async def _configured_policy_management():
    policy = _module("ai_policy_management", "ai_policy_management")
    return policy, await _call(policy.configured_policy_management)


@router.get("/ai-policy-management/policies")
async def list_operational_ai_policies(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    items = await _call(service.list_policies)
    return {"policies": items, "count": len(items)}


@router.get("/ai-policy-management/policies/{policy_id}")
async def get_operational_ai_policy(
    request: Request,
    policy_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _record_call(service.get_policy, policy_id)


@router.post("/ai-policy-management/policies", status_code=201)
async def create_operational_ai_policy(
    request: Request,
    body: schemas.AIPolicyCreateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.create_policy,
        body.policy_id,
        title=body.title,
        description=body.description,
        statements=[item.model_dump() for item in body.statements],
        actor=_actor(request),
    )


@router.post("/ai-policy-management/policies/{policy_id}/versions")
async def add_operational_ai_policy_version(
    request: Request,
    policy_id: str,
    body: schemas.AIPolicyVersionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.add_policy_version,
        policy_id,
        title=body.title,
        description=body.description,
        statements=[item.model_dump() for item in body.statements],
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


@router.post("/ai-policy-management/policies/{policy_id}/activate")
async def activate_operational_ai_policy(
    request: Request,
    policy_id: str,
    body: schemas.AIPolicyActivateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.activate_policy,
        policy_id,
        body.version,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
    )


@router.get("/ai-policy-management/exceptions")
async def list_operational_ai_policy_exceptions(
    request: Request,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    items = await _call(service.list_exceptions)
    return {"exceptions": items, "count": len(items)}


@router.get("/ai-policy-management/exceptions/{exception_key}")
async def get_operational_ai_policy_exception(
    request: Request,
    exception_key: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _record_call(service.get_exception, exception_key)


@router.post("/ai-policy-management/exceptions", status_code=201)
async def request_operational_ai_policy_exception(
    request: Request,
    body: schemas.AIPolicyExceptionRequestIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.request_exception,
        body.exception_key,
        policy_id=body.policy_id,
        policy_version=body.policy_version,
        scope=body.scope.model_dump(),
        reason=body.reason,
        compensating_control_ids=body.compensating_control_ids,
        expires_at=body.expires_at,
        requester=_human_principal(request),
    )


@router.post("/ai-policy-management/exceptions/{exception_key}/approve")
async def approve_operational_ai_policy_exception(
    request: Request,
    exception_key: str,
    body: schemas.AIPolicyExceptionApproveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.approve_exception,
        exception_key,
        expected_revision=body.expected_revision,
        approver=_human_principal(request),
    )


@router.post("/ai-policy-management/exceptions/{exception_key}/close")
async def close_operational_ai_policy_exception(
    request: Request,
    exception_key: str,
    body: schemas.AIPolicyExceptionCloseIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.close_exception,
        exception_key,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
        reason=body.reason,
    )


@router.post("/ai-policy-management/preview")
async def preview_operational_ai_policy(
    request: Request,
    body: schemas.AIPolicyPreviewIn,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(service.preview, body.context.model_dump())


@router.get("/ai-policy-management/evaluations")
async def list_operational_ai_policy_evaluations(
    request: Request,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    items = await _call(service.list_evaluations)
    return {"evaluations": items, "count": len(items)}


@router.get("/ai-policy-management/evaluations/{evaluation_key}")
async def get_operational_ai_policy_evaluation(
    request: Request,
    evaluation_key: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _record_call(service.get_evaluation, evaluation_key)


@router.post("/ai-policy-management/evaluations")
async def evaluate_operational_ai_policy(
    request: Request,
    body: schemas.AIPolicyEvaluateIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.evaluate,
        body.evaluation_key,
        body.context.model_dump(),
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


@router.post("/ai-policy-management/evaluations/{evaluation_key}/attest")
async def attest_operational_ai_policy_evaluation(
    request: Request,
    evaluation_key: str,
    body: schemas.AIPolicyAttestIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    _policy, service = await _configured_policy_management()
    return await _call(
        service.attest_evaluation,
        evaluation_key,
        expected_revision=body.expected_revision,
        actor=_human_principal(request),
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


@router.get("/policy-packs/{pack_id}/changes")
async def policy_pack_change_detail(
    request: Request,
    pack_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    packs = _module("policy_packs", "policy_packs")
    return await _record_call(packs.get_subscription_change_detail, pack_id)


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


@router.post(
    "/policy-packs/subscriptions/{subscription_id}/controls/{control_id}/attest"
)
async def attest_policy_pack_control(
    request: Request,
    subscription_id: str,
    control_id: str,
    body: schemas.PolicyPackAttestIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    human = _human_principal(request)
    packs = _module("policy_packs", "policy_packs")
    result = await _call(
        packs.attest_control,
        subscription_id,
        control_id,
        evidence_bindings=[
            binding.model_dump() for binding in body.evidence_bindings
        ],
        evidence_resolver=_policy_pack_evidence_resolver(actor=human),
        expected_revision=body.expected_revision,
        actor=human,
        idempotency_key=body.idempotency_key,
    )
    detail = await _record_call(
        packs.get_subscription_change_detail,
        result["subscription"]["pack_id"],
    )
    return {
        "subscription": detail,
        "idempotent": bool(result.get("idempotent")),
    }


@router.get("/ai-regulatory-feed/notices")
async def ai_regulatory_notices(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return {"notices": await _call(bridge.list_notices, limit=limit)}


@router.get("/ai-regulatory-feed/notices/{notice_id}")
async def ai_regulatory_notice(
    request: Request,
    notice_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return await _record_call(bridge.get_notice, notice_id)


@router.get("/ai-regulatory-feed/staleness")
async def ai_regulatory_staleness(
    request: Request,
    evidence_id: str = "",
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return {
        "staleness": await _call(
            bridge.list_active_staleness,
            evidence_id=evidence_id or None,
            limit=limit,
        )
    }


@router.post("/ai-regulatory-feed/alerts/{alert_id}/sync", status_code=201)
async def sync_ai_regulatory_alert(
    request: Request,
    alert_id: str,
    body: schemas.AIRegulatorySyncIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return await _call(
        bridge.sync_finance_alert,
        alert_id,
        rules=[rule.model_dump() for rule in body.rules],
        actor=_actor(request),
        deliver=body.deliver,
    )


@router.post("/ai-regulatory-feed/notices/{notice_id}/deliver")
async def deliver_ai_regulatory_notice(
    request: Request,
    notice_id: str,
    body: schemas.AIRegulatoryNoticeDeliveryIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return await _record_call(
        bridge.deliver_notice,
        notice_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


@router.post("/ai-regulatory-feed/staleness/{staleness_id}/resolve")
async def resolve_ai_regulatory_staleness(
    request: Request,
    staleness_id: str,
    body: schemas.AIRegulatoryStalenessResolveIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    bridge = _module("ai_regulatory_feed", "ai_regulatory_feed")
    return await _record_call(
        bridge.resolve_staleness,
        staleness_id,
        expected_revision=body.expected_revision,
        resolution=body.resolution,
        actor=_actor(request),
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


# AI business intake --------------------------------------------------------


@router.get("/ai-business-intake/drafts")
async def list_ai_business_intakes(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    items = await _call(intake.list_intakes)
    return {"intakes": items, "count": len(items)}


@router.get("/ai-business-intake/submissions")
async def list_submitted_ai_business_intakes(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    items = [
        item
        for item in await _call(intake.list_intakes)
        if item.get("status") == "pending_review"
    ]
    return {"submissions": items, "count": len(items)}


@router.post("/ai-business-intake/drafts", status_code=201)
async def start_ai_business_intake(
    request: Request,
    body: schemas.AIBusinessIntakeStartIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    return await _call(
        intake.start_intake,
        idempotency_key=body.idempotency_key,
        initial=body.initial,
        actor=_actor(request),
    )


@router.get("/ai-business-intake/drafts/{intake_id}")
async def get_ai_business_intake(
    request: Request,
    intake_id: str,
    include_submission: bool = True,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    return await _record_call(
        intake.get_intake,
        intake_id,
        include_submission=include_submission,
    )


@router.patch("/ai-business-intake/drafts/{intake_id}")
async def save_ai_business_intake(
    request: Request,
    intake_id: str,
    body: schemas.AIBusinessIntakeSaveIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    return await _call(
        intake.save_intake,
        intake_id,
        patch=body.patch,
        operation_id=body.operation_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


@router.post("/ai-business-intake/drafts/{intake_id}/submit")
async def submit_ai_business_intake(
    request: Request,
    intake_id: str,
    body: schemas.AIBusinessIntakeSubmitIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    intake = _module("ai_business_intake", "ai_business_intake")
    return await _call(
        intake.submit_intake,
        intake_id,
        operation_id=body.operation_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


# AI cost and ROI -----------------------------------------------------------


def _ai_cost_collector_runtime() -> dict[str, Any]:
    """Construct enabled collectors without exposing scopes or secret references."""

    from maverick.ai_cost_collectors import (
        AICostCollectorConfigError,
        configured_collector_limits,
        configured_collector_schedule,
        configured_collectors,
    )
    from maverick.config import config_source_errors, load_config

    if config_source_errors(include_tenant=True):
        raise AICostCollectorConfigError(
            "AI cost collector configuration is unreadable"
        )
    config = load_config() or {}
    if not isinstance(config, Mapping):
        raise AICostCollectorConfigError("AI cost configuration must be a table")
    section = config.get("ai_cost_roi", {})
    if not isinstance(section, Mapping):
        raise AICostCollectorConfigError("ai_cost_roi must be a table")
    return {
        "collectors": configured_collectors(section),
        "schedule": configured_collector_schedule(section),
        "limits": configured_collector_limits(section),
    }


@router.get("/ai-cost-roi/collectors")
async def get_ai_cost_collectors(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    _module("ai_cost_roi", "ai_cost_roi")
    runtime = await _call(_ai_cost_collector_runtime)
    schedule = runtime["schedule"]
    limits = runtime["limits"]
    return {
        "collectors": [
            {
                "provider": collector.provider.value,
                "collector_id": collector.collector_id,
            }
            for collector in runtime["collectors"]
        ],
        "schedule": {
            "poll_seconds": schedule.poll_seconds,
            "lookback_days": schedule.lookback_days,
        },
        "limits": {
            "timeout_seconds": limits.timeout_seconds,
            "page_cap": limits.page_cap,
            "record_cap": limits.record_cap,
            "max_period_days": limits.max_period_days,
            "page_size": limits.page_size,
            "max_bytes_billed": limits.max_bytes_billed,
        },
        "credentials_exposed": False,
    }


@router.post("/ai-cost-roi/collectors/{provider}/collect")
async def collect_ai_cost_provider(
    request: Request,
    provider: str,
    body: schemas.AICostCollectIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    actor = _human_principal(request)
    cost = _module("ai_cost_roi", "ai_cost_roi")
    provider_name = str(provider or "").strip().lower()
    if len(provider_name) > 32 or provider_name not in {
        item.value for item in cost.BillingProvider
    }:
        raise HTTPException(status_code=422, detail="unknown AI cost provider")
    runtime = await _call(_ai_cost_collector_runtime)
    collector = next(
        (
            item
            for item in runtime["collectors"]
            if item.provider.value == provider_name
        ),
        None,
    )
    if collector is None:
        raise HTTPException(status_code=403, detail="AI cost collector is not enabled")
    collection_request = await _call(cost.CollectionRequest, **body.model_dump())
    return await _call(
        cost.default_service.collect_and_ingest,
        collector,
        collection_request,
        actor=actor,
    )


def _ai_cost_citation(cost, value: schemas.AICostCitationIn):
    return cost.SourceCitation(**value.model_dump())


def _ai_cost_batch(cost, body: schemas.AICostBatchIn):
    provider = cost.BillingProvider(body.provider)
    records = []
    for item in body.records:
        records.append(cost.NormalizedBillingRecord(
            provider=provider,
            source_account_id=body.source_account_id,
            source_record_id=item.source_record_id,
            period_start=item.period_start,
            period_end=item.period_end,
            currency=item.currency,
            cost=item.cost,
            usage=tuple(
                cost.UsageQuantity(**usage.model_dump())
                for usage in item.usage
            ),
            service=item.service,
            resource_id=item.resource_id,
            citation=_ai_cost_citation(cost, item.citation),
            region=item.region,
            registry_asset_id=item.registry_asset_id,
            correction_of_source_record_id=item.correction_of_source_record_id,
            correction_reason=item.correction_reason,
        ))
    return cost.BillingCollection(
        provider=provider,
        collector_id=body.collector_id,
        source_account_id=body.source_account_id,
        batch_id=body.batch_id,
        period_start=body.period_start,
        period_end=body.period_end,
        collected_at=body.collected_at,
        manifest_citation=_ai_cost_citation(cost, body.manifest_citation),
        records=tuple(records),
    )


@router.post("/ai-cost-roi/observations/batches", status_code=201)
async def ingest_ai_cost_batch(
    request: Request,
    body: schemas.AICostBatchIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    return await _call(
        cost.default_service.ingest,
        _ai_cost_batch(cost, body),
        actor=_actor(request),
    )


@router.post("/ai-cost-roi/corrections", status_code=201)
async def ingest_ai_cost_corrections(
    request: Request,
    body: schemas.AICostBatchIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    if any(
        not item.correction_of_source_record_id or not item.correction_reason
        for item in body.records
    ):
        raise HTTPException(
            status_code=422,
            detail="every correction row must identify its original and reason",
        )
    cost = _module("ai_cost_roi", "ai_cost_roi")
    return await _call(
        cost.default_service.ingest,
        _ai_cost_batch(cost, body),
        actor=_actor(request),
    )


@router.get("/ai-cost-roi/receipts")
async def list_ai_cost_receipts(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    items = await _call(cost.default_service.list_receipts)
    return {"receipts": items, "count": len(items)}


def _ai_cost_report_kwargs(
    period_start: str,
    period_end: str,
    scenario: str | None,
    row_limit: int,
) -> dict[str, Any]:
    return {
        "period_start": period_start,
        "period_end": period_end,
        "scenario": scenario,
        "row_limit": row_limit,
    }


@router.get("/ai-cost-roi/portfolio")
async def get_ai_cost_portfolio_report(
    request: Request,
    period_start: str = Query(..., min_length=1, max_length=64),
    period_end: str = Query(..., min_length=1, max_length=64),
    scenario: str | None = Query(None, max_length=128),
    row_limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    return await _call(
        cost.default_service.portfolio_report,
        **_ai_cost_report_kwargs(period_start, period_end, scenario, row_limit),
    )


@router.get("/ai-cost-roi/departments/{department_id}")
async def get_ai_cost_department_report(
    request: Request,
    department_id: str,
    period_start: str = Query(..., min_length=1, max_length=64),
    period_end: str = Query(..., min_length=1, max_length=64),
    scenario: str | None = Query(None, max_length=128),
    row_limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    return await _call(
        cost.default_service.department_report,
        department_id,
        **_ai_cost_report_kwargs(period_start, period_end, scenario, row_limit),
    )


@router.get("/ai-cost-roi/assets/{asset_id}")
async def get_ai_cost_asset_report(
    request: Request,
    asset_id: str,
    period_start: str = Query(..., min_length=1, max_length=64),
    period_end: str = Query(..., min_length=1, max_length=64),
    scenario: str | None = Query(None, max_length=128),
    row_limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    return await _call(
        cost.default_service.asset_report,
        asset_id,
        **_ai_cost_report_kwargs(period_start, period_end, scenario, row_limit),
    )


@router.put("/ai-cost-roi/roi-assumptions/{assumption_key}")
async def upsert_ai_cost_roi_assumption(
    request: Request,
    assumption_key: str,
    body: schemas.AICostROIAssumptionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    cost = _module("ai_cost_roi", "ai_cost_roi")
    payload = body.model_dump()
    payload["citations"] = [
        _ai_cost_citation(cost, item) for item in body.citations
    ]
    return await _call(
        cost.default_service.upsert_roi_assumption,
        scenario=assumption_key,
        **payload,
        actor=_actor(request),
    )


# AI literacy ---------------------------------------------------------------


@router.get("/ai-literacy/requirements")
async def list_ai_literacy_requirements(
    request: Request,
    include_retired: bool = False,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    items = await _call(
        literacy.default_register.list_requirements,
        include_retired=include_retired,
    )
    return {"requirements": items, "count": len(items)}


@router.put("/ai-literacy/requirements/{requirement_key}")
async def upsert_ai_literacy_requirement(
    request: Request,
    requirement_key: str,
    body: schemas.AILiteracyRequirementIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.upsert_requirement,
        requirement_key,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.get("/ai-literacy/people/{person_id}/assignment")
async def get_ai_literacy_assignment(
    request: Request,
    person_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _record_call(
        literacy.default_register.get_person_assignment,
        person_id,
    )


@router.put("/ai-literacy/people/{person_id}/assignment")
async def set_ai_literacy_assignment(
    request: Request,
    person_id: str,
    body: schemas.AILiteracyAssignmentIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.set_person_assignments,
        person_id,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post(
    "/ai-literacy/people/{person_id}/requirements/{requirement_key}/completions",
    status_code=201,
)
async def record_ai_literacy_completion(
    request: Request,
    person_id: str,
    requirement_key: str,
    body: schemas.AILiteracyCompletionIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.record_completion,
        person_id,
        requirement_key,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post(
    "/ai-literacy/people/{person_id}/requirements/{requirement_key}/waivers",
    status_code=201,
)
async def grant_ai_literacy_waiver(
    request: Request,
    person_id: str,
    requirement_key: str,
    body: schemas.AILiteracyWaiverGrantIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.grant_waiver,
        person_id,
        requirement_key,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.post("/ai-literacy/waivers/{waiver_id}/revoke")
async def revoke_ai_literacy_waiver(
    request: Request,
    waiver_id: str,
    body: schemas.AILiteracyWaiverRevokeIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.revoke_waiver,
        waiver_id,
        **body.model_dump(),
        actor=_actor(request),
    )


@router.get("/ai-literacy/people/{person_id}/status")
async def get_ai_literacy_status(
    request: Request,
    person_id: str,
    as_of: float | None = Query(None, gt=0),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.compute_gaps,
        person_id,
        as_of=as_of,
    )


@router.get("/ai-literacy/people/{person_id}/receipts")
async def get_ai_literacy_receipts(
    request: Request,
    person_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    completions, waivers = await asyncio.gather(
        _call(literacy.default_register.list_completions, person_id),
        _call(literacy.default_register.list_waivers, person_id),
    )
    return {"completions": completions, "waivers": waivers}


@router.post("/ai-literacy/evidence-export")
async def export_ai_literacy_evidence(
    request: Request,
    body: schemas.AILiteracyEvidenceExportIn,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    literacy = _module("ai_literacy", "ai_literacy")
    return await _call(
        literacy.default_register.export_evidence,
        **body.model_dump(),
    )


# EU AI Act Article 73 incidents --------------------------------------------


def _article_73_authority_binding(request: Request):
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    principal = _human_principal(request)
    try:
        from maverick.config import config_source_errors, load_config

        if config_source_errors():
            raise HTTPException(
                status_code=503,
                detail="Article 73 authority directory is unreadable",
            )
        config = load_config()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Article 73 authority directory is unavailable",
        ) from exc
    section = config.get("eu_ai_incidents") if isinstance(config, Mapping) else None
    entries = section.get("authorities", []) if isinstance(section, Mapping) else []
    if not isinstance(entries, list) or len(entries) > 64:
        raise HTTPException(
            status_code=503,
            detail="Article 73 authority directory is malformed",
        )
    matches = [
        entry
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("principal") == principal
    ]
    if len(matches) != 1:
        raise HTTPException(
            status_code=403,
            detail="the authenticated user has no unique Article 73 authority binding",
        )
    entry = matches[0]
    if set(entry) != {"principal", "role", "authority_refs"}:
        raise HTTPException(
            status_code=503,
            detail="Article 73 authority binding is malformed",
        )
    refs = entry.get("authority_refs")
    if (
        not isinstance(refs, list)
        or not 1 <= len(refs) <= 64
        or any(not isinstance(ref, str) or not ref.strip() for ref in refs)
    ):
        raise HTTPException(
            status_code=503,
            detail="Article 73 authority references are malformed",
        )
    try:
        authority = incidents.HumanAuthority(principal, entry.get("role"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="Article 73 authority role is malformed",
        ) from exc
    return authority, list(dict.fromkeys(refs))


async def _article_73_safe(incidents, incident_id: str) -> dict[str, Any]:
    return await _record_call(incidents.get_safe_projection, incident_id)


@router.get("/eu-ai-incidents")
async def list_eu_ai_incidents(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    items = await _call(incidents.list_incidents, limit=limit)
    return {"incidents": items, "count": len(items)}


@router.get("/eu-ai-incidents/report-template")
async def get_article_73_report_template(
    request: Request,
    kind: str = Query("initial", pattern="^(initial|update|final)$"),
) -> dict[str, Any]:
    require_permission(request, "view")
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    return await _call(incidents.report_template, kind)


@router.get("/eu-ai-incidents/{incident_id}")
async def get_eu_ai_incident(
    request: Request,
    incident_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    return await _article_73_safe(incidents, incident_id)


@router.post("/eu-ai-incidents", status_code=201)
async def open_eu_ai_incident(
    request: Request,
    body: schemas.Article73IncidentOpenIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    payload = body.model_dump()
    payload["evidence_refs"] = [
        item.model_dump() for item in body.evidence_refs
    ]
    record = await _call(
        incidents.open_incident,
        payload.pop("title"),
        **payload,
        opened_by=_actor(request),
    )
    return await _article_73_safe(incidents, record["id"])


@router.post("/eu-ai-incidents/{incident_id}/routing")
async def route_eu_ai_incident(
    request: Request,
    incident_id: str,
    body: schemas.Article73RoutingIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    payload = body.model_dump()
    authority, authority_refs = _article_73_authority_binding(request)
    if body.decision != "report_to_authority":
        authority_refs = []
    await _call(
        incidents.record_routing_decision,
        incident_id,
        **payload,
        authority_refs=authority_refs,
        authority=authority,
    )
    return await _article_73_safe(incidents, incident_id)


@router.post("/eu-ai-incidents/{incident_id}/reports", status_code=201)
async def draft_eu_ai_incident_report(
    request: Request,
    incident_id: str,
    body: schemas.Article73ReportDraftIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    authority, _authority_refs = _article_73_authority_binding(request)
    await _call(
        incidents.draft_report,
        incident_id,
        **body.model_dump(),
        prepared_by=authority.principal,
    )
    return await _article_73_safe(incidents, incident_id)


@router.post("/eu-ai-incidents/{incident_id}/reports/{report_id}/approve")
async def approve_eu_ai_incident_report(
    request: Request,
    incident_id: str,
    report_id: str,
    body: schemas.Article73AuthorityIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    authority, _authority_refs = _article_73_authority_binding(request)
    await _call(
        incidents.approve_report,
        incident_id,
        report_id,
        expected_revision=body.expected_revision,
        authority=authority,
    )
    return await _article_73_safe(incidents, incident_id)


@router.post("/eu-ai-incidents/{incident_id}/reports/{report_id}/submission")
async def record_eu_ai_incident_submission(
    request: Request,
    incident_id: str,
    report_id: str,
    body: schemas.Article73SubmissionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    payload = body.model_dump()
    authority, _authority_refs = _article_73_authority_binding(request)
    await _call(
        incidents.record_submission,
        incident_id,
        report_id,
        **payload,
        authority=authority,
    )
    return await _article_73_safe(incidents, incident_id)


@router.get("/eu-ai-incidents/{incident_id}/reports/{report_id}/payload")
async def get_eu_ai_incident_report_payload(
    request: Request,
    incident_id: str,
    report_id: str,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    incidents = _module("eu_ai_incidents", "eu_ai_incidents")
    return await _record_call(
        incidents.get_report_payload,
        incident_id,
        report_id,
    )


# Semantic GRC writeback -----------------------------------------------------


def _grc_source(grc, body: schemas.GRCOutboundSourceIn):
    data = body.model_dump()
    common = {
        "asset_id": data["asset_id"],
        "severity": data["severity"],
        "source_revision": data["source_revision"],
        "source_digest": data["source_digest"],
    }
    if body.source_kind == "finding":
        return grc.FindingSource(
            finding_id=data["source_id"],
            finding_type=data["finding_type"],
            summary=data["summary"],
            **common,
        )
    if body.source_kind == "risk_acceptance":
        return grc.RiskAcceptanceSource(
            acceptance_id=data["source_id"],
            finding_id=data["finding_id"],
            rationale=data["rationale"],
            reviewer=data["reviewer"],
            decided_at=data["decided_at"],
            expires_at=data["expires_at"],
            status=data["acceptance_status"],
            **common,
        )
    return grc.AIIncidentSource(
        incident_id=data["source_id"],
        incident_source_id=data["incident_source_id"],
        summary=data["summary"],
        status=data["incident_status"],
        occurred_at=data["occurred_at"],
        resolution=data["resolution"],
        **common,
    )


@router.post("/grc-outbound/{target}/preview")
async def preview_grc_outbound_sync(
    request: Request,
    target: str,
    body: schemas.GRCOutboundSourceIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    _require_active_tenant()
    grc = _module("grc_outbound_sync", "grc_outbound_sync")
    try:
        system = grc.GRCSystem(target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unsupported GRC target") from exc
    service = grc.configured_sync()
    return await _call(service.preview, _grc_source(grc, body), target=system)


@router.post("/grc-outbound/{target}/sync")
async def execute_grc_outbound_sync(
    request: Request,
    target: str,
    body: schemas.GRCOutboundSourceIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    _require_active_tenant()
    human = _human_principal(request)
    grc = _module("grc_outbound_sync", "grc_outbound_sync")
    try:
        system = grc.GRCSystem(target)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unsupported GRC target") from exc
    service = grc.configured_sync()
    source = _grc_source(grc, body)
    # Refuse an unadmitted target before attempting endpoint or credential
    # resolution. The preview path applies the same strict semantic policy.
    await _call(service.preview, source, target=system)
    from maverick import grc_vendor_transports

    transport = await _call(
        grc_vendor_transports.transport_from_config,
        system,
    )
    return await _call(
        service.sync,
        source,
        target=system,
        transport=transport,
        actor=human,
    )


@router.get("/grc-outbound/{target}/{source_kind}/{source_id}")
async def get_grc_outbound_state(
    request: Request,
    target: str,
    source_kind: str,
    source_id: str,
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    grc = _module("grc_outbound_sync", "grc_outbound_sync")
    try:
        system = grc.GRCSystem(target)
        kind = grc.SourceKind(source_kind)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unsupported GRC identity") from exc
    service = grc.configured_sync()
    return await _record_call(
        service.get_state,
        target=system,
        source_kind=kind,
        source_id=source_id,
    )


@router.get("/grc-outbound/events")
async def list_grc_outbound_events(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "view")
    _require_active_tenant()
    grc = _module("grc_outbound_sync", "grc_outbound_sync")
    events = await _call(grc.configured_sync().list_events, limit=limit)
    return {"events": events, "count": len(events)}
