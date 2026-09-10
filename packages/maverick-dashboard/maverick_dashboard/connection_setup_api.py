"""Named renewable credentials; explicit consent and sealed app registration."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config, connection_oauth, connections
from maverick.paths import current_tenant_id_strict
from pydantic import BaseModel, ConfigDict, Field

from . import settings_store
from .auth import (
    caller_principal,
    has_global_permission,
    require_global_permission,
    require_permission,
)
from .draft_api import context_for

router = APIRouter(prefix="/api/v1/connection-setup", tags=["connection-setup"])


class Reviewed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: bool
    context: str = Field(pattern=r"^[a-f0-9]{64}$")


class ApplicationIn(Reviewed):
    provider: str = Field(max_length=32)
    client_id: str = Field(min_length=1, max_length=1024)
    client_secret: str | None = Field(default=None, max_length=4096)
    expected_revision: str = Field(default="", max_length=64)
    desktop_return: bool = False


class AuthorizeIn(Reviewed):
    profile: str = Field(max_length=64)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    expected_revision: str = Field(default="", max_length=64)
    site_url: str = Field(default="", max_length=256)
    return_profile: Literal["browser", "desktop-loopback-v1"] = "browser"
    native_handle: str = Field(default="", max_length=64, pattern=r"^$|^[a-f0-9]{64}$")
    prepare_ticket: str = Field(default="", max_length=8192)


class NativePrepareIn(Reviewed):
    profile: str = Field(max_length=64)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    expected_revision: str = Field(default="", max_length=64)
    site_url: str = Field(default="", max_length=256)


class ExchangeIn(Reviewed):
    state: str = Field(min_length=40, max_length=64)
    code: str = Field(min_length=1, max_length=8192)
    return_profile: Literal["browser", "desktop-loopback-v1"] = "browser"


class CancelIn(Reviewed):
    state: str = Field(min_length=40, max_length=64)
    return_profile: Literal["browser", "desktop-loopback-v1"] = "browser"


def _context(request):
    # An operator overlay or deployment switch changing while the provider
    # consent screen is open also invalidates that authorization transaction.
    loaded, effective = config.load_global_config(), config.load_config()
    if config.config_source_errors(include_tenant=True):
        raise HTTPException(503, "Connection configuration is unavailable.")
    values = [context_for(request), [{k: cfg.get(k) for k in ("connections", "oauth", "enterprise")}
                                   for cfg in (loaded, effective)], connections.deployment_override(),
              config.env_flag("MAVERICK_OAUTH_VAULT"), config.env_flag("LIGHTWORK_OAUTH_VAULT"),
              config.env_flag("MAVERICK_OAUTH_DESKTOP_RETURN"), config.env_flag("LIGHTWORK_OAUTH_DESKTOP_RETURN"),
              connection_oauth.desktop_instance_id()]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _authority(request, response, payload=None, *, operator=False):
    require_permission(request, "admin")
    if operator:
        require_global_permission(request, "admin")
    actor = caller_principal(request)
    if not actor or not actor.startswith("user:") or not current_tenant_id_strict():
        raise HTTPException(403, "Sign in as an administrator and select a workspace to connect accounts.")
    response.headers["Cache-Control"] = "private, no-store"
    context = _context(request)
    supplied = payload.context if payload else request.headers.get("X-Lightwork-Connection-Context")
    if supplied is not None and not hmac.compare_digest(supplied, context):
        raise HTTPException(409, "Account, workspace or deployment settings changed. Reload Connections.")
    if payload is not None:
        from .app import _require_same_origin
        _require_same_origin(request)
        if payload.confirm is not True:
            raise HTTPException(422, "Review and confirm the account connection action.")
    return actor, context


def _callback(request, return_profile="browser"):
    base = str(request.base_url).rstrip("/")
    parsed = urlsplit(base)
    if any(k in request.headers for k in ("x-forwarded-host", "forwarded")):
        # Managed proxies must configure the externally served base correctly;
        # untrusted forwarding headers cannot select a credential return target.
        raise HTTPException(409, "Authorization requires a trusted app return address.")
    if return_profile != "browser":
        if return_profile != connection_oauth.DESKTOP_PROFILE or base != "http://127.0.0.1:8765" or not connection_oauth.desktop_return_enabled():
            raise HTTPException(409, "Desktop authorization requires the enabled local app return profile.")
        return connection_oauth.DESKTOP_CALLBACK
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise HTTPException(409, "Remote account authorization requires HTTPS.")
    return base + "/connections"


def _call(operation):
    try:
        return operation()
    except (PermissionError, ValueError, connections.ConnectionVersionConflict) as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        # Provider responses may contain codes, secrets and account payloads.
        raise HTTPException(503, "Account authorization was not confirmed. Refresh Connections; start a new authorization if needed.") from exc


def _applications(actor, can_manage):
    available = connection_oauth.available_applications(actor)
    if not can_manage:
        return available
    managed = connection_oauth.managed_applications(actor)
    providers = {row["provider"] for row in managed}
    # A personal legacy registration remains usable, but its private revision
    # must never prefill the separate workspace registration editor.
    return managed + [row for row in available if row["provider"] not in providers]


@router.get("")
def overview(request: Request, response: Response):
    actor, context = _authority(request, response)
    enabled = connection_oauth.enabled()
    can_manage = has_global_permission(request, "admin")
    return {"context": context, "workspace_context": context_for(request), "can_manage_applications": can_manage,
        "enabled": enabled, "connections_enabled": connections.enabled(),
        "desktop_return_enabled": connection_oauth.desktop_return_enabled(),
        "desktop_return_available": bool(connection_oauth.desktop_instance_id()),
        "desktop_callback_uri": connection_oauth.DESKTOP_CALLBACK,
        "callback_uri": _callback(request), "profiles": [{"id": key, **value} for key, value in connection_oauth.PROFILES.items()],
        "applications": _call(lambda: _applications(actor, can_manage)) if enabled else [],
        "connections": _call(lambda: connections.list_connections(owner=actor)) if connections.enabled() else [],
        "live_provider_verified": False}


@router.post("/enable")
def enable(request: Request, response: Response, payload: Reviewed):
    _authority(request, response, payload, operator=True)
    if connections.deployment_override() is False or any(config.env_flag(name) is False for name in ("MAVERICK_OAUTH_VAULT", "LIGHTWORK_OAUTH_VAULT")):
        raise HTTPException(409, "Deployment policy disables saved account authorization.")
    with settings_store._locked(strict=True):
        _authority(request, response, payload, operator=True)
        data = settings_store._load_overlay_for_update()
        data.setdefault("connections", {})["enable"] = True
        data.setdefault("oauth", {})["vault"] = True
        settings_store._write(data)
    config.reset_config_cache()
    if not connection_oauth.enabled():
        raise HTTPException(409, "Saved account authorization remains blocked by deployment policy.")
    # Activation intentionally changes the configuration portion of context.
    return {"enabled": True, "reload_required": True}


@router.put("/application")
def application(request: Request, response: Response, payload: ApplicationIn):
    actor, _ = _authority(request, response, payload, operator=True)
    _call(lambda: connection_oauth.save_workspace_application(actor, provider=payload.provider, client_id=payload.client_id,
        client_secret=payload.client_secret, expected_revision=payload.expected_revision, desktop_return=payload.desktop_return))
    return overview(request, response)


@router.post("/authorize")
def authorize(request: Request, response: Response, payload: AuthorizeIn):
    actor, context = _authority(request, response, payload)
    callback = _callback(request, payload.return_profile)
    return _call(lambda: connection_oauth.begin(actor, profile=payload.profile, name=payload.name,
        expected_revision=payload.expected_revision, site_url=payload.site_url,
        context=context, redirect_uri=callback, return_profile=payload.return_profile,
        native_handle=payload.native_handle, prepare_ticket=payload.prepare_ticket))


@router.post("/desktop-return/prepare")
def prepare_desktop_return(request: Request, response: Response, payload: NativePrepareIn):
    actor, context = _authority(request, response, payload)
    _callback(request, connection_oauth.DESKTOP_PROFILE)
    return _call(lambda: connection_oauth.native_prepare_ticket(actor, profile=payload.profile,
        name=payload.name, expected_revision=payload.expected_revision, site_url=payload.site_url,
        context=context))


@router.post("/exchange")
def exchange(request: Request, response: Response, payload: ExchangeIn):
    actor, context = _authority(request, response, payload)
    callback = _callback(request, payload.return_profile)
    record = _call(lambda: connection_oauth.finish(actor, state=payload.state, code=payload.code,
        context=context, redirect_uri=callback, return_profile=payload.return_profile))
    return {"connection": record, "context": context, "live_provider_verified": False}


@router.post("/cancel")
def cancel(request: Request, response: Response, payload: CancelIn):
    actor, context = _authority(request, response, payload)
    callback = _callback(request, payload.return_profile)
    cancelled = _call(lambda: connection_oauth.cancel_consent(actor, state=payload.state,
        context=context, redirect_uri=callback, return_profile=payload.return_profile))
    return {"cancelled": cancelled, "context": context}


@router.post("/desktop-return/enable")
def enable_desktop_return(request: Request, response: Response, payload: Reviewed):
    _authority(request, response, payload, operator=True)
    if any(config.env_flag(name) is False for name in ("MAVERICK_OAUTH_DESKTOP_RETURN", "LIGHTWORK_OAUTH_DESKTOP_RETURN")):
        raise HTTPException(409, "Deployment policy disables desktop account return.")
    with settings_store._locked(strict=True):
        _authority(request, response, payload, operator=True)
        data = settings_store._load_overlay_for_update()
        data.setdefault("oauth", {})["desktop_return"] = True
        settings_store._write(data)
    config.reset_config_cache()
    return {"enabled": connection_oauth.desktop_return_enabled(), "reload_required": True}
