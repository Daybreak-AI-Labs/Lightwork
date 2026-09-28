"""Read-only choice lists for the /security/assurance page.

The page lets people pick existing records instead of typing identifiers,
version numbers, or fingerprints.  Every route here only reads; the write
routes the page calls keep their own permission, workspace, and freshness
checks unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from .auth import require_global_permission, require_permission
from .model_risk_api import (
    _call,
    _configured_conformity_workflow,
    _human_principal,
    _identity_control_runtime,
    _identity_governance_section,
    _module,
    _require_active_tenant,
)

router = APIRouter(prefix="/assurance/pickers", tags=["model-risk-assurance"])

_STORE_METADATA = frozenset({"revision", "created_at", "updated_at", "_audit_pending"})


def _record_digest(record: Mapping[str, Any]) -> str:
    """Fingerprint one stored record's content, excluding store bookkeeping."""

    content = {
        str(key): value
        for key, value in record.items()
        if str(key) not in _STORE_METADATA
    }
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _revision(record: Mapping[str, Any]) -> int:
    value = record.get("revision")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(status_code=503, detail="an assurance record is malformed")
    return value


@router.get("/package-forms")
async def package_form_definitions(request: Request) -> dict[str, Any]:
    """Field lists the guided evidence-package forms are built from."""

    require_permission(request, "operate")
    from maverick import (
        ai_bank_mrm,
        ai_conformity_workflow,
        ai_insurability,
        ai_regulated_verticals,
    )

    bank_sections = [
        {
            "key": section,
            "fields": [
                {
                    "key": field,
                    "kind": kind,
                    "choices": sorted(allowed) if allowed is not None else None,
                }
                for field, (kind, allowed) in specs.items()
            ],
        }
        for section, specs in ai_bank_mrm._FIELD_SPECS.items()
    ]
    return {
        "insurability": {
            "measurement_schema": ai_insurability.MEASUREMENT_SCHEMA,
            "measurements": list(ai_insurability._MEASUREMENT_NAMES),
            "measurement_states": sorted(ai_insurability._MEASUREMENT_STATES),
            "measurement_units": sorted(ai_insurability._MEASUREMENT_UNITS),
            "framework_postures": sorted(ai_insurability._FRAMEWORK_POSTURES),
            "safeguard_states": sorted(ai_insurability._SAFEGUARD_STATES),
            "severities": sorted(ai_insurability._SEVERITIES),
        },
        "regulated": {
            "nyc_checks": list(ai_regulated_verticals.NYC_LL144_CHECKS),
            "naic_checks": list(ai_regulated_verticals.NAIC_CHECKS),
        },
        "bank_mrm": {
            "section_schema": ai_bank_mrm.SECTION_SCHEMA,
            "sections": bank_sections,
        },
        "conformity": {
            "checklist_sections": {
                domain: list(sections)
                for domain, sections in ai_conformity_workflow.CHECKLIST_SECTIONS.items()
            },
        },
    }


@router.get("/conformity-evidence")
async def conformity_case_evidence(
    request: Request,
    case_id: str = Query(..., min_length=1, max_length=36),
) -> dict[str, Any]:
    """Evidence already added to one readiness case, for the checklist form."""

    require_permission(request, "view")
    _require_active_tenant()
    _conformity, service = await _configured_conformity_workflow()
    items = await _call(service.list_case_evidence, case_id)
    return {
        "case_id": case_id,
        "evidence": [
            {
                "binding_id": item["binding_id"],
                "evidence_type": item["evidence_type"],
                "source_ref": item["source_ref"],
            }
            for item in items
        ],
    }


def _finding_sources(officer: Any) -> list[dict[str, Any]]:
    rows = []
    for finding in officer.findings():
        rows.append(
            {
                "label": str(finding.get("summary") or ""),
                "asset_id": finding["asset_id"],
                "source": {
                    "source_kind": "finding",
                    "source_id": finding["id"],
                    "asset_id": finding["asset_id"],
                    "severity": finding["severity"],
                    "summary": finding["summary"],
                    # Findings are derived, not stored: each exact content
                    # fingerprint is its own first version.  A changed finding
                    # reuses the ID with a new fingerprint, which the outbound
                    # sync refuses as a conflict for a person to resolve.
                    "source_revision": 1,
                    "source_digest": finding["finding_sha256"],
                    "finding_type": finding["finding_type"],
                },
            }
        )
    return rows


