"""HTTP contracts for evidence, agent trust, model risk, and CRA operations."""
from __future__ import annotations

import hashlib
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated_assurance(tmp_path, monkeypatch):
    from maverick import audit, config
    from maverick.audit import writer as audit_writer

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[security_ops]
enable = true

[evidence_graph]
enable = true

[evidence_factory]
enable = true

[agent_trust_auditor]
enable = true
stale_after_seconds = 604800

[cra_ops]
enable = true

[model_risk_assurance]
enable = true
gate_promotions = true
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    for name in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_TENANT",
        "MAVERICK_TENANT_BY_USER",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(audit, "record", lambda *_args, **_kwargs: True)
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    yield config_path
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()


def _ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


def _create_product_release(*, with_sbom: bool = True):
    product = _ok(client.post(
        "/api/v1/security/assurance/cra/products",
        json={
            "name": "Lightwork",
            "description": "Governed enterprise agent platform.",
            "manufacturer": "Day AI Labs",
            "product_kind": "software",
            "support_until": time.time() + 365 * 86400,
        },
    ))
    product = _ok(client.post(
        f"/api/v1/security/assurance/cra/products/{product['id']}/applicability",
        json={
            "decision": "applicable",
            "rationale": "Human counsel recorded scope and evidence.",
            "expected_revision": product["revision"],
        },
    ))
    release = _ok(client.post(
        "/api/v1/security/assurance/cra/releases",
        json={
            "product_id": product["id"],
            "version": "1.2.3",
            "artifact_sha256": "a" * 64,
            "released_at": time.time(),
        },
    ))
    if with_sbom:
        release = _ok(client.post(
            f"/api/v1/security/assurance/cra/releases/{release['id']}/sbom",
            json={
                "document": {
                    "bomFormat": "CycloneDX",
                    "specVersion": "1.6",
                    "components": [],
                    "dependencies": [],
                },
                "source": "release-pipeline",
                "expected_revision": release["revision"],
            },
        ))
    return product, release


def test_assurance_summary_is_viewer_safe_and_reports_enabled_products():
    summary = _ok(client.get("/api/v1/security/assurance/summary"))
    assert summary["enabled"] == {
        "evidence_graph": True,
        "evidence_factory": True,
        "agent_trust_auditor": True,
        "cra_ops": True,
        "model_risk_assurance": True,
    }
    assert summary["counts"]["cra_products"] == 0
    page = client.get("/security/assurance")
    assert page.status_code == 200
    assert "Agent Trust Auditor" in page.text
    assert "Model Risk &amp; AI Assurance Officer" in page.text
    assert "CRA Product Security Officer" in page.text


def test_model_risk_http_flow_keeps_classification_and_review_human_governed():
    now = time.time()
    artifact_digest = hashlib.sha256(b"api-model-v1").hexdigest()
    observation = _ok(client.post(
        "/api/v1/security/assurance/model-risk/observations",
        json={
            "asset_type": "model",
            "source": "api_registry",
            "source_id": "customer-risk-model",
            "display_name": "Customer risk model",
            "version_digest": artifact_digest,
            "observed_at": now,
            "metadata": {"environment": "test"},
            "dependencies": [],
        },
    ))
    asset_id = observation["asset_id"]
    inventory = _ok(client.get(
        "/api/v1/security/assurance/model-risk/inventory"
    ))
    assert inventory["assets"][0]["asset_id"] == asset_id

    declaration = _ok(client.post(
        "/api/v1/security/assurance/model-risk/declarations",
        json={
            "asset_id": asset_id,
            "owner": "AI Risk Committee",
            "purpose": "Decision support for trained reviewers.",
            "intended_use": "Advisory scoring with human review.",
            "risk_tier": "medium",
            "risk_context": {"human_oversight": True},
            "eu_ai_act": {"category": "undetermined"},
        },
    ))
    refused = client.post(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}/review",
        json={
            "decision": "approved",
            "rationale": "This request has no human applicability assertion.",
            "expected_revision": declaration["revision"],
        },
    )
    assert refused.status_code == 422

    declaration = _ok(client.patch(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}",
        json={
            "expected_revision": declaration["revision"],
            "eu_ai_act": {
                "category": "not_applicable",
                "asserted_at": now,
                "as_of": "2026-07-21",
                "source_ref": "https://eur-lex.europa.eu/eli/reg/2024/1689/oj",
                "rationale": "A qualified human reviewed this test context.",
            },
        },
    ))
    declaration = _ok(client.post(
        f"/api/v1/security/assurance/model-risk/declarations/{asset_id}/review",
        json={
            "decision": "approved",
            "rationale": "Owner, use, risk context, and dated assertion reviewed.",
            "expected_revision": declaration["revision"],
        },
    ))
    assert declaration["status"] == "approved"
    assert declaration["legal_certification"] is False

    evidence = _ok(client.post(
        "/api/v1/security/assurance/model-risk/evidence",
        json={
            "asset_id": asset_id,
            "source_id": "evaluation-api-1",
            "evidence_kind": "evaluation",
            "result": "passed",
            "scope_digest": hashlib.sha256(b"evaluation-scope").hexdigest(),
            "artifact_digest": artifact_digest,
            "summary": "Bounded evaluation receipt for the exact artifact.",
            "metrics": {"cases": 42, "pass_rate": 1.0},
            "observed_at": now,
            "valid_until": now + 3600,
        },
    ))
    approved = _ok(client.post(
        f"/api/v1/security/assurance/model-risk/evidence/{evidence['id']}/review",
        json={
            "decision": "approved",
            "rationale": "Scope, artifact binding, and result were reviewed.",
            "expected_revision": evidence["revision"],
        },
    ))
    assert approved["status"] == "approved"
    stale = client.post(
        f"/api/v1/security/assurance/model-risk/evidence/{evidence['id']}/review",
        json={
            "decision": "revoked",
            "rationale": "Stale browser state.",
            "expected_revision": evidence["revision"],
        },
    )
    assert stale.status_code == 409


