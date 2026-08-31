"""Strict tenant-bound adapter for local privacy-notice governance.

Only content-free notice metadata crosses this boundary.  Tenant and actor
authority are derived from the active tenant and authenticated request; callers
cannot select either.  Publication records are durable worker intents only and
this adapter performs no provider I/O, SDK deployment, or site mutation.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission, require_permission

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_OP_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_KEY_REF_PATTERN = r"^(?:env|vault|secret):[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$"
_LOCALE_PATTERN = r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8}){0,3}$"
_JURISDICTION_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
_ERROR_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"

OpaqueId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=_ID_PATTERN),
]
ObjectPath = Annotated[
    str,
    ApiPath(min_length=1, max_length=128, pattern=_ID_PATTERN),
]
VersionPath = Annotated[int, ApiPath(ge=1, le=1_000_000)]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=192, pattern=_OP_PATTERN),
]
LeaseToken = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=192, pattern=_OP_PATTERN),
]
Digest = Annotated[
    str,
    StringConstraints(strict=True, min_length=64, max_length=64, pattern=_DIGEST_PATTERN),
]
SigningKeyRef = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=5,
        max_length=128,
        pattern=_KEY_REF_PATTERN,
    ),
]
Locale = Annotated[
    str,
    StringConstraints(strict=True, min_length=2, max_length=35, pattern=_LOCALE_PATTERN),
]
Jurisdiction = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=_JURISDICTION_PATTERN,
    ),
]
ErrorCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=48, pattern=_ERROR_PATTERN),
]
ApprovalRole = Literal["privacy", "legal", "brand", "accessibility"]


class _GenericValidationRoute(APIRoute):
    """Avoid reflecting rejected notice content, secrets, or lease tokens."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="privacy-notice request is invalid",
                ) from exc

        return handler


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApprovalPolicyIn(_StrictModel):
    required_roles: list[ApprovalRole] = Field(min_length=1, max_length=4)
    quorum: int = Field(ge=1, le=4)
    approval_ttl_seconds: int = Field(ge=60, le=31_536_000)
    expected_revision: int = Field(ge=0, le=1_000_000)
    idempotency_key: IdempotencyKey


class ReleaseCreateIn(_StrictModel):
    property_id: OpaqueId
    jurisdictions: list[Jurisdiction] = Field(min_length=1, max_length=64)
    locales: list[Locale] = Field(min_length=1, max_length=64)
    policy_sha256: Digest
    content_sha256: Digest
    configuration_sha256: Digest
    consent_publication_ref: OpaqueId
    approval_policy_id: OpaqueId
    effective_from: float | int = Field(gt=0, lt=100_000_000_000.0)
    effective_until: float | int = Field(gt=0, lt=100_000_000_000.0)
    retained_until: float | int = Field(gt=0, lt=100_000_000_000.0)
    update_proposal_ref: OpaqueId | None = None
    expected_latest_version: int = Field(ge=0, le=1_000_000)
    idempotency_key: IdempotencyKey


class RevisionIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=1_000_000)
    idempotency_key: IdempotencyKey


class ApprovalIn(RevisionIn):
    role: ApprovalRole
    expected_release_sha256: Digest


class ApprovalRevocationIn(_StrictModel):
    expected_approval_revision: int = Field(ge=1, le=1_000_000)
    idempotency_key: IdempotencyKey


class ScheduleIn(RevisionIn):
    activate_at: float | int = Field(gt=0, lt=100_000_000_000.0)
    expected_release_sha256: Digest
    max_attempts: int = Field(ge=1, le=20)


class ClaimPublicationIntentsIn(_StrictModel):
    limit: int = Field(default=10, ge=1, le=100)
    lease_seconds: int = Field(default=60, ge=1, le=3_600)


class FailPublicationIntentIn(RevisionIn):
    lease_token: LeaseToken
    error_code: ErrorCode
    retry_delay_seconds: int = Field(ge=0, le=604_800)


class AcknowledgePublicationIntentIn(RevisionIn):
    lease_token: LeaseToken
    acknowledgement_sha256: Digest
    acknowledgement_ref: OpaqueId


class RollbackIn(RevisionIn):
    target_version: int = Field(ge=1, le=1_000_000)
    reason_code: OpaqueId
    max_attempts: int = Field(ge=1, le=20)


class WithdrawIn(RevisionIn):
    reason_code: OpaqueId
    max_attempts: int = Field(ge=1, le=20)