def _acceptance_sources(officer: Any) -> list[dict[str, Any]]:
    rows = []
    for finding in officer.findings():
        record = officer.current_risk_acceptance(finding)
        if not isinstance(record, Mapping):
            continue
        rows.append(
            {
                "label": f"Accepted risk: {finding.get('summary') or ''}",
                "asset_id": record["asset_id"],
                "source": {
                    "source_kind": "risk_acceptance",
                    "source_id": record["id"],
                    "asset_id": record["asset_id"],
                    "severity": record["severity"],
                    "summary": str(finding.get("summary") or "Accepted risk"),
                    "source_revision": _revision(record),
                    "source_digest": _record_digest(record),
                    "finding_id": record["finding_id"],
                    "rationale": record["rationale"],
                    "reviewer": record["reviewer"],
                    "decided_at": float(record["decided_at"]),
                    "expires_at": float(record["expires_at"]),
                    "acceptance_status": record["status"],
                },
            }
        )
    return rows


def _incident_sources(officer: Any) -> list[dict[str, Any]]:
    rows = []
    for record in officer.list_incidents():
        rows.append(
            {
                "label": str(record.get("summary") or ""),
                "asset_id": record["asset_id"],
                "source": {
                    "source_kind": "ai_incident",
                    "source_id": record["id"],
                    "asset_id": record["asset_id"],
                    "severity": record["severity"],
                    "summary": record["summary"],
                    "source_revision": _revision(record),
                    "source_digest": _record_digest(record),
                    "incident_source_id": record["source_id"],
                    "incident_status": record["status"],
                    "occurred_at": float(record["occurred_at"]),
                    "resolution": str(record.get("resolution") or ""),
                },
            }
        )
    return rows


@router.get("/grc-sources")
async def grc_outbound_sources(
    request: Request,
    source_kind: Literal["finding", "risk_acceptance", "ai_incident"] = Query(...),
) -> dict[str, Any]:
    """Records that can be previewed or sent to ServiceNow or Archer."""

    require_permission(request, "operate")
    _require_active_tenant()
    officer = _module("model_risk_assurance", "model_risk_assurance")
    build = {
        "finding": _finding_sources,
        "risk_acceptance": _acceptance_sources,
        "ai_incident": _incident_sources,
    }[source_kind]
    sources = await _call(build, officer)
    return {"source_kind": source_kind, "sources": sources[:500], "count": len(sources)}


_NO_REGISTRY: dict[str, Any] = {
    "available": False,
    "capabilities": [],
    "federation_bindings": [],
    "purview_assets": [],
}


def _registry_choices(request: Request, tenant: str) -> dict[str, Any]:
    factory = getattr(
        request.app.state,
        "identity_governance_authority_registry_factory",
        None,
    )
    if not callable(factory):
        return dict(_NO_REGISTRY)
    try:
        registry = factory(tenant)
        bindings = registry.active_payloads("federation_binding")
        assets = registry.active_payloads("purview_asset_binding")
        rules = registry.active_payloads("capability_rule")
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return dict(_NO_REGISTRY)
    capabilities = sorted(
        {
            str(capability)
            for rule in rules
            for capability in (rule.get("capabilities") or ())
        }
    )
    return {
        "available": True,
        "capabilities": capabilities,
        "federation_bindings": [
            {
                "provider": row.get("provider"),
                "principal_ref": row.get("principal_ref"),
                "provider_subject_id": row.get("provider_subject_id"),
                "binding_sha256": row.get("binding_sha256"),
                "authority_revision": row.get("authority_revision"),
            }
            for row in bindings
        ],
        "purview_assets": [
            {
                "asset_id": row.get("asset_id"),
                "binding_sha256": row.get("binding_sha256"),
                "revision": row.get("revision"),
                "classification_codes": list(row.get("classification_codes") or ()),
            }
            for row in assets
        ],
    }


@router.get("/identity-controls")
async def identity_control_choices(request: Request) -> dict[str, Any]:
    """Enrolled agent identities, data assets, and prepared label changes."""

    require_global_permission(request, "admin")
    tenant = _require_active_tenant()
    _human_principal(request)
    result = await _call(_registry_choices, request, tenant)
    intents: list[dict[str, Any]] = []
    intents_available = False
    try:
        section = _identity_governance_section()
        runtime = _identity_control_runtime(request, section, "purview_writeback")
        service = runtime.require_purview_writeback()
        intents = await _call(service.list_intents, limit=100)
        intents_available = True
    except HTTPException:
        intents = []
    result["purview_intents"] = intents
    result["purview_intents_available"] = intents_available
    return result
