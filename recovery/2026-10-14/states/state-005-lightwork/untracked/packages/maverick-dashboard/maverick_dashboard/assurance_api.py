"""Operator API for platform, model, agent-trust, and product assurance."""
from __future__ import annotations

import asyncio
import importlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import api_schemas as schemas
from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/assurance", tags=["platform-assurance"])


def _actor(request: Request) -> str:
    from .api import _request_actor

    return _request_actor(request)


def _module(name: str, knob: str):
    module = importlib.import_module(f"maverick.{name}")
    if not module.enabled():
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
        from maverick.privacy_ops import RecordConflict

        if isinstance(exc, RecordConflict):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise HTTPException(
            status_code=503,
            detail=f"assurance operation failed safely ({type(exc).__name__})",
        ) from exc


async def _record_call(fn: Callable, *args, **kwargs) -> dict[str, Any]:
    record = await _call(fn, *args, **kwargs)
    if record is None:
        raise HTTPException(status_code=404, detail="no such assurance record")
    return record


@router.get("/summary")
async def assurance_summary(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    from maverick import (
        agent_trust_auditor,
        cra_ops,
        evidence_factory,
        evidence_graph,
        model_risk_assurance,
    )

    enabled = {
        "evidence_graph": evidence_graph.enabled(),
        "evidence_factory": evidence_factory.enabled(),
        "agent_trust_auditor": agent_trust_auditor.enabled(),
        "cra_ops": cra_ops.enabled(),
        "model_risk_assurance": model_risk_assurance.enabled(),
    }
    calls = (
        (enabled["evidence_graph"], evidence_graph.list_nodes),
        (enabled["evidence_factory"], evidence_factory.list_campaigns),
        (enabled["agent_trust_auditor"], agent_trust_auditor.list_observed_estate),
        (enabled["agent_trust_auditor"], agent_trust_auditor.findings),
        (enabled["cra_ops"], cra_ops.list_products),
        (enabled["cra_ops"], cra_ops.list_cases),
        (enabled["model_risk_assurance"], model_risk_assurance.list_inventory),
        (enabled["model_risk_assurance"], model_risk_assurance.list_evidence),
        (enabled["model_risk_assurance"], model_risk_assurance.findings),
    )

    async def _count(active, fn):
        return len(await _call(fn)) if active else 0

    values = await asyncio.gather(*(_count(active, fn) for active, fn in calls))
    counts = dict(zip(
        (
            "evidence_nodes",
            "evidence_campaigns",
            "observed_agents_and_services",
            "trust_findings",
            "cra_products",
            "cra_cases",
            "model_risk_assets",
            "model_risk_evidence",
            "model_risk_findings",
        ),
        values,
        strict=True,
    ))
    return {"enabled": enabled, "counts": counts}


# Continuous evidence -------------------------------------------------------

@router.get("/evidence/nodes")
async def evidence_nodes(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    graph = _module("evidence_graph", "evidence_graph")
    return {"nodes": await _call(graph.list_nodes)}


@router.get("/evidence/coverage")
async def evidence_coverage(request: Request, controls: str = "") -> dict[str, Any]:
    require_permission(request, "view")
    graph = _module("evidence_graph", "evidence_graph")
    required = [value.strip() for value in controls.split(",") if value.strip()]
    if len(required) > 512:
        raise HTTPException(status_code=422, detail="too many requested controls")
    return await _call(graph.coverage_report, required)


@router.post("/evidence/{node_id}/decision")
async def decide_evidence(
    request: Request,
    node_id: str,
    body: schemas.EvidenceReviewIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    graph = _module("evidence_graph", "evidence_graph")
    return await _record_call(
        graph.decide,
        node_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


# Agent Trust Auditor -------------------------------------------------------

@router.post("/agent-trust/refresh")
async def refresh_agent_trust(request: Request) -> dict[str, Any]:
    require_global_permission(request, "admin")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    return await _call(auditor.refresh_local_projection, actor=_actor(request))


@router.get("/agent-trust/estate")
async def agent_trust_estate(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    observed, declared = await _call(auditor.list_observed_estate), await _call(
        auditor.list_declarations
    )
    return {"observed": observed, "declared": declared}


@router.get("/agent-trust/findings")
async def agent_trust_findings(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    return {"findings": await _call(auditor.findings)}


@router.post("/agent-trust/declarations")
async def declare_agent_trust(
    request: Request,
    body: schemas.TrustDeclarationIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    return await _call(
        auditor.declare_entity,
        body.entity_id,
        owner=body.owner,
        purpose=body.purpose,
        risk_tier=body.risk_tier,
        lifecycle=body.lifecycle,
        allowed_tools=body.allowed_tools,
        actor=_actor(request),
    )


@router.patch("/agent-trust/declarations/{entity_id}")
async def update_agent_trust_declaration(
    request: Request,
    entity_id: str,
    body: schemas.TrustDeclarationUpdateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    changes = body.model_dump(exclude_none=True)
    expected_revision = changes.pop("expected_revision")
    return await _record_call(
        auditor.update_declaration,
        entity_id,
        expected_revision=expected_revision,
        actor=_actor(request),
        **changes,
    )


@router.get("/agent-trust/evidence-pack")
async def agent_trust_pack(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    auditor = _module("agent_trust_auditor", "agent_trust_auditor")
    return await _call(auditor.render_evidence_pack, actor=_actor(request))


# Production evidence factory ----------------------------------------------

@router.get("/evidence-factory/campaigns")
async def evidence_campaigns(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    factory = _module("evidence_factory", "evidence_factory")
    return {"campaigns": await _call(factory.list_campaigns)}


@router.post("/evidence-factory/campaigns")
async def start_evidence_campaign(
    request: Request,
    body: schemas.EvidenceCampaignIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    provenance = await _call(
        factory.build_provenance,
        commit=body.commit,
        image_digest=body.image_digest,
        deployment_id=body.deployment_id,
    )
    objectives = factory.ServiceObjectives(
        slo=body.slo,
        rto_seconds=body.rto_seconds,
        rpo_seconds=body.rpo_seconds,
    )
    return await _call(
        factory.start_campaign,
        title=body.title,
        provenance=provenance,
        objectives=objectives,
        controls=body.controls,
        actor=_actor(request),
    )


@router.post("/evidence-factory/campaigns/{campaign_id}/collect")
async def collect_evidence_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.RevisionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    collectors = await _call(factory.default_collectors)
    return await _call(
        factory.collect_campaign,
        campaign_id,
        expected_revision=body.expected_revision,
        collectors=collectors,
        actor=_actor(request),
    )


@router.post("/evidence-factory/campaigns/{campaign_id}/finalize")
async def finalize_evidence_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.RevisionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    return await _call(
        factory.finalize_campaign,
        campaign_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
    )


@router.post("/evidence-factory/campaigns/{campaign_id}/recover")
async def recover_evidence_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.EvidenceClaimActionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    return await _call(
        factory.recover_stale_campaign,
        campaign_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        reason=body.reason,
    )


@router.post("/evidence-factory/campaigns/{campaign_id}/abort")
async def abort_evidence_campaign(
    request: Request,
    campaign_id: str,
    body: schemas.EvidenceClaimActionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    return await _call(
        factory.abort_campaign,
        campaign_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        reason=body.reason,
    )


@router.post("/evidence-factory/drills/{drill_id}/recover")
async def recover_evidence_drill(
    request: Request,
    drill_id: str,
    body: schemas.EvidenceClaimActionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    return await _call(
        factory.recover_stale_drill,
        drill_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        reason=body.reason,
    )


@router.post("/evidence-factory/drills/{drill_id}/abort")
async def abort_evidence_drill(
    request: Request,
    drill_id: str,
    body: schemas.EvidenceClaimActionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    factory = _module("evidence_factory", "evidence_factory")
    return await _call(
        factory.abort_drill,
        drill_id,
        expected_revision=body.expected_revision,
        actor=_actor(request),
        reason=body.reason,
    )


# Model Risk & AI Assurance Officer ----------------------------------------

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
    payload = body.model_dump(exclude_none=True)
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


# CRA product security ------------------------------------------------------

@router.get("/cra/products")
async def cra_products(request: Request) -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return {"products": await _call(cra.list_products)}


@router.post("/cra/products")
async def create_cra_product(
    request: Request,
    body: schemas.CraProductIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.create_product, **body.model_dump(), actor=_actor(request))


@router.post("/cra/products/{product_id}/applicability")
async def decide_cra_product(
    request: Request,
    product_id: str,
    body: schemas.CraDecisionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.decide_product_applicability,
        product_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/cra/releases")
async def cra_releases(request: Request, product_id: str = "") -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return {"releases": await _call(cra.list_releases, product_id=product_id)}


@router.post("/cra/releases")
async def create_cra_release(
    request: Request,
    body: schemas.CraReleaseIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.create_release, **body.model_dump(), actor=_actor(request))


@router.post("/cra/releases/{release_id}/sbom")
async def attach_cra_sbom(
    request: Request,
    release_id: str,
    body: schemas.CraSbomIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.attach_sbom,
        release_id,
        body.document,
        source=body.source,
        actor=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/cra/cases")
async def cra_cases(request: Request, product_id: str = "") -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return {"cases": await _call(cra.list_cases, product_id=product_id)}


@router.post("/cra/cases")
async def open_cra_case(
    request: Request,
    body: schemas.CraCaseIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.open_case, **body.model_dump(), actor=_actor(request))


@router.post("/cra/cases/{case_id}/reportability")
async def decide_cra_case(
    request: Request,
    case_id: str,
    body: schemas.CraDecisionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.decide_case_reportability,
        case_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.post("/cra/cases/{case_id}/anchors")
async def record_cra_anchor(
    request: Request,
    case_id: str,
    body: schemas.CraAnchorIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.record_case_anchor,
        case_id,
        anchor=body.anchor,
        occurred_at=body.occurred_at,
        evidence=body.evidence,
        actor=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.post("/cra/cases/{case_id}/effective-date-override")
async def override_cra_effective_date(
    request: Request,
    case_id: str,
    body: schemas.CraCounselOverrideIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.override_case_effective_date,
        case_id,
        rationale=body.rationale,
        counsel=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/cra/reports")
async def cra_reports(request: Request, case_id: str = "") -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return {"reports": await _call(cra.list_reports, case_id=case_id)}


@router.post("/cra/reports")
async def draft_cra_report(
    request: Request,
    body: schemas.CraReportIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.draft_report, **body.model_dump(), actor=_actor(request))


@router.post("/cra/reports/{report_id}/approve")
async def approve_cra_report(
    request: Request,
    report_id: str,
    body: schemas.CraApprovalIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.approve_report,
        report_id,
        packet_sha256=body.packet_sha256,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.post("/cra/reports/{report_id}/submission-receipt")
async def record_cra_submission(
    request: Request,
    report_id: str,
    body: schemas.CraSubmissionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.record_submission_receipt,
        report_id,
        submitted_at=body.submitted_at,
        channel=body.channel,
        receipt_id=body.receipt_id,
        actor=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.post("/cra/reports/{report_id}/verified-filing-receipt")
async def record_verified_cra_filing_receipt(
    request: Request,
    report_id: str,
    body: schemas.CraVerifiedFilingReceiptIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.record_verified_filing_receipt,
        report_id,
        envelope=body.envelope.model_dump(by_alias=True),
        actor=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/cra/vex")
async def cra_vex(request: Request, release_id: str = "") -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return {"vex": await _call(cra.list_vex, release_id=release_id)}


@router.post("/cra/vex")
async def create_cra_vex(
    request: Request,
    body: schemas.CraVexIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.create_vex, **body.model_dump(), actor=_actor(request))


@router.post("/cra/vex/{vex_id}/decision")
async def decide_cra_vex(
    request: Request,
    vex_id: str,
    body: schemas.CraDecisionIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    cra = _module("cra_ops", "cra_ops")
    return await _record_call(
        cra.decide_vex,
        vex_id,
        decision=body.decision,
        rationale=body.rationale,
        reviewer=_actor(request),
        expected_revision=body.expected_revision,
    )


@router.get("/cra/products/{product_id}/evidence-pack")
async def cra_evidence_pack(request: Request, product_id: str) -> dict[str, Any]:
    require_permission(request, "operate")
    cra = _module("cra_ops", "cra_ops")
    return await _call(cra.render_product_security_pack, product_id)