class RegulatoryCitationIn(_StrictModel):
    document_ref: OpaqueId
    source_ref: OpaqueId
    evidence_sha256: Digest


class UpdateProposalIn(_StrictModel):
    notice_id: OpaqueId
    base_version: int = Field(ge=1, le=1_000_000)
    policy_sha256: Digest
    content_sha256: Digest
    configuration_sha256: Digest
    consent_publication_ref: OpaqueId
    regulatory_citations: list[RegulatoryCitationIn] = Field(
        min_length=1,
        max_length=64,
    )
    reason_codes: list[OpaqueId] = Field(min_length=1, max_length=64)
    retained_until: float | int = Field(gt=0, lt=100_000_000_000.0)
    idempotency_key: IdempotencyKey


class UpdateProposalReviewIn(RevisionIn):
    decision: Literal["approved", "rejected"]


class ExportNoticeIn(_StrictModel):
    signing_key_ref: SigningKeyRef
    retained_until: float | int = Field(gt=0, lt=100_000_000_000.0)
    idempotency_key: IdempotencyKey


class PurgeIn(_StrictModel):
    cutoff: float | int = Field(gt=0, lt=100_000_000_000.0)
    idempotency_key: IdempotencyKey


def _enabled() -> bool:
    from maverick import privacy_notice_governance as notice

    return notice.enabled()


def privacy_notice_governance_enabled_for_page() -> bool:
    """Read only the exact feature gate without opening tenant storage."""

    return _enabled()


def _notice():
    from maverick import privacy_notice_governance as notice

    try:
        notice.require_enabled()
    except notice.PrivacyNoticeConfigError as exc:
        raise HTTPException(
            status_code=403,
            detail="privacy-notice governance is disabled",
        ) from exc
    return notice


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using privacy-notice governance",
        )
    return tenant


def _principal(request: Request, tenant_id: str) -> str:
    verified = getattr(request.state, "principal", None)
    subject = getattr(verified, "principal", None) or getattr(verified, "sub", None)
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=401,
            detail="an authenticated principal is required for privacy-notice governance",
        )
    digest = hashlib.sha256(
        (f"lightwork.privacy-notice.identity.v1:{tenant_id}:{subject}").encode()
    ).hexdigest()
    return f"png-principal-{digest[:32]}"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _without_lease(value: Any) -> Any:
    """Recursively remove lease tokens from every non-claim response."""

    result = _jsonable(value)
    if isinstance(result, dict):
        return {key: _without_lease(item) for key, item in result.items() if key != "lease_token"}
    if isinstance(result, list):
        return [_without_lease(item) for item in result]
    return result


def _default_store_for(tenant_id: str, principal_ref: str):
    from maverick.paths import data_dir
    from maverick.privacy_notice_governance import PrivacyNoticeGovernanceStore

    return PrivacyNoticeGovernanceStore(
        data_dir(
            "privacy_automation",
            "privacy-notice-governance.sqlite3",
            tenant=tenant_id,
        ),
        principal_resolver=lambda: principal_ref,
    )


class PrivacyNoticeGovernanceDependencies:
    """Explicit tenant/principal-bound store seam for runtimes and tests."""

    def __init__(
        self,
        *,
        store_for: Callable[[str, str], Any] = _default_store_for,
    ) -> None:
        self.store_for = store_for


