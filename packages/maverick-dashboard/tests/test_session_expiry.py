"""Before sign-in ends, warn and offer to stay signed in; after, come back here.

Pages tell their script when the browser's sign-in ends. A local (passphrase)
sign-in can be kept going with one action, which replaces the session with a
new one and ends the old one; a company (SSO) sign-in signs in again with the
identity provider instead. Signing in again returns to the page the person was
on, never to another site.
"""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

PASSPHRASE = "a long local owner fixture phrase"
ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import os
    for name in tuple(os.environ):
        if name.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {})
    monkeypatch.setattr("maverick.config.load_global_config", lambda *a, **k: {})
    monkeypatch.setattr("maverick.config.config_source_errors", lambda **k: [])
    monkeypatch.setattr("maverick.world_model.DEFAULT_DB", tmp_path / "world.db")


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 53210),
                      headers={"Origin": ORIGIN}, follow_redirects=False)


def _owner(client):
    response = client.post("/auth/local-owner", data={"email": "owner@example.com", "password": PASSPHRASE,
                                                      "confirm": "yes"})
    assert response.status_code == 303, response.text
    return response


def _meta(page, name):
    found = re.search(r'<meta name="' + re.escape(name) + r'" content="([^"]*)">', page)
    return found.group(1) if found else None


def test_a_signed_in_page_says_when_sign_in_ends_and_how_to_keep_it():
    with _client() as client:
        _owner(client)
        page = client.get("/goals").text
        ends = int(_meta(page, "lightwork-session-expires"))
        assert abs(ends - (time.time() + 30 * 86400)) < 120
        assert abs(int(_meta(page, "lightwork-session-now")) - time.time()) < 120
        assert _meta(page, "lightwork-session-renew") == "renew"
        assert _meta(page, "lightwork-session-sign-in") == "/auth/local-login"
        assert "lightwork-session.js" in page
        chat = client.get("/chat").text
        assert _meta(chat, "lightwork-session-expires") and "lightwork-session.js" in chat


def test_no_sign_in_means_no_timer():
    with _client() as client:
        page = client.get("/goals").text
        assert _meta(page, "lightwork-session-expires") is None
        assert "lightwork-session.js" not in page


def test_staying_signed_in_replaces_the_session_and_ends_the_old_one():
    from maverick_dashboard import session_registry
    with _client() as client:
        _owner(client)
        before = client.get("/auth/session/status").json()
        old = session_registry.list_for("user:owner@example.com")
        assert len(old) == 1
        renewed = client.post("/auth/session/renew")
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["expires_at"] >= before["expires_at"] > time.time() + 29 * 86400
        assert "mvk_session=" in renewed.headers["set-cookie"]
        active = session_registry.list_for("user:owner@example.com")
        assert len(active) == 1 and active[0].sid != old[0].sid
        assert session_registry.status(old[0].sid) is False
        assert client.get("/auth/session/status").json()["expires_at"] == renewed.json()["expires_at"]
        assert client.get("/api/v1/features/modules").status_code == 200


def test_renewal_needs_a_live_same_site_session():
    with _client() as client:
        assert client.post("/auth/session/renew").status_code == 401
        assert client.get("/auth/session/status").json() == {"signed_in": False}
        _owner(client)
        cross = client.post("/auth/session/renew", headers={"Origin": "https://attacker.example"})
        assert cross.status_code == 403


def test_signing_in_again_returns_to_the_same_page_and_never_off_site():
    with _client() as client:
        _owner(client)
        client.cookies.clear()
        gone = client.get("/goals?status=active", headers={"Accept": "text/html"})
        assert gone.status_code == 302
        assert gone.headers["location"] == "/auth/local-login?return_to=%2Fgoals%3Fstatus%3Dactive"
        back = client.post("/auth/local-login?return_to=%2Fgoals%3Fstatus%3Dactive",
                           data={"email": "owner@example.com", "password": PASSPHRASE})
        assert back.status_code == 303 and back.headers["location"] == "/goals?status=active"
        client.cookies.clear()
        for unsafe in ("//evil.example/x", "https://evil.example/", "/\\evil.example"):
            away = client.post("/auth/local-login", params={"return_to": unsafe},
                               data={"email": "owner@example.com", "password": PASSPHRASE})
            assert away.status_code == 303 and away.headers["location"] in {"/", "/start"}, unsafe
            client.cookies.clear()
