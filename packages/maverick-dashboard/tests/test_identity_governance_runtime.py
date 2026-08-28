"""Focused runtime wiring tests for identity-governance connectors."""
from __future__ import annotations

import json
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


def _scheduled_section():
    return {
        "enable": True,
        "entra": {
            "enable": True,
            "tenant_id": "raw-tenant@example.invalid",
            "base_url": "https://graph.microsoft.com",
            "credential_secret_ref": "RAW_ENTRA_TOKEN",
        },
        "scheduler": {
            "enable": True,
            "poll_seconds": 60,
            "providers": {"entra": {"enable": True}},
        },
    }


def test_scheduler_metadata_is_removed_only_at_strict_adapter_boundary(monkeypatch):
    captured = []

    def configured(section, **_kwargs):
        captured.append(section)
        return ()

    monkeypatch.setattr(runtime, "configured_connectors", configured)
    runtime._adapters(_scheduled_section())

    assert len(captured) == 1
    assert "scheduler" not in captured[0]
    assert captured[0]["entra"]["credential_secret_ref"] == "RAW_ENTRA_TOKEN"


def test_scheduler_factory_is_default_off_and_never_called_when_unconfigured():
    calls = 0

    def factory(**_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("disabled scheduler factory must not run")

    assert runtime.build_scheduler({}, scheduler_factory=factory) is None
    status = runtime.scheduler_status({}, scheduler_factory=factory)
    assert calls == 0
    assert status["enabled"] is False
    assert status["runtime_attached"] is False
    assert status["automatic_start"] is False
    assert status["source_complete"] is False
    assert status["risk_generated"] is False


def test_explicit_scheduler_factory_adds_sanitized_runtime_status(monkeypatch):
    captured = []

    class Engine:
        def run_configured(self, _section, *, actor):
            raise AssertionError(f"status must not collect as {actor}")

        def status(self, _section):
            return {
                "schema": "lightwork.identity_governance.scheduler-runtime-status.v1",
                "enabled": True,
                "providers": [{"provider": "entra", "health": "never_run"}],
                "source_complete": False,
                "live_acceptance_verified": False,
                "complete_inventory_claimed": False,
                "risk_generated": False,
            }

    def factory(**kwargs):
        captured.append(kwargs)
        return Engine()

    monkeypatch.setattr(runtime, "_adapters", lambda _section: (_Adapter(),))
    section = _scheduled_section()
    status = runtime.connector_status(section, scheduler_factory=factory)

    continuous = status["continuous_collection"]
    assert captured == [{}]
    assert continuous["enabled"] is True
    assert continuous["configured_providers"] == ["entra"]
    assert continuous["runtime_attached"] is True
    assert continuous["automatic_start"] is False
    assert continuous["provider_writes"] is False
    assert continuous["runtime"]["risk_generated"] is False
    serialized = json.dumps(status, sort_keys=True)
    assert "raw-tenant@example.invalid" not in serialized
    assert "RAW_ENTRA_TOKEN" not in serialized


def test_scheduler_status_rejects_fabricated_completion_or_risk_claims():
    class InvalidEngine:
        def run_configured(self, _section, *, actor):
            del actor

        def status(self, _section):
            return {
                "source_complete": False,
                "live_acceptance_verified": False,
                "complete_inventory_claimed": False,
                "risk_generated": True,
            }

    with pytest.raises(ValueError, match="unsupported claim"):
        runtime.scheduler_status(
            _scheduled_section(),
            scheduler_factory=lambda: InvalidEngine(),
        )
