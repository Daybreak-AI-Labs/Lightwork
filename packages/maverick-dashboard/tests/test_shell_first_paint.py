"""The page shell paints after one stylesheet request.

On a phone, first paint waited for three render-blocking stylesheets, each
paying its own round trip. The shell head now links one: ``lightwork.css``,
which carries the preference and translation rules too. The Copilot and its
native controls load deferred, first among deferred scripts, so they no longer
compete with the stylesheet; ``lightwork-ui.js`` and the localization runtime
stay synchronous because inline page scripts and the first paint use them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "maverick_dashboard" / "static"
TEMPLATES = STATIC.parent / "templates"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_CONFIG", "MAVERICK_CONFIG_OVERLAY"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def _page(path: str) -> str:
    from maverick_dashboard.app import app

    response = TestClient(app, headers={"Origin": "http://testserver"}).get(path)
    assert response.status_code == 200, path
    return response.text


def _head(html: str) -> str:
    return html[html.index("<head"):html.index("</head>")]


@pytest.mark.parametrize("path", ["/overview", "/chat", "/settings"])
def test_the_shell_head_requests_one_stylesheet(path):
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', _head(_page(path)))
    assert len(links) == 1 and links[0].startswith("/static/lightwork.css"), links


def test_the_one_stylesheet_carries_the_preference_and_translation_rules():
    css = (STATIC / "lightwork.css").read_text(encoding="utf-8")
    for rule in (".preference-grid {", ".prefs__panel { max-height: min(80dvh, 40rem)",
                 ".active-workspace-banner { margin: 0 var(--space-3) var(--space-3);",
                 "[data-product-copy]{unicode-bidi:isolate}", ".locale-coverage{",
                 '[dir="rtl"] pre[lang="en"]'):
        assert rule in css, rule
    # The cascade order of the three old files is kept: preferences first,
    # the design system, then the translation rules last.
    assert css.index(".preference-grid {") < css.index(":root {")
    assert css.index("[data-product-copy]{unicode-bidi:isolate}") > css.index(".chats__empty")
    assert not (STATIC / "lightwork-preferences.css").exists()
    assert not (STATIC / "lightwork-i18n.css").exists()
    for template in TEMPLATES.glob("*.html"):
        source = template.read_text(encoding="utf-8")
        assert "lightwork-preferences.css" not in source and "lightwork-i18n.css" not in source, template.name


@pytest.mark.parametrize("path", ["/overview", "/chat"])
def test_copilot_scripts_are_deferred_ahead_of_page_scripts(path):
    page = _page(path)
    head = _head(page)
    scripts = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"[^>]*>", page)
    deferred = [src for src, tag in zip(scripts, re.findall(r"<script\b[^>]*\bsrc=\"[^\"]+\"[^>]*>", page), strict=True)
                if " defer" in tag]
    assert [src.split("?")[0] for src in deferred[:2]] == [
        "/static/lightwork-product-operations.js", "/static/lightwork-copilot.js"]
    for name in ("lightwork-product-operations.js", "lightwork-copilot.js"):
        assert page.count(name) == 1 and name in head, name
    # Inline page scripts call these as the page parses: they stay synchronous.
    for name in ("lightwork-ui.js", "lightwork-i18n.js"):
        tag = re.search(rf"<script\b[^>]*{re.escape(name)}[^>]*>", page).group(0)
        assert " defer" not in tag and " async" not in tag, tag
