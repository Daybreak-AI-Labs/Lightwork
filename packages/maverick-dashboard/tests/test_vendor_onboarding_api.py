"""Real dashboard routing through the offline vendor application service."""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from maverick import config, world_model
from maverick.governed_operations import ServiceContext
from maverick.vendor_onboarding import VendorOnboardingService
from maverick.vendor_onboarding_adapters import (
    EvidenceDocument,
    FakeGoogleDriveEvidence,
    FakeJiraTickets,
)
from maverick_dashboard.app import app
from maverick_dashboard.vendor_onboarding_composition import WorkflowBinding

BASE = "/api/v1/vendor-onboarding/cases"
NOW = 1_800_000_000.0


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_TENANT", "synthetic-a")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    (tmp_path / "config.toml").write_text(
        '[vendor_onboarding]\nenable=true\nresource_owner="procurement"\n'
        '[vendor_management]\nenable=true\n[ai_vendor_risk]\nenable=true\n'
        '[model_risk_assurance]\nenable=true\n', encoding="utf-8",
    )
    config.reset_config_cache()
    ctx = ServiceContext("synthetic-a", "user:reviewer", "procurement",
                         "user:reviewer", "jira", human=True)
    docs = FakeGoogleDriveEvidence({(ctx.tenant, "security-paper"): EvidenceDocument(
        "security-paper", "v1", NOW - 100, b"We do not train on customer data.",
    )})
    tickets = FakeJiraTickets()
    service = VendorOnboardingService(docs, tickets, lambda c, cap, res: c.binding() == ctx.binding(),
                                      clock=lambda: NOW)
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory",
                        lambda request: WorkflowBinding(service, ctx))
    client = TestClient(app, headers={"Origin": "http://testserver"})
    yield client, service, ctx, tickets
    config.reset_config_cache()


def _intake(client):
    result = client.post(BASE, json={"vendor_name": "Synthetic Support AI",
        "document_id": "security-paper", "project": "VEND", "purpose": "Draft replies"})
    assert result.status_code == 201, result.text
    return result.json()


def _step(client, row, step, **extra):
    result = client.post(f"{BASE}/{row['id']}/{step}",
                         json={"expected_revision": row["revision"], **extra})
    assert result.status_code == 200, result.text
    return result.json()


def test_http_workflow_review_correction_external_confirmation_and_acceptance(web):
    client, service, ctx, tickets = web
    row = _intake(client)
    row = _step(client, row, "collect-evidence")
    row = _step(client, row, "assess")
    qid = next(iter(row["assessment"]["answers"]))
    row = _step(client, row, "review", decision="approved", rationale="Restricted pilot",
                corrections={qid: {"answer": "unknown", "note": "Request clearer evidence"}},
                review_seconds=4.5)
    row = _step(client, row, "prepare-action")
    row = _step(client, row, "approve-action", confirm=True)
    row = _step(client, row, "execute", confirm=True)
    assert row["operation"]["external_confirmation"]["verified"] is True
    assert row["outcome"] is None
    row = _step(client, row, "accept", accepted=True, rationale="Accepted for the pilot")
    assert row["outcome"]["accepted"] is True
    assert row["outcome"]["released"] is False
    assert tickets.creates == 1
    assert service.get(ctx, row["id"])["metrics"]["human_review_seconds"] == 4.5
    assert len(client.get(BASE).json()["cases"]) == 1
    check = client.post(f"{BASE}/{row['id']}/check-renewal",
                        json={"expected_revision": row["revision"]})
    assert check.status_code == 200, check.text
    assert check.json()["classification"] == "human_fallback"
    assert tickets.creates == 1  # Readiness never triggers another external action.
    assert client.post(f"{BASE}/{row['id']}/check-renewal", json={
        "expected_revision": row["revision"], "now": 0, "customer_accepted": True,
    }).status_code == 422
    assert client.post(f"{BASE}/{row['id']}/check-renewal", json={
        "expected_revision": row["revision"] - 1,
    }).status_code == 409


def test_http_rejects_forged_authority_unknown_steps_and_stale_versions(web):
    client, *_ = web
    row = _intake(client)
    rejected = client.post(BASE, json={"vendor_name": "Fake", "document_id": "doc",
        "project": "VEND", "purpose": "test", "human": True, "tenant": "other"})
    assert rejected.status_code == 422
    assert client.post(f"{BASE}/{row['id']}/delete-everything", json={"expected_revision": 1}).status_code == 422
    _step(client, row, "collect-evidence")
    assert client.post(f"{BASE}/{row['id']}/assess", json={"expected_revision": row["revision"]}).status_code == 409


def test_http_requires_separate_explicit_approval_and_blocks_cross_origin(web):
    client, _, _, tickets = web
    row = _intake(client)
    response = client.post(f"{BASE}/{row['id']}/execute",
                           json={"expected_revision": row["revision"], "confirm": True})
    assert response.status_code == 409
    assert client.post(f"{BASE}/{row['id']}/approve-action",
                       json={"expected_revision": row["revision"], "confirm": False}).status_code == 422
    assert client.post(BASE, headers={"Origin": "https://attacker.invalid"}, json={}).status_code == 403
    assert tickets.creates == 0


def test_stock_composition_rejects_anonymous_or_static_identity(web, monkeypatch):
    from maverick_dashboard.vendor_onboarding_composition import bind_request

    client, *_ = web
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory", bind_request)
    response = client.get(BASE)
    assert response.status_code == 403
    assert "named authenticated" in response.text


def test_model_context_cannot_review(web, monkeypatch):
    client, service, ctx, _ = web
    row = _step(client, _step(client, _intake(client), "collect-evidence"), "assess")
    machine = replace(ctx, human=False)
    service.authorize = lambda c, cap, res: c.actor == ctx.actor
    monkeypatch.setattr(app.state, "vendor_onboarding_binding_factory",
                        lambda request: WorkflowBinding(service, machine))
    response = client.post(f"{BASE}/{row['id']}/review", json={
        "expected_revision": row["revision"], "decision": "approved", "rationale": "Model says so"})
    assert response.status_code == 403


def test_existing_dashboard_page_and_asset_are_usable(web):
    client, *_ = web
    response = client.get("/third-party/vendor-onboarding")
    assert response.status_code == 200, response.text
    assert "Human review" in response.text
    assert "Approve this exact Jira task" in response.text
    assert "no-store" in response.headers["cache-control"]
    asset = client.get("/static/lightwork-vendor-onboarding.js")
    assert asset.status_code == 200
    assert "textContent" in asset.text
    assert "innerHTML" not in asset.text
