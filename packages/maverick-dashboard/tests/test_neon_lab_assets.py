"""The neon-lab design system's assets are served same-origin and public:
the brand mark and the self-hosted faces load on the login and share pages
before any session exists, and the font route is an allowlist, never a
directory listing."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app


@pytest.fixture
def noauth(monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "secret-token")  # pragma: allowlist secret
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_brand_mark_is_public_png(noauth):
    r = noauth.get("/static/daybreak-mark.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert len(r.content) > 1000


@pytest.mark.parametrize("name", ["manrope-200.woff2", "manrope-600.woff2", "jbm-400.woff2"])
def test_fonts_are_public_immutable_woff2(noauth, name):
    r = noauth.get(f"/static/fonts/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("font/woff2")
    assert "immutable" in r.headers["cache-control"]
    assert r.content[:4] == b"wOF2"


@pytest.mark.parametrize("name", ["../lightwork.css", "inter-400.woff2", "manrope-200.ttf", "..%2F..%2Fapp.py"])
def test_font_route_is_an_allowlist(noauth, name):
    assert noauth.get(f"/static/fonts/{name}").status_code in (401, 404)   # not on the allowlist: gated or missing, never served


def test_stylesheet_declares_the_faces_and_the_dark_stage_tokens(noauth):
    css = noauth.get("/static/lightwork.css", headers={"Authorization": "Bearer secret-token"}).text
    assert 'font-family: "Manrope"' in css and 'font-family: "JetBrains Mono"' in css
    assert "url(/static/fonts/manrope-200.woff2)" in css
    assert "--bg: #04050A" in css and "--grad: linear-gradient" in css
    assert "color-scheme: dark;" in css and "color-scheme: light;" in css   # the theme pair survives the layer


def test_shell_uses_the_mark(noauth):
    page = noauth.get("/start", headers={"Authorization": "Bearer secret-token"}).text
    assert "/static/daybreak-mark.png" in page
    assert "/static/daybreak-logo.jpg" in page   # favicon stays on the lockup
