"""HTTP contract for the governed Model Risk assurance surface."""
from __future__ import annotations

import hashlib
import time

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _isolated_model_risk(tmp_path, monkeypatch):
    from maverick import audit, config
    from maverick.audit import writer as audit_writer

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """[evidence_graph]
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
    yield
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()


def _ok(response, expected=200):
    assert response.status_code == expected, response.text
    return response.json()


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




def test_model_risk_assurance_page_is_available():
    response = client.get("/security/assurance")
    assert response.status_code == 200
    assert "Model Risk &amp; AI Assurance Officer" in response.text
