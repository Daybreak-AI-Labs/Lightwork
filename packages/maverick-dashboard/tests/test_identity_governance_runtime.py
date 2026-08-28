"""Focused runtime wiring tests for identity-governance connectors."""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from maverick.ai_identity_governance_connectors import IdentityGovernanceLimitError
from maverick_dashboard import identity_governance_runtime as runtime


class _Response:
    def __init__(self, body: bytes, *, declared: int | None = None) -> None:
        self.status_code = 200
        self.headers = {"content-type": "application/json", "link": "next"}
        if declared is not None:
            self.headers["content-length"] = str(declared)
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def iter_bytes(self):
        yield self._body


class _Client:
    def __init__(self, response: _Response, calls: list[dict]) -> None:
        self.response = response
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def test_pinned_transport_preserves_closed_request_and_bounded_response(monkeypatch):
    from maverick import enterprise
    from maverick.tools import _ssrf

    calls = []
    response = _Response(b'{"value":[]}', declared=12)
    monkeypatch.setattr(enterprise, "enterprise_egress_denial", lambda *_a, **_k: None)
    monkeypatch.setattr(_ssrf, "safe_client", lambda *_a, **_k: _Client(response, calls))

    result = runtime.PinnedIdentityGovernanceHTTPTransport().request(
        "POST",
        "https://account.purview.azure.com/datamap/api/search/query",
        headers={"Authorization": "Bearer transient-secret"},
        json_body={"keywords": None, "limit": 100},
        timeout=12,
        max_response_bytes=1024,
    )
    assert result.final_url == (
        "https://account.purview.azure.com/datamap/api/search/query"
    )
    assert result.body == b'{"value":[]}'
    assert result.headers == {
        "content-type": "application/json",
        "content-length": "12",
        "link": "next",
    }
    assert calls[0]["method"] == "POST"
    assert calls[0]["json"] == {"keywords": None, "limit": 100}


def test_pinned_transport_refuses_oversized_declared_response(monkeypatch):
    from maverick import enterprise
    from maverick.tools import _ssrf

    response = _Response(b"{}", declared=2048)
    monkeypatch.setattr(enterprise, "enterprise_egress_denial", lambda *_a, **_k: None)
    monkeypatch.setattr(_ssrf, "safe_client", lambda *_a, **_k: _Client(response, []))
    with pytest.raises(IdentityGovernanceLimitError, match="too_large"):
        runtime.PinnedIdentityGovernanceHTTPTransport().request(
            "GET",
            "https://graph.microsoft.com/v1.0/servicePrincipals",
            headers={},
            json_body=None,
            timeout=12,
            max_response_bytes=1024,
        )


@dataclass
class _Snapshot:
    schema: str = "lightwork.identity_governance.snapshot.v1"
    provider: str = "entra"
    live_acceptance_verified: bool = False
    complete_inventory_claimed: bool = False


class _Adapter:
    provider = "entra"

    def collect(self):
        return _Snapshot()


def test_status_and_collection_never_claim_live_acceptance(monkeypatch):
    monkeypatch.setattr(runtime, "_adapters", lambda _section: (_Adapter(),))
    status = runtime.connector_status({"enable": True})
    assert status["configured_providers"] == ["entra"]
    assert status["live_acceptance_verified"] is False
    assert status["complete_inventory_claimed"] is False
    snapshot = runtime.collect_connector({"enable": True}, "entra")
    assert snapshot["provider"] == "entra"
    assert snapshot["live_acceptance_verified"] is False
    assert snapshot["complete_inventory_claimed"] is False
