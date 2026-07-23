"""The Finance workspace: the department chassis (worklist, stats, memory,
catalog, shared review pop-out) over the finance assessment types — and the
department isolation between /finance and /privacy."""
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
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    config.reset_config_cache()
    import maverick_dashboard.api as api
    api._world_cache.clear()
    yield
    config.reset_config_cache()
    api._world_cache.clear()


def _seed(type="sox_control", subject="Q3 revenue controls"):
    from maverick.assessment import TEMPLATES, AssessmentSession, save_session
    s = AssessmentSession(type=type, subject=subject)
    s.record(TEMPLATES[type].questions[0].id, "yes")
    save_session(s)
    return s


def test_page_renders_finance_worklist_and_catalog():
    _seed("sox_control", "Q3 revenue controls")
    resp = client.get("/finance")
    assert resp.status_code == 200
    html = resp.text
    assert "Q3 revenue controls" in html
    assert 'class="pw-review-btn"' in html
    # The shared pop-out rides the chassis.
    assert 'id="arv-dialog"' in html
    # The catalog lists the FINANCE frameworks — and only those.
    for label in ("SOX Control Assessment", "Fraud Risk Assessment",
                  "IT General Controls Assessment"):
        assert label in html, label
    assert "Privacy Impact Assessment" not in html
    # No privacy-records UI on the finance chassis.
    assert "Privacy records" not in html and "open DSARs" not in html


def test_department_isolation():
    _seed("sox_control", "Q3 revenue controls")
    from maverick.assessment import AssessmentSession, save_session
    p = AssessmentSession(type="pia", subject="Acme CRM")
    p.record("pia_transfers", "yes")
    save_session(p)
    finance_html = client.get("/finance").text
    privacy_html = client.get("/privacy").text
    # Each worklist carries its own department's records only.
    assert "Q3 revenue controls" in finance_html
    assert "Acme CRM" not in finance_html
    assert "Acme CRM" in privacy_html
    assert "Q3 revenue controls" not in privacy_html


def test_page_empty_state():
    resp = client.get("/finance")
    assert resp.status_code == 200
    assert "No assessments yet" in resp.text


def _as(monkeypatch, principal):
    from maverick_dashboard import api, auth

    monkeypatch.setattr(auth, "caller_principal", lambda request: principal)
    monkeypatch.setattr(api, "caller_principal", lambda request: principal)


def test_finance_page_requires_finance_suite(monkeypatch):
    from maverick_dashboard import suite_grants

    suite_grants.set_suites("user:legal", ["legal"])
    _as(monkeypatch, "user:legal")

    resp = client.get("/finance")

    assert resp.status_code == 403
    assert "department" in resp.json()["detail"]


def test_assessment_session_api_scopes_records_by_suite(monkeypatch):
    finance = _seed("sox_control", "Q3 revenue controls")
    privacy = _seed("pia", "Acme CRM")
    from maverick_dashboard import suite_grants

    suite_grants.set_suites("user:legal", ["legal"])
    _as(monkeypatch, "user:legal")

    listing = client.get("/api/v1/assess/sessions")
    assert listing.status_code == 200
    assert listing.json()["sessions"] == []
    assert client.get(f"/api/v1/assess/sessions/{finance.id}").status_code == 403
    assert client.post(
        f"/api/v1/assess/sessions/{finance.id}/followups",
        json={"questions": ["Who approved this?"]},
    ).status_code == 403
    assert client.post(
        f"/api/v1/assess/sessions/{finance.id}/decide",
        json={"decision": "approved"},
    ).status_code == 403

    suite_grants.set_suites("user:fin", ["finance"])
    _as(monkeypatch, "user:fin")

    listing = client.get("/api/v1/assess/sessions")
    assert listing.status_code == 200
    assert [s["id"] for s in listing.json()["sessions"]] == [finance.id]
    assert client.get(f"/api/v1/assess/sessions/{finance.id}").status_code == 200
    assert client.get(f"/api/v1/assess/sessions/{privacy.id}").status_code == 403
