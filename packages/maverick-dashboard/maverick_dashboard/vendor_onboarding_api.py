"""Transport validation for the tenant-private vendor onboarding service."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from maverick.governed_records import RecordConflict
from pydantic import BaseModel, ConfigDict, Field

from .auth import require_permission

router = APIRouter(prefix="/vendor-onboarding", tags=["vendor-onboarding"])


class ClosedBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class IntakeBody(ClosedBody):
    vendor_name: str = Field(min_length=1, max_length=200)
    document_id: str = Field(min_length=1, max_length=256)
    project: str = Field(min_length=1, max_length=32)
    purpose: str = Field(min_length=1, max_length=2000)
    freshness_days: int = Field(default=365, ge=1, le=730)
    renewal_of: str | None = Field(default=None, max_length=128)


class RevisionBody(ClosedBody):
    expected_revision: int = Field(ge=1)


class CorrectionBody(ClosedBody):
    answer: Literal["yes", "no", "na", "unknown"]
    note: str = Field(default="", max_length=2000)


class ReviewBody(RevisionBody):
    decision: Literal["approved", "rejected"]
    rationale: str = Field(min_length=1, max_length=4000)
    corrections: dict[str, CorrectionBody] | None = None
    review_seconds: float | None = Field(default=None, ge=0, le=86400)


class ApprovalBody(RevisionBody):
    confirm: Literal[True]


class AcceptanceBody(RevisionBody):
    accepted: bool
    rationale: str = Field(min_length=1, max_length=4000)


def _binding(request: Request):
    require_permission(request, "operate")
    factory = getattr(request.app.state, "vendor_onboarding_binding_factory", None)
    if not callable(factory):
        raise HTTPException(503, "Vendor onboarding composition is unavailable")
    try:
        return factory(request)
    except HTTPException:
        raise
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Vendor onboarding authority is unavailable") from exc


def _call(binding, operation: str, *args, **kwargs):
    try:
        return getattr(binding.service, operation)(binding.context, *args, **kwargs)
    except HTTPException:
        raise
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Vendor case or referenced record was not found") from exc
    except (ValueError, RecordConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        # Never expose provider payloads, credential values or storage paths.
        raise HTTPException(503, "Operation unavailable; reload case status before retrying") from exc


@router.get("/cases")
def cases(request: Request):
    return {"cases": _call(_binding(request), "list")}


@router.post("/cases", status_code=201)
def intake(request: Request, body: IntakeBody):
    return _call(_binding(request), "intake", **body.model_dump())


@router.get("/cases/{case_id}")
def case(request: Request, case_id: str):
    return _call(_binding(request), "get", case_id)


@router.post("/cases/{case_id}/review")
def review(request: Request, case_id: str, body: ReviewBody):
    return _call(_binding(request), "review", case_id, **body.model_dump())


@router.post("/cases/{case_id}/approve-action")
def approve(request: Request, case_id: str, body: ApprovalBody):
    return _call(_binding(request), "approve_action", case_id,
                 expected_revision=body.expected_revision)


@router.post("/cases/{case_id}/execute")
def execute(request: Request, case_id: str, body: ApprovalBody):
    binding = _binding(request)
    current = _call(binding, "get", case_id)
    if current["revision"] != body.expected_revision:
        raise HTTPException(409, "Case changed; reload the exact approved action")
    return _call(binding, "execute", case_id)


@router.post("/cases/{case_id}/reconcile")
def reconcile(request: Request, case_id: str, body: ApprovalBody):
    return _call(_binding(request), "reconcile", case_id)


@router.post("/cases/{case_id}/accept")
def accept(request: Request, case_id: str, body: AcceptanceBody):
    return _call(_binding(request), "accept", case_id, **body.model_dump())


@router.post("/cases/{case_id}/check-renewal")
def check_renewal(request: Request, case_id: str, body: RevisionBody):
    binding = _binding(request)
    current = _call(binding, "get", case_id)
    if current["revision"] != body.expected_revision:
        raise HTTPException(409, "Case changed; reload before checking renewal")
    return _call(binding, "check_renewal", case_id)


@router.post("/cases/{case_id}/{step}")
def step(request: Request, case_id: str,
         step: Literal["collect-evidence", "assess", "prepare-action"], body: RevisionBody):
    return _call(_binding(request), step.replace("-", "_"), case_id,
                 expected_revision=body.expected_revision)
