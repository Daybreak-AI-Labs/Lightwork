"""Contract, provenance, and failure-isolation tests for certified hunt connectors."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from maverick import env_hunt
from maverick.env_hunt.certification import (
    CONTRACT_TESTED,
    DECLARED,
    LIVE_CERTIFIED,
    ContractCheck,
    ContractTestResult,
    LiveProbeObservation,
    assess_connector,
    build_transport_binding,
    callable_implementation_digest,
    certification_manifest,
    certification_manifests,
    certification_policy,
    decide_configured_connector_use,
    decide_connector_use,
    host_allowed,
    normalize_rate_limit,
    run_contract_tests,
    run_live_probe,
    verify_live_receipt,
)
from maverick.env_hunt.connectors import (
    CloudTrailConnector,
    ConnectorRegistry,
    parse_github,
)
from maverick.env_hunt.models import QueryRequest


def _keypair():
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ).hex()
    key_id = __import__("hashlib").sha256(bytes.fromhex(public)).hexdigest()[:16]

    def signer(payload):
        return {
            "alg": "ed25519",
            "key_id": key_id,
            "pubkey": public,
            "sig": private.sign(payload).hex(),
        }

    return public, signer


def _cloudtrail_probe(_manifest):
    return LiveProbeObservation(
        endpoint_url="https://cloudtrail.us-east-1.amazonaws.com",
        api_version="2013-11-01",
        auth_mode="aws_workload_identity",
        granted_scopes=("cloudtrail:LookupEvents",),
        status_code=200,
        response_bytes=2048,
        elapsed_ms=40,
        pages=1,
        cursor_observed=False,
        rate_limit=normalize_rate_limit(
            200,
            {"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "99"},
        ),
    )


def _empty_cloudtrail_transport(_request):
    return ()


def _unrelated_cloudtrail_transport(_request):
    return ["not-the-certified-implementation"]


def _cloudtrail_binding(transport=_empty_cloudtrail_transport, *, tenant="customer-a"):
    return build_transport_binding(
        "cloudtrail",
        transport,
        endpoint_url="https://cloudtrail.us-east-1.amazonaws.com",
        api_version="2013-11-01",
        auth_mode="aws_workload_identity",
        granted_scopes=("cloudtrail:LookupEvents",),
        tenant=tenant,
    )


def test_eight_versioned_manifests_are_contract_tested_but_not_live_certified():
    manifests = certification_manifests()
    assert {manifest.connector for manifest in manifests} == {
        "cloudtrail",
        "guardduty",
        "entra",
        "sentinel",
        "okta",
        "splunk",
        "github",
        "elastic",
    }
    for manifest in manifests:
        assert manifest.manifest_version == 1
        assert manifest.lightwork_version
        assert manifest.auth.modes and manifest.auth.minimum_scopes
        assert manifest.transport.allowed_hosts
        assert manifest.transport.api_version
        assert manifest.transport.effect_classification == "read"
        assert manifest.transport.read_only is True
        assert manifest.transport.pagination and manifest.transport.cursor
        assert manifest.transport.rate_limit
        assert manifest.transport.deadline_seconds == 30
        assert manifest.transport.max_response_bytes == 8 * 1024 * 1024
        assert len(manifest.contract_tests) == 5
        result = run_contract_tests(manifest.connector)
        assert result.passed is True
        assert result.status == CONTRACT_TESTED
        assert assess_connector(manifest.connector).status == CONTRACT_TESTED

    failed = ContractTestResult(
        "cloudtrail",
        "0" * 64,
        (ContractCheck("cloudtrail.parser.v1", "0" * 64, False, "failed"),),
    )
    assert failed.status == DECLARED


def test_only_fresh_trusted_untampered_live_receipt_reaches_live_certified():
    public, signer = _keypair()
    legacy = run_live_probe(
        "cloudtrail",
        _cloudtrail_probe,
        signer=signer,
        now=1_000,
        ttl_seconds=3_600,
    )
    legacy_verification = verify_live_receipt(
        legacy,
        trusted_pubkey_hex=public,
        now=1_001,
    )
    assert legacy_verification.valid is False
    assert legacy_verification.status == CONTRACT_TESTED
    assert legacy_verification.reason == "receipt_transport_binding_required"

    binding = _cloudtrail_binding()
    receipt = run_live_probe(
        "cloudtrail",
        _cloudtrail_probe,
        signer=signer,
        now=1_000,
        ttl_seconds=3_600,
        transport_binding=binding,
    )
    assert verify_live_receipt(
        receipt,
        trusted_pubkey_hex=public,
        now=1_001,
    ).reason == "expected_transport_binding_required"
    verified = verify_live_receipt(
        receipt,
        trusted_pubkey_hex=public,
        now=1_001,
        expected_binding=binding,
    )
    assert verified.valid is True
    assert verified.status == LIVE_CERTIFIED
    assert verify_live_receipt(
        receipt,
        trusted_pubkey_hex=public,
        now=1_001,
        expected_binding=_cloudtrail_binding(tenant="customer-b"),
    ).reason == "receipt_transport_binding_mismatch"
    assert assess_connector(
        "cloudtrail",
        receipt=receipt,
        trusted_pubkey_hex=public,
        now=1_001,
        expected_binding=binding,
    ).status == LIVE_CERTIFIED

    tampered = json.loads(json.dumps(receipt))
    tampered["observation"]["response_bytes"] = 1
    tamper_check = verify_live_receipt(
        tampered,
        trusted_pubkey_hex=public,
        now=1_001,
        expected_binding=binding,
    )
    assert tamper_check.valid is False
    assert tamper_check.reason == "receipt_probe_id_invalid"
    stale = verify_live_receipt(
        receipt,
        trusted_pubkey_hex=public,
        now=4_601,
        expected_binding=binding,
    )
    assert stale.valid is False
    assert stale.reason == "receipt_stale"
    assert stale.status == CONTRACT_TESTED
    wrong_public, _wrong_signer = _keypair()
    assert verify_live_receipt(
        receipt,
        trusted_pubkey_hex=wrong_public,
        now=1_001,
        expected_binding=binding,
    ).reason == "receipt_signer_untrusted"
    assert verify_live_receipt(
        receipt,
        trusted_pubkey_hex=None,
        now=1_001,
        expected_binding=binding,
    ).reason == "trusted_pubkey_required"


def test_allowed_host_ports_and_read_only_contracts_fail_closed():
    cloudtrail = certification_manifest("cloudtrail")
    assert host_allowed(cloudtrail, "https://cloudtrail.us-west-2.amazonaws.com")
    assert not host_allowed(cloudtrail, "http://cloudtrail.us-west-2.amazonaws.com")
    assert not host_allowed(cloudtrail, "https://cloudtrail.us-west-2.amazonaws.com:444")
    assert not host_allowed(cloudtrail, "https://cloudtrail.us-west-2.amazonaws.com.evil.test")
    assert not host_allowed(cloudtrail, "https://user@cloudtrail.us-west-2.amazonaws.com")

    splunk = certification_manifest("splunk")
    endpoint = "https://splunk.customer.example:8089"
    assert not host_allowed(splunk, endpoint)
    assert host_allowed(
        splunk,
        endpoint,
        operator_allowed_hosts=("splunk.customer.example",),
    )
    assert all(
        manifest.transport.read_only is True
        and manifest.transport.effect_classification == "read"
        for manifest in certification_manifests()
    )


def test_live_probe_rejects_excess_scopes_and_binding_is_exact_and_secret_free():
    _public, signer = _keypair()

    def excessive_scope_probe(manifest):
        observation = _cloudtrail_probe(manifest)
        return {
            **observation.__dict__,
            "granted_scopes": (
                "cloudtrail:LookupEvents",
                "cloudtrail:DeleteTrail",
            ),
        }

    with pytest.raises(ValueError, match="exactly the manifest read scopes"):
        run_live_probe(
            "cloudtrail",
            excessive_scope_probe,
            signer=signer,
            now=1_000,
        )

    def transport_factory(secret):
        def transport(_request):
            if secret:
                return ()
            return ()

        return transport

    secret_a = "credential-a-must-not-appear"  # pragma: allowlist secret
    secret_b = "credential-b-must-not-appear"  # pragma: allowlist secret
    first = transport_factory(secret_a)
    second = transport_factory(secret_b)
    first_digest = callable_implementation_digest(first)
    assert first_digest == callable_implementation_digest(second)
    assert len(first_digest) == 64
    assert secret_a not in first_digest and secret_b not in first_digest
    assert first_digest != callable_implementation_digest(_unrelated_cloudtrail_transport)

    binding = _cloudtrail_binding(first)
    serialized = json.dumps(binding.to_dict(), sort_keys=True)
    assert secret_a not in serialized
    assert binding.granted_scopes == ("cloudtrail:LookupEvents",)


def test_429_metadata_is_bounded_allowlisted_and_drops_raw_headers():
    secret = "Bearer this-must-not-survive"  # pragma: allowlist secret
    metadata = normalize_rate_limit(
        429,
        {
            "Retry-After": "17",
            "X-RateLimit-Limit": "1000",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": "1704067200",
            "Authorization": secret,
            "Set-Cookie": secret,
        },
    )
    assert metadata == {
        "status_code": 429,
        "throttled": True,
        "retry_after_seconds": 17.0,
        "limit": 1000.0,
        "remaining": 0.0,
        "reset_at": 1704067200.0,
    }
    assert secret not in json.dumps(metadata)


def test_malformed_rows_are_individually_quarantined_without_raw_payload():
    secret = "Bearer row-secret-value"  # pragma: allowlist secret
    rows = [
        {"eventID": "good-1", "eventTime": 10, "eventName": "ConsoleLogin"},
        secret,
        {"eventID": "bad-2", "eventTime": "not-a-time", "password": "hidden"},  # pragma: allowlist secret
        {"eventID": "good-2", "eventTime": 20, "eventName": "CreateUser"},
        {"eventID": "outside-limit", "eventTime": 30, "eventName": "DeleteUser"},
    ]
    audit = []
    connector = CloudTrailConnector(lambda _request: iter(rows))
    registry = ConnectorRegistry(
        audit_recorder=lambda kind, payload: audit.append((kind, payload)) or True
    )
    registry.register(connector)
    batch = registry.ingest("cloudtrail", QueryRequest(0, 100, limit=4))

    assert [event.event_id for event in batch.events] == ["good-1", "good-2"]
    assert batch.received == 4
    assert batch.discarded == 2
    assert registry.last_quarantined_total == 2
    assert [item.row_number for item in registry.last_quarantine] == [2, 3]
    assert all(item.raw_persisted is False for item in registry.last_quarantine)
    serialized = json.dumps(audit, sort_keys=True)
    assert secret not in serialized
    assert "row-secret-value" not in serialized
    assert "hidden" not in serialized
    assert audit[0][1]["events_quarantined"] == 2
    assert audit[0][1]["raw_persisted"] is False

    bounded_audit = []
    bounded = ConnectorRegistry(
        audit_recorder=lambda kind, payload: bounded_audit.append((kind, payload)) or True
    )
    bounded.register(CloudTrailConnector(lambda _request: iter([secret] * 70)))
    bounded_batch = bounded.ingest("cloudtrail", QueryRequest(0, 100, limit=70))
    assert bounded_batch.received == bounded_batch.discarded == 70
    assert bounded.last_quarantined_total == 70
    assert len(bounded.last_quarantine) == 64
    assert bounded_audit[0][1]["quarantine_details_truncated"] is True
    assert secret not in json.dumps(bounded_audit)


def test_github_audit_and_security_parser_is_cleanly_available():
    audit_event = parse_github({
        "_document_id": "audit-1",
        "@timestamp": 1_704_067_200_000,
        "action": "repo.destroy",
        "actor": "octocat",
        "repo": "example/repository",
    })
    assert audit_event.source == "github.audit"
    assert audit_event.observed_at == 1_704_067_200
    security_event = parse_github({
        "id": "alert-1",
        "created_at": "2024-01-01T00:00:00Z",
        "rule": {"id": "js/sql-query-built-from-user-controlled-sources"},
        "repository": {"full_name": "example/repository"},
        "state": "open",
    })
    assert security_event.source == "github.security"
    assert security_event.category == "security_alert"


def test_live_probe_rejects_secret_bearing_or_raw_metadata_fields():
    _public, signer = _keypair()
    secret = "Bearer super-secret-token"  # pragma: allowlist secret

    def raw_probe(_manifest):
        result = dict(_cloudtrail_probe(_manifest).__dict__)
        result["headers"] = {"Authorization": secret}
        return result

    with pytest.raises(ValueError, match="forbidden metadata fields") as exc:
        run_live_probe("cloudtrail", raw_probe, signer=signer, now=1_000)
    assert secret not in str(exc.value)

    def query_secret_probe(_manifest):
        result = dict(_cloudtrail_probe(_manifest).__dict__)
        result["endpoint_url"] += "?access_token=super-secret-token"
        return result

    with pytest.raises(ValueError, match="query string") as exc:
        run_live_probe("cloudtrail", query_secret_probe, signer=signer, now=1_000)
    assert "super-secret-token" not in str(exc.value)


def test_enterprise_is_certified_only_unless_override_is_explicit_and_audited():
    public, signer = _keypair()
    binding = _cloudtrail_binding()
    receipt = run_live_probe(
        "cloudtrail",
        _cloudtrail_probe,
        signer=signer,
        now=1_000,
        ttl_seconds=3_600,
        transport_binding=binding,
    )
    live = decide_connector_use(
        "cloudtrail",
        enterprise=True,
        receipt=receipt,
        trusted_pubkey_hex=public,
        now=1_001,
        expected_binding=binding,
    )
    assert live.allowed is True
    assert live.certification_status == LIVE_CERTIFIED

    denied = decide_connector_use("cloudtrail", enterprise=True, now=1_001)
    assert denied.allowed is False
    assert denied.certification_status == CONTRACT_TESTED

    audit = []
    overridden = decide_connector_use(
        "cloudtrail",
        enterprise=True,
        allow_uncertified=True,
        actor="security-officer",
        reason="temporary migration window",
        audit_recorder=lambda kind, payload: audit.append((kind, payload)) or True,
        now=1_001,
    )
    assert overridden.allowed is True
    assert overridden.override_audited is True
    assert audit[0][0] == "env_hunt_connector_certification_override"
    assert "temporary migration window" not in json.dumps(audit)

    audit_rejected = decide_connector_use(
        "cloudtrail",
        enterprise=True,
        allow_uncertified=True,
        actor="security-officer",
        reason="temporary migration window",
        audit_recorder=lambda _kind, _payload: False,
        now=1_001,
    )
    assert audit_rejected.allowed is False
    assert audit_rejected.reason == "override_audit_rejected"
    assert decide_connector_use("cloudtrail", enterprise=False).allowed is True
    assert decide_connector_use("cloudtrail", enterprise=1).allowed is False


def test_configured_certification_policy_is_strict_and_enterprise_cannot_opt_out(
    monkeypatch,
):
    community = certification_policy(
        {"connectors": {"certification": {"certified_only": True}}},
        enterprise=False,
    )
    assert community.certified_only is True
    assert community.enterprise is False

    enterprise = certification_policy(
        {
            "connectors": {
                "certification": {
                    "certified_only": False,
                    "allow_uncertified": True,
                }
            }
        },
        enterprise=True,
    )
    assert enterprise.certified_only is True
    assert enterprise.allow_uncertified is True

    degraded = certification_policy(
        {"connectors": {"certification": "not-a-table"}},
        enterprise=True,
    )
    assert degraded.certified_only is True
    assert degraded.allow_uncertified is False
    assert degraded.config_degraded is True
    assert decide_configured_connector_use(
        "cloudtrail",
        config={"connectors": {"certification": "not-a-table"}},
        enterprise=True,
    ).reason == "certification_config_untrusted"

    monkeypatch.setattr("maverick.enterprise.enterprise_enabled", lambda: True)
    deployment_floor = certification_policy(
        {"connectors": {"certification": {"certified_only": False}}},
        enterprise=False,
    )
    assert deployment_floor.enterprise is True
    assert deployment_floor.certified_only is True


def test_factory_enforces_live_certification_before_exposing_connector_tools():
    config = {
        "connectors": {"certification": {"certified_only": True}},
        "env_hunt": {
            "enable": True,
            "connectors": {"cloudtrail": {"enable": True}},
        },
    }
    denied = env_hunt.ConnectorFactoryRegistry(config, enterprise=False)
    denied.register_transport("cloudtrail", lambda _request: ())
    assert denied.available_names() == ()
    assert denied.tools() == ()
    with pytest.raises(
        env_hunt.ConnectorCertificationRequired,
        match="enterprise_requires_live_receipt",
    ):
        denied.build_connector("cloudtrail")

    public, signer = _keypair()
    transport = _empty_cloudtrail_transport
    binding = _cloudtrail_binding(transport)
    receipt = run_live_probe(
        "cloudtrail",
        _cloudtrail_probe,
        signer=signer,
        now=1_000,
        ttl_seconds=3_600,
        transport_binding=binding,
    )
    registration_args = {
        "certification_receipts": {"cloudtrail": receipt},
        "trusted_certification_keys": {"cloudtrail": public},
        "certification_bindings": {"cloudtrail": binding},
        "certification_now": 1_001,
    }
    unrelated = env_hunt.ConnectorFactoryRegistry(
        config,
        enterprise=False,
        **registration_args,
    )
    with pytest.raises(ValueError, match="implementation does not match"):
        unrelated.register_transport("cloudtrail", _unrelated_cloudtrail_transport)

    live = env_hunt.ConnectorFactoryRegistry(
        config,
        enterprise=False,
        **registration_args,
    )
    live.register_transport("cloudtrail", transport)
    assert live.available_names() == ("cloudtrail",)
    assert [tool.name for tool in live.tools()] == ["env_hunt_query_cloudtrail"]
    assert live.certification_decision("cloudtrail").certification_status == LIVE_CERTIFIED


def test_factory_break_glass_override_is_explicit_audited_and_process_bounded():
    config = {
        "connectors": {
            "certification": {
                "certified_only": True,
                "allow_uncertified": True,
            }
        },
        "env_hunt": {
            "enable": True,
            "connectors": {"cloudtrail": {"enable": True}},
        },
    }
    audit = []
    factory = env_hunt.ConnectorFactoryRegistry(
        config,
        enterprise=False,
        audit_recorder=lambda kind, payload: audit.append((kind, payload)) or True,
        certification_actor="security-officer",
        certification_override_reason="temporary migration window",
        certification_now=1_001,
    )
    factory.register_transport("cloudtrail", lambda _request: ())
    assert factory.available_names() == ("cloudtrail",)
    assert factory.build_connector("cloudtrail").name == "cloudtrail"
    assert factory.available_names() == ("cloudtrail",)
    override_events = [event for event in audit if event[0].endswith("certification_override")]
    assert len(override_events) == 1
    assert "temporary migration window" not in json.dumps(override_events)


def test_enterprise_factory_forces_certified_only_even_if_config_says_false():
    factory = env_hunt.ConnectorFactoryRegistry(
        {
            "connectors": {"certification": {"certified_only": False}},
            "env_hunt": {
                "enable": True,
                "connectors": {"cloudtrail": {"enable": True}},
            },
        },
        enterprise=True,
    )
    factory.register_transport("cloudtrail", lambda _request: ())
    with pytest.raises(env_hunt.ConnectorCertificationRequired):
        factory.build_connector("cloudtrail")
