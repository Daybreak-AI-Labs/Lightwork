"""Runtime wiring for bounded identity/data-governance connector snapshots.

The provider adapters live in :mod:`maverick.ai_identity_governance_connectors`.
This module supplies the production HTTP and secret-resolution seams while
preserving their fixed-host, fixed-operation, no-redirect and bounded-response
contracts. Returned values are normalized pseudonymous snapshots only; this
module neither persists provider payloads nor exposes administrative writes.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

from maverick.ai_identity_governance_connectors import (
    PROVIDERS,
    BoundedHTTPResponse,
    IdentityGovernanceConfigError,
    IdentityGovernanceLimitError,
    IdentityGovernanceProtocolError,
    configured_connectors,
)


class PinnedIdentityGovernanceHTTPTransport:
    """DNS-pinned, redirect-disabled transport for the closed provider adapters."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None,
        timeout: float,
        max_response_bytes: int,
    ) -> BoundedHTTPResponse:
        from maverick.enterprise import enterprise_egress_denial
        from maverick.tools._ssrf import BlockedHost, safe_client

        if method not in {"GET", "POST"}:
            raise IdentityGovernanceProtocolError("connector_method_rejected")
        denial = enterprise_egress_denial(
            url,
            tool="ai_identity_governance_connectors",
        )
        if denial:
            raise IdentityGovernanceProtocolError("connector_egress_denied")
        try:
            with safe_client(url, timeout=timeout, follow_redirects=False) as client:
                with client.stream(
                    method,
                    url,
                    headers=dict(headers),
                    json=dict(json_body) if json_body is not None else None,
                ) as response:
                    declared = response.headers.get("content-length")
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except ValueError:
                            raise IdentityGovernanceProtocolError(
                                "connector_content_length_invalid"
                            ) from None
                        if declared_size < 0 or declared_size > max_response_bytes:
                            raise IdentityGovernanceLimitError(
                                "connector_response_too_large"
                            )
                    chunks: list[bytes] = []
                    observed = 0
                    for chunk in response.iter_bytes():
                        observed += len(chunk)
                        if observed > max_response_bytes:
                            raise IdentityGovernanceLimitError(
                                "connector_response_too_large"
                            )
                        chunks.append(chunk)
                    selected_headers = {
                        key: value
                        for key, value in response.headers.items()
                        if key.lower()
                        in {
                            "content-length",
                            "content-type",
                            "link",
                            "retry-after",
                        }
                    }
                    return BoundedHTTPResponse(
                        status_code=int(response.status_code),
                        headers=selected_headers,
                        body=b"".join(chunks),
                        # safe_client never follows redirects; the exact request
                        # URL is therefore the authoritative final URL. Its
                        # internal DNS pin may replace response.url with an IP.
                        final_url=url,
                    )
        except BlockedHost:
            raise IdentityGovernanceProtocolError("connector_ssrf_blocked") from None


def _secret(reference: str) -> str | None:
    from maverick.secret_provider import get_secret

    return get_secret(reference)


def _connector_section(section: object) -> object:
    """Remove only scheduler metadata before strict adapter validation."""

    if not isinstance(section, Mapping) or "scheduler" not in section:
        return section
    selected = dict(section)
    selected.pop("scheduler", None)
    return selected


def _adapters(section: object):
    transport = PinnedIdentityGovernanceHTTPTransport()
    transports = dict.fromkeys(PROVIDERS, transport)
    return configured_connectors(
        _connector_section(section),
        transports=transports,
        secret_resolver=_secret,
    )


