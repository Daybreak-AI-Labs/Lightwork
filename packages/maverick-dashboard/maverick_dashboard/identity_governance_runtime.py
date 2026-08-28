"""Runtime wiring for bounded identity/data-governance connector snapshots.

The provider adapters live in :mod:`maverick.ai_identity_governance_connectors`.
This module supplies the production HTTP and secret-resolution seams while
preserving their fixed-host, fixed-operation, no-redirect and bounded-response
contracts. Returned values are normalized pseudonymous snapshots only; this
module neither persists provider payloads nor exposes administrative writes.
"""
from __future__ import annotations

from collections.abc import Mapping
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


def _adapters(section: object):
    transport = PinnedIdentityGovernanceHTTPTransport()
    transports = dict.fromkeys(PROVIDERS, transport)
    return configured_connectors(
        section,
        transports=transports,
        secret_resolver=_secret,
    )


def connector_status(section: object) -> dict[str, Any]:
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
    "collect_connector",
    "connector_status",
]
