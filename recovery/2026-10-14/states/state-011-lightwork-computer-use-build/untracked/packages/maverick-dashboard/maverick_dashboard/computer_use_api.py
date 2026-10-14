"""Authenticated, tenant-bound HTTP projection of the computer-use runtime."""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from starlette.concurrency import run_in_threadpool

from ._shared import _get_sse_semaphore, _world
from .auth import (
    assert_goal_access,
    caller_principal,
    has_permission,
    require_permission,
)

RuntimeId = Annotated[
    str,
    Field(
        min_length=12,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]{12,128}$",
    ),
]
_FRAME_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FRAME_BYTES = 25 * 1024 * 1024


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ControlIn(_StrictModel):
    expected_revision: StrictInt = Field(ge=1, le=2**63 - 1)


class StopIn(ControlIn):
    reason: str = Field(default="dashboard operator requested immediate stop", max_length=500)


class CreateSessionIn(_StrictModel):
    surface: Literal["desktop", "browser"] = "desktop"
    backend: Literal["remote", "host"]
    monitor_id: StrictInt | None = Field(default=None, ge=1, le=64)
    goal_id: StrictInt | None = Field(default=None, ge=1, le=2**63 - 1)


class ComputerUseDependencies:
    """Small injection seam used by tests and alternative runtime hosts."""

    def __init__(self, runtime_factory: Callable[[], Any] | None = None) -> None:
        self.runtime_factory = runtime_factory


def _jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _jsonable(value.to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _readiness_contract(value: Any) -> dict[str, Any]:
    result = _jsonable(value)
    required_booleans = (
        "ready",
        "backend_ready",
        "execution_cell_enrolled",
        "approval_ready",
        "stop_path_ready",
        "image_tool_supported",
        "visual_evidence_required",
        "screenshot_custody_ready",
    )
    if (
        not isinstance(result, dict)
        or any(not isinstance(result.get(key), bool) for key in required_booleans)
        or result.get("backend") not in {"remote", "host", "invalid"}
        or not isinstance(result.get("checked_at"), str)
        or not isinstance(result.get("blockers"), list)
        or not isinstance(result.get("warnings"), list)
    ):
        raise HTTPException(status_code=503, detail="readiness contract is invalid")
    return result


def _safe_projection(value: Any, *, media_reader: bool) -> Any:
    """Remove runtime paths and attach an honest replay availability marker."""

    value = _jsonable(value)
    if isinstance(value, list):
        return [_safe_projection(item, media_reader=media_reader) for item in value]
    if not isinstance(value, dict):
        return value
    projected: dict[str, Any] = {}
    media_retained = False
    for key, item in value.items():
        if key == "media_ref":
            media_retained = isinstance(item, str) and bool(item)
            continue
        if key == "seal_reference":
            # This is an internal relative artifact locator. The seal status is
            # safe and useful; the path itself is not an HTTP capability.
            continue
        projected[key] = _safe_projection(item, media_reader=media_reader)
    if "observation_id" in value and "sha256" in value:
        available = media_retained and media_reader
        seal_status = str(value.get("seal_status") or "unknown").lower()
        custody_sealed = seal_status == "sealed"
        projected["media"] = {
            "available": available,
            "content_integrity_check_available": available,
            "custody_sealed": custody_sealed,
            "seal_status": seal_status,
            "custody_reason": (
                "The frame is recorded as cryptographically sealed; the replay endpoint verifies it before returning bytes."
                if custody_sealed
                else "The retained frame has a content hash but no verified custody seal."
            ),
            "missing_reason": None if available else (
                "No frame was retained for this observation."
                if not media_retained
                else "The runtime does not expose a verified media reader."
            ),
        }
    return projected


def _runtime(request: Request, dependencies: ComputerUseDependencies) -> Any:
    injected = getattr(request.app.state, "computer_use_runtime", None)
    if injected is not None:
        return injected
    factory = getattr(request.app.state, "computer_use_runtime_factory", None)
    if not callable(factory):
        factory = dependencies.runtime_factory
    if callable(factory):
        try:
            runtime = factory()
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503,
                detail="computer-use runtime is unavailable",
            ) from exc
        if runtime is None:
            raise HTTPException(status_code=503, detail="computer-use runtime is unavailable")
        return runtime
    try:
        from maverick.computer_use_runtime import ComputerRuntimeStore

        return ComputerRuntimeStore()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="computer-use runtime is unavailable",
        ) from exc


