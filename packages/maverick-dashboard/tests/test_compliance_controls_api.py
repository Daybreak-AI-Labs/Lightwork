"""The /compliance page's buttons: protection switches, retention, a person's
data (export / erase), auditor downloads, the AI Act self-check and the
control finder."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard import app as app_mod
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})

BANNED = re.compile(
    r"MAVERICK_|\[audit\]|\[retention\]|\[privacy\]|\[encryption\]|\[enterprise\]|"
    r"\[compliance\]|maverick (erase|export-user|retention)|~/\.maverick|"
    r"AES-256|export \.md|export \.csv|on-demand"
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    app_mod._world_cache.clear()
    yield
    app_mod._world_cache.clear()


def _as_viewer(monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:v", "viewer")
    monkeypatch.setattr(auth, "caller_principal", lambda request: "user:v")


def _seed_person():
    world = app_mod._world()
    conv = world.get_or_create_conversation(channel="slack", user_id="U123")
    world.append_turn(conv.id, "user", "hello there")
    return conv


def test_viewer_cannot_use_any_mutating_button(monkeypatch):
    _as_viewer(monkeypatch)
    assert client.post("/api/v1/compliance/switches/signed_audit").status_code == 403
    assert client.post("/api/v1/compliance/retention", json={"audit_days": 30}).status_code == 403
    assert client.post("/api/v1/compliance/retention/apply").status_code == 403
    assert client.get("/api/v1/compliance/people").status_code == 403
    body = {"channel": "slack", "user": "U123"}
    assert client.post("/api/v1/compliance/people/export", json=body).status_code == 403
    assert client.post("/api/v1/compliance/people/erase", json=body).status_code == 403
    assert client.get("/api/v1/compliance/downloads/soc2").status_code == 403


def test_switch_writes_only_a_strengthening_setting(monkeypatch):
    from maverick_dashboard import settings_store

    r = client.post("/api/v1/compliance/switches/signed_audit")
    assert r.status_code == 200, r.text
    assert r.json()["saved"] is True
    assert settings_store.load_overlay()["audit"]["sign"] is True
    assert client.post("/api/v1/compliance/switches/not_a_switch").status_code == 404
    with pytest.raises(ValueError):
        settings_store.set_compliance_settings({("audit", "sign"): False})
    with pytest.raises(ValueError):
        settings_store.set_compliance_settings({("security", "secure_defaults"): True})


def test_retention_periods_save_and_apply(monkeypatch):
    from maverick_dashboard import settings_store

    assert client.post("/api/v1/compliance/retention", json={"audit_days": 0}).status_code == 422
    assert client.post("/api/v1/compliance/retention", json={}).status_code == 422
    r = client.post("/api/v1/compliance/retention", json={"messages_days": 365})
    assert r.status_code == 200, r.text
    assert settings_store.load_overlay()["retention"]["messages_days"] == 365
    fields = client.get("/api/v1/compliance/retention").json()["fields"]
    assert {f["key"]: f["days"] for f in fields}["messages_days"] == 365
    import maverick.audit.retention as retention

    monkeypatch.setattr(retention, "enforce", lambda: {"messages": {"deleted": 4}})
    applied = client.post("/api/v1/compliance/retention/apply").json()
    assert applied["removed"] == 4 and "4 old record" in applied["message"]


def test_people_list_export_and_erase_are_workspace_scoped():
    _seed_person()
    people = client.get("/api/v1/compliance/people").json()["people"]
    assert [(p["channel"], p["user"], p["channel_label"]) for p in people] == [
        ("slack", "U123", "Slack")
    ]
    body = {"channel": "slack", "user": "U123"}
    export = client.post("/api/v1/compliance/people/export", json=body)
    assert export.status_code == 200
    assert "attachment" in export.headers["content-disposition"]
    assert "hello there" in export.text
    assert client.post(
        "/api/v1/compliance/people/export", json={"channel": "../x", "user": "U123"}
    ).status_code == 422
    erased = client.post("/api/v1/compliance/people/erase", json=body)
    assert erased.status_code == 200, erased.text
    assert erased.json()["erased"] is True
    assert client.get("/api/v1/compliance/people").json()["people"] == []
    again = client.post("/api/v1/compliance/people/erase", json=body).json()
    assert again["erased"] is False and "Nothing was found" in again["message"]


def test_ai_act_self_check_and_control_finder_are_plain():
    act = client.get("/api/v1/compliance/ai-act").json()
    assert act["banned_uses"] and act["high_risk_uses"]
    assert act["default_level"].startswith("Limited risk")
    found = client.get("/api/v1/compliance/control-finder", params={"q": "vendor has no dpa"}).json()
    assert found["controls"] and found["controls"][0]["references"]
    assert client.get("/api/v1/compliance/control-finder").json() == {"controls": []}


def test_assessment_downloads_reject_typed_references():
    assert client.get("/api/v1/compliance/downloads/oscal", params={"assessment": "../x"}).status_code == 422
    assert client.get("/api/v1/compliance/assessments").status_code == 200


def test_compliance_page_offers_buttons_not_config_keys(monkeypatch):
    import maverick.compliance as compliance
    from maverick.compliance import compliance_report

    real = compliance_report()
    forced = [
        type(c)(c.control, c.regulation, "action_needed" if c.status == "active" else c.status,
                c.detail, framework=c.framework)
        for c in real
    ]
    monkeypatch.setattr(compliance, "compliance_report", lambda: forced)
    body = client.get("/compliance").text
    assert 'data-cmp-switch="signed_audit"' in body
    assert "data-cmp-halt" in body
    assert 'href="#cmp-people"' in body
    assert 'id="cmp-retention-form"' in body
    assert "Download report" in body and "Download spreadsheet" in body
    assert 'id="cmp-erase"' in body and 'id="cmp-export"' in body
    assert not BANNED.search(body), BANNED.search(body)
    for check in real:
        assert check.detail not in body


def test_compliance_template_passes_a11y_audit():
    from pathlib import Path

    from maverick.a11y_audit import audit_file

    template = Path(app_mod.__file__).resolve().parent / "templates" / "compliance.html"
    assert audit_file(template) == []


def test_strict_mode_is_refused_until_sign_in_exists(monkeypatch):
    from maverick_dashboard import settings_store

    r = client.post("/api/v1/compliance/switches/strict_mode")
    assert r.status_code == 409 and "sign-in" in r.json()["detail"]
    assert "enterprise" not in settings_store.load_overlay()


def test_fingerprint_is_computed_on_the_server_and_needs_operate(monkeypatch):
    import hashlib

    data = b"evidence document bytes"
    r = client.post("/api/v1/compliance/fingerprint", content=data,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200
    assert r.json() == {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    assert client.post("/api/v1/compliance/fingerprint", content=b"",
                       headers={"Content-Type": "application/octet-stream"}).status_code == 422
    _as_viewer(monkeypatch)
    assert client.post("/api/v1/compliance/fingerprint", content=data,
                       headers={"Content-Type": "application/octet-stream"}).status_code == 403