def test_evidence_review_endpoint_enforces_cas():
    from maverick import evidence_graph

    node = evidence_graph.ingest(
        source="api-test",
        source_id="node-1",
        evidence_type="control_test",
        title="Tenant isolation",
        summary="Cross-tenant reads were refused.",
        controls=["TEN-01"],
        attributes={"result": "passed"},
        actor="collector",
    )
    approved = _ok(client.post(
        f"/api/v1/security/assurance/evidence/{node['id']}/decision",
        json={
            "decision": "approved",
            "rationale": "Reviewed the bound test receipt.",
            "expected_revision": node["revision"],
        },
    ))
    assert approved["status"] == "approved"
    stale = client.post(
        f"/api/v1/security/assurance/evidence/{node['id']}/decision",
        json={
            "decision": "rejected",
            "rationale": "Stale browser state.",
            "expected_revision": node["revision"],
        },
    )
    assert stale.status_code == 409


def test_evidence_campaign_abort_endpoint_is_admin_cas_governed():
    campaign = _ok(client.post(
        "/api/v1/security/assurance/evidence-factory/campaigns",
        json={
            "title": "Abort contract",
            "commit": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "deployment_id": "test-abort",
            "slo": {"availability_percent": 99.9},
            "rto_seconds": 300,
            "rpo_seconds": 60,
            "controls": ["RES-01"],
        },
    ))
    aborted = _ok(client.post(
        f"/api/v1/security/assurance/evidence-factory/campaigns/{campaign['id']}"
        "/abort",
        json={
            "expected_revision": campaign["revision"],
            "reason": "Operator cancelled this unused campaign.",
        },
    ))
    assert aborted["status"] == "aborted"
    assert aborted["abort"]["reason"] == "Operator cancelled this unused campaign."

    stale = client.post(
        f"/api/v1/security/assurance/evidence-factory/campaigns/{campaign['id']}"
        "/abort",
        json={
            "expected_revision": campaign["revision"],
            "reason": "Stale browser state.",
        },
    )
    assert stale.status_code == 409


def test_agent_trust_declaration_is_admin_governed_and_revision_checked():
    entity_id = "ATE-" + "1" * 32
    declaration = _ok(client.post(
        "/api/v1/security/assurance/agent-trust/declarations",
        json={
            "entity_id": entity_id,
            "owner": "platform-security",
            "purpose": "Read-only compliance collection.",
            "risk_tier": "high",
            "lifecycle": "proposed",
            "allowed_tools": ["evidence_read"],
        },
    ))
    updated = _ok(client.patch(
        f"/api/v1/security/assurance/agent-trust/declarations/{entity_id}",
        json={
            "lifecycle": "approved",
            "expected_revision": declaration["revision"],
        },
    ))
    assert updated["lifecycle"] == "approved"
    assert updated["revision"] == declaration["revision"] + 1


