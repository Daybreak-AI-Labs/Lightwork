"""Narrow machine credentials for dashboard operational endpoints.

The Prometheus scraper must not borrow an end-user session or the static
dashboard operator token.  This module owns an exact-path, metrics-only bearer
with a two-key rotation window.  Query-string credentials are intentionally
never inspected.
"""
from __future__ import annotations

import hmac
import os

from fastapi import HTTPException, Request

METRICS_TOKEN_ENV = "MAVERICK_METRICS_TOKEN"
METRICS_PREVIOUS_TOKEN_ENV = "MAVERICK_METRICS_TOKEN_PREVIOUS"
_MIN_TOKEN_BYTES = 32
_MAX_TOKEN_BYTES = 512


class ServiceCredentialConfigurationError(RuntimeError):
    """A production service credential is absent or unsafe."""


def _production_required() -> bool:
    """Treat an unreadable production posture as production, not as a bypass."""
    try:
        from maverick.production_scope import production_surface_required

        return bool(production_surface_required())
    except Exception:
        return True


def _configured_tokens() -> tuple[str, str]:
    return (
        os.environ.get(METRICS_TOKEN_ENV, ""),
        os.environ.get(METRICS_PREVIOUS_TOKEN_ENV, ""),
    )


def _token_configuration_error(value: str, name: str) -> str | None:
    size = len(value.encode("utf-8"))
    if size < _MIN_TOKEN_BYTES or size > _MAX_TOKEN_BYTES:
        return f"{name} must contain {_MIN_TOKEN_BYTES}-{_MAX_TOKEN_BYTES} bytes"
    if any(ord(char) < 0x21 or ord(char) > 0x7E for char in value):
        return f"{name} must contain visible ASCII without spaces"
    return None


def metrics_service_auth_enabled() -> bool:
    """Whether ``/metrics`` uses its dedicated service credential."""
    current, previous = _configured_tokens()
    return _production_required() or bool(current or previous)


def validate_metrics_service_configuration() -> None:
    """Fail startup when the production scraper credential cannot be trusted."""
    if not _production_required():
        return
    current, previous = _configured_tokens()
    errors: list[str] = []
    current_error = _token_configuration_error(current, METRICS_TOKEN_ENV)
    if current_error:
        errors.append(current_error)
    if previous:
        previous_error = _token_configuration_error(
            previous, METRICS_PREVIOUS_TOKEN_ENV
        )
        if previous_error:
            errors.append(previous_error)
        if current and hmac.compare_digest(previous, current):
            errors.append("current and previous metrics credentials must differ")
    dashboard = os.environ.get("MAVERICK_DASHBOARD_TOKEN", "")
    if current and dashboard and hmac.compare_digest(current, dashboard):
        errors.append("metrics and dashboard credentials must be distinct")
    if previous and dashboard and hmac.compare_digest(previous, dashboard):
        errors.append("metrics and dashboard credentials must be distinct")
    if errors:
        raise ServiceCredentialConfigurationError("; ".join(errors))


def _bearer(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return ""
    return value.strip()


def require_metrics_service_credential(request: Request) -> None:
    """Authenticate the exact ``/metrics`` request with the current/previous key."""
    if request.url.path != "/metrics":
        from .auth_metrics import record_auth_failure

        record_auth_failure("metrics_scope_violation")
        raise HTTPException(status_code=403, detail="service credential scope violation")
    current, previous = _configured_tokens()
    if not current:
        # A previous key is never authoritative by itself.  This prevents a
        # half-completed rotation from silently making the retired key current.
        from .auth_metrics import record_auth_failure

        record_auth_failure("metrics_credential_unavailable")
        raise HTTPException(status_code=503, detail="metrics credential unavailable")
    supplied = _bearer(request)
    matches = False
    if supplied:
        # Evaluate both comparisons even when the first matches so the overlap
        # window does not reveal which generation authenticated the request.
        matches_current = hmac.compare_digest(supplied, current)
        matches_previous = bool(previous) and hmac.compare_digest(supplied, previous)
        matches = matches_current or matches_previous
    if not matches:
        from .auth_metrics import record_auth_failure

        record_auth_failure("metrics_bad_token")
        raise HTTPException(
            status_code=401,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.metrics_service_authenticated = True


__all__ = [
    "METRICS_PREVIOUS_TOKEN_ENV",
    "METRICS_TOKEN_ENV",
    "ServiceCredentialConfigurationError",
    "metrics_service_auth_enabled",
    "require_metrics_service_credential",
    "validate_metrics_service_configuration",
]
