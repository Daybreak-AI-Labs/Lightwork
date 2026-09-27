"""Advanced setup stays available without obscuring primary actions or errors."""
from __future__ import annotations

from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.ids = {}
        self.nodes = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parents": list(self.stack)}
        self.nodes.append(node)
        if "id" in node["attrs"]:
            self.ids[node["attrs"]["id"]] = node
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def within(self, child, parent):
        return self.ids[parent] in self.ids[child]["parents"]

    def closed(self, identifier):
        node = self.ids[identifier]
        assert node["tag"] == "details"
        assert "open" not in node["attrs"]
        assert any(item["tag"] == "summary" and node in item["parents"] for item in self.nodes)


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import automation_events, automation_import, world_model
    from maverick_dashboard.app import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(automation_import, "enabled", lambda: True)
    monkeypatch.setattr(automation_events, "enabled", lambda: True)
    return TestClient(app, headers={"Origin": "http://testserver"})


def test_flow_technical_values_are_optional_and_primary_actions_stay_outside(client):
    response = client.get("/flows/designer")
    assert response.status_code == 200
    page = Markup(response.text)
    for disclosure, control in [("fd-test-data", "fd-sample"), ("fd-schedule-advanced", "fd-schedule")]:
        page.closed(disclosure)
        assert page.within(control, disclosure)
        assert "required" not in page.ids[control]["attrs"]
        for action in ["fd-save", "fd-publish", "fd-run", "fd-run-real"]:
            assert not page.within(action, disclosure)
    assert not page.within("fd-schedule-preset", "fd-schedule-advanced")
    assert not page.within("fd-notify", "fd-test-data")


def test_automation_advanced_forms_contain_their_own_submit_and_preserve_visible_status(client):
    response = client.get("/automations")
    assert response.status_code == 200
    page = Markup(response.text)
    for disclosure, form_id in [("auto-event-setup", "auto-event-form"), ("auto-import-advanced", "auto-import-form")]:
        page.closed(disclosure)
        assert page.within(form_id, disclosure)
        form = page.ids[form_id]
        assert any(node["tag"] == "button" and node["attrs"].get("type") == "submit"
                   and form in node["parents"] for node in page.nodes)
    for identifier in ["auto-event-list", "auto-event-poll", "auto-event-msg"]:
        assert not page.within(identifier, "auto-event-setup")
    assert not page.within("auto-import-results", "auto-import-advanced")
    page.closed("oauth-advanced-setup")
    assert not page.within("oauth-list", "oauth-advanced-setup")
    assert page.within("oauth-code", "oauth-advanced-setup")
    assert not page.within("oauth-get-url", "oauth-advanced-setup")
    assert not page.within("oauth-msg", "oauth-advanced-setup")
    page.closed("auto-event-config-advanced")
    assert page.within("auto-event-more", "auto-event-config-advanced")
    assert page.within("auto-event-extra-add", "auto-event-config-advanced")
    assert not page.within("auto-event-fields", "auto-event-config-advanced")
    assert page.ids["auto-event-template"]["tag"] == "select"
    assert page.ids["auto-event-interval"]["tag"] == "select"
    # No raw settings or definitions boxes remain.
    assert "auto-event-config" not in page.ids and "auto-import-defs" not in page.ids


def test_playbook_permissions_have_visible_selections_with_optional_exact_identifiers(client):
    response = client.get("/workflow-builder")
    assert response.status_code == 200
    page = Markup(response.text)
    for name in ["allow", "deny"]:
        disclosure = f"pb-{name}-advanced"
        page.closed(disclosure)
        assert page.within(f"pb-{name}", disclosure)
        assert "required" not in page.ids[f"pb-{name}"]["attrs"]
        assert not page.within(f"pb-{name}-summary", disclosure)
        assert not page.within("pb-save-btn", disclosure)
    assert not any(parent["tag"] == "details" for parent in page.ids["pb-gate-boundary"]["parents"])


def test_chat_keeps_full_log_closed_without_hiding_errors_or_human_inputs(client):
    from maverick import world_model

    world = world_model.WorldModel(world_model.DEFAULT_DB)
    goal_id = world.create_goal("Review the meeting notes", "")
    response = client.get(f"/chat/goal/{goal_id}")
    assert response.status_code == 200
    page = Markup(response.text)
    page.closed("work-activity")
    assert page.within("events", "work-activity")
    for identifier in ["stream-error", "question-panel", "answer-input", "result-section", "halt-card"]:
        assert not page.within(identifier, "work-activity")
    assert page.within("halt-resume-budget", "halt-resume-form")
    assert page.within("halt-resume", "halt-resume-form")
    assert page.ids["halt-resume"]["attrs"]["type"] == "submit"
