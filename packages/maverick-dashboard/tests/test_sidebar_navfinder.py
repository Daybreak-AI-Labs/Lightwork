"""Rendered contracts for the registry-backed sidebar page finder."""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

from fastapi.testclient import TestClient


class _NavFinderMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_sidebar = 0
        self.in_finder = 0
        self.sidebar_hrefs: list[str] = []
        self.finder_hrefs: list[str] = []
        self.finder_search: dict[str, str] = {}
        self.finder_attrs: dict[str, dict[str, str | None]] = {}
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
            self.finder_search[values["href"]] = values.get("data-search") or ""
            self.finder_attrs[values["href"]] = values

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
    client = _client(monkeypatch, tmp_path)
    response = client.get("/overview")
    assert response.status_code == 200
    markup = _NavFinderMarkup()
    markup.feed(response.text)
    # The finder's destinations are not in the page: the dialog fetches them
    # from /nav/finder on first open (the same role-filtered registry).
    assert markup.finder_hrefs == []
    items = client.get("/nav/finder")
    assert items.status_code == 200
    fragment = _NavFinderMarkup()
    fragment.feed('<dialog id="navfinder-dialog">' + items.text + "</dialog>")
    markup.finder_hrefs = fragment.finder_hrefs
    markup.finder_search = fragment.finder_search
    markup.finder_attrs = fragment.finder_attrs
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
    # Shell assets carry a content-hash query (W3-77); compare the path.
    assert "/static/lightwork-navfinder.js" in {src.split("?")[0] for src in markup.scripts}


def test_page_finder_destinations_cover_the_visible_registry_beyond_the_sidebar(monkeypatch, tmp_path):
    from maverick_dashboard import employee_companion_api, ui_visibility

    monkeypatch.setattr(employee_companion_api, "navigation_available", lambda request, *, admin: False)
    # Like the companions, these screens' routes need a named account the local
    # owner does not have, so the finder withholds them.
    unavailable = {"/companion", "/companion/admin", "/notifications", "/access-requests",
                   "/devices", "/get-started/employee"}

    markup = _overview_markup(monkeypatch, tmp_path)

    assert markup.sidebar_hrefs
    # The finder is the role-filtered registry INCLUDING pages folded out of
    # the rail (in_nav: False); the sidebar stays the curated subset of it.
    assert set(markup.sidebar_hrefs) < set(markup.finder_hrefs)
    assert markup.finder_hrefs == [
        ln["href"] for g in ui_visibility.finder_pages(None) for ln in g["links"]
        if ln["href"] not in unavailable
    ]
    assert unavailable.isdisjoint(markup.finder_hrefs)
    assert "/fleets" in markup.finder_hrefs and "/fleets" not in markup.sidebar_hrefs
    assert all(href.startswith("/") for href in markup.finder_hrefs)
    assert "#" not in markup.finder_hrefs
    assert not any(href.startswith("//") for href in markup.finder_hrefs)


def test_page_finder_accepts_the_workflow_name_for_the_grc_builder(monkeypatch, tmp_path):
    markup = _overview_markup(monkeypatch, tmp_path)

    search_terms = markup.finder_search["/compliance/grc-studio"].lower()
    assert "workflow" in search_terms
    assert "builder" in search_terms


def test_page_finder_indexes_each_page_heading_and_plain_synonyms(monkeypatch, tmp_path):
    from maverick_dashboard import ui_visibility

    markup = _overview_markup(monkeypatch, tmp_path)
    for href, attrs in markup.finder_attrs.items():
        assert attrs["data-label"].split("|")[0] == ui_visibility.page_for(href)["label"]
        assert attrs["data-heading"].split("|")[0] == ui_visibility.HEADINGS.get(href, "")
        assert attrs["data-synonyms"] == "|".join(ui_visibility.SYNONYMS.get(href, ()))
    assert markup.finder_attrs["/audit"]["data-heading"].startswith("Activity log")
    assert "Activity log" in markup.finder_search["/audit"]
    assert "approve" in markup.finder_attrs["/approvals"]["data-synonyms"].split("|")
    assert "emergency stop" in markup.finder_search["/agent-control-plane"]


# The heading the finder indexes is the one the page shows, whichever way the
# label and the heading are later brought together.
def test_indexed_heading_is_the_heading_the_page_shows(monkeypatch, tmp_path):
    from maverick_dashboard import ui_visibility

    client = _client(monkeypatch, tmp_path)
    for path in ("/audit", "/immune", "/replay", "/redact", "/simulate", "/run-tree", "/external-agents",
                 "/styles", "/perf", "/walkthroughs", "/workflows", "/users", "/help", "/my-work"):
        response = client.get(path)
        assert response.status_code == 200, path
        shown = re.search(r"<h1[^>]*>(.*?)</h1>", response.text, re.DOTALL).group(1)
        shown = " ".join(unescape(re.sub(r"<[^>]+>", "", shown)).split()).casefold()
        page = ui_visibility.page_for(path)
        assert shown in {page["label"].casefold(), ui_visibility.HEADINGS.get(path, "").casefold()}, path
