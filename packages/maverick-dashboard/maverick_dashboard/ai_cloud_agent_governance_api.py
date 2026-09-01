"""Strict operator API for governed managed-cloud AI agents.

Tenant and principal authority come only from authenticated server context.
Provider bindings, native identifiers, provider clients, credentials, signing
keys, policy evaluators, inventory adapters, and framework source documents are
deployment-owned dependencies and are never accepted by this HTTP contract.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_permission

Digest = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    ),
]
AgentRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^cag_[0-9a-f]{32}$"),
]
TargetRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^(?:cag|cad|cam|cat)_[0-9a-f]{32}$"),
]
PolicyRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^pol_[0-9a-f]{32}$"),
]
EvaluationRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^cpe_[0-9a-f]{32}$"),
]
IntentRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^cei_[0-9a-f]{32}$"),
]
ReviewRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^fwr_[0-9a-f]{32}$"),
]
AttestationRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^fwa_[0-9a-f]{32}$"),
]
EvidenceRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^evd_[0-9a-f]{32}$"),
]
WorkloadRef = Annotated[
    str,
    StringConstraints(strict=True, pattern=r"^wrk_[0-9a-f]{32}$"),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=8,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{7,255}$",
    ),
]
Code = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_.:-]{0,127}$",
    ),
]
ControlId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=6,
        max_length=69,
        pattern=r"^DBAI-[A-Z0-9][A-Z0-9.-]{0,63}$",
    ),
]
FrameworkVersion = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=6,
        max_length=14,
        pattern=r"^[0-9]{4}\.[0-9]{1,3}(?:\.[0-9]{1,3})?$",
    ),
]

EnforcementAction = Literal[
    "detach_data_source",
    "disable_agent",
    "quarantine_agent",
    "restrict_model",
    "restrict_tool",
]
ReconciliationResolution = Literal["applied", "no_change", "retry"]
ApplicabilityStatus = Literal["applicable", "needs_review", "not_applicable"]
ReviewDecision = Literal["approved", "rejected"]
ActivationAction = Literal["activate", "rollback"]

_NO_STORE = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}
_MAX_JSON_BODY_BYTES = 256 * 1024
_SERVER_OWNED_QUERY_FIELDS = frozenset(
    {
        "adapter",
        "binding_ref",
        "credential",
        "credential_env",
        "native_id",
        "principal",
        "principal_ref",
        "provider",
        "provider_url",
        "signing_key",
        "tenant",
        "tenant_id",
        "tenant_sha256",
    }
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _BoundedJSONRoute(APIRoute):
    """Cache one JSON body through a strict byte cap before FastAPI parses it."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def bounded(request: Request):
            if request.method in {"POST", "PUT", "PATCH"}:
                if request.headers.get("content-encoding") not in (None, "", "identity"):
                    raise _http(415, "encoded_cloud_agent_request_rejected")
                content_type = request.headers.get("content-type", "").split(";", 1)[0]
                if content_type.strip().lower() != "application/json":
                    raise _http(415, "cloud_agent_request_must_be_json")
                declared = request.headers.get("content-length")
                declared_size: int | None = None
                if declared is not None:
                    if not declared.isascii() or not declared.isdecimal():
                        raise _http(400, "cloud_agent_content_length_invalid")
                    declared_size = int(declared)
                    if declared_size > _MAX_JSON_BODY_BYTES:
                        raise _http(413, "cloud_agent_request_too_large")
                chunks: list[bytes] = []
                observed = 0
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    observed += len(chunk)
                    if observed > _MAX_JSON_BODY_BYTES:
                        raise _http(413, "cloud_agent_request_too_large")
                    chunks.append(bytes(chunk))
                if declared_size is not None and observed != declared_size:
                    raise _http(400, "cloud_agent_content_length_invalid")
                request._body = b"".join(chunks)  # noqa: SLF001 - Starlette cache contract
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "cloud_agent_governance_contract_invalid"},
                    headers=_NO_STORE,
                )
            except HTTPException as exc:
                headers = dict(exc.headers or {})
                headers.update(_NO_STORE)
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=exc.detail,
                    headers=headers,
                ) from exc

        return bounded


