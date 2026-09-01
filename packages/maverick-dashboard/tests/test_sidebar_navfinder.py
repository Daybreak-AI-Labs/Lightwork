"""Rendered contracts for the registry-backed sidebar page finder."""
from __future__ import annotations

from html.parser import HTMLParser

from fastapi.testclient import TestClient


class _NavFinderMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_sidebar = 0
        self.in_finder = 0
        self.sidebar_hrefs: list[str] = []
        self.finder_hrefs: list[str] = []
        self.ids: dict[str, dict[str, str | None]] = {}
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = dict(attrs)
        if tag == "aside" and values.get("id") == "sidebar":
            self.in_sidebar += 1
        if tag == "dialog" and values.get("id") == "navfinder-dialog":
            self.in_finder += 1
        element_id = values.get("id")
        if element_id:
            self.ids[element_id] = values
        if tag == "script" and values.get("src"):
            self.scripts.append(values["src"])
        if tag != "a" or not values.get("href"):
            return
        classes = set((values.get("class") or "").split())
        if self.in_sidebar and "navquick" not in classes:
            self.sidebar_hrefs.append(values["href"])
        if self.in_finder and "data-navfinder-target" in values:
            self.finder_hrefs.append(values["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "aside" and self.in_sidebar:
            self.in_sidebar -= 1
        if tag == "dialog" and self.in_finder:
            self.in_finder -= 1


def _client(monkeypatch, tmp_path) -> TestClient:
    from maverick import world_model
    from maverick_dashboard import app as dashboard_app

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    dashboard_app._world_cache.clear()
    return TestClient(dashboard_app.app, headers={"Origin": "http://testserver"})


def _overview_markup(monkeypatch, tmp_path) -> _NavFinderMarkup:
    response = _client(monkeypatch, tmp_path).get("/overview")
    assert response.status_code == 200
    markup = _NavFinderMarkup()
    markup.feed(response.text)
    return markup


def test_page_finder_has_an_accessible_dialog_and_combobox_contract(monkeypatch, tmp_path):
    markup = _overview_markup(monkeypatch, tmp_path)

    trigger = markup.ids["navfinder-open"]
    assert trigger["aria-haspopup"] == "dialog"
    assert trigger["aria-controls"] == "navfinder-dialog"
    assert trigger["aria-expanded"] == "false"

    dialog = markup.ids["navfinder-dialog"]
    assert dialog["aria-labelledby"] == "navfinder-title"
    assert dialog["aria-describedby"] == "navfinder-help"

    search = markup.ids["navfinder-input"]
    assert search["role"] == "combobox"
    assert search["aria-autocomplete"] == "list"
    assert search["aria-controls"] == "navfinder-results"
    assert search["aria-expanded"] == "false"
    assert markup.ids["navfinder-results"]["role"] == "listbox"
    assert markup.ids["navfinder-count"]["role"] == "status"
    assert "/static/lightwork-navfinder.js" in markup.scripts


def test_page_finder_destinations_exactly_match_visible_sidebar_registry(monkeypatch, tmp_path):
    markup = _overview_markup(monkeypatch, tmp_path)

    assert markup.sidebar_hrefs
    assert markup.finder_hrefs == markup.sidebar_hrefs
    assert all(href.startswith("/") for href in markup.finder_hrefs)
    assert "#" not in markup.finder_hrefs
    assert not any(href.startswith("//") for href in markup.finder_hrefs)
