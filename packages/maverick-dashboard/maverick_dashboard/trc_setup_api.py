"""Set up continuous-evidence collection from the installation's own settings.

The collector definitions and enrollments the scheduler needs are pinned by
the deployment's source manifests (provider, scope, connection key name and
control bindings live in trusted configuration). These routes let an
administrator create the definition and the enrollment for one configured
source with a button: every identifier, version and scope fingerprint is taken
from the manifest on the server, never from the browser. Approval of a new
definition still needs a second administrator through the existing route.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission, require_permission

router = APIRouter(prefix="/api/v1/compliance/continuous-evidence/setup", tags=["trc-setup"])

LOCAL_KEY = "local-soc2"
_PROVIDER_LABELS = {
    "aws_security_hub": "AWS Security Hub",
    "aws_config": "AWS Config",
    "azure_defender": "Microsoft Defender for Cloud",
    "azure_policy": "Azure Policy",
    "gcp_security_command_center": "Google Cloud Security Command Center",
    "github_security": "GitHub security alerts",
    "okta_posture": "Okta policies",
}


class EnrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    interval_seconds: int = Field(ge=3_600, le=366 * 24 * 3_600)
    retention_seconds: int = Field(ge=86_400, le=10 * 366 * 24 * 3_600)


def _sha(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _store(request: Request):
    from maverick import trc_continuous_evidence as evidence

    from . import trc_continuous_evidence_api as api

    try:
        evidence.require_enabled()
    except evidence.ContinuousEvidenceConfigError as exc:
        raise HTTPException(status_code=403, detail="Continuous evidence is turned off.") from exc
    tenant = api._tenant_id()
    principal = api._principal(request, tenant)
    factory = getattr(request.app.state, "trc_continuous_evidence_store_factory", None)
    try:
        if callable(factory):
            return evidence, tenant, factory(tenant_id=tenant, principal_ref=principal,
                                             principal_resolver=lambda: principal, request=request)
        return evidence, tenant, api._default_store_for(tenant, principal)
    except (evidence.ContinuousEvidenceError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Continuous evidence is not available right now.") from exc


def _manifests(request: Request, tenant: str) -> dict[str, dict[str, Any]]:
    """Configured sources as {key: plan}; plans never reach the browser whole."""
    from maverick.trc_evidence_collectors import LocalSoc2Collector

    from . import trc_continuous_evidence_api as api

    allowed = api._collector_ids(request)
    plans: dict[str, dict[str, Any]] = {}
    if LocalSoc2Collector.collector_id in allowed:
        plans[LOCAL_KEY] = {
            "label": "Lightwork's own security checks",
            "definition_id": "local-soc2", "definition_version": 1,
            "collector_id": LocalSoc2Collector.collector_id, "collector_key_id": "local-soc2-key",
            "capability_revision": 1, "controls": sorted(LocalSoc2Collector._LOCAL_CONTROLS),
            "asset_scope_sha256": _sha({"schema": "lightwork.trc-local-asset-scope.v1", "tenant": tenant}),
            "credential_profile_sha256": _sha({"schema": "lightwork.trc-local-credential-profile.v1", "tenant": tenant}),
            "enrollment_id": "local-soc2", "credential_ready": True,
        }
    composition = getattr(request.app.state, "trc_stock_evidence_composition", None)
    settings = getattr(composition, "_settings", None)
    sources = ()
    if callable(settings):
        try:
            sources = settings(tenant).sources
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail="Current evidence source settings could not be read. Your work is kept; refresh after the connection is available.",
            ) from exc
    else:
        directory = getattr(composition, "directory", None)
        sources = tuple(getattr(directory, "_sources", {}).values()) if directory is not None else ()
    present = getattr(composition, "_credential_ref_present", None)
    for source in sources:
        if source.collector_id not in allowed:
            continue
        plans[source.enrollment_id] = {
            "label": _PROVIDER_LABELS.get(source.provider, source.provider.replace("_", " ").title()),
            "definition_id": source.definition_id, "definition_version": source.definition_version,
            "collector_id": source.collector_id, "collector_key_id": source.collector_key_id,
            "capability_revision": source.capability_revision,
            "controls": [item.control_ref for item in source.bindings],
            "asset_scope_sha256": source.asset_scope_sha256,
            "credential_profile_sha256": source.credential_profile_sha256,
            "enrollment_id": source.enrollment_id,
            "credential_ready": bool(present(source.credential_env)) if callable(present) else None,
        }
    return plans


def _plan(request: Request, tenant: str, key: str) -> dict[str, Any]:
    plan = _manifests(request, tenant).get(key)
    if plan is None:
        raise HTTPException(status_code=404, detail="That source is not configured for this workspace.")
    return plan


@router.get("")
async def configured_sources(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    _evidence, tenant, _store_value = _store(request)
    plans = await run_in_threadpool(_manifests, request, tenant)
    return {"items": [
        {"key": key, "label": plan["label"], "definition_id": plan["definition_id"],
         "definition_version": plan["definition_version"], "enrollment_id": plan["enrollment_id"],
         "control_count": len(plan["controls"]), "credential_ready": plan["credential_ready"]}
        for key, plan in plans.items()
    ]}


@router.post("/{key}/definition")
async def register_definition(request: Request, key: str) -> dict[str, Any]:
    require_global_permission(request, "admin")
    evidence, tenant, store = _store(request)
    plan = _plan(request, tenant, key)

    def write():
        return store.register_definition(
            definition_id=plan["definition_id"], version=plan["definition_version"],
            collector_id=plan["collector_id"], collector_schema_version=1,
            collector_key_id=plan["collector_key_id"], capability_revision=plan["capability_revision"],
            allowed_control_refs=plan["controls"],
        )

    try:
        await run_in_threadpool(write)
    except evidence.ContinuousEvidenceConflict as exc:
        raise HTTPException(status_code=409, detail="This source is already set up.") from exc
    except evidence.ContinuousEvidenceError as exc:
        raise HTTPException(status_code=422, detail="This source's settings could not be used.") from exc
    return {"registered": True}


@router.post("/{key}/enrollment")
async def enroll(request: Request, key: str, body: EnrollIn) -> dict[str, Any]:
    require_global_permission(request, "admin")
    evidence, tenant, store = _store(request)
    plan = _plan(request, tenant, key)

    def write():
        definition = store.get_definition(plan["definition_id"], plan["definition_version"])
        return store.enroll(
            enrollment_id=plan["enrollment_id"], definition_id=plan["definition_id"],
            definition_version=plan["definition_version"],
            expected_definition_integrity_sha256=definition.integrity_sha256,
            asset_scope_sha256=plan["asset_scope_sha256"],
            credential_profile_sha256=plan["credential_profile_sha256"],
            control_refs=plan["controls"], interval_seconds=body.interval_seconds,
            operation_id=f"setup-{uuid.uuid4().hex}", retention_seconds=body.retention_seconds,
        )

    try:
        await run_in_threadpool(write)
    except evidence.ContinuousEvidenceNotFound as exc:
        raise HTTPException(status_code=409, detail="Set up and approve the collection first.") from exc
    except (evidence.ContinuousEvidenceConflict, evidence.ContinuousEvidencePermissionDenied) as exc:
        raise HTTPException(status_code=409, detail="The collection must be approved before it can start.") from exc
    except evidence.ContinuousEvidenceError as exc:
        raise HTTPException(status_code=422, detail="This source's settings could not be used.") from exc
    return {"enrolled": True}