def build_scheduler(
    section: object,
    *,
    scheduler_factory: Callable[..., object] | None = None,
    store: object | None = None,
    clock: Callable[[], float] | None = None,
    change_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> object | None:
    """Build, but never start, the explicitly configured scheduler engine.

    Missing schedules return ``None`` without invoking the factory.  The
    default engine has no change callback, so this seam cannot create dynamic
    risk or alerts unless a later integration explicitly supplies a bounded
    callback.
    """

    from . import identity_governance_scheduler as scheduler

    selected = {} if section is None else section
    if not scheduler.configured_schedules(selected):
        return None
    factory = scheduler_factory or scheduler.IdentityGovernanceScheduler
    kwargs: dict[str, object] = {}
    if store is not None:
        kwargs["store"] = store
    if clock is not None:
        kwargs["clock"] = clock
    if change_callback is not None:
        kwargs["change_callback"] = change_callback
    engine = factory(**kwargs)
    if not callable(getattr(engine, "status", None)) or not callable(
        getattr(engine, "run_configured", None)
    ):
        raise TypeError("identity-governance scheduler factory returned an invalid engine")
    return engine


def scheduler_status(
    section: object,
    *,
    scheduler_factory: Callable[..., object] | None = None,
) -> dict[str, Any]:
    """Expose opt-in schedule reality without starting background work."""

    from . import identity_governance_scheduler as scheduler

    selected = {} if section is None else section
    schedules = scheduler.configured_schedules(selected)
    status: dict[str, Any] = {
        "schema": "lightwork.identity_governance.scheduler-status.v1",
        "enabled": bool(schedules),
        "configured_providers": [item.provider for item in schedules],
        "poll_seconds": (scheduler.scheduler_poll_seconds(selected) if schedules else None),
        "runtime_attached": False,
        "automatic_start": False,
        "activation": "explicit_factory_required",
        "collection_mode": "bounded_read_only_snapshot",
        "source_state": "unavailable",
        "source_complete": False,
        "live_acceptance_verified": False,
        "complete_inventory_claimed": False,
        "provider_writes": False,
        "risk_generated": False,
    }
    if scheduler_factory is None or not schedules:
        return status
    engine = build_scheduler(
        selected,
        scheduler_factory=scheduler_factory,
    )
    assert engine is not None
    runtime_status = engine.status(selected)
    if not isinstance(runtime_status, Mapping):
        raise TypeError("identity-governance scheduler status must be a table")
    for field in (
        "source_complete",
        "live_acceptance_verified",
        "complete_inventory_claimed",
        "risk_generated",
    ):
        if runtime_status.get(field) is not False:
            raise ValueError("identity-governance scheduler status contains an unsupported claim")
    status["runtime_attached"] = True
    status["runtime"] = dict(runtime_status)
    return status


def connector_status(
    section: object,
    *,
    scheduler_factory: Callable[..., object] | None = None,
) -> dict[str, Any]:
    """Return configuration reality without resolving or disclosing secrets."""

    adapters = _adapters(section)
    configured = [adapter.provider for adapter in adapters]
    return {
        "schema": "lightwork.identity_governance.runtime-status.v1",
        "enabled": bool(configured) or bool(
            isinstance(section, Mapping) and section.get("enable") is True
        ),
        "supported_providers": list(PROVIDERS),
        "configured_providers": configured,
        "transport": {
            "read_only": True,
            "dns_pinned": True,
            "redirects_followed": False,
            "secret_values_exposed": False,
        },
        "continuous_collection": scheduler_status(
            section,
            scheduler_factory=scheduler_factory,
        ),
        "live_acceptance_verified": False,
        "complete_inventory_claimed": False,
    }


def collect_connector(section: object, provider: str) -> dict[str, Any]:
    """Collect one bounded snapshot through the exact configured provider seam."""

    selected = str(provider or "").strip().lower()
    if selected not in PROVIDERS:
        raise ValueError("identity-governance provider is unsupported")
    adapters = {adapter.provider: adapter for adapter in _adapters(section)}
    adapter = adapters.get(selected)
    if adapter is None:
        raise IdentityGovernanceConfigError("connector_disabled")
    return asdict(adapter.collect())


__all__ = [
    "PinnedIdentityGovernanceHTTPTransport",
    "build_scheduler",
    "collect_connector",
    "connector_status",
    "scheduler_status",
]
