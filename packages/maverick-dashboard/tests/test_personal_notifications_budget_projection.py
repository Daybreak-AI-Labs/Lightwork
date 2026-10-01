"""Budget warnings join, rather than replace, the personal source projection."""
from types import SimpleNamespace

from maverick import budget_guidance
from maverick_dashboard import personal_notifications_api as api


def test_budget_sources_preserve_current_owned_task_sources(monkeypatch):
    goal = SimpleNamespace(id=9, owner="user:alice", title="Own task", status="blocked",
                           result=None, deadline=None, project_id=None)
    world = SimpleNamespace(personal_alert_goals=lambda actor, **kw: [(goal, 1, 0)] if not kw['after_id'] else [],
                            open_questions=lambda _: [], list_projects=lambda **_: [])
    warning = {"ref": "budget:warning", "version": "first", "category": "blocked",
               "title": "Budget needs attention", "label": "Monthly cap approaching",
               "href": "/budget", "goal_id": None, "project_id": None}
    monkeypatch.setattr(api, "_app", lambda: SimpleNamespace(_world=lambda: world))
    monkeypatch.setattr(api.auth, "assert_goal_access", lambda *_: None)
    monkeypatch.setattr(api.auth, "has_permission", lambda *_: False)
    monkeypatch.setattr(api.auth, "caller_suites", lambda *_: None)
    observed = []
    monkeypatch.setattr(budget_guidance, "warning_sources", lambda actor: observed.append(actor) or [warning])
    sources, tasks, _, _ = api._projection(object(), "user:alice")
    assert {source["ref"] for source in sources} == {"goal:9:state", "budget:warning"}
    assert tasks == [{"id": 9, "title": "Own task"}]
    assert observed == ["user:alice"]
