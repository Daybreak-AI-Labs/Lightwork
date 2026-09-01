from __future__ import annotations

import hashlib
import json

import pytest
from maverick.ai_identity_governance_connectors import BoundedHTTPResponse
from maverick.ai_identity_governance_controls import (
    AgentPrincipalFederationObservation,
    ConditionalAccessObservation,
    ConditionalAccessObservationRequest,
    FederationObservationRequest,
    IdentityGovernanceControlConfigError,
    IdentityGovernanceDeliveryError,
    PurviewWritebackAck,
    PurviewWritebackRequest,
)
from maverick.ai_identity_governance_provider_wire import (
    FixedAuthorityObservationTransport,
    FixedAuthorityPurviewWritebackTransport,
    FixedProviderOperation,
)


def _sha(value: object) -> str:
    encoded = (
        value.encode()
        if isinstance(value, str)
        else json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
    )
    return hashlib.sha256(encoded).hexdigest()


TENANT = _sha("tenant")
PRINCIPAL = "prn_" + (_sha("principal")[:32])
SUBJECT = "sub_" + (_sha("subject")[:32])
BINDING = _sha("binding")
AUTHORITY = _sha("wire-authority")


class HTTP:
    dns_pinned = True
    redirects_followed = False

    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


def _response(
    status: int = 200,
    *,
    url: str = "https://graph.microsoft.com/v1.0/fixed",
) -> BoundedHTTPResponse:
    return BoundedHTTPResponse(
        status_code=status,
        headers={"content-type": "application/json"},
        body=b"{}",
        final_url=url,
    )


class ObservationAuthority:
    tenant_sha256 = TENANT
    provider = "entra"
    authority_revision = 7
    authority_sha256 = AUTHORITY

    def __init__(self, *, host="graph.microsoft.com"):
        self.host = host

    def build_observation_operation(self, purpose, request):
        assert purpose == "federation"
        return FixedProviderOperation.build(
            tenant_sha256=TENANT,
            provider="entra",
            operation="federation_observe",
            method="GET",
            url=f"https://{self.host}/v1.0/fixed",
            headers={"Authorization": "Bearer transient"},
            json_body=None,
            request_sha256=_sha(request.__dict__),
            authority_revision=7,
            authority_sha256=AUTHORITY,
            timeout_seconds=5,
            max_response_bytes=1024,
            retry_attempts=2,
            retry_backoff_seconds=0.01,
            idempotency_header_name=None,
        )

    def parse_federation_observation(self, request, _operation, _response):
        return AgentPrincipalFederationObservation(
            schema="lightwork.identity_governance.agent-principal-observation.v1",
            tenant_sha256=request.tenant_sha256,
            provider=request.provider,
            principal_ref=request.principal_ref,
            provider_subject_id=request.provider_subject_id,
            binding_sha256=request.binding_sha256,
            binding_revision=request.binding_revision,
            active=True,
            permission_ids=("prm_" + (_sha("permission")[:32]),),
            source_sha256=_sha("source"),
            observed_at=1_000.0,
        )

    def parse_conditional_access_observation(self, *_args):
        raise AssertionError("wrong parser")


def _federation_request() -> FederationObservationRequest:
    return FederationObservationRequest(
        schema="lightwork.identity_governance.agent-principal-query.v1",
        tenant_sha256=TENANT,
        provider="entra",
        principal_ref=PRINCIPAL,
        provider_subject_id=SUBJECT,
        binding_sha256=BINDING,
        binding_revision=3,
    )


def test_observation_transport_retries_and_preserves_exact_binding():
    http = HTTP([_response(503), _response()])
    transport = FixedAuthorityObservationTransport(
        tenant_sha256=TENANT,
        provider="entra",
        purpose="federation",
        fixed_hosts=("graph.microsoft.com",),
        authority=ObservationAuthority(),
        http=http,
        sleeper=lambda _seconds: None,
    )
    result = transport.observe(_federation_request())
    assert result.binding_sha256 == BINDING
    assert len(http.calls) == 2
    assert transport.status()["provider_contract_accepted"] is False
    assert transport.status()["live_acceptance_verified"] is False


