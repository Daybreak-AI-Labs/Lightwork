"""Strict HTTP composition for packaged governance agents.

The API never accepts tenant identifiers, actor identities, raw context bytes,
adapter authorities, execution credentials, or dispatch destinations from the
request body.  Those authorities are independently resolved by the deployed
application.  Consequently a caller can request analysis or execution, but
cannot choose the evidence bytes, model/adapter, provider transport, or runtime
principal that authorizes it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission, require_permission

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_OP_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$"

OpaqueId = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=_ID_PATTERN),
]
ObjectPath = Annotated[
    str,
    ApiPath(min_length=1, max_length=128, pattern=_ID_PATTERN),
]
IdempotencyKey = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=192, pattern=_OP_PATTERN),
]
AgentKind = Literal[
    "breach_response",
    "privacy_risk_assessment",
    "program_copilot",
    "knowledgebase_governance",
    "third_party_risk",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PolicyPublishIn(_StrictModel):
    revision: int = Field(ge=1, le=2_147_483_647)
    agent_kind: AgentKind
    allowed_capabilities: list[OpaqueId] = Field(min_length=1, max_length=32)
    required_approvals: int = Field(ge=1, le=8)
    execution_enabled: bool = False
    approver_principals: list[OpaqueId] = Field(min_length=2, max_length=8)
    valid_until: float | int = Field(gt=0, lt=100_000_000_000.0)
    idempotency_key: IdempotencyKey


class PolicyRevokeIn(_StrictModel):
    revision: int = Field(ge=1, le=2_147_483_647)
    reason_code: Literal[
        "evidence_sufficient",
        "evidence_partial",
        "evidence_conflicting",
        "evidence_stale",
        "authority_required",
        "policy_threshold",
        "human_override",
        "false_positive",
        "risk_accepted",
        "not_applicable",
    ]


class ContextReferenceIn(_StrictModel):
    item_id: OpaqueId
    source_kind: Literal[
        "audit",
        "assessment",
        "control",
        "document",
        "incident",
        "inventory",
        "knowledge",
        "metric",
        "policy",
        "provider_signal",
        "risk",
        "vendor",
    ]
    source_ref: OpaqueId
    source_revision: int = Field(ge=1, le=2_147_483_647)


class AnalyzeRunIn(_StrictModel):
    policy_id: OpaqueId
    policy_revision: int = Field(ge=1, le=2_147_483_647)
    capability: OpaqueId
    context: list[ContextReferenceIn] = Field(min_length=1, max_length=1_000)
    idempotency_key: IdempotencyKey


class ReviewRunIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=2_147_483_647)
    decision: Literal["approve", "reject"]
    rationale_code: Literal[
        "evidence_sufficient",
        "evidence_partial",
        "evidence_conflicting",
        "evidence_stale",
        "authority_required",
        "policy_threshold",
        "human_override",
        "false_positive",
        "risk_accepted",
        "not_applicable",
    ]


class ExpectedRevisionIn(_StrictModel):
    expected_revision: int = Field(ge=1, le=2_147_483_647)


class RevokeRunIn(ExpectedRevisionIn):
    reason_code: Literal[
        "evidence_sufficient",
        "evidence_partial",
        "evidence_conflicting",
        "evidence_stale",
        "authority_required",
        "policy_threshold",
        "human_override",
        "false_positive",
        "risk_accepted",
        "not_applicable",
    ]


class PurgeIn(_StrictModel):
    limit: int = Field(default=100, ge=1, le=1_000)


def _enabled() -> bool:
    from maverick import governance_agents as ops

    return ops.enabled()


def governance_agents_enabled_for_page() -> bool:
    """Read only the feature gate; do not construct stores or adapters."""

    return _enabled()


def _ops():
    from maverick import governance_agents as ops

    try:
        ops.require_enabled()
    except ops.GovernanceAgentConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return ops


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using governance agents",
        )
    return tenant


def _principal(request: Request, tenant_id: str) -> str:
    verified = getattr(request.state, "principal", None)
    subject = getattr(verified, "principal", None) or getattr(verified, "sub", None)
    if not isinstance(subject, str) or not subject:
        raise HTTPException(
            status_code=401,
            detail="an authenticated principal is required for governance agents",
        )
    digest = hashlib.sha256(
        (f"lightwork.governance-agent.identity.v1:{tenant_id}:{subject}").encode()
    ).hexdigest()
    return f"ga-principal-{digest[:32]}"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _default_store_for(
    tenant_id: str,
    principal_ref: str,
    policy_approver_resolver: Callable[[str, str, int], Sequence[str]] | None,
    analysis_binding_resolver: Callable[[str, str], Any] | None = None,
    execution_binding_resolver: Callable[[str, str], Any] | None = None,
    execution_token_escrow: Callable[[str, str, str, str], bool] | None = None,
    execution_token_resolver: Callable[[str, str], str] | None = None,
):
    from maverick.governance_agents import GovernanceAgentStore
    from maverick.paths import data_dir

    return GovernanceAgentStore(
        data_dir("governance_agents", "governance-agents.sqlite3", tenant=tenant_id),
        principal_resolver=lambda: principal_ref,
        policy_approver_resolver=policy_approver_resolver,
        analysis_binding_resolver=analysis_binding_resolver,
        execution_binding_resolver=execution_binding_resolver,
        execution_token_escrow=execution_token_escrow,
        execution_token_resolver=execution_token_resolver,
    )


class GovernanceAgentDependencies:
    """Server-owned authority seams for evidence, adapters, and dispatch."""

    def __init__(
        self,
        *,
        store_for: Callable[[str, str, Any], Any] = _default_store_for,
        context_resolver: Callable[[str, Sequence[Mapping[str, object]]], Any] | None = None,
        adapter_resolver: Callable[[str, str], tuple[Any, Any]] | None = None,
        dispatcher_resolver: Callable[[str, str], Any] | None = None,
        execution_token_escrow: Callable[[str, str, str, str], bool] | None = None,
        execution_token_resolver: Callable[[str, str], str] | None = None,
    ) -> None:
        self.store_for = store_for
        self.context_resolver = context_resolver
        self.adapter_resolver = adapter_resolver
        self.dispatcher_resolver = dispatcher_resolver
        self.execution_token_escrow = execution_token_escrow
        self.execution_token_resolver = execution_token_resolver


def create_governance_agents_router(  # noqa: C901 - explicit endpoint trust boundaries
    dependencies: GovernanceAgentDependencies | None = None,
) -> APIRouter:
    deps = dependencies or GovernanceAgentDependencies()
    router = APIRouter(prefix="/governance-agents", tags=["governance-agents"])

    def authority(request: Request):
        ops = _ops()
        tenant = _tenant_id()
        principal = _principal(request, tenant)
        factory = getattr(request.app.state, "governance_agents_store_factory", None)
        policy_resolver = getattr(
            request.app.state, "governance_agent_policy_approver_resolver", None
        )
        analysis_resolver = getattr(
            request.app.state,
            "governance_agent_adapter_resolver",
            deps.adapter_resolver,
        )
        execution_resolver = getattr(
            request.app.state,
            "governance_agent_dispatcher_resolver",
            deps.dispatcher_resolver,
        )
        token_escrow = getattr(
            request.app.state,
            "governance_agent_execution_token_escrow",
            deps.execution_token_escrow,
        )
        token_resolver = getattr(
            request.app.state,
            "governance_agent_execution_token_resolver",
            deps.execution_token_resolver,
        )

        def resolver() -> str:
            return principal

        try:
            store = (
                factory(
                    tenant_id=tenant,
                    principal_ref=principal,
                    principal_resolver=resolver,
                    policy_approver_resolver=policy_resolver,
                    analysis_binding_resolver=analysis_resolver,
                    execution_binding_resolver=execution_resolver,
                    execution_token_escrow=token_escrow,
                    execution_token_resolver=token_resolver,
                    request=request,
                )
                if callable(factory)
                else deps.store_for(
                    tenant,
                    principal,
                    policy_resolver,
                    analysis_resolver,
                    execution_resolver,
                    token_escrow,
                    token_resolver,
                )
            )
        except ops.GovernanceAgentConfigError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (ops.GovernanceAgentError, OSError) as exc:
            raise HTTPException(
                status_code=503, detail="governance-agent store is unavailable"
            ) from exc
        if store is None:
            raise HTTPException(status_code=503, detail="governance-agent store is unavailable")
        return ops, tenant, principal, store

    async def call(ops, function, *args, **kwargs):
        try:
            return await run_in_threadpool(function, *args, **kwargs)
        except ops.GovernanceAgentNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ops.GovernanceAgentPermissionDenied as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ops.GovernanceAgentConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ops.GovernanceAgentValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except ops.GovernanceAgentConfigError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ops.GovernanceAgentIntegrityError as exc:
            raise HTTPException(
                status_code=503,
                detail="governance-agent integrity verification failed",
            ) from exc
        except ops.GovernanceAgentError as exc:
            raise HTTPException(
                status_code=503, detail="governance-agent operation failed"
            ) from exc

    def context_resolver(request: Request):
        return getattr(
            request.app.state,
            "governance_agent_context_resolver",
            deps.context_resolver,
        )

    def adapter_resolver(request: Request):
        return getattr(
            request.app.state,
            "governance_agent_adapter_resolver",
            deps.adapter_resolver,
        )

    def dispatcher_resolver(request: Request):
        return getattr(
            request.app.state,
            "governance_agent_dispatcher_resolver",
            deps.dispatcher_resolver,
        )

    @router.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        require_permission(request, "view")
        tenant = _tenant_id()
        return {
            "schema": "lightwork.governance-agent-status.v1",
            "enabled": _enabled(),
            "principal_ref": _principal(request, tenant),
            "packaged_agents": [
                "breach_response",
                "knowledgebase_governance",
                "privacy_risk_assessment",
                "program_copilot",
                "third_party_risk",
            ],
            "raw_context_accepted_over_http": False,
            "caller_selected_adapters": False,
            "caller_selected_dispatchers": False,
            "execution_default_off": True,
            "durable_execution_token_vault_required": True,
            "human_review_required": True,
            "live_provider_acceptance_verified": False,
            "licensed_content_corpus_bundled": False,
        }

    @router.put("/policies/{policy_id}")
    async def publish_policy(request: Request, policy_id: ObjectPath, body: PolicyPublishIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        return _jsonable(
            await call(
                ops,
                store.publish_policy,
                tenant,
                policy_id=policy_id,
                **body.model_dump(),
            )
        )

    @router.post("/policies/{policy_id}/revoke")
    async def revoke_policy(request: Request, policy_id: ObjectPath, body: PolicyRevokeIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        return _jsonable(
            await call(
                ops,
                store.revoke_policy,
                tenant,
                policy_id,
                **body.model_dump(),
            )
        )

    @router.put("/runs/{run_id}/analysis")
    async def analyze(request: Request, run_id: ObjectPath, body: AnalyzeRunIn):
        require_permission(request, "operate")
        ops, tenant, _principal_ref, store = authority(request)
        resolve_context = context_resolver(request)
        resolve_adapter = adapter_resolver(request)
        if not callable(resolve_context) or not callable(resolve_adapter):
            raise HTTPException(
                status_code=503,
                detail="governance-agent analysis composition is unavailable",
            )
        references = [item.model_dump() for item in body.context]
        resolved_context = await call(ops, resolve_context, tenant, references)
        policy = await call(
            ops,
            store.get_policy,
            tenant,
            body.policy_id,
            revision=body.policy_revision,
        )
        resolved_authority, resolved_adapter = await call(
            ops,
            resolve_adapter,
            tenant,
            policy.agent_kind,
        )
        payload = body.model_dump(exclude={"context"})
        return _jsonable(
            await call(
                ops,
                store.submit_run,
                tenant,
                run_id=run_id,
                context=resolved_context,
                adapter_authority=resolved_authority,
                adapter=resolved_adapter,
                **payload,
            )
        )

    @router.get("/runs/{run_id}")
    async def get_run(request: Request, run_id: ObjectPath):
        require_permission(request, "view")
        ops, tenant, _principal_ref, store = authority(request)
        return _jsonable(await call(ops, store.get_run, tenant, run_id))

    @router.get("/runs")
    async def list_runs(
        request: Request,
        state: str | None = Query(default=None, max_length=32),
        limit: int = Query(default=100, ge=1, le=1_000),
    ):
        require_permission(request, "view")
        ops, tenant, _principal_ref, store = authority(request)
        items = await call(ops, store.list_runs, tenant, state=state, limit=limit)
        return {"items": _jsonable(items)}

    @router.post("/runs/{run_id}/review")
    async def review(request: Request, run_id: ObjectPath, body: ReviewRunIn):
        require_permission(request, "operate")
        ops, tenant, _principal_ref, store = authority(request)
        return _jsonable(await call(ops, store.review, tenant, run_id, **body.model_dump()))

    @router.post("/runs/{run_id}/execute")
    async def execute(request: Request, run_id: ObjectPath, body: ExpectedRevisionIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        resolve_dispatcher = dispatcher_resolver(request)
        if not callable(resolve_dispatcher):
            raise HTTPException(
                status_code=503,
                detail="governance-agent execution composition is unavailable",
            )
        run = await call(ops, store.get_run, tenant, run_id)
        try:
            binding = await run_in_threadpool(resolve_dispatcher, tenant, run.agent_kind)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="governance-agent execution composition is unavailable",
            ) from exc
        if not isinstance(binding, tuple) or len(binding) != 3 or not callable(binding[1]):
            raise HTTPException(
                status_code=503,
                detail="governance-agent execution composition is unavailable",
            )
        dispatcher = binding[1]
        return _jsonable(
            await call(
                ops,
                store.execute,
                tenant,
                run_id,
                dispatcher=dispatcher,
                **body.model_dump(),
            )
        )

    @router.post("/runs/{run_id}/recover")
    async def recover(request: Request, run_id: ObjectPath, body: ExpectedRevisionIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        resolve_dispatcher = dispatcher_resolver(request)
        if not callable(resolve_dispatcher):
            raise HTTPException(
                status_code=503,
                detail="governance-agent recovery composition is unavailable",
            )
        run = await call(ops, store.get_run, tenant, run_id)
        binding = await run_in_threadpool(resolve_dispatcher, tenant, run.agent_kind)
        if not isinstance(binding, tuple) or len(binding) != 3 or not callable(binding[1]):
            raise HTTPException(
                status_code=503,
                detail="governance-agent recovery composition is unavailable",
            )
        dispatcher = binding[1]
        return _jsonable(
            await call(
                ops,
                store.recover_execution,
                tenant,
                run_id,
                dispatcher=dispatcher,
                **body.model_dump(),
            )
        )

    @router.post("/runs/{run_id}/revoke")
    async def revoke_run(request: Request, run_id: ObjectPath, body: RevokeRunIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        return _jsonable(await call(ops, store.revoke_run, tenant, run_id, **body.model_dump()))

    @router.post("/retention/purge")
    async def purge(request: Request, body: PurgeIn):
        require_global_permission(request, "admin")
        ops, tenant, _principal_ref, store = authority(request)
        return {"purged": await call(ops, store.purge_expired, tenant, **body.model_dump())}

    @router.get("/integrity")
    async def integrity(request: Request):
        require_global_permission(request, "audit")
        ops, tenant, _principal_ref, store = authority(request)
        result = _jsonable(await call(ops, store.verify_integrity, tenant))
        result["valid"] = True
        return result

    return router


router = create_governance_agents_router()

__all__ = [
    "GovernanceAgentDependencies",
    "create_governance_agents_router",
    "governance_agents_enabled_for_page",
    "router",
]
