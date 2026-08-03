"""Real dashboards on Overview / Spend / Workforce: the shared _charts.html
primitives (donut / columns / bars) render populated charts from the world
ledger, and every page stays honest when the ledger is empty."""
from __future__ import annotations

from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)


def _seed_world():
    from maverick.world_model import WorldModel
    w = WorldModel()
    done = w.create_goal("Vendor DPA review", domain="legal_privacy")
    eid = w.start_episode(done)
    w.end_episode(eid, "ok", "success", cost_dollars=1.25,
                  input_tokens=10_000, output_tokens=1_500, tool_calls=6)
    w.set_goal_status(done, "done")
    blocked = w.create_goal("Access recert", domain="sec_access_review")
    w.set_goal_status(blocked, "blocked")
    return w


def test_overview_empty_still_offers_first_goal(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = _client().get("/overview").text
    assert "Start your first goal" in t
    assert "viz-donut" not in t   # no charts drawn over an empty record


def test_overview_dashboard_goal_mix_and_weekly(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _seed_world()
    t = _client().get("/overview").text
    assert "Goal mix" in t and "viz-donut" in t
    # Status legend is text beside the color dot — never hue alone.
    for label in ("Done", "Active", "Blocked", "Queued"):
        assert label in t
    assert "Activity — last 10 weeks" in t
    # 10 weekly buckets, each a paired started/delivered column.
    assert t.count("viz-cols__col") == 10


def test_spend_dashboard_daily_burn_and_outcomes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _seed_world()
    t = _client().get("/spend").text
    assert "Daily burn — last 30 days" in t
    assert t.count("viz-cols__col") == 30
    assert "Run outcomes" in t and "Succeeded" in t and "Failed" in t
    assert "Token mix" in t and "10,000" in t


def test_workforce_dashboard_department_delivery(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _seed_world()
    t = _client().get("/workforce").text
    assert "Delivery by department" in t and "Spend by department" in t
    # The legal_privacy goal rolls up to its business suite by title.
    assert "1 of 1" in t and "$1.25" in t


def test_workforce_empty_record_skips_charts(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    t = _client().get("/workforce").text
    assert "Delivery by department" not in t   # honest: no record, no chart
