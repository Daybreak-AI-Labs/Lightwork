"""Authenticated in-app knowledge access and source-rights requests."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from maverick.knowledge_access import (
    KNOWLEDGE_EXPORT_SCHEMA,
    KnowledgeAccessDenied,
    KnowledgeAccessService,
    KnowledgeActorAuthority,
    KnowledgeArchiveUnavailable,
    KnowledgeRequestCapacity,
    KnowledgeRequestConflict,
    KnowledgeRequestNotFound,
)
from maverick.knowledge_content_projection import ExistingKnowledgeContentProjector
from maverick.paths import current_tenant_id_strict
from maverick.privacy_ops import RecordConflict
from maverick.research_registry import (
    ResearchAssetRef,
    ResearchRegistryConflict,
    ResearchRegistryDenied,
    ResearchRegistryNotFound,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from . import rbac
from .auth import (
    caller_principal,
    has_global_permission,
    require_global_permission,
    require_permission,
    role_for_principal,
)
from .draft_api import context_for

API_BASE = "/api/v1/knowledge-access"
PAGE_PATH = "/knowledge-access"
JS_PATH = "/static/lightwork-knowledge-access.js"
CSS_PATH = "/static/lightwork-knowledge-access.css"
_STATIC_DIR = Path(__file__).parent / "static"
_MAX_REQUEST_BYTES = 128 * 1024


class _PrivateKnowledgeRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_validation(request):
            declared = request.headers.get("content-length")
            if declared is not None:
                try:
                    if int(declared) < 0 or int(declared) > _MAX_REQUEST_BYTES:
                        return JSONResponse(
                            status_code=413,
                            content={"detail": "Knowledge access request is too large"},
                            headers={"Cache-Control": "private, no-store"},
                        )
                except ValueError:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Knowledge access request length is malformed"},
                        headers={"Cache-Control": "private, no-store"},
                    )
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"detail": "Knowledge access fields are malformed"},
                    headers={"Cache-Control": "private, no-store"},
                )

        return private_validation


router = APIRouter(
    prefix=API_BASE,
    tags=["knowledge-access"],
    route_class=_PrivateKnowledgeRoute,
)
page_router = APIRouter(tags=["knowledge-access"])

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
_PURPOSE_PATTERN = r"^[a-z][a-z0-9_.-]{0,63}$"
_SHA_PATTERN = r"^[a-f0-9]{64}$"
Identifier = Annotated[str, Field(pattern=_ID_PATTERN)]
Purpose = Annotated[str, Field(pattern=_PURPOSE_PATTERN)]
Sha256 = Annotated[str, Field(pattern=_SHA_PATTERN)]


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        populate_by_name=True,
    )


class _Bound(_Strict):
    context: Sha256


class _Reviewed(_Bound):
    confirm: bool


class AssetReference(_Strict):
    kind: Literal["dataset"]
    asset_id: Identifier
    version: Identifier
    manifest_sha256: Sha256
    reference_schema: Literal["lightwork.research-registry-ref.v1"] = Field(
        "lightwork.research-registry-ref.v1",
        alias="schema",
    )

    def exact(self) -> ResearchAssetRef:
        return ResearchAssetRef(
            kind=self.kind,
            asset_id=self.asset_id,
            version=self.version,
            manifest_sha256=self.manifest_sha256,
            schema=self.reference_schema,
        )


class SearchIn(_Bound):
    query: str = Field(default="", max_length=512)
    purpose: Purpose
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)


class DetailIn(_Bound):
    reference: AssetReference
    purpose: Purpose


class ExportIn(_Bound):
    references: list[AssetReference] = Field(min_length=1, max_length=100)
    purpose: Purpose

    @model_validator(mode="after")
    def unique_references(self):
        keys = [tuple(item.model_dump(by_alias=True).values()) for item in self.references]
        if len(keys) != len(set(keys)):
            raise ValueError("knowledge export references must be unique")
        return self


class RightsRequestIn(_Reviewed):
    reference: AssetReference
    purpose: Purpose | None = None
    request_type: Literal["correction", "deletion", "exclusion"]
    reason: str = Field(min_length=1, max_length=2_000)
    request_key: Identifier
    proposed_correction: str | None = Field(default=None, max_length=8_192)


class DecisionIn(_Reviewed):
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    decision: Literal["approve", "deny"]
    evidence_sha256: Sha256


class ArchiveIn(_Reviewed):
    expected_revision: int = Field(ge=1, le=2**31 - 1)


class RetentionHoldIn(_Reviewed):
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    hold_id: Identifier
    basis: str = Field(min_length=1, max_length=2_000)
    expires_at: float = Field(gt=0)
    evidence_sha256: Sha256


class RetentionReleaseIn(_Reviewed):
    expected_revision: int = Field(ge=1, le=2**31 - 1)
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_sha256: Sha256


_SERVICE_LOCK = threading.Lock()


def _enabled() -> bool:
    from maverick.config import knowledge_access_enabled

    return knowledge_access_enabled()


def _actor_authority(
    tenant: str,
    principal: str,
    action: str,
    purpose: str,
) -> KnowledgeActorAuthority:
    """Resolve the current tenant RBAC grant into a core authority binding."""

    if not principal.startswith("user:"):
        raise PermissionError("a named dashboard user is required")
    role = role_for_principal(principal)
    permissions = sorted(rbac.permissions_for(role))
    required = "view" if action == "read" else "operate"
    if required not in permissions:
        raise PermissionError("the current dashboard role does not allow this knowledge use")
    material = json.dumps(
        {
            "schema": "lightwork.dashboard-knowledge-authorization.v1",
            "tenant": tenant,
            "principal": principal,
            "role": role,
            "permissions": permissions,
            "action": action,
            "purpose": purpose,
        },
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return KnowledgeActorAuthority(
        tenant=tenant,
        principal=principal,
        action=action,
        purpose=purpose,
        authorization_sha256=hashlib.sha256(material).hexdigest(),
    )


def _service(request: Request) -> KnowledgeAccessService:
    if not _enabled():
        raise KnowledgeAccessDenied("governed knowledge access is disabled")
    state = request.app.state
    factory = getattr(state, "knowledge_access_service_factory", None)
    if callable(factory):
        service = factory(request)
        if not isinstance(service, KnowledgeAccessService):
            raise TypeError("knowledge access service factory returned the wrong type")
        return service
    current = getattr(state, "knowledge_access_service", None)
    if isinstance(current, KnowledgeAccessService):
        return current
    with _SERVICE_LOCK:
        current = getattr(state, "knowledge_access_service", None)
        if isinstance(current, KnowledgeAccessService):
            return current
        from .research_workspace_api import _registry_service

        resolver = getattr(state, "knowledge_content_resolver", None)
        actor_resolver = getattr(state, "knowledge_actor_authority_resolver", None)
        from maverick.knowledge_content_projection import correction_source_store

        corrections = correction_source_store()
        current = KnowledgeAccessService(
            registry=_registry_service(state),
            content_resolver=(
                resolver
                if callable(resolver)
                else ExistingKnowledgeContentProjector(correction_store=corrections)
            ),
            actor_authority_resolver=(
                actor_resolver if callable(actor_resolver) else _actor_authority
            ),
            correction_store=corrections,
        )
        state.knowledge_access_service = current
        return current


def _binding(
    request: Request,
    response: Response,
    payload: _Bound | None = None,
    *,
    administrator: bool = False,
) -> tuple[str, str, str]:
    if not _enabled():
        raise HTTPException(404, "Governed knowledge access is disabled")
    require_permission(request, "operate")
    if administrator:
        require_global_permission(request, "admin")
    actor = caller_principal(request)
    tenant = current_tenant_id_strict()
    if not actor or not actor.startswith("user:") or not tenant:
        raise HTTPException(
            403,
            "Sign in with a named account and select a knowledge workspace",
        )
    response.headers["Cache-Control"] = "private, no-store"
    context = context_for(request)
    if payload is not None:
        from .app import _require_same_origin

        _require_same_origin(request)
        if not hmac.compare_digest(payload.context, context):
            raise HTTPException(
                409,
                "Account or workspace changed; reload knowledge access",
            )
        if isinstance(payload, _Reviewed) and payload.confirm is not True:
            raise HTTPException(422, "Review and confirm this knowledge rights action")
    return tenant, actor, context


async def _call(function, *args, **kwargs):
    return await run_in_threadpool(call_native, function, *args, **kwargs)


def call_native(function, *args, **kwargs):
    """Shared sanitized native boundary for HTTP handlers and reviewed actions."""
    try:
        return function(*args, **kwargs)
    except (ResearchRegistryNotFound, KnowledgeRequestNotFound) as exc:
        raise HTTPException(404, "Knowledge record was not found in this workspace") from exc
    except (ResearchRegistryDenied, KnowledgeAccessDenied) as exc:
        raise HTTPException(
            409,
            "Current source access is unavailable. Request fresh access or evidence.",
        ) from exc
    except KnowledgeRequestCapacity as exc:
        raise HTTPException(507, str(exc)) from exc
    except KnowledgeArchiveUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except (
        ResearchRegistryConflict,
        KnowledgeRequestConflict,
        RecordConflict,
    ) as exc:
        raise HTTPException(
            409,
            "Knowledge record changed; reload its current status before retrying",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(403, "Current workspace authority is required") from exc
    except ValueError as exc:
        raise HTTPException(422, "Knowledge access input is malformed") from exc
    except Exception as exc:  # noqa: BLE001 - never disclose source adapter/storage internals
        raise HTTPException(503, "Governed knowledge access is unavailable") from exc


def _registry_purposes(service, tenant: str, actor: str) -> list[str]:
    return service.available_purposes(tenant, actor)


@router.get("/task-policy")
def task_policy(request: Request, response: Response):
    from maverick.knowledge_task_context import enabled
    from maverick.runner import configured_max_dollars, configured_max_wall_seconds
    _, _, context = _binding(request, response)
    return {"context": context, "enabled": enabled(), "max_dollars": configured_max_dollars(),
            "max_wall_seconds": configured_max_wall_seconds(),
            "mode": "source_analysis", "maximum_sources": 5}


@router.get("/analyses/{goal_id}")
def source_analysis(request: Request, response: Response, goal_id: int):
    from maverick.knowledge_task_context import SourceContextUnavailable, read_analysis

    from .api import _world
    _, actor, context = _binding(request, response)
    try:
        value = read_analysis(_world(), goal_id, actor)
    except SourceContextUnavailable as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, "Current source checks are unavailable. No analysis was released.") from exc
    return {"context": context, **value}


@router.get("/purposes")
async def purposes(request: Request, response: Response):
    """The uses your approved sources allow, for the search picker.

    Read-only and bound to the caller's workspace like every other read here;
    each search still rechecks the purpose against the exact source version.
    """
    tenant, actor, context = _binding(request, response)
    codes = await _call(_registry_purposes, _service(request), tenant, actor)
    return {"context": context, "status": "available" if codes else "empty",
            "items": [{"code": code} for code in codes], "count": len(codes)}


@router.post("/search")
async def search(payload: SearchIn, request: Request, response: Response):
    tenant, actor, context = _binding(request, response, payload)
    result = await _call(
        _service(request).search,
        tenant,
        actor,
        query=payload.query,
        purpose=payload.purpose,
        limit=payload.limit,
        cursor=payload.cursor,
    )
    return {"context": context, **result}


@router.post("/detail")
async def detail(payload: DetailIn, request: Request, response: Response):
    tenant, actor, context = _binding(request, response, payload)
    result = await _call(
        _service(request).detail,
        tenant,
        actor,
        payload.reference.exact(),
        purpose=payload.purpose,
    )
    return {"context": context, **result}


@router.post("/export")
async def export(payload: ExportIn, request: Request, response: Response):
    tenant, actor, context = _binding(request, response, payload)
    result = await _call(
        _service(request).export,
        tenant,
        actor,
        tuple(item.exact() for item in payload.references),
        purpose=payload.purpose,
    )
    if result.get("schema") != KNOWLEDGE_EXPORT_SCHEMA or result.get("complete") is not True:
        raise HTTPException(503, "Knowledge export could not be verified as complete")
    encoded = json.dumps(
        {"context": context, **result},
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return Response(
        encoded,
        media_type="application/json",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="knowledge-access-export.json"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/requests")
async def list_requests(
    request: Request,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=250)] = 100,
    archived: bool = False,
    ready_to_archive: bool = False,
):
    tenant, actor, context = _binding(request, response)
    service = _service(request)
    admin = has_global_permission(request, "admin")
    if archived and ready_to_archive:
        raise HTTPException(422, "Choose one request view")
    if ready_to_archive and not admin:
        raise HTTPException(403, "Administrator access is required for this view")
    if admin:
        items = await _call(service.list_requests, tenant, limit=limit,
                            archived=archived, ready_to_archive=ready_to_archive)
    else:
        items = await _call(service.feedback_requests, tenant, actor,
                            limit=limit, archived=archived)
    return {
        "schema": "lightwork.knowledge-rights-requests.v1",
        "context": context,
        "items": items,
        "count": len(items),
        "can_review": admin,
        "archived": archived,
        "ready_to_archive": ready_to_archive,
        "capacity": await _call(service.request_capacity, tenant) if admin else None,
    }


@router.get("/requests/{request_id}")
async def get_request(request_id: str, request: Request, response: Response):
    tenant, actor, context = _binding(request, response)
    service = _service(request)
    if has_global_permission(request, "admin"):
        item = await _call(service.get_request, tenant, request_id)
    else:
        items = await _call(service.feedback_requests, tenant, actor, request_id=request_id)
        if not items:
            raise HTTPException(404, "Request was not found")
        item = items[0]
    return {
        "schema": "lightwork.knowledge-rights-request-detail.v1",
        "context": context,
        "item": item,
    }


@router.post("/requests", status_code=201)
async def submit_request(
    payload: RightsRequestIn,
    request: Request,
    response: Response,
):
    tenant, actor, context = _binding(
        request,
        response,
        payload,
        administrator=False,
    )
    service = _service(request)
    admin = has_global_permission(request, "admin")
    if not admin and not payload.purpose:
        raise HTTPException(422, "Choose the use under which you opened this source")
    result = await _call(
        service.submit_request if admin else service.submit_feedback,
        tenant,
        actor,
        payload.reference.exact(),
        request_type=payload.request_type,
        reason=payload.reason,
        request_key=payload.request_key,
        proposed_correction=payload.proposed_correction,
        **({} if admin else {"purpose": payload.purpose}),
    )
    return {"context": context, **result}


@router.post("/requests/{request_id}/decision")
async def decide_request(
    request_id: str,
    payload: DecisionIn,
    request: Request,
    response: Response,
):
    tenant, actor, context = _binding(
        request,
        response,
        payload,
        administrator=True,
    )
    result = await _call(
        _service(request).decide_request,
        tenant,
        actor,
        request_id,
        expected_revision=payload.expected_revision,
        decision=payload.decision,
        evidence_sha256=payload.evidence_sha256,
    )
    return {"context": context, **result}


@router.post("/requests/{request_id}/archive")
async def archive_request(
    request_id: str, payload: ArchiveIn, request: Request, response: Response,
):
    tenant, actor, context = _binding(
        request, response, payload, administrator=True,
    )
    result = await _call(
        _service(request).archive_request, tenant, actor, request_id,
        expected_revision=payload.expected_revision,
    )
    return {"context": context, **result}


@router.post("/requests/{request_id}/retention-hold")
async def place_retention_hold(
    request_id: str,
    payload: RetentionHoldIn,
    request: Request,
    response: Response,
):
    tenant, actor, context = _binding(
        request,
        response,
        payload,
        administrator=True,
    )
    result = await _call(
        _service(request).place_retention_hold,
        tenant,
        actor,
        request_id,
        expected_revision=payload.expected_revision,
        hold_id=payload.hold_id,
        basis=payload.basis,
        expires_at=payload.expires_at,
        evidence_sha256=payload.evidence_sha256,
    )
    return {"context": context, **result}


@router.post("/requests/{request_id}/retention-hold/release")
async def release_retention_hold(
    request_id: str,
    payload: RetentionReleaseIn,
    request: Request,
    response: Response,
):
    tenant, actor, context = _binding(
        request,
        response,
        payload,
        administrator=True,
    )
    result = await _call(
        _service(request).release_retention_hold,
        tenant,
        actor,
        request_id,
        expected_revision=payload.expected_revision,
        reason=payload.reason,
        evidence_sha256=payload.evidence_sha256,
    )
    return {"context": context, **result}


_PAGE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'self' blob:; manifest-src 'none'; media-src 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; worker-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), clipboard-read=(), clipboard-write=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}


@page_router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
async def knowledge_access_page(request: Request, response: Response):
    if any(key not in {"theme", "density", "font", "lang"} for key in request.query_params):
        raise HTTPException(404, "Knowledge access page unavailable")
    tenant, actor, context = _binding(request, response)
    from .app import templates

    purpose_state = "available"
    try:
        purpose_codes = await run_in_threadpool(
            _registry_purposes, _service(request), tenant, actor
        )
        if not purpose_codes:
            purpose_state = "empty"
    except Exception:  # noqa: BLE001 - unavailable is distinct from an empty inventory
        purpose_codes = []
        purpose_state = "unavailable"
    nonce = secrets.token_urlsafe(18)
    page = templates.TemplateResponse(
        request,
        "knowledge_access.html",
        {
            "knowledge_context": context,
            "knowledge_tenant": tenant,
            "knowledge_actor": actor,
            "knowledge_can_review": has_global_permission(request, "admin"),
            "knowledge_purposes": purpose_codes,
            "knowledge_purpose_state": purpose_state,
            "knowledge_api_base": API_BASE,
            "csp_style_nonce": nonce,
        },
    )
    for name, value in _PAGE_HEADERS.items():
        if name == "Content-Security-Policy":
            value = value.replace("style-src 'self'", f"style-src 'self' 'nonce-{nonce}'")
        page.headers[name] = value
    return page


@page_router.get(JS_PATH, include_in_schema=False)
async def knowledge_access_javascript():
    return FileResponse(
        _STATIC_DIR / "lightwork-knowledge-access.js",
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


@page_router.get(CSS_PATH, include_in_schema=False)
async def knowledge_access_stylesheet():
    return FileResponse(
        _STATIC_DIR / "lightwork-knowledge-access.css",
        media_type="text/css; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


__all__ = [
    "API_BASE",
    "CSS_PATH",
    "JS_PATH",
    "PAGE_PATH",
    "page_router",
    "router",
]


@page_router.get("/static/lightwork-source-analysis.js", include_in_schema=False)
def source_analysis_script():
    return FileResponse(_STATIC_DIR / "lightwork-source-analysis.js", media_type="application/javascript")
