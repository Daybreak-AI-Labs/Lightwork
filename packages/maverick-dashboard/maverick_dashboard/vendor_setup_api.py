"""Reviewed in-app activation and workspace connection binding, without probes."""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config, connections
from maverick import vendor_onboarding_setup as setup
from maverick.governed_records import RecordConflict
from maverick.paths import current_tenant_id_strict
from pydantic import BaseModel, ConfigDict, Field

from . import settings_store
from .auth import caller_principal, require_global_permission
from .draft_api import context_for

router = APIRouter(prefix="/api/v1/vendor-setup", tags=["vendor-setup"])


class Reviewed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool
    context: str = Field(pattern=r"^[a-f0-9]{64}$")
    configuration: str = Field(pattern=r"^[a-f0-9]{64}$")


class BindingIn(Reviewed):
    expected_revision: int = Field(ge=0)
    ticket: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    document: str = Field(default="", max_length=64)
    ticket_revision: str = Field(min_length=1, max_length=64)
    document_revision: str = Field(default="", max_length=64)


def _configuration():
    global_config, effective = config.load_global_config(), config.load_config()
    if config.config_source_errors(include_tenant=True):
        raise HTTPException(503, "Configuration is unavailable. No setup settings were changed.")
    selected = [{key: value.get(key) for key in setup.REQUIRED_MODULES}
                for value in (global_config, effective)]
    selected.append({"connections_override": connections.deployment_override()})
    return hashlib.sha256(json.dumps(selected, sort_keys=True).encode()).hexdigest(), global_config


def _authority(request, response, payload=None):
    require_global_permission(request, "admin")
    actor = caller_principal(request)
    if not actor or not actor.startswith("user:") or not current_tenant_id_strict():
        raise HTTPException(403, "Sign in as an administrator and select a workspace.")
    response.headers["Cache-Control"] = "private, no-store"
    fingerprint, loaded = _configuration()
    supplied = request.headers.get("X-Lightwork-Setup-Context")
    if supplied is not None and not hmac.compare_digest(supplied, context_for(request)):
        raise HTTPException(409, "Your account or workspace changed. Reload setup.")
    if payload is not None:
        from .app import _require_same_origin
        _require_same_origin(request)
        if payload.confirm is not True:
            raise HTTPException(422, "Review and confirm the setup change.")
        if not hmac.compare_digest(payload.context, context_for(request)) or not hmac.compare_digest(payload.configuration, fingerprint):
            raise HTTPException(409, "Account, workspace or settings changed. Reload and review setup.")
    return actor, fingerprint, loaded


@router.get("")
def overview(request: Request, response: Response):
    actor, fingerprint, loaded = _authority(request, response)
    try:
        record = setup.current()
        candidates = connections.list_connections(owner=actor) if connections.enabled() else []
        binding = record["binding"] if record else asdict(setup.VendorOnboardingSettings.from_config({"vendor_onboarding": dict(loaded.get("vendor_onboarding") or {}, enable=False)}))
        return {"context": context_for(request), "configuration": fingerprint,
            "revision": (record or {}).get("revision", 0), "binding": binding,
            "principal": actor, "connections_enabled": connections.enabled(),
            "modules_enabled": all((loaded.get(key) or {}).get("enable") is True for key in setup.REQUIRED_MODULES),
            "connections": [{key: row[key] for key in ("name", "connector", "base_url", "revision", "has_token")} for row in candidates if row["connector"] in {"jira", "gdrive"}],
            "live_provider_verified": False}
    except Exception as exc:
        raise HTTPException(503, "Vendor setup records are unavailable.") from exc


@router.post("/enable")
def enable(request: Request, response: Response, payload: Reviewed):
    _authority(request, response, payload)
    if connections.deployment_override() is False:
        raise HTTPException(409, "Deployment policy disables Connections.")
    with settings_store._locked(strict=True):
        _authority(request, response, payload)
        data = settings_store._load_overlay_for_update()
        for key in setup.REQUIRED_MODULES:
            data.setdefault(key, {})["enable"] = True
        settings_store._write(data)
    config.reset_config_cache()
    state = overview(request, response)
    if not state["modules_enabled"] or not state["connections_enabled"]:
        raise HTTPException(409, "Deployment policy prevents this activation. Review current setup.")
    return state


@router.put("")
def bind(request: Request, response: Response, payload: BindingIn):
    actor, _, _ = _authority(request, response, payload)
    try:
        setup.save(actor=actor, ticket=payload.ticket, document=payload.document,
            ticket_revision=payload.ticket_revision, document_revision=payload.document_revision,
            expected_revision=payload.expected_revision)
    except (PermissionError, ValueError, RecordConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Vendor setup was not confirmed. Reload before retrying.") from exc
    return overview(request, response)
