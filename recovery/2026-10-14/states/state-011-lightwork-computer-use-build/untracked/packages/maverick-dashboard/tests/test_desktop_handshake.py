from __future__ import annotations

import hashlib
import hmac

from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick_dashboard import auth
from maverick_dashboard.desktop_handshake import (
    DESKTOP_CHALLENGE_HEADER,
    DESKTOP_READY_PATH,
    router,
)


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, headers={"Origin": "http://tauri.localhost"})


def test_valid_challenge_returns_verifiable_proof_without_secret(monkeypatch) -> None:
    secret = "desktop-process-secret-with-enough-entropy"  # pragma: allowlist secret
    challenge = "a4" * 32
    monkeypatch.setenv("MAVERICK_DESKTOP_TOKEN", secret)

    response = _client().get(
        DESKTOP_READY_PATH,
        headers={DESKTOP_CHALLENGE_HEADER: challenge},
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert response.headers["vary"] == "Origin"
    body = response.json()
    assert body["schema"] == "lightwork.desktop-ready.v1"
    assert body["product"] == "lightwork"
    assert body["version"]
    assert body["instance"]
    assert body["proof"] == hmac.new(
        secret.encode(), challenge.encode(), hashlib.sha256
    ).hexdigest()
    assert secret not in response.text
    assert challenge not in response.text


def test_missing_secret_or_bad_challenge_discloses_no_readiness(monkeypatch) -> None:
    monkeypatch.delenv("MAVERICK_DESKTOP_TOKEN", raising=False)
    absent = _client().get(
        DESKTOP_READY_PATH,
        headers={DESKTOP_CHALLENGE_HEADER: "aa" * 32},
    )
    assert absent.status_code == 404
    assert "lightwork" not in absent.text.lower()

    monkeypatch.setenv("MAVERICK_DESKTOP_TOKEN", "configured")
    for challenge in ("", "not-hex", "aa", "A4" * 32, "aa" * 65):
        response = _client().get(
            DESKTOP_READY_PATH,
            headers={DESKTOP_CHALLENGE_HEADER: challenge},
        )
        assert response.status_code == 404
        assert "lightwork" not in response.text.lower()


def test_only_fixed_desktop_origins_are_admitted(monkeypatch) -> None:
    monkeypatch.setenv("MAVERICK_DESKTOP_TOKEN", "configured")
    challenge = "1f" * 32
    for origin in ("https://example.test", "null", ""):
        response = _client().get(
            DESKTOP_READY_PATH,
            headers={"Origin": origin, DESKTOP_CHALLENGE_HEADER: challenge},
        )
        assert response.status_code == 404
        assert "access-control-allow-origin" not in response.headers

    native = _client().get(
        DESKTOP_READY_PATH,
        headers={
            "Origin": "tauri://localhost",
            DESKTOP_CHALLENGE_HEADER: challenge,
        },
    )
    assert native.status_code == 200
    assert native.headers["access-control-allow-origin"] == "tauri://localhost"


def test_preflight_allows_only_get_and_the_challenge_header() -> None:
    client = _client()
    accepted = client.options(
        DESKTOP_READY_PATH,
        headers={
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": DESKTOP_CHALLENGE_HEADER,
        },
    )
    assert accepted.status_code == 204
    assert accepted.headers["access-control-allow-methods"] == "GET"
    assert accepted.headers["access-control-allow-headers"] == DESKTOP_CHALLENGE_HEADER

    extra = client.options(
        DESKTOP_READY_PATH,
        headers={
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": f"{DESKTOP_CHALLENGE_HEADER}, authorization",
        },
    )
    assert extra.status_code == 404
    wrong_method = client.options(
        DESKTOP_READY_PATH,
        headers={
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": DESKTOP_CHALLENGE_HEADER,
        },
    )
    assert wrong_method.status_code == 404


def test_desktop_handshake_is_reviewed_as_self_authenticating() -> None:
    from maverick_dashboard import app as dashboard_app

    assert auth._self_authenticated_path(DESKTOP_READY_PATH)
    assert DESKTOP_READY_PATH in dashboard_app._AUTH_EXEMPT
