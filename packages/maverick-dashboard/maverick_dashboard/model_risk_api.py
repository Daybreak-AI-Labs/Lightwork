"""Operator API for governed Model Risk & AI Assurance."""
from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import model_risk_schemas as schemas
from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/assurance", tags=["model-risk-assurance"])


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
