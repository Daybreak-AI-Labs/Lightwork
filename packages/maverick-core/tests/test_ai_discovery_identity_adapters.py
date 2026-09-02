"""Provider audit adapters reduce identity telemetry before the sensor spool."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from maverick.ai_discovery_identity_adapters import (
    EntraConsentDiscoveryAdapter,
    EntraSignInDiscoveryAdapter,
    GoogleWorkspaceLoginDiscoveryAdapter,
    GoogleWorkspaceTokenDiscoveryAdapter,
    IdentityAuditAdapterError,
    IdentityAuditAdapterLimitError,
    OktaSystemLogDiscoveryAdapter,
    ReviewedAIApplication,
    source_application_sha256,
)
from maverick.ai_discovery_sensor_agents import (
    AGENT_CONFIG_SCHEMA,
    DiscoverySensorAgent,
    SensorAgentConfig,
    SensorAgentConfigError,
    TransportResponse,
)
from maverick.ai_discovery_sensor_clients import normalize_identity_audit

_SENSOR_ID = "DSN-" + "a" * 32
_CONFIG_SHA = "b" * 64
_NOW = 1_700_000_000.0
_BINDING_SALT = b"identity-audit-test-salt-not-for-production"


def _config(tmp_path: Path) -> SensorAgentConfig:
    return SensorAgentConfig.from_mapping(
        {
            "schema": AGENT_CONFIG_SCHEMA,
            "enable": True,
            "consent_acknowledged": True,
            "consent_reference": "consenting-identity-audit-estate",
            "source_kind": "identity_directory",
            "source_scope_code": "consenting_identity_audit",
            "coverage_limitations": [
                "reviewed_app_bindings_only",
                "supplied_events_only",
                "no_enterprise_completeness_claim",
            ],
            "endpoint_url": "http://127.0.0.1:8899",
            "allow_loopback_http": True,
            "key_file": str(tmp_path / "identity.pem"),
            "spool_file": str(tmp_path / "identity.sqlite"),
            "binding": {
                "tenant_ref": "consenting-test-tenant",
                "agent_id": "identity-directory-sensor",
                "sensor_id": _SENSOR_ID,
                "sensor_revision": 1,
                "config_sha256": _CONFIG_SHA,
            },
            "environment_code": "consenting_test",
            "deployment_type": "saas",
            "context_codes": ["managed_device"],
            "batch_size": 10,
            "max_pending": 100,
            "request_timeout_seconds": 5.0,
            "lease_seconds": 30.0,
            "base_backoff_seconds": 1.0,
        }
    )


class _RecordingTransport:
    def __init__(self) -> None:
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        return TransportResponse(201)


def _binding(source: str, application_ref: str) -> ReviewedAIApplication:
    return ReviewedAIApplication(
        source=source,
        source_application_sha256=source_application_sha256(
            source, application_ref, binding_salt=_BINDING_SALT
        ),
        ai_service_code="openai",
        ai_product_code="chatgpt",
        provider_code="openai",
    )


def _adapter(source: str, application_ref: str):
    constructors = {
        "okta_system_log": OktaSystemLogDiscoveryAdapter,
        "entra_signin": EntraSignInDiscoveryAdapter,
        "entra_consent": EntraConsentDiscoveryAdapter,
        "google_workspace_login": GoogleWorkspaceLoginDiscoveryAdapter,
        "google_workspace_token": GoogleWorkspaceTokenDiscoveryAdapter,
    }
    return constructors[source](
        [_binding(source, application_ref)],
        environment_code="consenting_test",
        binding_salt=_BINDING_SALT,
    )


def _record(source: str, application_ref: str) -> dict:
    if source == "okta_system_log":
        return {
            "uuid": "okta-private-event-id",
            "published": "2023-11-14T22:13:20Z",
            "eventType": "user.authentication.sso",
            "outcome": {"result": "SUCCESS"},
            "target": [{"id": application_ref, "type": "AppInstance"}],
            "actor": {"alternateId": "private.person@example.test"},
            "debugContext": {"debugData": {"requestId": "private-request"}},
        }
    if source == "entra_signin":
        return {
            "id": "entra-private-event-id",
            "createdDateTime": "2023-11-14T22:13:20Z",
            "appId": application_ref,
            "status": {"errorCode": 0},
            "signInEventTypes": ["interactiveUser"],
            "userPrincipalName": "private.person@example.test",
            "ipAddress": "203.0.113.41",
        }
    if source == "entra_consent":
        return {
            "id": "entra-private-consent-id",
            "activityDateTime": "2023-11-14T22:13:20Z",
            "activityDisplayName": "Consent to application",
            "targetResources": [{"id": application_ref, "displayName": "Private app"}],
            "initiatedBy": {"user": {"userPrincipalName": "private.person@example.test"}},
        }
    application_name = "login" if source == "google_workspace_login" else "token"
    return {
        "id": {
            "applicationName": application_name,
            "time": "2023-11-14T22:13:20Z",
            "uniqueQualifier": f"google-private-{application_name}-event",
        },
        "events": [
            {
                "name": "private_provider_event_name",
                "parameters": [
                    {"name": "client_id", "value": application_ref},
                    {"name": "actor_email", "value": "private.person@example.test"},
                    {"name": "access_token", "value": "private-token-value"},
                ],
            }
        ],
        "actor": {"email": "private.person@example.test"},
    }


@pytest.mark.parametrize(
    "source",
    [
        "okta_system_log",
        "entra_signin",
        "entra_consent",
        "google_workspace_login",
        "google_workspace_token",
    ],
)
def test_each_identity_provider_adapter_emits_only_the_closed_sensor_shape(source: str) -> None:
    raw_application = f"private-{source}-application-id"
    event = _adapter(source, raw_application).normalize(
        _record(source, raw_application), base_context_codes=("managed_device",)
    )

    assert event is not None
    assert event.domain_category == "identity_directory"
    assert event.ai_service_code == event.provider_code == "openai"
    assert event.ai_product_code == "chatgpt"
    assert "managed_device" in event.context_codes
    encoded = json.dumps(asdict(event), sort_keys=True)
    for prohibited in (
        raw_application,
        "private.person@example.test",
        "private-token-value",
        "203.0.113.41",
        "Private app",
        "private_provider_event_name",
    ):
        assert prohibited not in encoded
    assert set(asdict(event)) == {
        "event_sha256",
        "observed_at",
        "ai_service_code",
        "ai_product_code",
        "provider_code",
        "domain_category",
        "deployment_type",
        "environment_code",
        "context_codes",
    }


def test_unreviewed_or_ambiguous_source_apps_never_become_discovery_events() -> None:
    first = "private-okta-app-one"
    second = "private-okta-app-two"
    adapter = OktaSystemLogDiscoveryAdapter(
        [_binding("okta_system_log", first)],
        environment_code="consenting_test",
        binding_salt=_BINDING_SALT,
    )
    assert adapter.normalize(_record("okta_system_log", second)) is None

    ambiguous = OktaSystemLogDiscoveryAdapter(
        [_binding("okta_system_log", first), _binding("okta_system_log", second)],
        environment_code="consenting_test",
        binding_salt=_BINDING_SALT,
    )
    raw = _record("okta_system_log", first)
    raw["target"].append({"id": second, "type": "AppInstance"})
    with pytest.raises(IdentityAuditAdapterError) as exc:
        ambiguous.normalize(raw)
    assert first not in str(exc.value) and second not in str(exc.value)


def test_adapter_reuses_normalized_spool_and_signed_delivery_without_raw_payload(tmp_path: Path) -> None:
    raw_application = "private-entra-app-id"
    transport = _RecordingTransport()
    agent = DiscoverySensorAgent(
        _config(tmp_path), signer=lambda _message: b"s" * 64, transport=transport,
        clock=lambda: _NOW,
    )
    adapter = _adapter("entra_signin", raw_application)

    report = adapter.ingest([_record("entra_signin", raw_application)], agent)
    assert report == {"input": 1, "normalized": 1, "unmatched": 0, "invalid": 0, "duplicate": 0}
    assert agent.deliver_once()["delivered"] == 1
    assert len(transport.requests) == 1
    spool_bytes = agent.config.spool_file.read_bytes()
    request_body = transport.requests[0].body.decode("utf-8")
    for prohibited in (
        raw_application,
        "private.person@example.test",
        "203.0.113.41",
        _BINDING_SALT.decode("utf-8"),
    ):
        assert prohibited.encode("utf-8") not in spool_bytes
        assert prohibited not in request_body
    with pytest.raises(SensorAgentConfigError, match="reviewed identity audit adapter"):
        agent.normalize(_record("entra_signin", raw_application))


def test_identity_adapter_page_bound_stops_before_queueing_unbounded_source_records(tmp_path: Path) -> None:
    raw_application = "private-google-client"
    agent = DiscoverySensorAgent(
        _config(tmp_path), signer=lambda _message: b"s" * 64, clock=lambda: _NOW,
    )
    adapter = _adapter("google_workspace_token", raw_application)
    with pytest.raises(IdentityAuditAdapterLimitError):
        adapter.ingest(
            [_record("google_workspace_token", raw_application)] * 501,
            agent,
        )
    assert agent.spool.counts()["pending"] == 0


def test_identity_normalizer_cannot_accept_raw_fields_or_unreviewed_taxonomy() -> None:
    event = normalize_identity_audit(
        source_event_sha256="a" * 64,
        observed_at=_NOW,
        ai_service_code="openai",
        ai_product_code="chatgpt",
        provider_code="openai",
        environment_code="workforce",
        context_codes=("managed_device",),
    )
    assert event.domain_category == "identity_directory"
    with pytest.raises(IdentityAuditAdapterError):
        ReviewedAIApplication(
            source="entra_signin",
            source_application_sha256="b" * 64,
            ai_service_code="openai",
            ai_product_code="employee_42",
            provider_code="openai",
        )
