"""Deadline alerts: each (record, state, deadline) alerts exactly once, restarts
never re-send, lead times come from config, quiet hours hold non-urgent
alerts, and routing prefers the assignee over the workspace owner."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from maverick import deadline_alerts as da
from maverick.world_model import WorldModel

DAY = 86_400.0
HOUR = 3_600.0
# A fixed "now" at 10:00 local so due_today / quiet-hour maths is stable.
NOW = time.mktime((2026, 10, 5, 10, 0, 0, 0, 0, -1))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_PUBLIC_URL", raising=False)
    from maverick import config
    config.reset_config_cache()
    # Webhook emission is exercised on its own; keep it out of the alert tests.
    monkeypatch.setattr(da, "_emit_webhook", lambda alert, world: None)


@pytest.fixture
def world(tmp_path: Path):
    return WorldModel(path=tmp_path / "world.db")


def _config(**overrides):
    section = {"enabled": True, "owner": "Dana (privacy owner)"}
    section.update(overrides)
    return {"deadline_alerts": section}


def _deadline(record_type="dsar", record_id="R1", due_in=2 * DAY, assignee="", title="access request from a@b.com"):
    return da.Deadline(record_type, record_id, title, NOW + due_in, "/privacy/dsar/" + record_id, assignee)


def _run(world, deadlines, *, now=NOW, config=None, sent=None):
    sent = [] if sent is None else sent

    def notify(body, **kw):
        sent.append((kw.get("title"), body, kw.get("priority")))
        return 1

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(da, "collect", lambda **kw: list(deadlines))
        summary = da.run_cycle(now=now, world=world, config=config or _config(), notify=notify)
    return summary, sent


def test_due_soon_alert_goes_out_once(world):
    d = _deadline(due_in=2 * DAY - 60)  # inside the 2-day lead
    summary, sent = _run(world, [d])
    assert summary["sent"] == 1 and summary["alerts"][0]["state"] == "due_soon_2d"
    assert len(sent) == 1
    title, body, priority = sent[0]
    assert title.startswith("Due soon:") and "a@b.com" in body and "/privacy/dsar/R1" in body
    assert "For: Dana (privacy owner)" in body and priority == "default"
    # The same cycle again: nothing new.
    summary, sent = _run(world, [d], sent=sent)
    assert summary["sent"] == 0 and summary["already_sent"] == 1 and len(sent) == 1


def test_overdue_alert_goes_out_once_and_is_urgent(world):
    d = _deadline(due_in=-3 * DAY)
    summary, sent = _run(world, [d])
    assert summary["alerts"][0]["state"] == "overdue" and sent[0][2] == "high"
    assert "Overdue" in sent[0][0] and "was due 3 day(s) ago" in sent[0][1]
    summary, sent = _run(world, [d], now=NOW + HOUR, sent=sent)
    assert summary["sent"] == 0 and len(sent) == 1


def test_restart_does_not_resend(world, tmp_path):
    d = _deadline(due_in=DAY)
    _run(world, [d])
    # A fresh process opens the same database: the ledger, not memory, decides.
    again = WorldModel(path=tmp_path / "world.db")
    summary, sent = _run(again, [d])
    assert summary["sent"] == 0 and summary["already_sent"] == 1 and sent == []


def test_each_state_transition_alerts_once(world):
    d = _deadline(due_in=6 * DAY)
    s1, sent = _run(world, [d])                          # 7-day warning
    s2, sent = _run(world, [d], now=NOW + 5 * DAY, sent=sent)   # 2-day warning
    s3, sent = _run(world, [d], now=NOW + 6 * DAY - HOUR, sent=sent)  # due today (same local day)
    s4, sent = _run(world, [d], now=NOW + 7 * DAY, sent=sent)     # overdue
    assert [s["alerts"][0]["state"] for s in (s1, s2, s3, s4)] == [
        "due_soon_7d", "due_soon_2d", "due_today", "overdue"]
    assert len(sent) == 4


def test_extended_deadline_starts_over(world):
    d = _deadline(due_in=DAY)
    _run(world, [d])
    extended = da.Deadline(d.record_type, d.record_id, d.title, d.due_at + 10 * DAY, d.link)
    summary, sent = _run(world, [extended], now=NOW + 9 * DAY)
    assert summary["sent"] == 1 and summary["alerts"][0]["state"] == "due_soon_2d"


def test_lead_times_come_from_config(world):
    d = _deadline(due_in=13 * DAY)
    summary, _ = _run(world, [d])
    assert summary["sent"] == 0  # default leads are 7 and 2 days
    summary, sent = _run(world, [d], config=_config(lead_days={"dsar": [14, 3]}))
    assert summary["alerts"][0]["state"] == "due_soon_14d"


def test_incident_uses_hour_leads_and_ignores_quiet_hours(world):
    inc = da.Deadline("incident", "I1", "lost laptop", NOW + 3 * HOUR, "/privacy#incidents")
    dsar = _deadline(due_in=DAY)
    cfg = _config(quiet_hours="09:00-11:00")  # NOW is 10:00 local
    assert da.in_quiet_hours(NOW, da.settings(cfg))
    summary, sent = _run(world, [inc, dsar], config=cfg)
    assert summary["sent"] == 1 and summary["held_quiet"] == 1
    assert summary["alerts"][0]["state"] == "due_soon_4h" and sent[0][2] == "high"
    # Once quiet hours end the held alert goes out, still exactly once.
    summary, sent = _run(world, [inc, dsar], now=NOW + 2 * HOUR, config=cfg, sent=sent)
    assert summary["sent"] == 1 and summary["alerts"][0]["record_type"] == "dsar"


def test_quiet_hours_window_crossing_midnight():
    cfg = da.settings(_config(quiet_hours="22:00-07:00"))
    at_23 = time.mktime((2026, 10, 5, 23, 30, 0, 0, 0, -1))
    at_06 = time.mktime((2026, 10, 6, 6, 30, 0, 0, 0, -1))
    at_12 = time.mktime((2026, 10, 6, 12, 0, 0, 0, 0, -1))
    assert da.in_quiet_hours(at_23, cfg) and da.in_quiet_hours(at_06, cfg)
    assert not da.in_quiet_hours(at_12, cfg)


def test_assignee_routes_before_owner(world):
    mine = _deadline(record_id="A", due_in=DAY, assignee="Priya")
    nobody = _deadline(record_id="B", due_in=DAY)
    summary, sent = _run(world, [mine, nobody])
    by_id = {a["record_id"]: a for a in summary["alerts"]}
    assert by_id["A"]["recipient"] == "Priya" and by_id["A"]["recipient_kind"] == "assignee"
    assert by_id["B"]["recipient"] == "Dana (privacy owner)" and by_id["B"]["recipient_kind"] == "owner"
    assert any("For: Priya" in body for _t, body, _p in sent)


def test_digest_mode_sends_one_message_per_recipient(world):
    deadlines = [_deadline(record_id="A", due_in=DAY, assignee="Priya"),
                 _deadline(record_id="B", due_in=DAY, assignee="Priya"),
                 _deadline(record_id="C", due_in=-DAY)]
    summary, sent = _run(world, deadlines, config=_config(mode="digest"))
    assert summary["sent"] == 3 and len(sent) == 2
    priya = next(m for m in sent if m[0] == "Deadlines for Priya")
    assert "2 deadline(s)" in priya[1] and priya[1].count("\n- ") == 2
    assert all(row["mode"] == "digest" for row in da.alerts_for("dsar", "A", world=world))


def test_alerts_for_record_and_recent(world):
    _run(world, [_deadline(record_id="Z", due_in=DAY)])
    rows = da.alerts_for("dsar", "Z", world=world)
    assert len(rows) == 1 and rows[0]["state"] == "due_soon_2d" and rows[0]["backends"] == 1
    assert da.recent_alerts(world=world)[0]["record_id"] == "Z"
    assert da.alerts_for("dsar", "other", world=world) == []


def test_disabled_sends_nothing(world):
    summary, sent = _run(world, [_deadline(due_in=-DAY)], config=_config(enabled=False))
    assert summary == {"evaluated": 0, "sent": 0, "held_quiet": 0, "already_sent": 0,
                       "alerts": [], "enabled": False} and sent == []


def test_settings_defaults_and_bounds(monkeypatch):
    cfg = da.settings({"deadline_alerts": {"poll_seconds": 5, "mode": "weird",
                                           "lead_days": {"dsar": ["9", "x", "3"]},
                                           "lead_hours": {"incident": [48]}}})
    assert cfg["poll_seconds"] == 60 and cfg["mode"] == "immediate"
    assert cfg["lead_days"]["dsar"] == [9, 3] and cfg["lead_days"]["vendor"] == [30, 7]
    assert cfg["lead_hours"]["incident"] == [48] and cfg["lead_hours"]["approval"] == [24, 4]
    # Enabled follows the privacy register when unset.
    monkeypatch.setattr("maverick.privacy_ops.enabled", lambda: False)
    assert da.settings({})["enabled"] is False


def test_collects_real_dsar_and_incident_deadlines(monkeypatch):
    from maverick import audit, privacy_ops
    monkeypatch.setattr(audit, "record", lambda *a, **k: True)
    from maverick import webhook_events
    monkeypatch.setattr(webhook_events, "emit", lambda *a, **k: 0)
    req = privacy_ops.open_dsar("a@b.com", "access", opened_by="t")
    inc = privacy_ops.open_incident("laptop", severity="high", reported_by="t")
    found = {(d.record_type, d.record_id): d for d in da.collect(now=time.time())}
    assert found[("dsar", req["id"])].due_at == pytest.approx(req["due_at"])
    assert found[("dsar", req["id"])].link == "/privacy/dsar/" + req["id"]
    assert found[("incident", inc["id"])].due_at == pytest.approx(inc["notify_deadline_at"])
    privacy_ops.close_dsar(req["id"], closed_by="t")
    privacy_ops.decide_incident_notification(inc["id"], False, rationale="no personal data", decided_by="t")
    assert not [d for d in da.collect(now=time.time()) if d.record_type in ("dsar", "incident")]


def test_webhook_events_fire_for_due_and_overdue(world, monkeypatch):
    monkeypatch.undo()  # restore the real _emit_webhook
    from maverick import webhook_events
    events = []
    monkeypatch.setattr(webhook_events, "emit", lambda ev, **kw: events.append((ev, kw["payload"]["state"])) or 1)
    monkeypatch.setattr(da, "collect", lambda **kw: [_deadline(record_id="A", due_in=DAY),
                                                     _deadline(record_id="B", due_in=-DAY),
                                                     da.Deadline("approval", "9", "deploy", NOW - 1, "/approvals")])
    da.run_cycle(now=NOW, world=world, config=_config(), notify=lambda *a, **k: 1)
    assert ("dsar.due_soon", "due_soon_2d") in events
    assert ("dsar.overdue", "overdue") in events
    assert ("approval.expired", "overdue") in events
