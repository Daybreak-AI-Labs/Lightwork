"""Turnkey 'connect this account' dashboard flow: list providers, build the
consent URL, exchange the code into the sealed vault. Admin-gated mutations."""
from __future__ import annotations

from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


def test_list_providers_is_public_and_lists_presets():
    r = client.get("/api/v1/oauth/providers")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()["providers"]}
    assert {"google", "slack", "github"} <= names


def test_authorize_url_requires_vault(monkeypatch):
    monkeypatch.delenv("MAVERICK_OAUTH_VAULT", raising=False)
    monkeypatch.setattr("maverick.oauth_vault.enabled", lambda: False)
    r = client.post("/api/v1/oauth/slack/authorize-url",
                    json={"redirect_uri": "https://a/cb"})
    assert r.status_code == 403


def test_authorize_url_builds_consent_url(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_OAUTH_VAULT", "1")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "cid-9")
    r = client.post("/api/v1/oauth/slack/authorize-url",
                    json={"redirect_uri": "https://a/cb", "scopes": ["chat:write"]})
    assert r.status_code == 200
    body = r.json()
    assert body["authorize_url"].startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=cid-9" in body["authorize_url"]
    assert "scope=chat%3Awrite" in body["authorize_url"]
    assert body["verifier"]


def test_authorize_url_missing_client_id_is_400(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_OAUTH_VAULT", "1")
    monkeypatch.delenv("SLACK_OAUTH_CLIENT_ID", raising=False)
    r = client.post("/api/v1/oauth/slack/authorize-url",
                    json={"redirect_uri": "https://a/cb"})
    assert r.status_code == 400
    assert "SLACK_OAUTH_CLIENT_ID" in r.json()["detail"]


def test_authorize_url_unknown_provider_is_404(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_OAUTH_VAULT", "1")
    r = client.post("/api/v1/oauth/nope/authorize-url",
                    json={"redirect_uri": "https://a/cb"})
    assert r.status_code == 404


def test_exchange_seals_token_and_reports_connected(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_OAUTH_VAULT", "1")
    monkeypatch.setenv("SLACK_OAUTH_CLIENT_ID", "cid-9")
    put = {}

    class _Vault:
        def put(self, provider, record):
            put["provider"], put["record"] = provider, record

        def providers(self):
            return ["slack"] if put else []

    monkeypatch.setattr("maverick.oauth_vault.get_vault", lambda *a, **k: _Vault())
    monkeypatch.setattr(
        "maverick.tools.oauth_helper._post_form",
        lambda url, data: {"access_token": "AT", "refresh_token": "RT", "expires_in": 99},
    )
    r = client.post("/api/v1/oauth/slack/exchange",
                    json={"code": "the-code", "redirect_uri": "https://a/cb", "verifier": "v"})
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True and body["has_refresh_token"] is True
    assert "access_token" not in body                 # redacted summary only
    assert put["provider"] == "slack" and put["record"]["access_token"] == "AT"


def test_exchange_unknown_provider_is_404(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_OAUTH_VAULT", "1")
    r = client.post("/api/v1/oauth/nope/exchange",
                    json={"code": "c", "redirect_uri": "https://a/cb"})
    assert r.status_code == 404
