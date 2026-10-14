"""Uniform production HTTP/API abuse and browser-boundary controls."""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

DEFAULT_MAX_BODY_BYTES = 1 * 1024 * 1024
DEFAULT_MULTIPART_BODY_BYTES = 2 * 1024 * 1024
MAX_QUERY_STRING_BYTES = 8 * 1024
MAX_QUERY_FIELDS = 64
MAX_PAGINATION_LIMIT = 1_000
MAX_PAGINATION_OFFSET = 1_000_000
MAX_CURSOR_BYTES = 2_048

_MAX_API_CONCURRENCY_ENV = "MAVERICK_API_MAX_CONCURRENCY"
_API_RATE_LIMIT_ENV = "MAVERICK_API_RATE_LIMIT_PER_MINUTE"
_DEFAULT_API_CONCURRENCY = 64
_DEFAULT_API_RATE = 1_200
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_SENSITIVE_QUERY_NAMES = frozenset(
    {"token", "access_token", "api_key", "authorization", "bearer"}
)
_PAGINATION_LIMIT_NAMES = frozenset({"limit", "page_size", "per_page", "top"})

_admission_lock = threading.Lock()
_recent_api_requests: deque[float] = deque()
_api_inflight = 0


class ProductionApiBoundaryError(RuntimeError):
    """Production API boundary configuration is invalid."""


def production_api_boundary_required() -> bool:
    """Treat production-policy ambiguity as requiring the strict boundary."""
    try:
        from maverick.production_scope import production_surface_required

        return bool(production_surface_required())
    except Exception:
        return True


def _configured_positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProductionApiBoundaryError(f"{name} must be an integer") from exc
    if value < 1:
        raise ProductionApiBoundaryError(f"{name} must be positive")
    return value


def validate_production_api_boundary() -> None:
    """Validate operator knobs before accepting production traffic."""
    if not production_api_boundary_required():
        return
    concurrency = _configured_positive_int(
        _MAX_API_CONCURRENCY_ENV, _DEFAULT_API_CONCURRENCY
    )
    rate = _configured_positive_int(_API_RATE_LIMIT_ENV, _DEFAULT_API_RATE)
    if concurrency > 1_024:
        raise ProductionApiBoundaryError(
            f"{_MAX_API_CONCURRENCY_ENV} must be at most 1024"
        )
    if rate > 100_000:
        raise ProductionApiBoundaryError(
            f"{_API_RATE_LIMIT_ENV} must be at most 100000"
        )


def require_production_idempotency_key(
    value: str | None,
    *,
    field: str = "Idempotency-Key",
) -> str:
    """Require a bounded opaque retry identity for consequential production work."""
    raw = str(value or "")
    if not production_api_boundary_required():
        return raw.strip()
    # Production keys are opaque operation identities. Silently trimming them
    # would collapse two distinct wire values onto one durable receipt, so
    # reject whitespace through the visible-ASCII check below.
    key = raw
    if not key:
        raise HTTPException(
            status_code=428,
            detail=f"{field} is required for this production operation",
        )
    if not 16 <= len(key.encode("utf-8")) <= 255:
        raise HTTPException(status_code=400, detail=f"{field} must be 16-255 bytes")
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in key):
        raise HTTPException(
            status_code=400,
            detail=f"{field} must contain visible ASCII without spaces",
        )
    return key


def public_error_detail(status_code: int, detail):
    """Return a stable production-safe HTTP error without exception internals."""
    if not production_api_boundary_required():
        return detail
    return {
        400: "invalid request",
        401: "unauthorized",
        402: "entitlement required",
        403: "forbidden",
        404: "not found",
        405: "method not allowed",
        409: "conflict",
        410: "gone",
        413: "request body too large",
        414: "URI too long",
        422: "request validation failed",
        428: "precondition required",
        429: "too many requests",
        500: "internal server error",
        501: "not implemented",
        502: "upstream service unavailable",
        503: "service unavailable",
        504: "upstream service unavailable",
    }.get(int(status_code), "request failed")


