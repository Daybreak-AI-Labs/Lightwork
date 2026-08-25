"""The privacy-estate API surfaces: vendors, consent, and the data map.

Mutating /api/v1 requests carry a same-origin Origin (the CSRF contract).
Each surface is opt-in: the API answers 403 until its section is enabled.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

_CONFIG = (
    "[vendor_management]\nenable = true\n\n"
    "[consent_registry]\nenable = true\n"
    'subject_salt = "dashboard-test-salt-0123456789"\n\n'
    "[data_map]\nenable = true\n\n"
    "[privacy_ops]\nenable = true\n"
)


@pytest.fixture(autouse=True)
def _fresh_world(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    for name in (
        "MAVERICK_CONNECTIONS", "MAVERICK_ENTERPRISE",
        "MAVERICK_DASHBOARD_TOKEN", "MAVERICK_DASHBOARD_REQUIRE_AUTH",
        "MAVERICK_OIDC_ENABLED", "MAVERICK_PROXY_AUTH",
        "MAVERICK_GRAPH_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "config.toml").write_text(_CONFIG, encoding="utf-8")
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    import maverick_dashboard.api as api
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    audit_writer._default = None
    audit_writer._defaults.clear()
    api._world_cache.clear()


def test_every_surface_is_403_until_enabled(tmp_path):
    from maverick import config
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    assert client.get("/api/v1/vendors").status_code == 403
    assert client.get("/api/v1/consent/posture").status_code == 403
    assert client.get("/api/v1/data-map/assets").status_code == 403


def test_vendor_register_round_trip_and_conflict():
    resp = client.post("/api/v1/vendors", json={
        "name": "Acme Analytics", "category": "analytics",
        "criticality": "high", "owner": "procurement"})
    assert resp.status_code == 201, resp.text
    vendor = resp.json()
    assert vendor["id"].startswith("VMR-")

    dup = client.post("/api/v1/vendors", json={
        "name": "Acme Analytics", "category": "analytics",
        "criticality": "low", "owner": "procurement"})
    assert dup.status_code == 409

    single = client.get(f"/api/v1/vendors/{vendor['id']}")
    assert single.status_code == 200
    assert single.json()["criticality"] == "high"
    assert client.get("/api/v1/vendors/VMR-missing").status_code == 404

    listing = client.get("/api/v1/vendors")
    assert [v["name"] for v in listing.json()["vendors"]] == ["Acme Analytics"]

    posture = client.get("/api/v1/vendors/posture")
    assert posture.status_code == 200
    assert posture.json()["vendors"] == 1


def test_vendor_questionnaire_lifecycle_over_the_api():
    vendor = client.post("/api/v1/vendors", json={
        "name": "Globex", "category": "hosting",
        "criticality": "medium", "owner": "it"}).json()
    sent = client.post(
        f"/api/v1/vendors/{vendor['id']}/questionnaires",
        json={"assessment_type": "vendor_risk",
              "revision": vendor["revision"]})
    assert sent.status_code == 201, sent.text
    assert sent.json()["form_path"].startswith("/form/")

    bad = client.post(
        f"/api/v1/vendors/{vendor['id']}/questionnaires",
        json={"assessment_type": "not-a-template",
              "revision": sent.json()["revision"]})
    assert bad.status_code == 422

    back = client.get(f"/api/v1/vendors/{vendor['id']}/questionnaires")
    assert back.status_code == 200
    assert back.json()["questionnaires"][0]["status"] == "sent"


def test_vendor_status_note_and_onetrust_import():
    vendor = client.post("/api/v1/vendors", json={
        "name": "Initech", "category": "billing",
        "criticality": "low", "owner": "finance"}).json()
    moved = client.post(
        f"/api/v1/vendors/{vendor['id']}/status",
        json={"status": "under_review", "revision": vendor["revision"]})
    assert moved.status_code == 200, moved.text
    stale = client.post(
        f"/api/v1/vendors/{vendor['id']}/status",
        json={"status": "active", "revision": vendor["revision"]})
    assert stale.status_code == 409

    noted = client.post(
        f"/api/v1/vendors/{vendor['id']}/notes",
        json={"note": "sanctions screen hit", "severity": "critical",
              "revision": moved.json()["revision"]})
    assert noted.status_code == 201

    imported = client.post("/api/v1/vendors/import-onetrust", json={
        "csv_text": "Vendor Name,Vendor Type,Tier\nUmbrella,security,Tier 1\n"})
    assert imported.status_code == 201
    assert imported.json()["imported"] == 1


def test_consent_round_trip_and_digest_only_export():
    resp = client.post("/api/v1/consent/records", json={
        "subject_ref": "alice@example.com", "purpose": "marketing emails",
        "status": "granted", "policy_version": "2026-01", "channel": "web"})
    assert resp.status_code == 201, resp.text
    assert "alice" not in resp.text

    prefs = client.post("/api/v1/consent/preferences",
                        json={"subject_ref": "alice@example.com"})
    assert prefs.status_code == 200
    assert prefs.json()["purposes"]["marketing_emails"]["status"] == "granted"

    posture = client.get("/api/v1/consent/posture")
    assert posture.json()["records"] == 1

    export = client.get("/api/v1/consent/export")
    assert export.status_code == 200
    assert "alice" not in export.text
    assert export.json()["receipts"][0]["purpose"] == "marketing_emails"

    imported = client.post("/api/v1/consent/import-onetrust", json={
        "csv_text": ("Identifier,Purpose,Status,Version\n"
                     "bob@example.com,Newsletters,Opt-Out,2\n")})
    assert imported.status_code == 201
    assert imported.json() == {"imported": 1, "skipped": 0}


def test_data_map_register_classify_and_schema_import():
    resp = client.post("/api/v1/data-map/assets", json={
        "name": "crm.contacts", "system": "snowflake", "kind": "table",
        "owner": "data-team", "columns": ["id", "email", "diagnosis"]})
    assert resp.status_code == 201, resp.text
    asset = resp.json()
    assert asset["special_category"] is True
    assert "email" in asset["categories"]

    single = client.get(f"/api/v1/data-map/assets/{asset['id']}")
    assert single.status_code == 200
    assert client.get("/api/v1/data-map/assets/DMA-none").status_code == 404

    linked = client.post(
        f"/api/v1/data-map/assets/{asset['id']}/links",
        json={"kind": "ropa", "reference": "ROPA-1",
              "revision": asset["revision"]})
    assert linked.status_code == 201

    flagged = client.post("/api/v1/data-map/classify", json={
        "columns": ["order_total", "customer_email"]})
    assert flagged.status_code == 200
    assert set(flagged.json()["flagged"]) == {"customer_email"}

    imported = client.post("/api/v1/data-map/import-schema", json={
        "csv_text": ("table_name,column_name\n"
                     "payments,iban\npayments,amount\n"),
        "system": "postgres", "owner": "data-team"})
    assert imported.status_code == 201
    assert imported.json() == {"imported": 1, "skipped": 0}

    posture = client.get("/api/v1/data-map/posture")
    assert posture.json()["assets"] == 2
    assert posture.json()["by_category"]["financial"] == 1


def test_privacy_page_renders_the_estate_tabs():
    client.post("/api/v1/vendors", json={
        "name": "Acme Analytics", "category": "analytics",
        "criticality": "high", "owner": "procurement"})
    page = client.get("/privacy")
    assert page.status_code == 200
    assert "Privacy estate" in page.text
    assert "Acme Analytics" in page.text
    assert "Census SharePoint sites" in page.text
    assert "Record consent event" in page.text


def test_privacy_page_explains_what_is_off(tmp_path):
    from maverick import config
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    page = client.get("/privacy")
    assert page.status_code == 200
    assert "The vendor register is off" in page.text
    assert "The consent registry is off" in page.text
    assert "The data map is off" in page.text


def test_sharepoint_sync_without_a_token_is_503():
    resp = client.post("/api/v1/data-map/sync-sharepoint",
                       json={"owner": "it"})
    assert resp.status_code == 503
    assert "Sites.Read.All" in resp.json()["detail"]


def test_vendor_contracts_and_renewals_over_the_api():
    import time as time_module

    vendor = client.post("/api/v1/vendors", json={
        "name": "Hooli", "category": "cloud",
        "criticality": "high", "owner": "it"}).json()
    recorded = client.post(
        f"/api/v1/vendors/{vendor['id']}/contracts",
        json={"kind": "dpa", "reference": "doc://dpa",
              "renews_at": time_module.time() + 5 * 86400,
              "revision": vendor["revision"]})
    assert recorded.status_code == 201, recorded.text
    renewals = client.get("/api/v1/vendors/contracts/renewals")
    assert renewals.status_code == 200
    assert [entry["kind"] for entry in renewals.json()["due_soon"]] == ["dpa"]
    assert renewals.json()["overdue"] == []


def test_privacy_notices_and_site_scan_validation():
    published = client.post("/api/v1/consent/notices", json={
        "version": "2026-01", "text": "We process personal data to ..."})
    assert published.status_code == 201, published.text
    dup = client.post("/api/v1/consent/notices", json={
        "version": "2026-01", "text": "Other wording"})
    assert dup.status_code == 409

    listing = client.get("/api/v1/consent/notices")
    assert listing.json()["notices"][0]["version"] == "2026-01"
    single = client.get("/api/v1/consent/notices/2026-01")
    assert single.status_code == 200
    assert single.json()["text"].startswith("We process")
    assert client.get("/api/v1/consent/notices/nope").status_code == 404

    coverage = client.get("/api/v1/consent/notices/coverage")
    assert coverage.status_code == 200
    assert coverage.json()["covered"] is True

    scan = client.post("/api/v1/consent/site-scan",
                       json={"url": "http://insecure.example/"})
    assert scan.status_code == 422
    assert "https" in scan.json()["detail"]


def test_questionnaire_page_and_submission_without_automation_events():
    vendor = client.post("/api/v1/vendors", json={
        "name": "Pied Piper", "category": "compression",
        "criticality": "medium", "owner": "it"}).json()
    sent = client.post(
        f"/api/v1/vendors/{vendor['id']}/questionnaires",
        json={"assessment_type": "vendor_risk",
              "revision": vendor["revision"]}).json()
    form_path = sent["form_path"]

    # The vendor's link renders the questionnaire -- no account, no auth.
    page = client.get(form_path)
    assert page.status_code == 200
    assert "Pied Piper" in page.text
    assert "Submit answers" in page.text
    assert client.get("/form/not-a-real-token").status_code == 404

    # Submissions land even though automation events are disabled: the
    # questionnaire token itself authorizes the POST.
    posted = client.post(form_path, json={"vr_q1": "yes"})
    assert posted.status_code == 201, posted.text
    back = client.get(f"/api/v1/vendors/{vendor['id']}/questionnaires")
    assert back.json()["questionnaires"][0]["status"] == "answered"
    assert back.json()["questionnaires"][0]["score"] is not None

    # A non-questionnaire token stays gated on automation events.
    assert client.post("/form/other-token", json={"x": "1"}).status_code == 404


def test_dsar_estate_worksheet_over_the_api():
    opened = client.post("/api/v1/privacy/dsar", json={
        "subject_id": "alice@example.com", "kind": "access"})
    assert opened.status_code == 201, opened.text
    request_id = opened.json()["id"]

    client.post("/api/v1/data-map/assets", json={
        "name": "crm.contacts", "system": "snowflake", "kind": "table",
        "owner": "data-team", "columns": ["id", "email"]})

    plan = client.get(f"/api/v1/privacy/dsar/{request_id}/estate-worksheet")
    assert plan.status_code == 200, plan.text
    body = plan.json()
    assert body["platform_holdings"]["enabled"] is True
    assert [asset["name"] for asset in body["data_map"]["assets"]] == [
        "crm.contacts"]
    assert client.get(
        "/api/v1/privacy/dsar/DSAR-none/estate-worksheet"
    ).status_code == 404


def test_privacy_page_renders_contracts_notices_and_scan():
    import time as time_module

    vendor = client.post("/api/v1/vendors", json={
        "name": "Vandelay", "category": "import-export",
        "criticality": "low", "owner": "ops"}).json()
    client.post(
        f"/api/v1/vendors/{vendor['id']}/contracts",
        json={"kind": "dpa", "reference": "doc://dpa",
              "renews_at": time_module.time() + 3 * 86400,
              "revision": vendor["revision"]})
    client.post("/api/v1/consent/notices", json={
        "version": "2026-01", "text": "We process personal data to ..."})

    page = client.get("/privacy")
    assert page.status_code == 200
    assert "Contracts &amp; renewals" in page.text
    assert "renewing within 30 days" in page.text
    assert "Privacy notices" in page.text
    assert "2026-01" in page.text
    assert "Site scan" in page.text
    assert 'id="pe-scan-go"' in page.text
