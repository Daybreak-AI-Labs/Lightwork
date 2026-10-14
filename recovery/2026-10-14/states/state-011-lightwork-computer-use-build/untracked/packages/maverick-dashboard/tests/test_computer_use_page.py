from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick_dashboard import computer_use_page as page
from maverick_dashboard.copilot_context import context_for_page

PACKAGE = Path(__file__).resolve().parents[1] / "maverick_dashboard"


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, dict(attrs)))


def _templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(PACKAGE / "templates"))
    templates.env.globals.update(
        theme="dark",
        font="default",
        lang="en",
        density="comfortable",
        custom_theme_css="",
        custom_theme_names=[],
        dir="ltr",
        nav_groups=[],
        t=lambda key: key,
    )
    return templates


@pytest.fixture()
def client(monkeypatch):
    permissions = {"view", "operate", "audit", "admin"}
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    monkeypatch.setattr(
        page,
        "has_permission",
        lambda _request, permission: permission in permissions,
    )
    monkeypatch.setattr(
        page,
        "has_global_permission",
        lambda _request, permission: permission in permissions,
    )
    app = FastAPI()
    app.include_router(page.create_computer_use_page_router(_templates()))
    return TestClient(app, headers={"Origin": "http://testserver"}), permissions


def test_console_is_hardened_liquid_glass_and_has_unique_accessible_controls(client) -> None:
    browser, _permissions = client
    response = browser.get(page.PAGE_PATH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private, max-age=0"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["permissions-policy"]
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp
    assert "style-src 'self' 'nonce-" in csp
    assert "form-action 'none'" in csp
    assert "cu-glass" in response.text
    assert "backdrop-filter:blur" in response.text
    assert 'data-copilot-page="/computer-use"' in response.text
    assert 'src="/static/lightwork-computer-use.js"' in response.text
    assert "Open authoritative queue" in response.text
    assert "content hash" in response.text
    assert "cryptographic custody seal" in response.text

    parser = _Markup()
    parser.feed(response.text)
    ids = [attrs["id"] for _tag, attrs in parser.tags if attrs.get("id")]
    duplicates = [name for name, count in Counter(ids).items() if count > 1]
    assert duplicates == []
    for control in ("cu-pause", "cu-resume", "cu-takeover", "cu-stop"):
        assert control in ids
    live_regions = [attrs for _tag, attrs in parser.tags if attrs.get("aria-live")]
    assert live_regions


def test_viewer_gets_replay_but_no_creation_or_enabled_mutations(
    client, monkeypatch
) -> None:
    browser, permissions = client
    permissions.clear()
    permissions.add("view")
    response = browser.get(page.PAGE_PATH)
    assert response.status_code == 200

    parser = _Markup()
    parser.feed(response.text)
    by_id = {
        attrs["id"]: attrs
        for _tag, attrs in parser.tags
        if isinstance(attrs.get("id"), str)
    }
    assert "cu-create-form" not in by_id
    assert "disabled" in by_id["cu-pause"]
    assert "disabled" in by_id["cu-stop"]
    assert 'href="/settings#computer-use-readiness"' not in response.text


def test_query_surface_is_closed(client) -> None:
    browser, _permissions = client
    assert browser.get("/computer-use?tenant=other").status_code == 404
    assert browser.get("/computer-use?theme=dark").status_code == 200


def test_computer_use_copilot_distinguishes_hash_and_custody() -> None:
    context = context_for_page("/computer-use")
    assert context.title == "Computer Use"
    joined = " ".join((*context.capabilities, *context.boundaries)).lower()
    assert "content hash" in joined
    assert "cryptographic custody seal" in joined
    assert "take over" in " ".join(context.capabilities).lower()
