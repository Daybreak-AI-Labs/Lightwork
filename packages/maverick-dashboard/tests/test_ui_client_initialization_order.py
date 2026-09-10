"""Dependent initializers run only after the shell provides shared UI helpers."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize(("path", "helper_call"), [
    ("/deliverables", "window.mvWhen(ts)"),
    ("/perf", 'mvEl("td", null, c)'),
    ("/chat/goal/{goal_id}", "window.mvWhen(ts)"),
])
def test_page_initializers_follow_shared_helpers(monkeypatch, tmp_path, path, helper_call):
    from maverick import world_model
    from maverick_dashboard import app as dashboard

    db = tmp_path / "world.db"
    monkeypatch.setattr(world_model, "DEFAULT_DB", db)
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    dashboard._world_cache.clear()
    if "{goal_id}" in path:
        goal_id = world_model.WorldModel(db).create_goal("Review the result", "Test")
        path = path.format(goal_id=goal_id)
    client = TestClient(dashboard.app, headers={"Origin": "http://testserver"})
    page = client.get(path)
    assert page.status_code == 200
    dependency = page.text.index('<script src="/static/lightwork-ui.js')
    dependent_call = page.text.index(helper_call)
    initializer = page.text.rfind("<script", 0, dependent_call)
    assert dependency < initializer < dependent_call
