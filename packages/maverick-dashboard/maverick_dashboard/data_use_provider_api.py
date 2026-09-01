"""Authenticated native-provider API for Data Use Governance.

The surface accepts enrollment metadata and a decision identifier only.  It
never accepts SQL, a provider statement, or caller-authored control IR: every
write is compiled again from the tenant's authoritative governance records.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi import Path as ApiPath
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/data-use/providers", tags=["data-use-providers"])

_LOG = logging.getLogger(__name__)

ProviderId = Literal["snowflake", "databricks"]
_TOKEN = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$"
_IDEMPOTENCY = r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,191}$"
_DIGEST = r"^[0-9a-f]{64}$"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnrollBindingIn(_StrictModel):
    account_or_workspace_id: str = Field(..., min_length=1, max_length=255)
    asset_id: str = Field(..., pattern=_TOKEN)
    catalog: str = Field(..., min_length=1, max_length=255)
    schema_name: str = Field(..., min_length=1, max_length=255)
    table: str = Field(..., min_length=1, max_length=255)
    principal: str = Field(..., min_length=1, max_length=255)
    compute_id: str = Field(..., min_length=1, max_length=255)
    field_map: dict[str, str] = Field(..., min_length=1, max_length=256)
    mask_bindings: dict[str, str] = Field(default_factory=dict, max_length=32)
    row_filter_bindings: dict[str, str] = Field(default_factory=dict, max_length=256)
    state_anchor: str | None = Field(default=None, min_length=1, max_length=767)
    catalog_snapshot_sha256: str = Field(..., pattern=_DIGEST)
    field_inventory_complete: bool
    expected_revision: int = Field(..., ge=0, le=2**31 - 1)
    idempotency_key: str = Field(..., pattern=_IDEMPOTENCY)


class RevokeBindingIn(_StrictModel):
    expected_revision: int = Field(..., ge=1, le=2**31 - 1)
    reason_sha256: str = Field(..., pattern=_DIGEST)
    idempotency_key: str = Field(..., pattern=_IDEMPOTENCY)


class ApplyDecisionIn(_StrictModel):
    expected_decision_sha256: str = Field(..., pattern=_DIGEST)
    idempotency_key: str = Field(..., pattern=_IDEMPOTENCY)


def _tenant_id() -> str:
    from maverick.paths import current_tenant_id_strict

    tenant = current_tenant_id_strict()
    if tenant is None:
        raise HTTPException(
            status_code=409,
            detail="select an active tenant before using native data-use providers",
        )
    return tenant


def _config() -> Mapping[str, Any]:
    from maverick import config

    try:
        if config.config_source_errors():
            return {}
        loaded = config.load_config()
    except Exception:
        return {}
    return loaded if isinstance(loaded, Mapping) else {}


def _provider_ops(provider_id: str):
    from maverick import data_use_provider_adapters as ops

    if not ops.provider_config_enabled(provider_id, _config()):
        raise HTTPException(
            status_code=403,
            detail=(
                f"{provider_id} provider writes are disabled; enable the root, "
                "compiler, enforcement, and direct provider gates"
            ),
        )
    return ops


def _binding_store(ops, tenant_id: str):
    from maverick.paths import data_dir

    return ops.ProviderBindingStore(
        data_dir(
            "data_use_governance",
            "data-use-providers.sqlite3",
            tenant=tenant_id,
        )
    )


def _adapter(
    request: Request,
    *,
    provider_id: str,
    tenant_id: str,
    binding_id: str,
    store,
):
    from maverick import data_use_provider_adapters as ops

    from . import data_use_provider_runtime as runtime

    factory = getattr(request.app.state, "data_use_provider_adapter_factory", None)
    try:
        if callable(factory):
            return factory(
                tenant_id=tenant_id,
                provider_id=provider_id,
                binding_id=binding_id,
                config=_config(),
                store=store,
            )
        return runtime.build_adapter(
            tenant_id=tenant_id,
            provider_id=provider_id,
            binding_id=binding_id,
            config=_config(),
            store=store,
            credential_owner=getattr(request.app.state, "data_use_provider_credential_owner", None),
            transport=getattr(request.app.state, "data_use_provider_transport", None),
        )
    except ops.DataUseProviderConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ops.DataUseProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _operation_sha256(
    *,
    operation: str,
    tenant_id: str,
    provider_id: str,
    binding_id: str,
    idempotency_key: str,
) -> str:
    return hashlib.sha256(
        (
            "lightwork.data-use.provider-operation.v1:"
            f"{operation}:{tenant_id}:{provider_id}:{binding_id}:{idempotency_key}"
        ).encode()
    ).hexdigest()


async def _provider_call(ops, function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except ops.DataUseProviderNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ops.DataUseProviderConflict, ops.DataUseProviderAmbiguousEffect) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ops.DataUseProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ops.DataUseProviderConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ops.DataUseProviderCredentialError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ops.DataUseProviderProtocolError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (
        ops.DataUseProviderRequestNotSent,
        ops.DataUseProviderTransportAmbiguous,
        ops.DataUseProviderUnavailable,
    ) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ops.DataUseProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _provider_audit_required(config: Mapping[str, Any]) -> bool:
    from .data_use_provider_audit_runtime import provider_audit_config_enabled

    return provider_audit_config_enabled(config)


def _provider_audit_principal(request: Request, tenant_id: str) -> str:
    """Return only a server-derived authority reference for composition wiring."""

    from .auth import caller_principal

    principal = caller_principal(request)
    # Global admin authorization has already run.  Auth-off local access and a
    # static dashboard token intentionally have no end-user subject, so bind
    # those modes to an explicit server-owned operator reference.  Hash both
    # modes so an injected composition never receives a raw caller identity.
    subject = principal or "operator:authorized-local"
    digest = hashlib.sha256(
        (f"lightwork.data-use.provider-audit.api-principal.v1:{tenant_id}:{subject}").encode()
    ).hexdigest()
    return f"provider-audit-api.{digest[:40]}"


async def _provider_audit_call(function, *args, **kwargs):
    from maverick import data_use_provider_audit as audit

    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except audit.DataUseProviderAuditNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (
        audit.DataUseProviderAuditConflict,
        audit.DataUseProviderAuditLeaseLost,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except audit.DataUseProviderAuditValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except audit.DataUseProviderAuditConfigError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except audit.DataUseProviderAuditIntegrityError as exc:
        raise HTTPException(
            status_code=503,
            detail="provider-audit integrity verification failed",
        ) from exc
    except audit.DataUseProviderAuditError as exc:
        raise HTTPException(
            status_code=503,
            detail="provider-audit operation failed",
        ) from exc
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="provider-audit dependency is unavailable",
        ) from exc


async def _provider_audit_composition(request: Request, tenant_id: str):
    factory = getattr(
        request.app.state,
        "data_use_provider_audit_composition_factory",
        None,
    )
    if not callable(factory):
        raise HTTPException(
            status_code=503,
            detail="provider-audit composition is unavailable; provider write was not attempted",
        )
    composition = await _provider_audit_call(
        factory,
        tenant_id=tenant_id,
        principal_ref=_provider_audit_principal(request, tenant_id),
        request=request,
        require_worker=False,
    )
    if not callable(getattr(composition, "stage_operation", None)) or not callable(
        getattr(composition, "complete_operation", None)
    ):
        raise HTTPException(
            status_code=503,
            detail="provider-audit composition is unavailable; provider write was not attempted",
        )
    return composition


def _provider_audit_ref(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value != value.strip()
        or not value.isprintable()
    ):
        raise RuntimeError(f"provider-audit {label} is unavailable")
    return value


def _registered_provider_audit(completion: object) -> dict[str, Any]:
    authority = getattr(completion, "authority", None)
    expectation = getattr(completion, "expectation", None)
    return {
        "required": True,
        "authority_id": _provider_audit_ref(
            getattr(authority, "authority_id", None), "authority identifier"
        ),
        "expectation_id": _provider_audit_ref(
            getattr(expectation, "expectation_id", None), "expectation identifier"
        ),
        "state": _provider_audit_ref(getattr(authority, "state", None), "authority state"),
    }


def _authority():
    from .data_use_governance_api import _authority as governance_authority
    from .data_use_governance_api import _compiler_ops

    governance_ops, tenant, governance_store = governance_authority()
    return governance_ops, _compiler_ops(), tenant, governance_store


async def _compile(
    decision_id: str,
    expected_decision_sha256: str,
    *,
    native_adapter: object | None = None,
) -> dict[str, Any]:
    governance_ops, compiler_ops, tenant, governance_store = _authority()
    compiler = compiler_ops.DataUsePolicyCompiler(
        governance_store,
        native_adapter=native_adapter,
    )
    try:
        frozen = await run_in_threadpool(
            compiler.compile,
            tenant,
            decision_id,
            expected_decision_sha256=expected_decision_sha256,
        )
        return compiler_ops.thaw_control_ir(frozen)
    except compiler_ops.DataUseCompilationStale as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except compiler_ops.DataUseCompilationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except compiler_ops.DataUsePolicyCompilerError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except governance_ops.DataUseGovernanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/status")
async def provider_status(request: Request) -> dict[str, Any]:
    """Configuration status only; this endpoint never performs provider I/O."""

    require_permission(request, "view")
    config = _config()
    from maverick import data_use_provider_adapters as ops

    return {
        "schema": "lightwork.data-use.provider-runtime-status.v1",
        "providers": {
            provider: {
                "enabled": ops.provider_config_enabled(provider, config),
                "server_compiled_ir_only": True,
                "arbitrary_sql_accepted": False,
                "credentials_persisted": False,
                "live_provider_acceptance_verified": False,
            }
            for provider in ("snowflake", "databricks")
        },
    }


@router.get("/{provider_id}/bindings")
async def list_bindings(
    request: Request,
    provider_id: ProviderId,
    limit: int = Query(100, ge=1, le=499),
) -> dict[str, Any]:
    require_permission(request, "operate")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    rows = await _provider_call(
        ops, store.list_bindings, tenant, provider_id=provider_id, limit=limit + 1
    )
    return {"items": rows[:limit], "truncated": len(rows) > limit}


@router.get("/{provider_id}/bindings/{binding_id}")
async def get_binding(request: Request, provider_id: ProviderId, binding_id: str) -> dict[str, Any]:
    require_permission(request, "operate")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    item = await _provider_call(ops, store.get_binding, tenant, binding_id)
    if item is None or item.get("provider_id") != provider_id:
        raise HTTPException(status_code=404, detail="provider binding does not exist")
    return item


@router.put("/{provider_id}/bindings/{binding_id}")
async def enroll_binding(
    request: Request,
    provider_id: ProviderId,
    binding_id: str,
    body: EnrollBindingIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    from . import data_use_provider_runtime as runtime

    config = _config()
    section = runtime.provider_section(config, provider_id)
    origin = section.get("origin")
    if not isinstance(origin, str):
        raise HTTPException(status_code=403, detail="provider origin is not configured")
    metadata = runtime.credential_metadata(config, provider_id)
    payload = body.model_dump()
    schema_name = payload.pop("schema_name")
    state_anchor = payload.pop("state_anchor")
    common = {
        **payload,
        "origin": origin,
        "schema": schema_name,
        "credential_key_id": metadata.key_id,
        "credential_revision": metadata.key_revision,
        "credential_authority_sha256": metadata.authority_sha256,
    }
    if provider_id == "snowflake":
        if state_anchor is None:
            raise HTTPException(
                status_code=422, detail="Snowflake requires a state_anchor tag binding"
            )
        function = store.enroll_snowflake
        common["state_anchor"] = state_anchor
    else:
        if state_anchor is not None:
            raise HTTPException(
                status_code=422, detail="Databricks state_anchor is fixed by the adapter"
            )
        function = store.enroll_databricks
    return await _provider_call(ops, function, tenant, binding_id, **common)


@router.post("/{provider_id}/bindings/{binding_id}/revoke")
async def revoke_binding(
    request: Request,
    provider_id: ProviderId,
    binding_id: str,
    body: RevokeBindingIn,
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    existing = await _provider_call(ops, store.get_binding, tenant, binding_id)
    if existing is None or existing.get("provider_id") != provider_id:
        raise HTTPException(status_code=404, detail="provider binding does not exist")
    return await _provider_call(ops, store.revoke_binding, tenant, binding_id, **body.model_dump())


@router.get("/{provider_id}/bindings/{binding_id}/runtime-status")
async def binding_runtime_status(
    request: Request, provider_id: ProviderId, binding_id: str
) -> dict[str, Any]:
    require_permission(request, "operate")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    adapter = _adapter(
        request,
        provider_id=provider_id,
        tenant_id=tenant,
        binding_id=binding_id,
        store=store,
    )
    return await _provider_call(ops, adapter.status)


async def _execute_decision(
    request: Request,
    *,
    provider_id: ProviderId,
    binding_id: str,
    decision_id: str,
    body: ApplyDecisionIn,
    operation: Literal["apply", "rollback"],
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    binding = await _provider_call(ops, store.get_binding, tenant, binding_id)
    if binding is None or binding.get("provider_id") != provider_id:
        raise HTTPException(status_code=404, detail="provider binding does not exist")
    config = _config()
    audit_required = _provider_audit_required(config)
    audit_composition = (
        await _provider_audit_composition(request, tenant) if audit_required else None
    )
    adapter = _adapter(
        request,
        provider_id=provider_id,
        tenant_id=tenant,
        binding_id=binding_id,
        store=store,
    )
    control_ir = await _compile(
        decision_id,
        body.expected_decision_sha256,
        native_adapter=adapter,
    )
    idempotency_sha256 = _operation_sha256(
        operation=operation,
        tenant_id=tenant,
        provider_id=provider_id,
        binding_id=binding_id,
        idempotency_key=body.idempotency_key,
    )
    audit_authority_id: str | None = None
    if audit_composition is not None:
        staged = await _provider_audit_call(
            audit_composition.stage_operation,
            provider_id=provider_id,
            binding_id=binding_id,
            operation_idempotency_sha256=idempotency_sha256,
            control_ir=control_ir,
            operation=operation,
        )
        try:
            audit_authority_id = _provider_audit_ref(
                getattr(staged, "authority_id", None), "authority identifier"
            )
        except RuntimeError as exc:
            raise HTTPException(
                status_code=503,
                detail="provider-audit authority is unavailable; provider write was not attempted",
            ) from exc
    function = adapter.apply_control_ir if operation == "apply" else adapter.rollback_control_ir
    result = await _provider_call(ops, function, control_ir, idempotency_sha256=idempotency_sha256)
    response = {**dict(result), "operation_idempotency_sha256": idempotency_sha256}
    if audit_composition is not None and audit_authority_id is not None:
        try:
            completion = await _provider_audit_call(
                audit_composition.complete_operation,
                audit_authority_id,
            )
            response["provider_audit"] = _registered_provider_audit(completion)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            # The provider mutation already succeeded.  Preserve that truthful
            # result and expose only the durable recovery state; the scheduler
            # or an exact retry can finalize/register the staged authority.
            _LOG.warning(
                "provider-audit completion deferred after provider success (%s)",
                type(exc).__name__,
            )
            response["provider_audit"] = {
                "required": True,
                "authority_id": audit_authority_id,
                "expectation_id": None,
                "state": "recovery_pending",
            }
    return response


@router.post("/{provider_id}/bindings/{binding_id}/decisions/{decision_id}/apply")
async def apply_decision(
    request: Request,
    provider_id: ProviderId,
    binding_id: str,
    decision_id: str,
    body: ApplyDecisionIn,
) -> dict[str, Any]:
    """Perform explicit external I/O using server-compiled desired state."""

    return await _execute_decision(
        request,
        provider_id=provider_id,
        binding_id=binding_id,
        decision_id=decision_id,
        body=body,
        operation="apply",
    )


@router.post("/{provider_id}/bindings/{binding_id}/decisions/{decision_id}/rollback")
async def rollback_decision(
    request: Request,
    provider_id: ProviderId,
    binding_id: str,
    decision_id: str,
    body: ApplyDecisionIn,
) -> dict[str, Any]:
    """Perform explicit safe-deny rollback; prior native state is not restored."""

    return await _execute_decision(
        request,
        provider_id=provider_id,
        binding_id=binding_id,
        decision_id=decision_id,
        body=body,
        operation="rollback",
    )


@router.get("/{provider_id}/bindings/{binding_id}/operations/{idempotency_sha256}")
async def get_operation(
    request: Request,
    provider_id: ProviderId,
    binding_id: str,
    idempotency_sha256: str = ApiPath(..., pattern=_DIGEST),
) -> dict[str, Any]:
    require_permission(request, "operate")
    ops = _provider_ops(provider_id)
    tenant = _tenant_id()
    store = _binding_store(ops, tenant)
    item = await _provider_call(
        ops,
        store.get_operation,
        tenant_id=tenant,
        provider_id=provider_id,
        binding_id=binding_id,
        idempotency_sha256=idempotency_sha256,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="provider operation does not exist")
    return item


__all__ = ["router"]