def _identity() -> tuple[str, str]:
    try:
        from maverick.computer_use_runtime import runtime_identity

        tenant_id, principal = runtime_identity()
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="computer-use identity authority is unavailable",
        ) from exc
    if not isinstance(tenant_id, str) or not tenant_id:
        raise HTTPException(status_code=503, detail="computer-use tenant authority is unavailable")
    if not isinstance(principal, str) or not principal:
        raise HTTPException(status_code=503, detail="computer-use principal authority is unavailable")
    return tenant_id, principal


def _actor(request: Request) -> str:
    principal = caller_principal(request)
    if isinstance(principal, str) and principal.strip():
        return principal.strip()
    return _identity()[1]


def _require_same_origin(request: Request) -> None:
    # Resolved lazily because the root app imports and includes this router.
    from . import app as app_module

    app_module._require_same_origin(request)


def _read_scope(request: Request) -> str | None:
    """Viewers are owner-scoped; operators/auditors remain tenant-scoped."""

    principal = caller_principal(request)
    if principal is None:
        # Auth-off local mode is already bounded to the __local__ tenant.
        return None
    if has_permission(request, "operate") or has_permission(request, "audit"):
        return None
    return principal


async def _call(function, *args, **kwargs):
    try:
        return await run_in_threadpool(function, *args, **kwargs)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="computer-use record not found") from exc
    except PermissionError as exc:
        # Session access denial and a missing session are intentionally
        # indistinguishable across an owner or tenant boundary.
        raise HTTPException(status_code=404, detail="computer-use record not found") from exc
    except RuntimeError as exc:
        if type(exc).__name__ == "SessionConflict":
            raise HTTPException(status_code=409, detail=str(exc)[:500]) from exc
        raise HTTPException(status_code=503, detail="computer-use runtime is unavailable") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="computer-use runtime is unavailable") from exc