def test_observation_transport_rejects_authority_url_outside_fixed_host():
    transport = FixedAuthorityObservationTransport(
        tenant_sha256=TENANT,
        provider="entra",
        purpose="federation",
        fixed_hosts=("graph.microsoft.com",),
        authority=ObservationAuthority(host="evil.example"),
        http=HTTP([_response()]),
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(ValueError, match="fixed authority"):
        transport.observe(_federation_request())


class OktaConditionalAuthority(ObservationAuthority):
    provider = "okta"

    def build_observation_operation(self, purpose, request):
        assert purpose == "conditional_access"
        return FixedProviderOperation.build(
            tenant_sha256=TENANT,
            provider="okta",
            operation="conditional_access_observe",
            method="GET",
            url="https://tenant.okta.com/api/v1/fixed",
            headers={"Authorization": "SSWS transient"},
            json_body=None,
            request_sha256=_sha(request.__dict__),
            authority_revision=7,
            authority_sha256=AUTHORITY,
            timeout_seconds=5,
            max_response_bytes=1024,
            retry_attempts=1,
            retry_backoff_seconds=0.01,
            idempotency_header_name=None,
        )

    def parse_conditional_access_observation(
        self, request, _operation, _response
    ):
        return ConditionalAccessObservation(
            schema="lightwork.identity_governance.conditional-access-observation.v1",
            tenant_sha256=request.tenant_sha256,
            provider=request.provider,
            principal_ref=request.principal_ref,
            provider_subject_id=request.provider_subject_id,
            binding_sha256=request.binding_sha256,
            binding_revision=request.binding_revision,
            outcome="challenge",
            policy_sha256=_sha("okta-policy"),
            reason_codes=("policy.challenge",),
            source_sha256=_sha("okta-source"),
            observed_at=1_000.0,
        )


def test_okta_conditional_access_uses_same_fixed_authority_boundary():
    request = ConditionalAccessObservationRequest(
        schema="lightwork.identity_governance.conditional-access-query.v1",
        tenant_sha256=TENANT,
        provider="okta",
        principal_ref=PRINCIPAL,
        provider_subject_id=SUBJECT,
        binding_sha256=BINDING,
        binding_revision=3,
    )
    transport = FixedAuthorityObservationTransport(
        tenant_sha256=TENANT,
        provider="okta",
        purpose="conditional_access",
        fixed_hosts=("tenant.okta.com",),
        authority=OktaConditionalAuthority(),
        http=HTTP([_response(url="https://tenant.okta.com/api/v1/fixed")]),
        sleeper=lambda _seconds: None,
    )
    result = transport.observe(request)
    assert result.outcome == "challenge"
    assert result.observation_only is True
    assert result.live_acceptance_verified is False


def _purview_request() -> PurviewWritebackRequest:
    material = {
        "schema": "lightwork.identity_governance.purview-writeback-request.v1",
        "operation": "apply",
        "tenant_sha256": TENANT,
        "intent_id": "IGPW-" + ("1" * 32),
        "principal_ref": PRINCIPAL,
        "asset_id": "ast_" + (_sha("asset")[:32]),
        "source_binding_sha256": _sha("asset-binding"),
        "source_revision": 2,
        "classification_codes": ("sensitive",),
        "previous_classification_codes": (),
        "idempotency_sha256": _sha("idempotency"),
    }
    return PurviewWritebackRequest(
        **material,
        request_sha256=_sha(material),
    )


class PurviewAuthority:
    tenant_sha256 = TENANT
    provider = "purview"
    authority_revision = 4
    authority_sha256 = AUTHORITY

    def __init__(self):
        self.recoveries = 0

    def build_purview_operation(self, request):
        return FixedProviderOperation.build(
            tenant_sha256=TENANT,
            provider="purview",
            operation="purview_apply",
            method="POST",
            url="https://account.purview.azure.com/fixed",
            headers={"Idempotency-Key": request.idempotency_sha256},
            json_body={"fixed": True},
            request_sha256=_sha(request.__dict__),
            authority_revision=4,
            authority_sha256=AUTHORITY,
            timeout_seconds=5,
            max_response_bytes=1024,
            retry_attempts=2,
            retry_backoff_seconds=0.01,
            idempotency_header_name="Idempotency-Key",
        )

    @staticmethod
    def _ack(request):
        return PurviewWritebackAck(
            schema="lightwork.identity_governance.purview-writeback-ack.v1",
            operation=request.operation,
            tenant_sha256=request.tenant_sha256,
            intent_id=request.intent_id,
            request_sha256=request.request_sha256,
            idempotency_sha256=request.idempotency_sha256,
            result_binding_sha256=_sha("result-binding"),
            result_revision=3,
            receipt_sha256=_sha("receipt"),
            accepted=True,
        )

    def parse_purview_ack(self, request, _operation, _response):
        return self._ack(request)

    def recover_purview_ack(self, request, _operation):
        self.recoveries += 1
        return self._ack(request)


def test_purview_ambiguous_write_requires_and_uses_recovery():
    authority = PurviewAuthority()
    http = HTTP([RuntimeError("credential must not escape")])
    transport = FixedAuthorityPurviewWritebackTransport(
        tenant_sha256=TENANT,
        provider="purview",
        fixed_hosts=("account.purview.azure.com",),
        authority=authority,
        http=http,
        sleeper=lambda _seconds: None,
    )
    result = transport.apply(_purview_request())
    assert result.accepted is True
    assert authority.recoveries == 1
    assert len(http.calls) == 1
    assert transport.status()["provider_write_accepted"] is False


def test_transport_fails_closed_without_dns_pinned_http():
    http = HTTP([_response()])
    http.dns_pinned = False
    with pytest.raises(
        IdentityGovernanceControlConfigError,
        match="http_boundary_invalid",
    ):
        FixedAuthorityObservationTransport(
            tenant_sha256=TENANT,
            provider="entra",
            purpose="federation",
            fixed_hosts=("graph.microsoft.com",),
            authority=ObservationAuthority(),
            http=http,
        )


def test_purview_unrecovered_ambiguity_is_not_reported_as_success():
    authority = PurviewAuthority()
    authority.recover_purview_ack = lambda *_args: None
    transport = FixedAuthorityPurviewWritebackTransport(
        tenant_sha256=TENANT,
        provider="purview",
        fixed_hosts=("account.purview.azure.com",),
        authority=authority,
        http=HTTP([RuntimeError("first"), RuntimeError("second")]),
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(
        IdentityGovernanceDeliveryError,
        match="ambiguous_unrecovered",
    ):
        transport.apply(_purview_request())