class PolicyEvaluationIn(_StrictModel):
    agent_ref: AgentRef
    expected_agent_revision: int = Field(ge=1, le=2**63 - 1)
    policy_ref: PolicyRef
    policy_revision: int = Field(ge=1, le=2**63 - 1)
    policy_sha256: Digest
    requested_action: EnforcementAction
    target_ref: TargetRef
    idempotency_key: IdempotencyKey


class IntentPreparationIn(_StrictModel):
    evaluation_ref: EvaluationRef
    idempotency_key: IdempotencyKey


class IntentDispatchIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=2**63 - 1)
    idempotency_key: IdempotencyKey


class ReconciliationIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=2**63 - 1)
    resolution: ReconciliationResolution
    evidence_ref: EvidenceRef
    evidence_sha256: Digest
    observed_inventory_revision: int = Field(ge=1, le=2**63 - 1)
    idempotency_key: IdempotencyKey


class FrameworkEvidenceLinkIn(_StrictModel):
    evidence_ref: EvidenceRef
    evidence_sha256: Digest
    relation_code: Code


class FrameworkApplicabilityIn(_StrictModel):
    control_id: ControlId
    workload_ref: WorkloadRef
    status: ApplicabilityStatus
    reason_code: Code
    evidence_links: list[FrameworkEvidenceLinkIn] = Field(
        default_factory=list,
        max_length=128,
    )


class FrameworkReviewIn(_StrictModel):
    pack_version: FrameworkVersion
    decision: ReviewDecision
    applicability: list[FrameworkApplicabilityIn] = Field(
        min_length=1,
        max_length=2_000,
    )
    idempotency_key: IdempotencyKey


class FrameworkAttestationIn(_StrictModel):
    review_ref: ReviewRef
    expected_review_revision: int = Field(ge=1, le=2**63 - 1)
    evidence_links: list[FrameworkEvidenceLinkIn] = Field(
        min_length=1,
        max_length=128,
    )
    idempotency_key: IdempotencyKey


class FrameworkActivationIn(_StrictModel):
    pack_version: FrameworkVersion
    review_ref: ReviewRef
    attestation_ref: AttestationRef
    expected_activation_revision: int = Field(ge=0, le=2**63 - 1)
    action: ActivationAction = "activate"
    idempotency_key: IdempotencyKey


class CloudAgentGovernanceOperatorRuntimeLike(Protocol):
    tenant_id: str
    principal_ref: str

    def status(self) -> Mapping[str, object]: ...

    def list_inventory(self, *, offset: int, limit: int) -> Mapping[str, object]: ...

    def verify_integrity(self) -> Mapping[str, object]: ...

    def evaluate_policy(self, **kwargs: object) -> object: ...

    def prepare_intent(self, **kwargs: object) -> object: ...

    def get_intent(self, intent_ref: str) -> object: ...

    def dispatch_intent(self, intent_ref: str, **kwargs: object) -> object: ...

    def reconcile_intent(self, intent_ref: str, **kwargs: object) -> object: ...

    def review_framework_pack(self, **kwargs: object) -> object: ...

    def attest_framework_review(self, **kwargs: object) -> object: ...

    def activate_framework_pack(self, **kwargs: object) -> object: ...


class CloudAgentGovernanceDependencies:
    """Lazy, server-owned composition seams for deployments and tests."""

    def __init__(
        self,
        *,
        runtime_for: Callable[[str, str, Request], CloudAgentGovernanceOperatorRuntimeLike]
        | None = None,
        enabled_probe: Callable[[str], bool] | None = None,
    ) -> None:
        for dependency in (runtime_for, enabled_probe):
            if dependency is not None and not callable(dependency):
                raise TypeError("cloud-agent governance dependency must be callable")
        self.runtime_for = runtime_for
        self.enabled_probe = enabled_probe or (lambda _tenant_id: False)


