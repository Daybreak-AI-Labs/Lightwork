"""The Privacy workspace: the module view over assessment records — worklist
with the risk pair and aging, stats, precedents, catalog, and the shared
review pop-out on the page."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


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


def _seed(subject="Acme CRM", risky=True, followups=False):
    from maverick.assessment import AssessmentSession, add_followups, save_session
    s = AssessmentSession(type="pia", subject=subject)
    s.record("pia_transfers", "yes" if risky else "no")
    s.record("pia_security", "yes")
    save_session(s)
    if followups:
        add_followups(s.id, ["Which sub-processors?"])
    return s


def test_page_renders_worklist_stats_and_catalog():
    _seed("Acme CRM", risky=True)
    _seed("Globex HRIS", risky=False, followups=True)
    resp = client.get("/privacy")
    assert resp.status_code == 200
    html = resp.text
    # Worklist rows with the risk pair and review triggers.
    assert "Acme CRM" in html and "Globex HRIS" in html
    assert 'class="pw-review-btn"' in html
    # The shared pop-out is on the page.
    assert 'id="arv-dialog"' in html
    # The catalog lists the PRIVACY frameworks — and only those (the finance
    # templates belong to a future Finance workspace, not this one).
    for label in ("Privacy Impact Assessment", "AI Risk Assessment",
                  "Vendor Risk Assessment"):
        assert label in html, label
    assert "SOX Control Assessment" not in html
    assert "Fraud Risk Assessment" not in html
    # Follow-up state surfaces as awaiting answers.
    assert "awaiting answers" in html


def test_page_empty_state():
    resp = client.get("/privacy")
    assert resp.status_code == 200
    assert "No assessments yet" in resp.text


def test_templates_catalog_api():
    resp = client.get("/api/v1/assess/templates")
    assert resp.status_code == 200
    types = {t["type"] for t in resp.json()["templates"]}
    assert {"pia", "aira", "vendor_risk", "hipaa", "soc2", "pci_dss"} <= types
    assert all(t["questions"] > 0 for t in resp.json()["templates"])


def test_approvals_page_still_carries_shared_popout():
    resp = client.get("/approvals")
    assert resp.status_code == 200
    assert resp.text.count('id="arv-dialog"') == 1
    assert "mvAssessmentReview" in resp.text


def test_records_section_renders_all_four_types():
    from maverick import privacy_ops
    privacy_ops.review_dpa(
        "Acme Corp", "processor acts on documented instructions from the "
        "controller; audit rights; retention for 12 months",
        document_name="acme-dpa.pdf")
    privacy_ops.register_ai_system(
        "CV screener", "ranks job candidates for hiring")
    s = _seed("Acme CRM")
    privacy_ops.draft_ropa_from_assessment(s.id)
    privacy_ops.open_dsar("jordan@example.com", "erasure", channel="email")

    html = client.get("/privacy").text
    # DPA reviews tab: vendor row + the clause-detail trigger.
    assert "Acme Corp" in html and "acme-dpa.pdf" in html
    assert 'class="btn pw-dpa-clauses"' in html
    # AI registry tab: high-risk tier from the Annex III employment test.
    assert "CV screener" in html
    assert ">high</span>" in html
    # RoPA tab: the drafted Art. 30 entry with provenance + CSV export.
    assert "Processing via Acme CRM" in html
    assert "/api/v1/privacy/ropa/export.csv" in html
    # DSAR tab: subject + 30-day clock; erasure needs the operator step.
    assert "jordan@example.com" in html
    assert "d left" in html
    # Hero stats grew the records pair.
    assert "open DSARs" in html and "Art. 30 entries" in html


def test_records_section_hidden_when_disabled(monkeypatch, tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[privacy_ops]\nenable = false\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_CONFIG", str(cfg))
    from maverick import config
    config.reset_config_cache()
    html = client.get("/privacy").text
    assert "Privacy records" not in html
    assert "open DSARs" not in html
    # The assessment workspace itself still stands.
    assert "Worklist" in html