def create_privacy_notice_governance_router(  # noqa: C901, PLR0915
    dependencies: PrivacyNoticeGovernanceDependencies | None = None,
) -> APIRouter:
    deps = dependencies or PrivacyNoticeGovernanceDependencies()
    router = APIRouter(
        prefix="/privacy/notices",
        tags=["privacy-notice-governance"],
        route_class=_GenericValidationRoute,
    )

    def authority(request: Request):
        notice = _notice()
        tenant = _tenant_id()
        principal = _principal(request, tenant)
        factory = getattr(
            request.app.state,
            "privacy_notice_governance_store_factory",
            None,
        )

        def resolver() -> str:
            return principal

        try:
            store = (
                factory(
                    tenant_id=tenant,
                    principal_ref=principal,
                    principal_resolver=resolver,
                    request=request,
                )
                if callable(factory)
                else deps.store_for(tenant, principal)
            )
        except (notice.PrivacyNoticeGovernanceError, OSError) as exc:
            raise HTTPException(
                status_code=503,
                detail="privacy-notice governance store is unavailable",
            ) from exc
        if store is None:
            raise HTTPException(
                status_code=503,
                detail="privacy-notice governance store is unavailable",
            )
        return notice, tenant, store

    async def call(notice, function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except notice.PrivacyNoticeNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail="privacy-notice record was not found",
            ) from exc
        except notice.PrivacyNoticePermissionDenied as exc:
            raise HTTPException(
                status_code=403,
                detail="privacy-notice operation is not permitted",
            ) from exc
        except notice.PrivacyNoticeConflict as exc:
            raise HTTPException(
                status_code=409,
                detail="privacy-notice state conflict",
            ) from exc
        except notice.PrivacyNoticeValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail="privacy-notice request is invalid",
            ) from exc
        except notice.PrivacyNoticeConfigError as exc:
            raise HTTPException(
                status_code=503,
                detail="privacy-notice configuration is unavailable",
            ) from exc
        except notice.PrivacyNoticeIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail="privacy-notice integrity verification failed",
            ) from exc
        except notice.PrivacyNoticeGovernanceError as exc:
            raise HTTPException(
                status_code=503,
                detail="privacy-notice operation failed",
            ) from exc

    @router.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        require_permission(request, "view")
        tenant = _tenant_id()
        principal = _principal(request, tenant)
        from maverick.privacy_notice_governance import (
            PrivacyNoticeGovernanceStore,
        )

        return {
            "schema": "lightwork.privacy-notice-governance-status.v1",
            "enabled": _enabled(),
            "principal_ref": principal,
            **PrivacyNoticeGovernanceStore.product_boundaries(),
            "publication_intents_are_worker_intent_only": True,
            "publication_transport_worker_implemented": False,
            "regulatory_reference_resolver_required": True,
            "claim_is_only_live_lease_token_response": True,
            "status_request_external_network_io": False,
        }

    @router.put("/approval-policies/{policy_id}")
    async def set_approval_policy(
        request: Request,
        policy_id: ObjectPath,
        body: ApprovalPolicyIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.set_approval_policy,
                tenant,
                policy_id,
                **body.model_dump(),
            )
        )

    @router.get("/approval-policies/{policy_id}")
    async def get_approval_policy(request: Request, policy_id: ObjectPath):
        require_permission(request, "view")
        notice, tenant, store = authority(request)
        return _without_lease(await call(notice, store.get_approval_policy, tenant, policy_id))

    @router.put("/releases/{notice_id}/versions")
    async def create_release(
        request: Request,
        notice_id: ObjectPath,
        body: ReleaseCreateIn,
    ):
        require_permission(request, "operate")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.create_release,
                tenant,
                notice_id,
                **body.model_dump(),
            )
        )

    @router.get("/releases/{notice_id}/versions/{version}")
    async def get_release(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
    ):
        require_permission(request, "view")
        notice, tenant, store = authority(request)
        return _without_lease(await call(notice, store.get_release, tenant, notice_id, version))

    @router.post("/releases/{notice_id}/versions/{version}/submit")
    async def submit_for_review(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        body: RevisionIn,
    ):
        require_permission(request, "operate")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.submit_for_review,
                tenant,
                notice_id,
                version,
                **body.model_dump(),
            )
        )

    @router.post("/releases/{notice_id}/versions/{version}/approvals")
    async def approve_release(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        body: ApprovalIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.approve_release,
                tenant,
                notice_id,
                version,
                **body.model_dump(),
            )
        )

    @router.post("/releases/{notice_id}/versions/{version}/approvals/{role}/revoke")
    async def revoke_approval(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        role: ApprovalRole,
        body: ApprovalRevocationIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.revoke_approval,
                tenant,
                notice_id,
                version,
                role=role,
                **body.model_dump(),
            )
        )

    @router.post("/releases/{notice_id}/versions/{version}/schedule")
    async def schedule_release(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        body: ScheduleIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.schedule_release,
                tenant,
                notice_id,
                version,
                **body.model_dump(),
            )
        )

    @router.post("/publication-intents/claim")
    async def claim_publication_intents(
        request: Request,
        body: ClaimPublicationIntentsIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        items = await call(
            notice,
            store.claim_publication_intents,
            tenant,
            **body.model_dump(),
        )
        return {"items": _jsonable(items), "count": len(items)}

    @router.post("/publication-intents/{intent_id}/fail")
    async def fail_publication_intent(
        request: Request,
        intent_id: ObjectPath,
        body: FailPublicationIntentIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.fail_publication_intent,
                tenant,
                intent_id,
                **body.model_dump(),
            )
        )

    @router.post("/publication-intents/{intent_id}/requeue")
    async def requeue_publication_intent(
        request: Request,
        intent_id: ObjectPath,
        body: RevisionIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.requeue_publication_intent,
                tenant,
                intent_id,
                **body.model_dump(),
            )
        )

    @router.post("/publication-intents/{intent_id}/acknowledge")
    async def acknowledge_publication_intent(
        request: Request,
        intent_id: ObjectPath,
        body: AcknowledgePublicationIntentIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.ack_publication_intent,
                tenant,
                intent_id,
                **body.model_dump(),
            )
        )

    @router.post("/releases/{notice_id}/versions/{version}/rollback")
    async def rollback_release(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        body: RollbackIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.rollback_release,
                tenant,
                notice_id,
                version,
                **body.model_dump(),
            )
        )

    @router.post("/releases/{notice_id}/versions/{version}/withdraw")
    async def withdraw_release(
        request: Request,
        notice_id: ObjectPath,
        version: VersionPath,
        body: WithdrawIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.withdraw_release,
                tenant,
                notice_id,
                version,
                **body.model_dump(),
            )
        )

    @router.get("/effective/{property_id}")
    async def current_effective_release(
        request: Request,
        property_id: ObjectPath,
        jurisdiction: Jurisdiction = Query(),
        locale: Locale = Query(),
        at: float | None = Query(default=None, gt=0, lt=100_000_000_000.0),
    ):
        require_permission(request, "view")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.current_effective_release,
                tenant,
                property_id,
                jurisdiction,
                locale,
                at=at,
            )
        )

    @router.put("/update-proposals/{proposal_id}")
    async def create_update_proposal(
        request: Request,
        proposal_id: ObjectPath,
        body: UpdateProposalIn,
    ):
        require_permission(request, "operate")
        notice, tenant, store = authority(request)
        payload = body.model_dump()
        payload["regulatory_citations"] = [
            citation.model_dump() for citation in body.regulatory_citations
        ]
        return _without_lease(
            await call(
                notice,
                store.create_update_proposal,
                tenant,
                proposal_id,
                **payload,
            )
        )

    @router.get("/update-proposals/{proposal_id}")
    async def get_update_proposal(request: Request, proposal_id: ObjectPath):
        require_permission(request, "view")
        notice, tenant, store = authority(request)
        return _without_lease(await call(notice, store.get_update_proposal, tenant, proposal_id))

    @router.post("/update-proposals/{proposal_id}/review")
    async def review_update_proposal(
        request: Request,
        proposal_id: ObjectPath,
        body: UpdateProposalReviewIn,
    ):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.review_update_proposal,
                tenant,
                proposal_id,
                **body.model_dump(),
            )
        )

    @router.put("/releases/{notice_id}/exports/{export_id}")
    async def export_notice(
        request: Request,
        notice_id: ObjectPath,
        export_id: ObjectPath,
        body: ExportNoticeIn,
    ):
        require_permission(request, "operate")
        notice, tenant, store = authority(request)
        return _without_lease(
            await call(
                notice,
                store.export_notice,
                tenant,
                notice_id,
                export_id,
                **body.model_dump(),
            )
        )

    @router.get("/exports/{export_id}")
    async def get_export(request: Request, export_id: ObjectPath):
        require_permission(request, "view")
        notice, tenant, store = authority(request)
        return _without_lease(await call(notice, store.get_export, tenant, export_id))

    @router.post("/retention/purge")
    async def purge_expired(request: Request, body: PurgeIn):
        require_global_permission(request, "admin")
        notice, tenant, store = authority(request)
        return _without_lease(await call(notice, store.purge_expired, tenant, **body.model_dump()))

    @router.get("/integrity")
    async def verify_integrity(request: Request):
        require_global_permission(request, "audit")
        notice, tenant, store = authority(request)
        result = _without_lease(await call(notice, store.verify_integrity, tenant))
        result["valid"] = True
        return result

    return router


router = create_privacy_notice_governance_router()

__all__ = [
    "PrivacyNoticeGovernanceDependencies",
    "create_privacy_notice_governance_router",
    "privacy_notice_governance_enabled_for_page",
    "router",
]