class CloudAgentGovernanceOperatorRuntime:
    """Tenant/principal-bound projection over the core governance authority."""

    __slots__ = ("_authority", "_backend", "_tenant_authority", "principal_ref", "tenant_id")

    def __init__(
        self,
        *,
        tenant_id: str,
        principal_ref: str,
        backend: object,
        tenant_authority: object,
    ) -> None:
        from maverick import ai_cloud_agent_governance as cloud

        if (
            not isinstance(tenant_id, str)
            or not tenant_id.strip()
            or len(tenant_id) > 512
            or not isinstance(principal_ref, str)
            or not principal_ref.startswith("prn_")
        ):
            raise cloud.CloudAgentBindingError("operator runtime authority is invalid")
        if not isinstance(backend, cloud.CloudAgentGovernanceRuntime):
            raise cloud.CloudAgentBindingError("cloud-agent backend runtime is invalid")
        if not isinstance(tenant_authority, cloud.TenantAuthority):
            raise cloud.CloudAgentBindingError("tenant authority is invalid")
        expected_tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected_tenant, tenant_authority.tenant_sha256):
            raise cloud.CloudAgentBindingError("tenant authority does not match request context")
        if backend.config.enable is not True:
            raise cloud.CloudAgentGovernanceDisabled("cloud-agent governance is disabled")
        self.tenant_id = tenant_id
        self.principal_ref = principal_ref
        self._backend = backend
        self._authority = backend.authority
        self._tenant_authority = tenant_authority

    def status(self) -> Mapping[str, object]:
        from maverick import ai_cloud_agent_governance as cloud

        bindings: list[dict[str, object]] = []
        for provider in self._backend.config.enabled_providers:
            bound = False
            active = False
            try:
                self._authority.resolve_provider_binding(
                    self._tenant_authority,
                    provider=provider,
                    require_active=False,
                )
                bound = True
                self._authority.resolve_provider_binding(
                    self._tenant_authority,
                    provider=provider,
                    require_active=True,
                )
                active = True
            except cloud.CloudAgentRevoked:
                bound = True
            except cloud.CloudAgentBindingError:
                pass
            bindings.append({"provider": provider, "bound": bound, "active": active})
        return {
            "schema": "lightwork.cloud-agent-governance-api-status.v1",
            "enabled": True,
            "tenant_bound": True,
            "principal_bound": True,
            "configured_providers": list(self._backend.config.enabled_providers),
            "provider_bindings": bindings,
            "databricks_framework_enabled": (self._backend.config.databricks_framework_enable),
            "inventory_runner_configured": self._backend.inventory_runner is not None,
            "store_mode": self._backend.store_mode,
            "production_durability_configured": False,
            "production_runtime_available": False,
            "canonical_authority_process_local": True,
            "live_provider_inventory_acceptance_verified": False,
            "live_provider_write_acceptance_verified": False,
            "live_customer_acceptance_verified": False,
            "provider_acceptance_is_write_proof": False,
            "relationship_completeness_is_inferred": False,
        }

    def list_inventory(self, *, offset: int, limit: int) -> Mapping[str, object]:
        providers: list[dict[str, object]] = []
        for provider in self._backend.config.enabled_providers:
            page = self._authority.list_inventory(
                self._tenant_authority,
                provider=provider,
                offset=offset,
                limit=limit,
            )
            records: list[dict[str, object]] = []
            for record in page.records:
                relationships = record.agent.relationships
                records.append(
                    {
                        "provider": record.provider,
                        "agent_ref": record.agent.agent_ref,
                        "record_revision": record.record_revision,
                        "inventory_revision": record.inventory_revision,
                        "configuration_sha256": record.agent.configuration_sha256,
                        "lifecycle_state": record.agent.lifecycle_state,
                        "first_seen_at": record.first_seen_at,
                        "last_seen_at": record.last_seen_at,
                        "drifted": record.drifted,
                        "relationship_sha256": relationships.relationship_sha256,
                        "tool_refs": list(relationships.tool_refs),
                        "model_refs": list(relationships.model_refs),
                        "data_source_refs": list(relationships.data_source_refs),
                        "tool_inventory_complete": relationships.tool_inventory_complete,
                        "model_inventory_complete": relationships.model_inventory_complete,
                        "data_source_inventory_complete": (
                            relationships.data_source_inventory_complete
                        ),
                        "state_sha256": record.state_sha256,
                    }
                )
            providers.append(
                {
                    "provider": provider,
                    "offset": page.offset,
                    "next_offset": page.next_offset,
                    "limit": page.limit,
                    "total": page.total,
                    "metadata": (
                        None
                        if page.metadata is None
                        else {
                            "inventory_revision": page.metadata.inventory_revision,
                            "snapshot_complete": page.metadata.snapshot_complete,
                            "incomplete_reason_codes": list(page.metadata.incomplete_reason_codes),
                            "captured_at": page.metadata.captured_at,
                            "agent_count": page.metadata.agent_count,
                            "state_sha256": page.metadata.state_sha256,
                        }
                    ),
                    "records": records,
                    "page_sha256": page.page_sha256,
                }
            )
        return {
            "schema": "lightwork.cloud-agent-governance-inventory.v1",
            "providers": providers,
            "raw_provider_identifiers_included": False,
            "inventory_is_completeness_claim": False,
        }

    def verify_integrity(self) -> Mapping[str, object]:
        return {
            "schema": "lightwork.cloud-agent-governance-integrity.v1",
            "tenant_state_root_sha256": self._authority.verify_tenant_integrity(
                self._tenant_authority
            ),
            "verified": True,
        }

    def evaluate_policy(self, **kwargs: object) -> object:
        return self._authority.evaluate_policy(self._tenant_authority, **kwargs)

    def prepare_intent(self, **kwargs: object) -> object:
        return self._authority.prepare_enforcement_intent(
            self._tenant_authority,
            **kwargs,
        )

    def get_intent(self, intent_ref: str) -> object:
        return self._authority.get_enforcement_intent(
            self._tenant_authority,
            intent_ref=intent_ref,
        )

    def dispatch_intent(self, intent_ref: str, **kwargs: object) -> object:
        return self._authority.dispatch_enforcement_intent(
            self._tenant_authority,
            intent_ref=intent_ref,
            **kwargs,
        )

    def reconcile_intent(self, intent_ref: str, **kwargs: object) -> object:
        return self._authority.resolve_enforcement_reconciliation(
            self._tenant_authority,
            intent_ref=intent_ref,
            resolver_ref=self.principal_ref,
            **kwargs,
        )

    @staticmethod
    def _evidence_links(values: Sequence[Mapping[str, object]]) -> tuple[object, ...]:
        from maverick import ai_cloud_agent_governance as cloud

        return tuple(
            cloud.FrameworkEvidenceLink(
                value["evidence_ref"],
                value["evidence_sha256"],
                value["relation_code"],
            )
            for value in values
        )

    def review_framework_pack(self, **kwargs: object) -> object:
        from maverick import ai_cloud_agent_governance as cloud

        raw_applicability = kwargs.pop("applicability")
        if not isinstance(raw_applicability, Sequence):
            raise ValueError("framework applicability is invalid")
        applicability = tuple(
            cloud.FrameworkApplicability(
                item["control_id"],
                item["workload_ref"],
                item["status"],
                item["reason_code"],
                self._evidence_links(item["evidence_links"]),
            )
            for item in raw_applicability
        )
        return self._authority.review_framework_pack(
            self._tenant_authority,
            reviewer_ref=self.principal_ref,
            applicability=applicability,
            **kwargs,
        )

    def attest_framework_review(self, **kwargs: object) -> object:
        raw_links = kwargs.pop("evidence_links")
        if not isinstance(raw_links, Sequence):
            raise ValueError("framework evidence links are invalid")
        return self._authority.attest_framework_review(
            self._tenant_authority,
            attestor_ref=self.principal_ref,
            evidence_links=self._evidence_links(raw_links),
            **kwargs,
        )

    def activate_framework_pack(self, **kwargs: object) -> object:
        return self._authority.activate_framework_pack(
            self._tenant_authority,
            **kwargs,
        )


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise _http(409, "active_tenant_required")
    return tenant