def test_cra_http_flow_keeps_legal_decision_and_submission_human_gated(
    _isolated_assurance,
):
    from maverick import config, cra_ops

    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    public_key = public_bytes.hex()
    key_id = hashlib.sha256(public_bytes).hexdigest()[:16]
    configured = _isolated_assurance.read_text(encoding="utf-8").replace(
        "[cra_ops]\nenable = true",
        "[cra_ops]\n"
        "enable = true\n"
        f'filing_receipt_trusted_public_keys = ["{public_key}"]',
    )
    _isolated_assurance.write_text(configured, encoding="utf-8")
    config.reset_config_cache()

    class _Signer:
        def __init__(self):
            self.key_id = key_id

        @staticmethod
        def sign(message: bytes) -> str:
            return private_key.sign(message).hex()

    _, release = _create_product_release()
    case = _ok(client.post(
        "/api/v1/security/assurance/cra/cases",
        json={
            "release_id": release["id"],
            "case_type": "severe_incident",
            "title": "Potential severe incident",
            "summary": "Product integrity impact is under review.",
            "awareness_at": time.time(),
            "external_id": "INC-API-1",
        },
    ))
    assert case["reportability"]["status"] == "undetermined"
    case = _ok(client.post(
        f"/api/v1/security/assurance/cra/cases/{case['id']}/reportability",
        json={
            "decision": "reportable",
            "rationale": "Human PSIRT and counsel confirmed Article 14 scope.",
            "expected_revision": case["revision"],
        },
    ))
    report = _ok(client.post(
        "/api/v1/security/assurance/cra/reports",
        json={
            "case_id": case["id"],
            "stage": "early_warning",
            "content": {
                "summary": "Early warning content reviewed for sensitivity.",
                "suspected_malicious": True,
            },
            "evidence_node_ids": [],
        },
    ))
    refused = client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}/submission-receipt",
        json={
            "submitted_at": time.time(),
            "channel": "CRA Single Reporting Platform",
            "receipt_id": "SRP-refused",
            "expected_revision": report["revision"],
        },
    )
    assert refused.status_code == 422

    missing_packet_digest = client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}/approve",
        json={
            "rationale": "The approval request omitted the reviewed packet digest.",
            "expected_revision": report["revision"],
        },
    )
    assert missing_packet_digest.status_code == 422
    wrong_packet_digest = client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}/approve",
        json={
            "packet_sha256": "0" * 64,
            "rationale": "This is not the packet shown to the reviewer.",
            "expected_revision": report["revision"],
        },
    )
    assert wrong_packet_digest.status_code == 422

    approved = _ok(client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}/approve",
        json={
            "packet_sha256": report["packet_sha256"],
            "rationale": "An authorised human reviewed the exact packet.",
            "expected_revision": report["revision"],
        },
    ))
    attested = _ok(client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}/submission-receipt",
        json={
            "submitted_at": time.time(),
            "channel": "CRA Single Reporting Platform",
            "receipt_id": "SRP-accepted",
            "expected_revision": approved["revision"],
        },
    ))
    assert attested["status"] == "approved"
    assert attested["submission"] is None
    assert (
        attested["local_submission_attestation"]["verification_status"]
        == "unverified_local_attestation"
    )
    envelope = cra_ops.build_filing_receipt_envelope(
        report_id=report["id"],
        packet_sha256=report["packet_sha256"],
        stage=report["stage"],
        case_id=report["case_id"],
        submitted_at=time.time(),
        channel="CRA Single Reporting Platform",
        receipt_id="SRP-verified",
        signer=_Signer(),
    )
    submitted = _ok(client.post(
        f"/api/v1/security/assurance/cra/reports/{report['id']}"
        "/verified-filing-receipt",
        json={
            "envelope": envelope,
            "expected_revision": attested["revision"],
        },
    ))
    assert submitted["status"] == "submitted"
    assert (
        submitted["submission"]["verification_status"]
        == "verified_external_receipt"
    )


def test_cra_sbom_endpoint_binds_normalised_document_to_release():
    _, release = _create_product_release(with_sbom=False)
    attached = _ok(client.post(
        f"/api/v1/security/assurance/cra/releases/{release['id']}/sbom",
        json={
            "document": {
                "bomFormat": "CycloneDX",
                "specVersion": "1.6",
                "components": [
                    {
                        "bom-ref": "pkg:pypi/httpx@0.27.0",
                        "type": "library",
                        "name": "httpx",
                        "version": "0.27.0",
                    }
                ],
                "dependencies": [],
            },
            "source": "release-pipeline",
            "expected_revision": release["revision"],
        },
    ))
    assert attached["sbom"]["component_count"] == 1
    assert attached["release_binding_sha256"] != release["release_binding_sha256"]


def test_assurance_http_body_cap_rejects_declared_oversize_before_parsing():
    response = client.post(
        "/api/v1/security/assurance/cra/products",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(1024 * 1024 + 1),
        },
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "assurance request body too large"}


def test_assurance_schemas_bound_nested_and_per_item_values():
    long_tool = client.post(
        "/api/v1/security/assurance/agent-trust/declarations",
        json={
            "entity_id": "ATE-" + "2" * 32,
            "allowed_tools": ["t" * 257],
        },
    )
    assert long_tool.status_code == 422

    nested_slo = client.post(
        "/api/v1/security/assurance/evidence-factory/campaigns",
        json={
            "title": "Bounded campaign",
            "commit": "a" * 40,
            "image_digest": "sha256:" + "b" * 64,
            "deployment_id": "test",
            "slo": {"availability": {"target": 99.9}},
            "rto_seconds": 60,
            "rpo_seconds": 60,
        },
    )
    assert nested_slo.status_code == 422

    oversized_report_string = client.post(
        "/api/v1/security/assurance/cra/reports",
        json={
            "case_id": "CASE-does-not-matter",
            "stage": "early_warning",
            "content": {"summary": "x" * 16_001},
        },
    )
    assert oversized_report_string.status_code == 422


def test_assurance_sbom_schema_rejects_excessive_nesting_before_store_access():
    document: dict[str, object] = {"leaf": True}
    for _ in range(65):
        document = {"nested": document}
    response = client.post(
        "/api/v1/security/assurance/cra/releases/REL-does-not-matter/sbom",
        json={
            "document": document,
            "source": "schema-test",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 422
