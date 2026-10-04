"""Each agent keeps a short record of who changed its instructions or model, when, and what."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick import paths
from maverick_dashboard import agent_changelog as changelog
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr("maverick.config.dashboard_overrides_path", lambda: tmp_path / "dashboard.toml")


def _view(**fields):
    base = {"name": "proc_po_chaser", "description": "Chases POs", "persona": "You are the PO Confirmation Chaser.",
            "allow_tools": ["read_file"], "deny_tools": [], "max_risk": "medium", "knowledge_sources": [],
            "models": {}, "workflow": [{"name": "read", "instruction": "Read the POs", "tools": ["read_file"], "gate": ""}],
            "output": {"shape": "table"}}
    base.update(fields)
    return base


def test_pack_changes_are_described_in_plain_words():
    before = _view()
    after = _view(persona="You are a stricter PO chaser.", models={"orchestrator": "openai:gpt-5.4"},
                  allow_tools=["read_file", "web_search"])
    changes = changelog.describe_agent_change(before, after)
    assert "Changed its instructions" in changes
    assert "Changed what it is allowed to do" in changes
    assert any(c.startswith("Changed the AI model it uses for the Planner") and "GPT-5.4" in c for c in changes)
    assert changelog.describe_agent_change(before, before) == []
    assert changelog.describe_agent_change(None, after) == ["Created this agent"]


def test_entries_are_newest_first_and_include_workspace_wide_changes():
    changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice")
    changelog.record(changelog.EVERY_AGENT, ["Changed the default AI model for every agent"], by="user:bob")
    changelog.record("legal_nda_desk", ["Changed its instructions"], by="user:carol")
    rows = changelog.entries("proc_po_chaser")
    assert [row["changes"][0] for row in rows] == [
        "Changed the default AI model for every agent", "Changed its instructions"]
    assert rows[1]["by"] == "user:alice"
    assert changelog.record("proc_po_chaser", [], by="user:alice") is None


def test_changelog_rows_stay_in_their_workspace():
    with paths.tenant_scope(tenant="alpha"):
        changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice")
    with paths.tenant_scope(tenant="beta"):
        assert changelog.entries("proc_po_chaser") == []


def test_version_label_names_the_agent_and_the_last_change_before_the_answer():
    changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice")
    row = changelog.entries("proc_po_chaser")[0]
    label = changelog.version_label("proc_po_chaser", answered_at=row["ts"] + 60)
    assert label["agent"] == "PO Confirmation Chaser"
    assert label["updated_at"] == row["ts"]
    assert label["changed_since"] == []
    later = changelog.version_label("proc_po_chaser", answered_at=row["ts"] - 60)
    assert later["updated_at"] is None
    assert later["changed_since"][0]["changes"] == ["Changed its instructions"]
    general = changelog.version_label("", answered_at=row["ts"] + 60)
    assert general["agent"] == "Lightwork"


def test_saving_and_resetting_an_agent_writes_the_changelog(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_DOMAINS_DIR", str(tmp_path / "domains"))
    r = client.post("/api/v1/agents/proc_po_chaser/override", json={"persona": "You are the strict PO Confirmation Chaser."})
    assert r.status_code == 200, r.text
    rows = changelog.entries("proc_po_chaser", include_workspace=False)
    assert rows and rows[0]["changes"] == ["Changed its instructions"]
    r = client.delete("/api/v1/agents/proc_po_chaser/override")
    assert r.status_code == 200, r.text
    assert changelog.entries("proc_po_chaser", include_workspace=False)[0]["changes"] == ["Reset to the built-in version"]


def test_role_and_default_model_changes_are_recorded_for_every_agent(monkeypatch):
    r = client.post("/api/v1/roles/orchestrator/override",
                    json={"system_addendum": "Always answer in British English."})
    assert r.status_code == 200, r.text
    rows = changelog.entries(changelog.EVERY_AGENT)
    assert rows[0]["changes"] == ["Changed the instructions the Planner follows for every agent"]
    r = client.post("/settings/models", data={"model": "anthropic:claude-sonnet-4-6"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    newest = changelog.entries(changelog.EVERY_AGENT)[0]["changes"][0]
    assert newest.startswith("Changed the default AI model for every agent")


def test_the_agent_editor_shows_who_changed_it_when_and_what(monkeypatch):
    changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice@example.com")
    body = client.get("/agents?name=proc_po_chaser").text
    history = body.split('id="agent-changelog"', 1)[1].split("</section>", 1)[0]
    assert "Change history" in history
    assert "alice@example.com" in history and "Changed its instructions" in history


def test_people_who_are_not_administrators_see_that_an_administrator_changed_it(monkeypatch):
    from maverick_dashboard import app as app_module

    monkeypatch.setattr(app_module, "has_global_permission", lambda request, permission: False)
    changelog.record("proc_po_chaser", ["Changed its instructions"], by="user:alice@example.com")
    history = client.get("/agents?name=proc_po_chaser").text.split('id="agent-changelog"', 1)[1]
    history = history.split("</section>", 1)[0]
    assert "alice" not in history and "An administrator" in history
