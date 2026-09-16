"""The goal page reads as a conversation: withheld steps fold into one line,
the result renders as prose, and the agent's question is answered in place."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def page_env(monkeypatch, tmp_path):
    from maverick import world_model
    from maverick_dashboard import app as dash_app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    dash_app._world_cache.clear()
    world = world_model.WorldModel(world_model.DEFAULT_DB)
    yield TestClient(dash_app.app, headers={"Origin": "http://testserver"}), world
    world.close()


def test_goal_page_folds_the_kernels_withheld_markers_and_renders_prose(page_env):
    from maverick.blackboard import Blackboard

    client, world = page_env
    gid = world.create_goal("create a marketing plan for this app", "")
    world.set_goal_status(gid, "done", result="## Summary\n\n- one\n- two")

    text = client.get(f"/chat/goal/{gid}").text
    # The page learns the placeholder sentence from the kernel, never from a
    # copy of the string, so a reworded placeholder still folds.
    assert json.dumps(Blackboard._WITHHELD_CONTENT) in text
    assert json.dumps(Blackboard._REDACTION_UNAVAILABLE_CONTENT) in text
    # The result is prose the renderer upgrades in place, not a <pre> dump.
    assert 'id="result" tabindex="0" aria-label="Goal result" class="prose" data-markdown' in text
    assert 'src="/static/lightwork-markdown.js' in text
    # The work log is a step list; the raw "[agent] kind" trace line is gone.
    assert 'id="events" role="log" aria-live="polite" aria-label="Goal progress" tabindex="0"' in text
    assert "class=\"steps\"" in text
    assert "'[' + fmtAgent(e.agent) + '] '" not in text
    # Status stays machine-readable for the scripts; the page shows no raw badge.
    assert 'id="status" class="badge done" data-status="done" hidden>done<' in text
    # The chat shell, not the product shell: no product navigation or top bar.
    assert 'class="chat-shell' in text
    assert 'aria-label="Chats"' in text
    assert 'id="nav-toggle"' not in text and 'class="topbar"' not in text


def test_blocked_goal_reads_as_paused_and_keeps_the_question_in_the_conversation(page_env):
    client, world = page_env
    gid = world.create_goal("create a marketing plan for this app", "")
    world.set_goal_status(gid, "blocked")
    world.ask("Which audience first?", goal_id=gid)

    text = client.get(f"/chat/goal/{gid}").text
    assert 'id="status" class="badge blocked" data-status="blocked" hidden>paused<' in text
    assert "Lightwork needs one thing from you" in text
    assert 'id="qa-thread"' in text
    # Answering re-opens the live stream instead of leaving the page on a
    # finished stream with a Resume button.
    assert "resumeFollowing()" in text
    assert "recordExchange(questionText.textContent, ans)" in text


def test_markdown_renderer_is_served_as_javascript(page_env):
    client, _ = page_env
    response = client.get("/static/lightwork-markdown.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert "innerHTML" not in response.text
    assert "root.lwMarkdown = api" in response.text
