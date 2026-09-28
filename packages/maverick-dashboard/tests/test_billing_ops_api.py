"""Billing page: plan contents and Stripe billing status, preview and send."""
from __future__ import annotations

import html as _html
import re

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
BASE = "/api/v1/admin/billing-ops"
_BANNED = re.compile(r"\b(tenant|principal|payload|json|toml|webhook|sdk|entitlements?)\b|/api/v1|MAVERICK_",
                     re.IGNORECASE)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.audit import writer as audit_writer
    from maverick_dashboard import app as dash_app
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit_writer, "_default", None)
    audit_writer._defaults.clear()
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT", "MAVERICK_CLIENT_ID", "MAVERICK_STRIPE_ENABLE",
                 "MAVERICK_STRIPE_SECRET_KEY", "MAVERICK_STRIPE_BILL_METERED"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    from maverick.tenant.registry import create_tenant
    create_tenant("acme", plan="pro", display_name="Acme")
    yield TestClient(dash_app.app, headers=ORIGIN)
    config.reset_config_cache()
    dash_app._world_cache.clear()
    audit_writer._defaults.clear()


def _as(monkeypatch, role: str, who: str = "user:someone"):
    from maverick_dashboard import auth, rbac
    rbac.set_role(who, role)
    monkeypatch.setattr(auth, "caller_principal", lambda request: who)


def _audit_verbs(monkeypatch):
    from maverick.audit import writer
    seen: list[dict] = []
    real = writer.audit_event

    def spy(kind, **payload):
        seen.append(payload)
        return real(kind, **payload)
    monkeypatch.setattr(writer, "audit_event", spy)
    import maverick.audit as audit_pkg
    monkeypatch.setattr(audit_pkg, "audit_event", spy)
    return seen


def test_billing_controls_need_an_installation_administrator(client, monkeypatch):
    _as(monkeypatch, "operator")
    assert client.get(BASE + "/plan", params={"workspace": "acme"}).status_code == 403
    assert client.get(BASE + "/stripe").status_code == 403
    assert client.get(BASE + "/stripe/preview", params={"workspace": "acme"}).status_code == 403
    assert client.post(BASE + "/stripe/sync", json={"workspace": "acme", "confirm": True}).status_code == 403


def test_plan_contents_come_from_the_plan_in_plain_words(client):
    data = client.get(BASE + "/plan", params={"workspace": "acme"}).json()
    assert data["plan"] == "Pro" and "Chat apps" in data["features"]
    assert data["daily_limit"] == "$100 a day" and data["tasks_at_once"] == "5"
    assert client.get(BASE + "/plan", params={"workspace": "ghost"}).status_code == 404


def test_stripe_status_preview_and_a_confirmed_send(client, monkeypatch):
    status = client.get(BASE + "/stripe").json()
    assert status["ready"] is False and "turned off" in status["summary"]
    assert not _BANNED.search(status["summary"])
    preview = client.get(BASE + "/stripe/preview", params={"workspace": "acme"}).json()
    assert preview["can_sync"] is False and "only a preview" in preview["summary"]
    bad = client.get(BASE + "/stripe/preview", params={"workspace": "acme", "since": "2026-02-01",
                                                       "until": "2026-01-01"})
    assert bad.status_code == 422
    assert client.post(BASE + "/stripe/sync", json={"workspace": "acme"}).status_code == 409
    seen = _audit_verbs(monkeypatch)
    off = client.post(BASE + "/stripe/sync", json={"workspace": "acme", "confirm": True})
    assert off.status_code == 409 and "Nothing else was changed" in off.json()["detail"]
    assert any(p.get("verb") == "stripe_sync" for p in seen)  # recorded before contacting Stripe

    from maverick import billing_stripe
    calls = []

    def fake_sync(tenant_id, card, *, since=None, until=None, client=None):
        calls.append((tenant_id, since, until))
        return {"customer": "cus_123", "subscription": {"status": "active", "plan": "pro"}}
    monkeypatch.setattr(billing_stripe, "sync_tenant_for_period", fake_sync)
    sent = client.post(BASE + "/stripe/sync", json={"workspace": "acme", "since": "2026-01-01",
                                                    "until": "2026-01-31", "confirm": True}).json()
    assert calls == [("acme", "2026-01-01", "2026-01-31")]
    assert "Acme is set up in Stripe" in sent["summary"] and "cus_123" in sent["technical"][0]["text"]


def test_billing_page_shows_the_new_panels_to_administrators(client):
    page = client.get("/billing")
    assert page.status_code == 200
    for marker in ('id="plan-form"', 'id="stripe-form"', "/static/lightwork-billing-ops.js", "Preview the charge"):
        assert marker in page.text
    main = page.text.split('id="plan-contents"', 1)[1].split("<script", 1)[0]
    text = _html.unescape(re.sub(r"<[^>]+>", " ", main))
    assert not _BANNED.search(text)
    assert client.get("/static/lightwork-billing-ops.js").status_code == 200
