"""Tenant-bound operator API for privacy-minimising data discovery.

Provider access and reviewed data-map/RoPA delivery are deliberately injected
at the application boundary.  Request bodies can identify enrolled metadata
and bounded work, but cannot provide a tenant, actor, worker, reviewer, raw
source value, provider credential, detector result, or callback payload.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission, require_permission

router = APIRouter(
    prefix="/privacy/data-discovery",
    tags=["privacy-data-discovery"],
)

_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_IDEMPOTENCY = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"
_PROVIDER = r"^[a-z][a-z0-9_-]{0,63}$"
_ENV = r"^[A-Z][A-Z0-9_]{1,127}$"
_DIGEST = r"^[0-9a-f]{64}$"

OpaqueId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=_ID),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=192,
        pattern=_IDEMPOTENCY,
    ),
]
ProviderName = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=_PROVIDER),
]
EnvironmentRef = Annotated[
    str,
    StringConstraints(strict=True, min_length=2, max_length=128, pattern=_ENV),
]
Digest = Annotated[
    str,
    StringConstraints(strict=True, min_length=64, max_length=64, pattern=_DIGEST),
]
ExactOrigin = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=2_048),
]
ObjectPath = Annotated[
    str,
    ApiPath(min_length=1, max_length=128, pattern=_ID),
]

SourceKind = Literal["structured", "unstructured"]
SourceState = Literal["active", "suspended", "revoked"]
JobState = Literal["queued", "leased", "complete", "dead_letter"]
ReviewDecision = Literal["confirmed", "reclassified", "false_positive"]
Category = Literal[
    "authentication_secret",
    "biometric_identifier",
    "date_of_birth",
    "email_address",
    "financial_account",
    "government_identifier",
    "health_information",
    "ip_address",
    "payment_card",
    "person_name",
    "phone_number",
    "physical_address",
    "precise_location",
    "unknown_personal_data",
]
FailureCode = Literal[
    "adapter_error",
    "credential_unavailable",
    "operator_cancelled",
    "provider_contract_invalid",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ScanLimitsIn(_StrictModel):
    max_records: int = Field(default=10_000, ge=1, le=1_000_000)
    max_files: int = Field(default=1_000, ge=1, le=100_000)
    max_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=2**40)
    max_seconds: float = Field(default=300.0, ge=0.1, le=86_400.0)
    max_findings: int = Field(default=10_000, ge=1, le=100_000)
    max_fields_per_record: int = Field(default=256, ge=1, le=10_000)
    max_record_bytes: int = Field(
        default=256 * 1024,
        ge=1,
        le=64 * 1024 * 1024,
    )
    max_document_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=1,
        le=256 * 1024 * 1024,
    )


class SourceEnrollmentIn(_StrictModel):
    kind: SourceKind
    provider: ProviderName
    origin: ExactOrigin
    resource_ref: OpaqueId
    credential_env: EnvironmentRef
    evidence_key_env: EnvironmentRef
    expected_revision: int = Field(default=0, ge=0, le=2**31 - 1)
    idempotency_key: IdempotencyKey


class SourceStateIn(_StrictModel):
    state: SourceState
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    idempotency_key: IdempotencyKey


class ScheduleIn(_StrictModel):
    source_id: OpaqueId
    interval_seconds: int = Field(ge=1, le=365 * 24 * 3_600)
    limits: ScanLimitsIn = Field(default_factory=ScanLimitsIn)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retention_seconds: int = Field(
        default=90 * 24 * 3_600,
        ge=1,
        le=10 * 365 * 24 * 3_600,
    )
    active: bool = True
    expected_revision: int = Field(default=0, ge=0, le=2**31 - 1)
    idempotency_key: IdempotencyKey
    next_run_at: float | None = Field(
        default=None,
        gt=0,
        lt=100_000_000_000.0,
    )


class EnqueueDueIn(_StrictModel):
    limit: int = Field(default=100, ge=1, le=100)


class ManualScanIn(_StrictModel):
    limits: ScanLimitsIn = Field(default_factory=ScanLimitsIn)
    max_attempts: int = Field(default=3, ge=1, le=20)
    retention_seconds: int = Field(
        default=90 * 24 * 3_600,
        ge=1,
        le=10 * 365 * 24 * 3_600,
    )
    idempotency_key: IdempotencyKey


class ClaimIn(_StrictModel):
    limit: int = Field(default=10, ge=1, le=100)
    lease_seconds: float = Field(default=600.0, ge=1.0, le=3_600.0)


class FailJobIn(_StrictModel):
    error_code: FailureCode
    base_backoff_seconds: float = Field(default=5.0, ge=0.1, le=3_600.0)


class ReplayIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    idempotency_key: IdempotencyKey


class ReviewProposalIn(_StrictModel):
    decision: ReviewDecision
    effective_category: Category | None = None
    freshness_seconds: int = Field(ge=1, le=365 * 24 * 3_600)
    expected_finding_revision: int = Field(ge=1, le=2**31 - 1)
    idempotency_key: IdempotencyKey


class ReviewApprovalIn(_StrictModel):
    expected_version: int = Field(ge=1, le=2**31 - 1)
    expected_integrity_sha256: Digest
    idempotency_key: IdempotencyKey


class RetentionIn(_StrictModel):
    idempotency_key: IdempotencyKey


def _ops():
    from maverick import privacy_data_discovery as ops

    try:
        ops.require_enabled()
    except ops.PrivacyDataDiscoveryConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ops


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using privacy data discovery",
        )
    return tenant


def _principal_subject(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    subject = getattr(principal, "sub", None)
    return str(subject or "local-operator")


def _identity_ref(request: Request, tenant_id: str, purpose: str) -> str:
    digest = hashlib.sha256(
        (
            "lightwork.privacy-data-discovery.identity.v1:"
            f"{purpose}:{tenant_id}:{_principal_subject(request)}"
        ).encode()
    ).hexdigest()
    return f"pdd-{purpose}-{digest[:32]}"


def _actor_ref(request: Request, tenant_id: str) -> str:
    return _identity_ref(request, tenant_id, "actor")


def _reviewer_ref(request: Request, tenant_id: str) -> str:
    return _identity_ref(request, tenant_id, "reviewer")


def _worker_ref(request: Request, tenant_id: str) -> str:
    return _identity_ref(request, tenant_id, "worker")


def _callback_authority(request: Request, tenant_id: str) -> Callable[[Any], None] | None:
    authority = getattr(
        request.app.state,
        "privacy_data_discovery_review_callback",
        None,
    )
    if authority is None:
        return None
    callback = (
        authority
        if callable(authority)
        else getattr(authority, "apply_reviewed_classification", None)
    )
    if not callable(callback):
        raise HTTPException(
            status_code=503,
            detail="reviewed data-map/RoPA callback authority is misconfigured",
        )

    def tenant_checked(update: Any) -> None:
        if getattr(update, "tenant_id", None) != tenant_id:
            raise RuntimeError("review callback tenant binding failed")
        callback(update)

    return tenant_checked


def _store(ops, tenant_id: str, request: Request):
    from maverick.paths import data_dir

    return ops.PrivacyDataDiscoveryStore(
        data_dir(
            "privacy_automation",
            "privacy-data-discovery.sqlite3",
            tenant=tenant_id,
        ),
        review_callback=_callback_authority(request, tenant_id),
    )


def _coordinator(request: Request, ops, tenant_id: str, store):
    factory = getattr(
        request.app.state,
        "privacy_data_discovery_coordinator_factory",
        None,
    )
    if callable(factory):
        return factory(tenant_id=tenant_id, store=store)
    structured = getattr(
        request.app.state,
        "privacy_data_discovery_structured_sources",
        {},
    )
    unstructured = getattr(
        request.app.state,
        "privacy_data_discovery_unstructured_sources",
        {},
    )
    credential_check = getattr(
        request.app.state,
        "privacy_data_discovery_credential_ref_present",
        None,
    )
    if not isinstance(structured, Mapping) or not isinstance(unstructured, Mapping):
        raise HTTPException(
            status_code=503,
            detail="privacy discovery provider adapter registry is misconfigured",
        )
    if credential_check is not None and not callable(credential_check):
        raise HTTPException(
            status_code=503,
            detail="privacy discovery credential owner is misconfigured",
        )
    return ops.PrivacyDataDiscoveryCoordinator(
        store,
        structured_sources=structured,
        unstructured_sources=unstructured,
        credential_ref_present=credential_check,
    )


def _authority(request: Request):
    ops = _ops()
    tenant = _tenant_id()
    return ops, tenant, _store(ops, tenant, request)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _items(values: tuple[Any, ...], limit: int) -> dict[str, Any]:
    return {
        "items": [_jsonable(value) for value in values[:limit]],
        "truncated": len(values) > limit,
    }


def _report_payload(report: Any) -> dict[str, Any]:
    result = _jsonable(report)
    return {
        **result,
        "absence_is_proven": False,
        "completeness_scope": "declared_source_and_persisted_scan_bounds_only",
        "raw_data_returned": False,
        "live_provider_acceptance_verified": False,
    }


def _provider_names(value: object) -> tuple[list[str], bool]:
    if not isinstance(value, Mapping):
        return [], False
    names = sorted(
        key
        for key in value
        if isinstance(key, str) and re.fullmatch(_PROVIDER, key) is not None
    )
    return names[:100], len(names) > 100


async def _call(ops, function, *args, **kwargs) -> Any:
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except ops.PrivacyDataDiscoveryConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ops.PrivacyDataDiscoveryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ops.PrivacyDataDiscoveryConflict, ops.PrivacyDataDiscoveryLeaseLost) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ops.PrivacyDataDiscoveryValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ops.PrivacyDataDiscoveryIntegrityError as exc:
        raise HTTPException(
            status_code=503,
            detail="privacy data discovery integrity validation failed",
        ) from exc
    except ops.PrivacyDataDiscoveryAdapterError as exc:
        raise HTTPException(
            status_code=503,
            detail="privacy data discovery external adapter or callback failed",
        ) from exc
    except ops.PrivacyDataDiscoveryError as exc:
        raise HTTPException(
            status_code=503,
            detail="privacy data discovery operation failed",
        ) from exc


def _limits(ops, body: ScanLimitsIn):
    return ops.ScanLimits(**body.model_dump())


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _ops()
    structured = getattr(
        request.app.state,
        "privacy_data_discovery_structured_sources",
        {},
    )
    unstructured = getattr(
        request.app.state,
        "privacy_data_discovery_unstructured_sources",
        {},
    )
    callback = getattr(
        request.app.state,
        "privacy_data_discovery_review_callback",
        None,
    )
    structured_names, structured_truncated = _provider_names(structured)
    unstructured_names, unstructured_truncated = _provider_names(unstructured)
    return {
        "schema": "lightwork.privacy-data-discovery-runtime-status.v1",
        "enabled": True,
        "structured_provider_adapters": structured_names,
        "unstructured_provider_adapters": unstructured_names,
        "provider_adapter_list_truncated": (
            structured_truncated or unstructured_truncated
        ),
        "external_scan_execution": "explicit_execute_endpoint_only",
        "raw_data_returned": False,
        "live_provider_acceptance_verified": False,
        "completeness_claim": False,
        "callback_delivery": {
            "configured": callback is not None,
            "approved_reviews_only": True,
            "durable_outbox": False,
            "automatic_retry": False,
            "failure_contract": "approval_remains_durable_and_manual_redelivery_is_required",
        },
    }


@router.get("/posture")
async def posture(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    result = await _call(ops, store.privacy_posture)
    return {
        **_jsonable(result),
        "tenant_bound": bool(tenant),
        "callback_outbox_available": False,
    }


@router.put("/sources/{source_id}")
async def enroll_source(
    request: Request,
    source_id: ObjectPath,
    body: SourceEnrollmentIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    result = await _call(
        ops,
        store.enroll_source,
        tenant,
        source_id=source_id,
        actor_ref=_actor_ref(request, tenant),
        **body.model_dump(),
    )
    return {
        **_jsonable(result),
        "credential_value_persisted": False,
        "external_side_effect": False,
    }


@router.post("/sources/{source_id}/state")
async def set_source_state(
    request: Request,
    source_id: ObjectPath,
    body: SourceStateIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    result = await _call(
        ops,
        store.set_source_state,
        tenant,
        source_id,
        actor_ref=_actor_ref(request, tenant),
        **body.model_dump(),
    )
    return _jsonable(result)


@router.get("/sources")
async def list_sources(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(ops, store.list_sources, tenant, limit=limit + 1)
    return _items(rows, limit)


@router.get("/sources/{source_id}")
async def source_detail(request: Request, source_id: ObjectPath) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    return _jsonable(await _call(ops, store.get_source, tenant, source_id))


@router.put("/schedules/{schedule_id}")
async def upsert_schedule(
    request: Request,
    schedule_id: ObjectPath,
    body: ScheduleIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    payload = body.model_dump(exclude={"limits"})
    result = await _call(
        ops,
        store.upsert_schedule,
        tenant,
        schedule_id=schedule_id,
        limits=_limits(ops, body.limits),
        actor_ref=_actor_ref(request, tenant),
        **payload,
    )
    return _jsonable(result)


@router.post("/schedules/enqueue-due")
async def enqueue_due(request: Request, body: EnqueueDueIn) -> dict[str, Any]:
    require_permission(request, "operate")
    ops, tenant, store = _authority(request)
    rows = await _call(
        ops,
        store.enqueue_due,
        tenant,
        actor_ref=_actor_ref(request, tenant),
        limit=body.limit,
    )
    return {"items": [_jsonable(row) for row in rows], "enqueued": len(rows)}


@router.get("/schedules")
async def list_schedules(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(ops, store.list_schedules, tenant, limit=limit + 1)
    return _items(rows, limit)


@router.get("/schedules/{schedule_id}")
async def schedule_detail(request: Request, schedule_id: ObjectPath) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    return _jsonable(await _call(ops, store.get_schedule, tenant, schedule_id))


@router.post("/sources/{source_id}/jobs")
async def enqueue_manual_scan(
    request: Request,
    source_id: ObjectPath,
    body: ManualScanIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    ops, tenant, store = _authority(request)
    payload = body.model_dump(exclude={"limits"})
    result = await _call(
        ops,
        store.enqueue_scan,
        tenant,
        source_id,
        limits=_limits(ops, body.limits),
        actor_ref=_actor_ref(request, tenant),
        **payload,
    )
    return _jsonable(result)


@router.post("/jobs/claim")
async def claim_jobs(request: Request, body: ClaimIn) -> dict[str, Any]:
    require_permission(request, "operate")
    ops, tenant, store = _authority(request)
    rows = await _call(
        ops,
        store.claim_jobs,
        tenant,
        worker_id=_worker_ref(request, tenant),
        **body.model_dump(),
    )
    return {
        "items": [_jsonable(row) for row in rows],
        "lease_owner_bound_to_request_principal": True,
    }


@router.post("/jobs/{job_id}/execute")
async def execute_job(request: Request, job_id: ObjectPath) -> dict[str, Any]:
    """Execute one leased scan; injected adapters may perform external I/O."""

    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    job = await _call(ops, store.get_job, tenant, job_id)
    coordinator = _coordinator(request, ops, tenant, store)
    report = await _call(
        ops,
        coordinator.execute_job,
        job,
        worker_id=_worker_ref(request, tenant),
    )
    return {
        "report": _report_payload(report),
        "external_side_effect": True,
        "provider_network_io_possible": True,
        "raw_data_returned": False,
        "live_provider_acceptance_verified": False,
        "deadline_contract": "cooperative_adapter_deadline_checked_between_items",
    }


@router.post("/jobs/{job_id}/fail")
async def fail_job(
    request: Request,
    job_id: ObjectPath,
    body: FailJobIn,
) -> dict[str, Any]:
    require_permission(request, "operate")
    ops, tenant, store = _authority(request)
    result = await _call(
        ops,
        store.fail_job,
        tenant,
        job_id,
        worker_id=_worker_ref(request, tenant),
        **body.model_dump(),
    )
    return _jsonable(result)


@router.post("/jobs/{job_id}/replay")
async def replay_job(
    request: Request,
    job_id: ObjectPath,
    body: ReplayIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    result = await _call(
        ops,
        store.replay_dead_letter,
        tenant,
        job_id,
        actor_ref=_actor_ref(request, tenant),
        **body.model_dump(),
    )
    return _jsonable(result)


@router.get("/jobs")
async def list_jobs(
    request: Request,
    state: JobState | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(ops, store.list_jobs, tenant, state=state, limit=limit + 1)
    return _items(rows, limit)


@router.get("/jobs/{job_id}")
async def job_detail(request: Request, job_id: ObjectPath) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    return _jsonable(await _call(ops, store.get_job, tenant, job_id))


@router.get("/inventory")
async def inventory(
    request: Request,
    source_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_ID,
    ),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    result = _jsonable(
        await _call(ops, store.inventory, tenant, source_id=source_id)
    )
    return {
        **result,
        "absence_is_proven": False,
        "approved_findings_only_are_authoritative_updates": True,
    }


@router.get("/reports/diff")
async def diff_reports(
    request: Request,
    base_report_id: str = Query(min_length=1, max_length=128, pattern=_ID),
    target_report_id: str = Query(min_length=1, max_length=128, pattern=_ID),
    limit: int = Query(default=1_000, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    result = _jsonable(
        await _call(
            ops,
            store.diff_reports,
            tenant,
            base_report_id,
            target_report_id,
        )
    )
    added = result.pop("added_finding_sha256")
    removed = result.pop("removed_finding_sha256")
    return {
        **result,
        "added_finding_sha256": added[:limit],
        "removed_finding_sha256": removed[:limit],
        "added_count": len(added),
        "removed_count": len(removed),
        "truncated": len(added) > limit or len(removed) > limit,
        "absence_is_proven": False,
        "digest_only": True,
    }


@router.get("/reports")
async def list_reports(
    request: Request,
    source_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=128,
        pattern=_ID,
    ),
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(
        ops,
        store.list_reports,
        tenant,
        source_id=source_id,
        limit=limit + 1,
    )
    return {
        "items": [_report_payload(row) for row in rows[:limit]],
        "truncated": len(rows) > limit,
        "absence_is_proven": False,
    }


@router.get("/reports/{report_id}")
async def report_detail(request: Request, report_id: ObjectPath) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    return _report_payload(await _call(ops, store.get_report, tenant, report_id))


@router.get("/reports/{report_id}/findings")
async def list_findings(
    request: Request,
    report_id: ObjectPath,
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(
        ops,
        store.list_findings,
        tenant,
        report_id=report_id,
        limit=limit + 1,
    )
    return {
        **_items(rows, limit),
        "digest_only_evidence": True,
        "raw_values_returned": False,
    }


@router.get("/findings/{finding_id}")
async def finding_detail(request: Request, finding_id: ObjectPath) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    return _jsonable(await _call(ops, store.get_finding, tenant, finding_id))


@router.post("/findings/{finding_id}/reviews")
async def propose_review(
    request: Request,
    finding_id: ObjectPath,
    body: ReviewProposalIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    finding = await _call(ops, store.get_finding, tenant, finding_id)
    result = await _call(
        ops,
        store.propose_review,
        tenant,
        finding_id,
        evidence_sha256=finding.evidence_sha256,
        prepared_by=_reviewer_ref(request, tenant),
        **body.model_dump(),
    )
    return {
        **_jsonable(result),
        "evidence_bound_from_authoritative_finding": True,
        "prepared_by_request_principal": True,
        "data_map_callback_invoked": False,
    }


@router.get("/findings/{finding_id}/reviews")
async def list_reviews(
    request: Request,
    finding_id: ObjectPath,
    limit: int = Query(default=100, ge=1, le=1_999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(
        ops,
        store.list_reviews,
        tenant,
        finding_id=finding_id,
        limit=limit + 1,
    )
    return _items(rows, limit)


@router.post("/reviews/{review_id}/approve")
async def approve_review(
    request: Request,
    review_id: ObjectPath,
    body: ReviewApprovalIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    callback_configured = _callback_authority(request, tenant) is not None
    before = await _call(ops, store.get_review, tenant, review_id)
    callback_attempted = callback_configured and before.status == "pending"
    result = await _call(
        ops,
        store.approve_review,
        tenant,
        review_id,
        approved_by=_reviewer_ref(request, tenant),
        **body.model_dump(),
    )
    return {
        **_jsonable(result),
        "four_eyes_enforced": True,
        "approved_only_callback": True,
        "callback_configured": callback_configured,
        "callback_attempted_this_request": callback_attempted,
        "callback_acknowledged": True if callback_attempted else None,
        "callback_durable_outbox": False,
    }


@router.get("/audit")
async def audit_events(
    request: Request,
    limit: int = Query(default=500, ge=1, le=999),
) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    rows = await _call(ops, store.audit_events, tenant, limit=limit + 1)
    return _items(rows, limit)


@router.get("/audit/verify")
async def verify_audit(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    ops, tenant, store = _authority(request)
    verified = await _call(ops, store.verify_audit_chain, tenant)
    return {"verified": bool(verified), "tenant_bound": True}


@router.post("/retention/purge")
async def purge_retention(request: Request, body: RetentionIn) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops, tenant, store = _authority(request)
    count = await _call(
        ops,
        store.purge_expired,
        tenant,
        actor_ref=_actor_ref(request, tenant),
        idempotency_key=body.idempotency_key,
    )
    return {
        "purged_reports": count,
        "raw_data_purged": False,
        "digest_only_audit_tombstones_retained": True,
    }


__all__ = ["router"]
