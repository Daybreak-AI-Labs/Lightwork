"""Named renewable credentials; explicit consent and sealed app registration."""
from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from maverick import config, connection_oauth, connections
from maverick.paths import current_tenant_id_strict
from pydantic import BaseModel, ConfigDict, Field

from . import settings_store
from .auth import caller_principal, require_global_permission
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


class AuthorizeIn(Reviewed):
    profile: str = Field(max_length=64)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    expected_revision: str = Field(default="", max_length=64)
    site_url: str = Field(default="", max_length=256)


class ExchangeIn(Reviewed):
    state: str = Field(min_length=40, max_length=64)
    code: str = Field(min_length=1, max_length=8192)


def _context(request):
    # An operator overlay or deployment switch changing while the provider
    # consent screen is open also invalidates that authorization transaction.
    loaded, effective = config.load_global_config(), config.load_config()
    if config.config_source_errors(include_tenant=True):
        raise HTTPException(503, "Connection configuration is unavailable.")
    values = [context_for(request), [{k: cfg.get(k) for k in ("connections", "oauth", "enterprise")}
                                   for cfg in (loaded, effective)], connections.deployment_override(),
              config.env_flag("MAVERICK_OAUTH_VAULT"), config.env_flag("LIGHTWORK_OAUTH_VAULT")]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _authority(request, response, payload=None):
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


def _callback(request):
    base = str(request.base_url).rstrip("/")
    parsed = urlsplit(base)
    if any(k in request.headers for k in ("x-forwarded-host", "forwarded")):
        # Managed proxies must configure the externally served base correctly;
        # untrusted forwarding headers cannot select a credential return target.
        raise HTTPException(409, "Authorization requires a trusted app return address.")
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


@router.get("")
def overview(request: Request, response: Response):
    actor, context = _authority(request, response)
    enabled = connection_oauth.enabled()
    return {"context": context, "enabled": enabled, "connections_enabled": connections.enabled(),
        "callback_uri": _callback(request), "profiles": [{"id": key, **value} for key, value in connection_oauth.PROFILES.items()],
        "applications": _call(lambda: connection_oauth.applications(actor)) if enabled else [],
        "connections": _call(lambda: connections.list_connections(owner=actor)) if connections.enabled() else [],
        "live_provider_verified": False}


@router.post("/enable")
def enable(request: Request, response: Response, payload: Reviewed):
    _authority(request, response, payload)
    if connections.deployment_override() is False or any(config.env_flag(name) is False for name in ("MAVERICK_OAUTH_VAULT", "LIGHTWORK_OAUTH_VAULT")):
        raise HTTPException(409, "Deployment policy disables saved account authorization.")
    with settings_store._locked(strict=True):
        _authority(request, response, payload)
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
    actor, _ = _authority(request, response, payload)
    _call(lambda: connection_oauth.save_application(actor, provider=payload.provider, client_id=payload.client_id,
        client_secret=payload.client_secret, expected_revision=payload.expected_revision))
    return overview(request, response)


@router.post("/authorize")
def authorize(request: Request, response: Response, payload: AuthorizeIn):
    actor, context = _authority(request, response, payload)
    return _call(lambda: connection_oauth.begin(actor, profile=payload.profile, name=payload.name,
        expected_revision=payload.expected_revision, site_url=payload.site_url,
        context=context, redirect_uri=_callback(request)))


@router.post("/exchange")
def exchange(request: Request, response: Response, payload: ExchangeIn):
    actor, context = _authority(request, response, payload)
    record = _call(lambda: connection_oauth.finish(actor, state=payload.state, code=payload.code,
        context=context, redirect_uri=_callback(request)))
    return {"connection": record, "context": context, "live_provider_verified": False}
