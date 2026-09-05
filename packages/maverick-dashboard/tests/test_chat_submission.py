"""Chat saves complete reviewed instructions and rejects oversized drafts."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def chat_env(monkeypatch, tmp_path):
    from maverick import world_model
    from maverick_dashboard import app as dashboard

    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    world = world_model.WorldModel(world_model.DEFAULT_DB)
    monkeypatch.setattr(dashboard, "_world", lambda: world)
    monkeypatch.setattr(dashboard, "require_provider_or_400", lambda: None)
    monkeypatch.setattr(dashboard, "check_goal_rate_limit", lambda request: None)
    calls = []

    async def run(goal_id, **kwargs):
        goal = world.get_goal(goal_id)
        calls.append((goal.title, goal.description, kwargs))

    monkeypatch.setattr("maverick.runner.run_goal_in_background_async", run)
    yield TestClient(dashboard.app, headers={"Origin": "http://testserver"}), world, calls
    world.close()


def test_chat_submits_full_prepared_template_at_goal_limits(chat_env):
    from maverick_dashboard.api_schemas import GoalIn

    client, world, calls = chat_env
    title = "T" * 200
    ending = "\nFinal instruction: keep this.\n"
    description = "A" * (16_000 - len(ending)) + ending
    assert len(description) == 16_000
    GoalIn(title=title, description=description)  # Same contract as REST goals.
    response = client.post("/chat/send", data={"title": title, "description": description}, follow_redirects=False)
    assert response.status_code == 303, response.text
    goals = world.list_goals()
    assert len(goals) == 1
    assert goals[0].title == title
    assert goals[0].description == description
    assert calls == [(title, description, {"allowed_suites": None})]


@pytest.mark.parametrize("field, value", [("title", "T" * 201), ("description", "D" * 16_001)], ids=["title", "description"])
def test_chat_rejects_oversized_drafts_before_creating_or_running_goal(chat_env, field, value):
    client, world, calls = chat_env
    data = {"title": "Review the report", "description": "Keep the full instructions"}
    data[field] = value
    response = client.post("/chat/send", data=data, follow_redirects=False)
    assert response.status_code == 422, response.text
    assert "shorten" in response.json()["detail"].lower()
    assert not world.list_goals()
    assert not calls


def test_chat_empty_details_keeps_the_title_as_its_brief(chat_env):
    client, world, calls = chat_env
    response = client.post("/chat/send", data={"title": "  Review the report  ", "description": " \n "}, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert world.list_goals()[0].description == "Review the report"
    assert calls[0][:2] == ("Review the report", "Review the report")
