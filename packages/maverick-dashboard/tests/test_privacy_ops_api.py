"""Privacy ops over the dashboard API: DPA reviews, AI registry, RoPA
(+ Art. 30 CSV), and the DSAR tracker.

Mutating /api/v1 requests carry a same-origin Origin (the CSRF contract).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

DPA_TEXT = """The processor shall process personal data only on documented
instructions from the controller, is bound by confidentiality, implements the
technical and organisational measures of Article 32, uses no sub-processor
without prior written authorization, shall assist the controller with data
subject requests, shall notify the controller of any personal data breach,
shall delete or return all personal data upon termination, grants audit
rights, relies on Standard Contractual Clauses for transfers, and retains
data for 12 months (retention schedule)."""


@pytest.fixture(autouse=True)
def _fresh_world(tmp_path, monkeypatch):
    from maverick import config, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    config.reset_config_cache()
    import maverick_dashboard.api as api
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    api._world_cache.clear()


def test_dpa_review_round_trip():
    resp = client.post("/api/v1/privacy/dpa-reviews", json={
        "vendor": "Acme Corp", "document_name": "acme-dpa.pdf",
        "text": DPA_TEXT})
    assert resp.status_code == 201, resp.text
    rec = resp.json()
    assert rec["clauses_present"] >= 9
    assert rec["residual_risk"] in ("minimal", "low")
    listed = client.get("/api/v1/privacy/dpa-reviews").json()["reviews"]
    assert listed[0]["vendor"] == "Acme Corp"
    full = client.get(f"/api/v1/privacy/dpa-reviews/{rec['id']}").json()
    assert len(full["clauses"]) == rec["clauses_total"]
    assert client.get("/api/v1/privacy/dpa-reviews/nope").status_code == 404


def test_ai_registry_classifies_on_create():
    resp = client.post("/api/v1/privacy/ai-systems", json={
        "name": "CV screener", "purpose": "ranks job candidates for hiring"})
    assert resp.status_code == 201
    assert resp.json()["tier"] == "high"
    systems = client.get("/api/v1/privacy/ai-systems").json()["systems"]
    assert systems[0]["name"] == "CV screener"


def test_ropa_upsert_from_assessment_and_csv_export():
    from maverick.assessment import AssessmentSession, save_session
    s = AssessmentSession(type="pia", subject="Acme CRM")
    s.record("pia_transfers", "yes", "us-east-1, no SCCs")
    s.record("pia_retention", "no")
    save_session(s)

    resp = client.post(f"/api/v1/privacy/ropa/from-assessment/{s.id}")
    assert resp.status_code == 201, resp.text
    entry = resp.json()
    assert "Acme CRM" in entry["activity"]
    # Manual edit through the same upsert.
    resp = client.post("/api/v1/privacy/ropa", json={
        "ropa_id": entry["id"], "controller": "Company Ltd"})
    assert resp.status_code == 201
    assert resp.json()["controller"] == "Company Ltd"

    csv_resp = client.get("/api/v1/privacy/ropa/export.csv")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "Acme CRM" in csv_resp.text and "Company Ltd" in csv_resp.text

    assert client.post(
        "/api/v1/privacy/ropa/from-assessment/nope").status_code == 404


def test_dsar_lifecycle_over_real_export():
    resp = client.post("/api/v1/privacy/dsar", json={
        "subject_id": "jordan@example.com", "kind": "access",
        "channel": "email"})
    assert resp.status_code == 201
    rid = resp.json()["id"]
    rows = client.get("/api/v1/privacy/dsar").json()["requests"]
    assert rows[0]["days_left"] >= 28

    done = client.post(f"/api/v1/privacy/dsar/{rid}/fulfill")
    assert done.status_code == 200
    assert done.json()["status"] == "fulfilled"
    assert "counts" in done.json()["fulfillment"]
    # The list survives fulfillment: the export bundle written next to the
    # records must not be mistaken for a request (regression: KeyError).
    rows = client.get("/api/v1/privacy/dsar").json()["requests"]
    assert [r["status"] for r in rows if r["id"] == rid] == ["fulfilled"]
    # Fulfilling again 404s (no longer open); close is idempotent-friendly.
    assert client.post(f"/api/v1/privacy/dsar/{rid}/fulfill").status_code == 404
    assert client.post(f"/api/v1/privacy/dsar/{rid}/close").status_code == 200


def test_dsar_erasure_never_destructive_from_api():
    rid = client.post("/api/v1/privacy/dsar", json={
        "subject_id": "u1", "kind": "erasure", "channel": "slack"}).json()["id"]
    ready = client.post(f"/api/v1/privacy/dsar/{rid}/fulfill").json()
    assert ready["status"] == "awaiting_erasure"
    assert ready["fulfillment"]["erase_command"].startswith("maverick erase")


def test_disabled_by_config_knob(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[privacy_ops]\nenable = false\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    from maverick import config
    config.reset_config_cache()
    assert client.get("/api/v1/privacy/dsar").status_code == 403
    assert client.post("/api/v1/privacy/ai-systems", json={
        "name": "x", "purpose": "y"}).status_code == 403


def test_validation():
    assert client.post("/api/v1/privacy/dsar", json={
        "subject_id": "x", "kind": "espionage"}).status_code == 422
    assert client.post("/api/v1/privacy/dpa-reviews", json={
        "vendor": "", "text": "x"}).status_code == 422
