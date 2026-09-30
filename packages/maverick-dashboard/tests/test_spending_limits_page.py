"""Admin > Spending limits: workspace, everyone and per-person limits."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    return TestClient(dash_app.app, headers=_ORIGIN)


def test_page_starts_with_no_limits_and_plain_controls(client):
    r = client.get("/spending-limits")
    assert r.status_code == 200
    text = r.text
    assert "By default there is no limit" in text
    for action in ("/spending-limits/workspace", "/spending-limits/default"):
        assert f'action="{action}"' in text
    assert "per task" in text and "per day" in text and "per month" in text
    # No developer language in the page's own content (the shared menu aside).
    body = text[text.index("By default there is no limit"):text.index("reset at midnight UTC")]
    for word in ("principal", "tenant", "JSON", "config.toml", "maverick "):
        assert word not in body


def test_admin_sets_and_clears_every_kind_of_limit(client):
    from maverick import spending_limits
    from maverick.quotas import UsageLedger
    UsageLedger().record("user:alice", 3.25, 0, 0)

    r = client.post("/spending-limits/workspace",
                    data={"choice": "limit", "amount": "500", "period": "month"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/spending-limits?saved=workspace"
    r = client.post("/spending-limits/default",
                    data={"choice": "limit", "amount": "40", "period": "day"},
                    follow_redirects=False)
    assert r.headers["location"] == "/spending-limits?saved=default"
    r = client.post("/spending-limits/person",
                    data={"principal": "user:alice", "amount": "12.5", "period": "task"},
                    follow_redirects=False)
    assert r.headers["location"] == "/spending-limits?saved=person"
    assert spending_limits.load()["workspace"] == {"amount": 500.0, "period": "month"}
    assert spending_limits.load()["default"] == {"amount": 40.0, "period": "day"}
    assert spending_limits.load()["users"] == {"user:alice": {"amount": 12.5, "period": "task"}}

    page = client.get("/spending-limits?saved=person").text
    assert "Personal limit saved." in page
    assert "$12.50 per task" in page and "$3.25" in page
    assert "Remove personal limit" in page

    client.post("/spending-limits/person", data={"principal": "user:alice", "action": "remove"})
    client.post("/spending-limits/workspace", data={"choice": "none"})
    client.post("/spending-limits/default", data={"choice": "none"})
    assert spending_limits.load() | {"updated_by": "", "updated_at": 0} == {
        "workspace": None, "default": None, "users": {}, "updated_by": "", "updated_at": 0}


def test_bad_input_shows_a_plain_message_never_echoed_text(client):
    r = client.post("/spending-limits/workspace",
                    data={"choice": "limit", "amount": "-4", "period": "month"},
                    follow_redirects=False)
    assert r.headers["location"] == "/spending-limits?error=amount"
    page = client.get("/spending-limits?error=amount").text
    assert "Enter the limit as an amount in dollars, more than $0." in page
    # Arbitrary text in the address is never shown on the page.
    assert "Injected words" not in client.get("/spending-limits?error=Injected+words").text


def test_changes_require_same_origin(monkeypatch, tmp_path):
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick_dashboard import app as dash_app
    c = TestClient(dash_app.app, headers={"origin": "http://evil.example"})
    r = c.post("/spending-limits/workspace",
               data={"choice": "limit", "amount": "5", "period": "day"},
               follow_redirects=False)
    assert r.status_code == 403
    from maverick import spending_limits
    assert spending_limits.load()["workspace"] is None


def test_spending_limits_is_in_the_admin_menu():
    from maverick_dashboard import ui_visibility
    entry = next(p for p in ui_visibility.PAGES if p["path"] == "/spending-limits")
    assert entry["group"] == "Admin" and entry["floor"] == "admin"
