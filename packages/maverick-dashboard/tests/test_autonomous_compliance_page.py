from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from maverick import compliance_catalog as catalog
from maverick.a11y_audit import audit_file
from maverick_dashboard import autonomous_compliance_page as page
from maverick_dashboard import ui_visibility

PACKAGE = Path(page.__file__).resolve().parent
TEMPLATE = PACKAGE / "templates" / "autonomous_compliance.html"


class _CardBadges(HTMLParser):
    """Map each card link's href to the badge classes rendered inside it."""

    def __init__(self) -> None:
        super().__init__()
        self.by_href: dict[str, set[str]] = {}
        self.h1 = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        d = dict(attrs)
        if tag == "h1":
            self.h1 += 1
        if tag == "a" and "card" in (d.get("class") or ""):
            self._href = d.get("href")
            self.by_href.setdefault(self._href or "", set())
        if tag == "span" and self._href is not None:
            classes = (d.get("class") or "").split()
            if "badge" in classes:
                self.by_href[self._href].update(c for c in classes if c != "badge")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self._href = None


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
        # Render the real, role-filtered nav so the sidebar entry is genuine.
        nav_groups=ui_visibility.nav_groups(None),
        t=lambda key: key,
    )
    return templates


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(page, "require_permission", lambda _request, _permission: None)
    app = FastAPI()
    app.include_router(page.create_autonomous_compliance_page_router(_templates()))
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_page_renders_with_one_h1_and_every_suite(client: TestClient) -> None:
    response = client.get(page.PAGE_PATH)
    assert response.status_code == 200
    body = response.text
    parser = _CardBadges()
    parser.feed(body)
    assert parser.h1 == 1
    assert "Autonomous Compliance" in body
    for suite in catalog.suites():
        assert escape(suite.label) in body
        assert f'id="acm-suite-{suite.key}"' in body


def test_every_module_card_carries_the_catalog_badge(client: TestClient) -> None:
    parser = _CardBadges()
    parser.feed(client.get(page.PAGE_PATH).text)
    for suite in catalog.suites():
        for module in suite.modules:
            assert module.path in parser.by_href, module.path
            expected = catalog.STATUS_BADGE_CLASS[module.status]
            assert expected in parser.by_href[module.path], (module.path, module.status)


def test_labels_note_and_pricing_call_are_present(client: TestClient) -> None:
    body = client.get(page.PAGE_PATH).text
    assert "What the labels mean" in body
    for status in catalog.STATUS_ORDER:
        assert catalog.STATUS_LABELS[status] in body
    assert page.PRICING_MAILTO in body
    assert "chris@daybreakailabs.com" in body
    assert "Autonomous%20Compliance%20pricing" in body


def test_nav_entry_is_registered_and_heads_the_govern_group() -> None:
    entry = next(p for p in ui_visibility.PAGES if p["path"] == page.PAGE_PATH)
    assert entry["group"] == "Govern"
    assert entry["floor"] == "view"
    assert entry["default"] == "view"
    # First page in the Govern group in registry order -> top of the nav group.
    govern = [p for p in ui_visibility.PAGES if p["group"] == "Govern"]
    assert govern[0]["path"] == page.PAGE_PATH


def test_nav_entry_renders_in_the_sidebar(client: TestClient) -> None:
    body = client.get(page.PAGE_PATH).text
    assert f'href="{page.PAGE_PATH}"' in body


def test_page_rejects_unknown_query_and_requires_a_template_authority(
    client: TestClient,
) -> None:
    assert client.get(f"{page.PAGE_PATH}?debug=1").status_code == 404
    assert client.get(f"{page.PAGE_PATH}?theme=light").status_code == 200
    with pytest.raises(TypeError):
        page.create_autonomous_compliance_page_router(object())  # type: ignore[arg-type]


def test_template_passes_the_static_a11y_audit() -> None:
    assert audit_file(TEMPLATE) == []