def create_computer_use_router(  # noqa: C901 - explicit route-local authority gates
    dependencies: ComputerUseDependencies | None = None,
) -> APIRouter:
    deps = dependencies or ComputerUseDependencies()
    router = APIRouter(prefix="/computer-use", tags=["computer-use"])

    @router.get("/readiness")
    async def readiness(request: Request):
        require_permission(request, "view")
        runtime = _runtime(request, deps)
        result = _readiness_contract(await _call(runtime.readiness))
        return {
            "schema": "lightwork.computer-use-readiness.v1",
            "readiness": result,
            "media_reader_available": callable(
                getattr(runtime, "read_observation_media", None)
            ),
            "controls": [
                action
                for action in (
                    "pause", "resume", "takeover", "release_takeover", "recover", "stop"
                )
                if callable(getattr(runtime, action, None))
            ],
            "setup_path": "/settings#computer-use-readiness",
        }

    @router.get("/sessions")
    async def sessions(
        request: Request,
        limit: int = Query(default=100, ge=1, le=200),
    ):
        require_permission(request, "view")
        runtime = _runtime(request, deps)
        tenant_id, _ = _identity()
        rows = await _call(runtime.list_sessions, tenant_id, _read_scope(request))
        items = _safe_projection(
            list(rows)[:limit],
            media_reader=callable(getattr(runtime, "read_observation_media", None)),
        )
        return {
            "schema": "lightwork.computer-use-session-list.v1",
            "items": items,
            "count": len(items),
            "limit": limit,
            "truncated": len(rows) > limit,
        }

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request, body: CreateSessionIn):
        """Create a governed session record only after live readiness passes."""

        _require_same_origin(request)
        require_permission(request, "operate")
        runtime = _runtime(request, deps)
        readiness_report = _readiness_contract(await _call(runtime.readiness))
        readiness_passed = (
            readiness_report.get("ready") is True
            and readiness_report.get("backend_ready") is True
            and readiness_report.get("approval_ready") is True
            and readiness_report.get("stop_path_ready") is True
            and (
                readiness_report.get("visual_evidence_required") is not True
                or readiness_report.get("screenshot_custody_ready") is True
            )
        )
        if not readiness_passed:
            blockers = readiness_report.get("blockers") or []
            messages = [
                str(item.get("message", ""))
                for item in blockers[:3]
                if isinstance(item, dict) and item.get("message")
            ]
            raise HTTPException(
                status_code=409,
                detail="; ".join(messages) or "computer-use readiness has not passed",
            )
        configured_backend = readiness_report.get("backend")
        if configured_backend not in {"remote", "host"}:
            raise HTTPException(status_code=503, detail="readiness backend is unavailable")
        if body.backend != configured_backend:
            raise HTTPException(
                status_code=409,
                detail="requested backend does not match the verified runtime backend",
            )
        if body.goal_id is not None:
            goal = await run_in_threadpool(_world().get_goal, int(body.goal_id))
            if goal is None:
                raise HTTPException(status_code=404, detail="no such goal")
            assert_goal_access(request, goal)
        tenant_id, _ = _identity()
        result = await _call(
            runtime.create_session,
            tenant_id,
            _actor(request),
            goal_id=str(body.goal_id) if body.goal_id is not None else None,
            surface=body.surface,
            monitor_id=body.monitor_id,
        )
        return {
            "schema": "lightwork.computer-use-session-created.v1",
            "session": _safe_projection(
                result,
                media_reader=callable(getattr(runtime, "read_observation_media", None)),
            ),
        }

    @router.get("/sessions/{session_id}")
    async def session(request: Request, session_id: RuntimeId):
        require_permission(request, "view")
        runtime = _runtime(request, deps)
        tenant_id, _ = _identity()
        result = await _call(
            runtime.get_session,
            session_id,
            tenant_id,
            _read_scope(request),
        )
        return _safe_projection(
            result,
            media_reader=callable(getattr(runtime, "read_observation_media", None)),
        )

    @router.get("/sessions/{session_id}/events")
    async def events(
        request: Request,
        session_id: RuntimeId,
        after_revision: int = Query(default=0, ge=0, le=2**63 - 1),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        require_permission(request, "view")
        runtime = _runtime(request, deps)
        tenant_id, _ = _identity()
        result = await _call(
            runtime.list_events,
            session_id,
            tenant_id,
            _read_scope(request),
            after_revision=after_revision,
            limit=limit,
        )
        return _safe_projection(
            result,
            media_reader=callable(getattr(runtime, "read_observation_media", None)),
        )

    @router.get("/sessions/{session_id}/events/wait")
    async def wait_for_events(
        request: Request,
        session_id: RuntimeId,
        after_revision: int = Query(default=0, ge=0, le=2**63 - 1),
        limit: int = Query(default=200, ge=1, le=500),
        timeout_ms: int = Query(default=10_000, ge=0, le=25_000),
    ):
        """Bounded authenticated long poll for session state and frame events."""

        require_permission(request, "view")
        runtime = _runtime(request, deps)
        tenant_id, _ = _identity()
        owner = _read_scope(request)

        async def read():
            return await _call(
                runtime.list_events,
                session_id,
                tenant_id,
                owner,
                after_revision=after_revision,
                limit=limit,
            )

        result = await read()
        rows = _jsonable(result)
        if not isinstance(rows, dict):
            raise HTTPException(status_code=503, detail="event stream contract is invalid")
        timed_out = False
        if not rows.get("events") and timeout_ms:
            semaphore = _get_sse_semaphore()
            if semaphore.locked():
                raise HTTPException(
                    status_code=503,
                    detail="too many concurrent event waits; retry shortly",
                    headers={"Retry-After": "5"},
                )
            await semaphore.acquire()
            try:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + (timeout_ms / 1000)
                while not rows.get("events"):
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        timed_out = True
                        break
                    if await request.is_disconnected():
                        timed_out = True
                        break
                    await asyncio.sleep(min(0.25, remaining))
                    result = await read()
                    rows = _jsonable(result)
                    if not isinstance(rows, dict):
                        raise HTTPException(
                            status_code=503,
                            detail="event stream contract is invalid",
                        )
            finally:
                semaphore.release()
        projected = _safe_projection(
            rows,
            media_reader=callable(getattr(runtime, "read_observation_media", None)),
        )
        projected["timed_out"] = timed_out
        return projected

    @router.get("/sessions/{session_id}/observations/{observation_id}/media")
    async def observation_media(
        request: Request,
        session_id: RuntimeId,
        observation_id: RuntimeId,
    ) -> Response:
        require_permission(request, "view")
        runtime = _runtime(request, deps)
        reader = getattr(runtime, "read_observation_media", None)
        if not callable(reader):
            raise HTTPException(status_code=404, detail="verified observation media unavailable")
        tenant_id, _ = _identity()
        seal_status = "unknown"
        observation_reader = getattr(runtime, "get_observation", None)
        if callable(observation_reader):
            observation = _jsonable(await _call(
                observation_reader,
                session_id,
                observation_id,
                tenant_id,
                _read_scope(request),
            ))
            if isinstance(observation, dict):
                reported = str(observation.get("seal_status") or "unknown").lower()
                if reported in {"sealed", "unsealed", "seal_error"}:
                    seal_status = reported
        payload, media_type, digest = await _call(
            reader,
            session_id,
            observation_id,
            tenant_id,
            _read_scope(request),
        )
        if (
            not isinstance(payload, bytes)
            or len(payload) > _MAX_FRAME_BYTES
            or media_type not in {"image/png", "image/jpeg"}
        ):
            raise HTTPException(status_code=503, detail="observation media contract is invalid")
        if not isinstance(digest, str) or not _FRAME_DIGEST.fullmatch(digest):
            raise HTTPException(status_code=503, detail="observation media contract is invalid")
        if hashlib.sha256(payload).hexdigest() != digest:
            raise HTTPException(status_code=503, detail="observation media contract is invalid")
        return Response(
            payload,
            media_type=media_type,
            headers={
                "Cache-Control": "no-store, private, max-age=0",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cross-Origin-Resource-Policy": "same-origin",
                "ETag": f'"sha256-{digest}"',
                "Pragma": "no-cache",
                "X-Lightwork-Content-Integrity": "sha256-checked",
                "X-Lightwork-Custody-Seal": seal_status,
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def mutate(
        request: Request,
        session_id: str,
        action: Literal[
            "pause", "resume", "takeover", "release_takeover", "recover", "stop"
        ],
        body: ControlIn,
    ):
        _require_same_origin(request)
        require_permission(request, "operate")
        runtime = _runtime(request, deps)
        tenant_id, _ = _identity()
        function = getattr(runtime, action, None)
        if not callable(function):
            raise HTTPException(status_code=503, detail="computer-use control is unavailable")
        kwargs: dict[str, Any] = {"expected_revision": body.expected_revision}
        if action in {"takeover", "release_takeover"}:
            kwargs["operator"] = _actor(request)
        if action == "stop":
            kwargs["reason"] = body.reason if isinstance(body, StopIn) else "operator requested"
        result = await _call(function, session_id, tenant_id, None, **kwargs)
        return {
            "schema": "lightwork.computer-use-control-result.v1",
            "action": action,
            "session": _safe_projection(
                result,
                media_reader=callable(getattr(runtime, "read_observation_media", None)),
            ),
        }

    @router.post("/sessions/{session_id}/pause")
    async def pause(request: Request, session_id: RuntimeId, body: ControlIn):
        return await mutate(request, session_id, "pause", body)

    @router.post("/sessions/{session_id}/resume")
    async def resume(request: Request, session_id: RuntimeId, body: ControlIn):
        return await mutate(request, session_id, "resume", body)

    @router.post("/sessions/{session_id}/takeover")
    async def takeover(request: Request, session_id: RuntimeId, body: ControlIn):
        return await mutate(request, session_id, "takeover", body)

    @router.post("/sessions/{session_id}/release_takeover")
    async def release_takeover(request: Request, session_id: RuntimeId, body: ControlIn):
        return await mutate(request, session_id, "release_takeover", body)

    @router.post("/sessions/{session_id}/recover")
    async def recover(request: Request, session_id: RuntimeId, body: ControlIn):
        return await mutate(request, session_id, "recover", body)

    @router.post("/sessions/{session_id}/stop")
    async def stop(request: Request, session_id: RuntimeId, body: StopIn):
        return await mutate(request, session_id, "stop", body)

    return router


router = create_computer_use_router()

__all__ = [
    "ComputerUseDependencies",
    "ControlIn",
    "CreateSessionIn",
    "StopIn",
    "create_computer_use_router",
    "router",
]