def public_validation_errors(errors: list[dict]) -> list[dict]:
    """Retain field/type diagnostics while removing values and parser internals."""
    if not production_api_boundary_required():
        return errors
    safe: list[dict] = []
    for error in errors[:64]:
        if not isinstance(error, dict):
            continue
        safe.append({
            "loc": error.get("loc", []),
            "type": str(error.get("type") or "validation_error")[:128],
        })
    return safe


def _effective_http_scheme(connection) -> str:
    url = getattr(connection, "url", None)
    scope = getattr(connection, "scope", {})
    if not isinstance(scope, dict):
        scope = {}
    scheme = str(getattr(url, "scheme", "") or scope.get("scheme") or "http").lower()
    if scheme == "ws":
        scheme = "http"
    elif scheme == "wss":
        scheme = "https"
    forwarded = str(connection.headers.get("x-forwarded-proto") or "")
    forwarded = forwarded.split(",", 1)[0].strip().lower()
    if forwarded in {"http", "https"}:
        scheme = forwarded
    return scheme


def _origin_tuple(value: str, *, referer: bool) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(value)
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").rstrip(".").lower()
        if scheme not in {"http", "https"} or not host:
            return None
        if not referer and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
        return scheme, host, port
    except ValueError:
        return None


def same_origin(connection, value: str, *, referer: bool = False) -> bool:
    """Compare scheme, canonical host, and effective port for HTTP or WebSocket."""
    actual = _origin_tuple(str(value or ""), referer=referer)
    if actual is None:
        return False
    try:
        scheme = _effective_http_scheme(connection)
        url = getattr(connection, "url", None)
        host = (getattr(url, "hostname", "") or "").rstrip(".").lower()
        port = getattr(url, "port", None)
    except ValueError:
        return False
    if not host:
        try:
            request_target = urlparse(
                f"//{str(connection.headers.get('host') or '').strip()}"
            )
            host = (request_target.hostname or "").rstrip(".").lower()
            port = request_target.port
        except ValueError:
            return False
    if not host:
        return False
    if port is None:
        port = 443 if scheme == "https" else 80
    # A TLS-terminating proxy commonly leaves the internal request port at 80.
    # Once it asserts https, compare against the public default 443 unless an
    # explicit forwarded host port is present.
    forwarded_host = str(connection.headers.get("x-forwarded-host") or "")
    if forwarded_host:
        try:
            public = urlparse(f"//{forwarded_host.split(',', 1)[0].strip()}")
            host = (public.hostname or host).rstrip(".").lower()
            port = public.port or (443 if scheme == "https" else 80)
        except ValueError:
            return False
    elif str(connection.headers.get("x-forwarded-proto") or ""):
        port = 443 if scheme == "https" else 80
    return actual == (scheme, host, int(port))


def request_origin_allowed(request: Request) -> bool:
    """Fail-closed same-origin policy for unsafe ambient-browser requests."""
    if request.method not in _UNSAFE_METHODS:
        return True
    origin = request.headers.get("origin")
    if origin:
        return same_origin(request, origin)
    referer = request.headers.get("referer")
    if referer:
        return same_origin(request, referer, referer=True)
    return False


def websocket_origin_allowed(websocket) -> bool:
    origin = websocket.headers.get("origin")
    return bool(origin) and same_origin(websocket, origin)


def _body_limit(request: Request) -> int:
    # The first production profile excludes attachments, voice, finance,
    # security-ingest, privacy-review, and every other large-upload surface.
    # Giving those source-tree paths larger pre-routing limits would let an
    # unadmitted request consume tens of MiB before the default-deny route
    # dependency can return 404. The retained chat form carries text only and
    # fits under the ordinary multipart ceiling.
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        return DEFAULT_MULTIPART_BODY_BYTES
    return DEFAULT_MAX_BODY_BYTES