def _principal_ref(request: Request, tenant_id: str) -> str:
    subject = caller_principal(request)
    if subject is None:
        subject = "authorized-local-operator"
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 512:
        raise _http(401, "authenticated_principal_required")
    digest = hashlib.sha256(
        f"lightwork.cloud-agent-governance.api-principal.v1:{tenant_id}:{subject}".encode()
    ).hexdigest()
    return f"prn_{digest[:32]}"


def _http(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail, headers=_NO_STORE)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump())
    if hasattr(value, "payload") and callable(value.payload):
        return _jsonable(value.payload())
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _response(value: object) -> JSONResponse:
    return JSONResponse(content=_jsonable(value), headers=_NO_STORE)


def create_ai_cloud_agent_governance_router(  # noqa: C901
    dependencies: CloudAgentGovernanceDependencies | None = None,
) -> APIRouter:
    deps = dependencies or CloudAgentGovernanceDependencies()
    router = APIRouter(
        prefix="/assurance/cloud-agents",
        tags=["ai-cloud-agent-governance"],
        route_class=_BoundedJSONRoute,
    )

    def capability(request: Request, tenant_id: str) -> bool:
        if any(key in _SERVER_OWNED_QUERY_FIELDS for key in request.query_params):
            raise _http(422, "server_owned_selector_rejected")
        selected = getattr(
            request.app.state,
            "ai_cloud_agent_governance_enabled_probe",
            None,
        )
        probe = selected if callable(selected) else deps.enabled_probe
        try:
            result = probe(tenant_id)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise _http(503, "cloud_agent_governance_configuration_unavailable") from exc
        if type(result) is not bool:
            raise _http(503, "cloud_agent_governance_configuration_unavailable")
        return result

    def authority(
        request: Request,
    ) -> tuple[object, str, str, CloudAgentGovernanceOperatorRuntimeLike]:
        from maverick import ai_cloud_agent_governance as cloud

        tenant = _tenant_id()
        if not capability(request, tenant):
            raise _http(403, "cloud_agent_governance_disabled")
        principal = _principal_ref(request, tenant)
        selected = getattr(
            request.app.state,
            "ai_cloud_agent_governance_runtime_factory",
            None,
        )
        factory = selected if callable(selected) else deps.runtime_for
        if not callable(factory):
            raise _http(503, "cloud_agent_governance_authority_unavailable")
        try:
            runtime = factory(tenant, principal, request)
        except (KeyboardInterrupt, SystemExit):
            raise
        except (cloud.CloudAgentGovernanceError, OSError, RuntimeError, ValueError) as exc:
            raise _http(503, "cloud_agent_governance_authority_unavailable") from exc
        required = (
            "status",
            "list_inventory",
            "verify_integrity",
            "evaluate_policy",
            "prepare_intent",
            "get_intent",
            "dispatch_intent",
            "reconcile_intent",
            "review_framework_pack",
            "attest_framework_review",
            "activate_framework_pack",
        )
        if any(not callable(getattr(runtime, name, None)) for name in required):
            raise _http(503, "cloud_agent_governance_authority_unavailable")
        if runtime.tenant_id != tenant or runtime.principal_ref != principal:
            raise _http(503, "cloud_agent_governance_authority_unavailable")
        return cloud, tenant, principal, runtime

    async def call(cloud: object, function: Callable[..., object], *args: object, **kwargs: object):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except (KeyboardInterrupt, SystemExit):
            raise
        except cloud.CloudAgentGovernanceDisabled as exc:  # type: ignore[attr-defined]
            raise _http(403, "cloud_agent_governance_disabled") from exc
        except cloud.CloudAgentAmbiguousEffect as exc:  # type: ignore[attr-defined]
            raise _http(409, "cloud_agent_dispatch_requires_reconciliation") from exc
        except (
            cloud.CloudAgentStaleRevision,  # type: ignore[attr-defined]
            cloud.CloudAgentIdempotencyConflict,  # type: ignore[attr-defined]
            cloud.CloudAgentRevoked,  # type: ignore[attr-defined]
            cloud.CloudAgentBindingError,  # type: ignore[attr-defined]
            cloud.CloudAgentStateError,  # type: ignore[attr-defined]
        ) as exc:
            raise _http(409, "cloud_agent_governance_state_conflict") from exc
        except ValueError as exc:
            raise _http(422, "cloud_agent_governance_contract_invalid") from exc
        except cloud.CloudAgentIntegrityError as exc:  # type: ignore[attr-defined]
            raise _http(503, "cloud_agent_governance_integrity_failed") from exc
        except (
            cloud.CloudAgentAdapterError,  # type: ignore[attr-defined]
            cloud.CloudAgentGovernanceError,  # type: ignore[attr-defined]
            OSError,
            RuntimeError,
        ) as exc:
            raise _http(503, "cloud_agent_governance_dependency_unavailable") from exc

    @router.get("/status")
    async def status(request: Request):
        require_permission(request, "view")
        tenant = _tenant_id()
        if not capability(request, tenant):
            return _response(
                {
                    "schema": "lightwork.cloud-agent-governance-api-status.v1",
                    "enabled": False,
                    "complete": False,
                    "runtime_constructed": False,
                    "production_durability_configured": False,
                    "production_runtime_available": False,
                    "live_provider_inventory_acceptance_verified": False,
                    "live_provider_write_acceptance_verified": False,
                    "live_customer_acceptance_verified": False,
                }
            )
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.status))

    @router.get("/inventory")
    async def inventory(
        request: Request,
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        require_permission(request, "view")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.list_inventory, offset=offset, limit=limit))

    @router.get("/integrity")
    async def integrity(request: Request):
        require_permission(request, "audit")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.verify_integrity))

    @router.post("/policy-evaluations")
    async def evaluate_policy(request: Request, body: PolicyEvaluationIn):
        require_permission(request, "operate")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.evaluate_policy, **body.model_dump()))

    @router.post("/enforcement-intents")
    async def prepare_intent(request: Request, body: IntentPreparationIn):
        require_permission(request, "admin")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.prepare_intent, **body.model_dump()))

    @router.get("/enforcement-intents/{intent_ref}")
    async def get_intent(request: Request, intent_ref: IntentRef):
        require_permission(request, "view")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(await call(cloud, runtime.get_intent, intent_ref))

    @router.post("/enforcement-intents/{intent_ref}/dispatch")
    async def dispatch_intent(
        request: Request,
        intent_ref: IntentRef,
        body: IntentDispatchIn,
    ):
        require_permission(request, "admin")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(
            await call(
                cloud,
                runtime.dispatch_intent,
                intent_ref,
                **body.model_dump(),
            )
        )

    @router.post("/enforcement-intents/{intent_ref}/reconcile")
    async def reconcile_intent(
        request: Request,
        intent_ref: IntentRef,
        body: ReconciliationIn,
    ):
        require_permission(request, "admin")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(
            await call(
                cloud,
                runtime.reconcile_intent,
                intent_ref,
                **body.model_dump(),
            )
        )

    @router.post("/databricks/framework-reviews")
    async def review_framework(request: Request, body: FrameworkReviewIn):
        require_permission(request, "operate")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(
            await call(
                cloud,
                runtime.review_framework_pack,
                **body.model_dump(),
            )
        )

    @router.post("/databricks/framework-attestations")
    async def attest_framework(request: Request, body: FrameworkAttestationIn):
        require_permission(request, "admin")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(
            await call(
                cloud,
                runtime.attest_framework_review,
                **body.model_dump(),
            )
        )

    @router.post("/databricks/framework-activations")
    async def activate_framework(request: Request, body: FrameworkActivationIn):
        require_permission(request, "admin")
        cloud, _tenant, _principal, runtime = authority(request)
        return _response(
            await call(
                cloud,
                runtime.activate_framework_pack,
                **body.model_dump(),
            )
        )

    return router


router = create_ai_cloud_agent_governance_router()

__all__ = [
    "CloudAgentGovernanceDependencies",
    "CloudAgentGovernanceOperatorRuntime",
    "CloudAgentGovernanceOperatorRuntimeLike",
    "FrameworkActivationIn",
    "FrameworkApplicabilityIn",
    "FrameworkAttestationIn",
    "FrameworkEvidenceLinkIn",
    "FrameworkReviewIn",
    "IntentDispatchIn",
    "IntentPreparationIn",
    "PolicyEvaluationIn",
    "ReconciliationIn",
    "create_ai_cloud_agent_governance_router",
    "router",
]
