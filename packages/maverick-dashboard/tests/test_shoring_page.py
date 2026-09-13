"""The /shoring page: hero, Shoring P&L, book of work, add-a-function form,
and the exception desk. Renders one h1, is a11y-clean, and is in the nav."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from maverick_dashboard.app import app

client = TestClient(app, headers={"Origin": "http://testserver"})


@pytest.fixture(autouse=True)
def _fresh_world(tmp_path, monkeypatch):
    from maverick import config, world_model
    from maverick_dashboard import api as api_mod

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_SHORING", raising=False)
    config.reset_config_cache()
    api_mod._world_cache.clear()
    yield
    config.reset_config_cache()
    api_mod._world_cache.clear()


def _add_function(**over):
    body = {
        "key": "ap", "name": "Accounts Payable", "department": "finance_ap",
        "scope": "Invoice to pay", "status": "running", "owner": "cfo",
        "slas": [{"name": "Throughput", "metric": "throughput_per_day", "target": 1.0}],
    }
    body.update(over)
    assert client.post("/api/v1/shoring/function", json=body).status_code == 200


def test_unavailable_book_is_an_explicit_recoverable_page_error(monkeypatch):
    from maverick import shoring

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic private storage detail")

    monkeypatch.setattr(shoring, "book_status", unavailable)
    response = client.get("/shoring")
    assert response.status_code == 503
    assert "Restore access" in response.text
    assert "private storage detail" not in response.text


def test_page_renders_pieces():
    resp = client.get("/shoring")
    assert resp.status_code == 200
    html = resp.text
    assert html.count("<h1") == 1                       # exactly one page title
    assert "Autonomous Shoring" in html                 # hero title
    assert "Shoring P&amp;L" in html                    # the P&L summary
    assert "Book of work" in html
    assert 'id="sh-form"' in html                       # add-a-function form
    assert "Exception desk" in html
    assert "/approvals" in html                         # exception desk links out


def test_page_shows_book_and_sla_indicator():
    from maverick import world_model
    w = world_model.WorldModel(world_model.DEFAULT_DB)
    gid = w.create_goal("g", "d", domain="finance_ap")
    ep = w.start_episode(gid)
    w.end_episode(ep, "ran", "success", cost_dollars=1.0)
    w.set_goal_status(gid, "done")
    _add_function()
    html = client.get("/shoring").text
    assert "Accounts Payable" in html
    assert "finance_ap" in html
    # The lifecycle badge and an SLA verdict word are present.
    assert "running" in html
    assert "breached" in html   # 1 delivered over 90d is far under 1/day


def test_page_exception_desk_lists_department_approvals():
    from maverick import world_model
    w = world_model.WorldModel(world_model.DEFAULT_DB)
    w.create_approval("wire.transfer", risk="high", scope="finance_ap payment")
    _add_function()
    html = client.get("/shoring").text
    assert "awaiting sign-off" in html
    assert "wire.transfer" in html


def test_page_empty_state():
    html = client.get("/shoring").text
    assert "No functions in the book of work yet" in html


def test_page_is_accessible():
    from maverick import a11y_audit
    _add_function()
    html = client.get("/shoring").text
    findings = a11y_audit.audit_html(html)
    assert not findings, [f"{f.rule}: {f.detail}" for f in findings]


def test_page_in_nav_registry_after_savings():
    from maverick_dashboard import ui_visibility
    paths = [p["path"] for p in ui_visibility.PAGES]
    assert "/shoring" in paths
    entry = next(p for p in ui_visibility.PAGES if p["path"] == "/shoring")
    assert entry["group"] == "Observe"
    assert entry["floor"] == "view"
    assert entry["label"] == "Autonomous Shoring"
    # Ordered immediately after /savings (nav order = registry order).
    assert paths.index("/shoring") == paths.index("/savings") + 1


def test_page_disabled_is_404(monkeypatch):
    monkeypatch.setenv("MAVERICK_SHORING", "0")
    assert client.get("/shoring").status_code == 404