async def _bounded_body(request: Request, maximum: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            length = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid request length") from None
        if length < 0:
            raise HTTPException(status_code=400, detail="invalid request length")
        if length > maximum:
            raise HTTPException(status_code=413, detail="request body too large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > maximum:
            raise HTTPException(status_code=413, detail="request body too large")
    return bytes(body)


def _validate_query(request: Request) -> None:
    raw = request.scope.get("query_string", b"")
    if len(raw) > MAX_QUERY_STRING_BYTES:
        raise HTTPException(status_code=414, detail="query string too large")
    items = request.query_params.multi_items()
    if len(items) > MAX_QUERY_FIELDS:
        raise HTTPException(status_code=400, detail="too many query parameters")
    for name, raw_value in items:
        key = name.casefold()
        if key in _SENSITIVE_QUERY_NAMES:
            raise HTTPException(
                status_code=400,
                detail="credentials are not accepted in query parameters",
            )
        if key in _PAGINATION_LIMIT_NAMES:
            try:
                value = int(raw_value)
            except ValueError:
                continue
            if value < 0 or value > MAX_PAGINATION_LIMIT:
                raise HTTPException(status_code=400, detail="pagination limit out of range")
        elif key in {"offset", "page"}:
            try:
                value = int(raw_value)
            except ValueError:
                continue
            if value < 0 or value > MAX_PAGINATION_OFFSET:
                raise HTTPException(status_code=400, detail="pagination offset out of range")
        elif key == "cursor" and len(raw_value.encode("utf-8")) > MAX_CURSOR_BYTES:
            raise HTTPException(status_code=400, detail="pagination cursor too large")


def _admit_api_request(now: float) -> tuple[bool, str]:
    global _api_inflight
    concurrency = _configured_positive_int(
        _MAX_API_CONCURRENCY_ENV, _DEFAULT_API_CONCURRENCY
    )
    rate = _configured_positive_int(_API_RATE_LIMIT_ENV, _DEFAULT_API_RATE)
    cutoff = now - 60.0
    with _admission_lock:
        while _recent_api_requests and _recent_api_requests[0] <= cutoff:
            _recent_api_requests.popleft()
        if _api_inflight >= concurrency:
            return False, "concurrency"
        if len(_recent_api_requests) >= rate:
            return False, "rate"
        _recent_api_requests.append(now)
        _api_inflight += 1
    return True, ""


def _release_api_request() -> None:
    global _api_inflight
    with _admission_lock:
        _api_inflight = max(0, _api_inflight - 1)


async def enforce_production_api_boundary(request: Request, call_next):
    """Bound every production request body and every API query/admission slot."""
    if not production_api_boundary_required():
        return await call_next(request)
    is_api = request.url.path.startswith("/api/")
    admitted = False
    try:
        if is_api:
            _validate_query(request)
            admitted, reason = _admit_api_request(time.monotonic())
            if not admitted:
                if reason == "rate":
                    return JSONResponse(
                        {"detail": "too many requests"},
                        status_code=429,
                        headers={"Retry-After": "60"},
                    )
                return JSONResponse(
                    {"detail": "service busy"},
                    status_code=503,
                    headers={"Retry-After": "1"},
                )
        if request.method in _UNSAFE_METHODS:
            body = await _bounded_body(request, _body_limit(request))
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            request._body = body  # noqa: SLF001 - bounded ASGI replay
            request._receive = replay_receive  # noqa: SLF001 - bounded ASGI replay
        return await call_next(request)
    except HTTPException as exc:
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )
    finally:
        if admitted:
            _release_api_request()


def reset_api_admission_state_for_tests() -> None:
    """Reset process-local admission state; intentionally test-only."""
    global _api_inflight
    with _admission_lock:
        _recent_api_requests.clear()
        _api_inflight = 0


__all__ = [
    "MAX_CURSOR_BYTES",
    "MAX_PAGINATION_LIMIT",
    "MAX_PAGINATION_OFFSET",
    "MAX_QUERY_FIELDS",
    "MAX_QUERY_STRING_BYTES",
    "ProductionApiBoundaryError",
    "enforce_production_api_boundary",
    "production_api_boundary_required",
    "public_error_detail",
    "public_validation_errors",
    "require_production_idempotency_key",
    "request_origin_allowed",
    "reset_api_admission_state_for_tests",
    "same_origin",
    "validate_production_api_boundary",
    "websocket_origin_allowed",
]
