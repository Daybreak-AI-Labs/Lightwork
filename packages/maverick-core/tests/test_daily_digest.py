"""The daily summary: composition per person, time zones, empty-skip, opt-out,
send audit and the sender hook."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_DAILY_DIGEST", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    yield
    config.reset_config_cache()


@pytest.fixture
def recorded(monkeypatch):
    import maverick.audit as audit

    rows: list[tuple[str, dict]] = []
    monkeypatch.setattr(audit, "record", lambda kind, **p: rows.append((kind, p)) or True)
    return rows


def _world(tmp_path):
    from maverick.world_model import WorldModel

    return WorldModel(tmp_path / "world.db")


def _seed(world):
    """Alice owns a finished goal with a question and spend; Bob owns another."""
    mine = world.create_goal("Weekly forecast", owner="user:alice")
    theirs = world.create_goal("Bob's audit", owner="user:bob")
    for goal in (mine, theirs):
        world.set_goal_status(goal, "done", result="All good.")
    ep = world.start_episode(mine)
    world.end_episode(ep, summary="s", outcome="ok", cost_dollars=1.25, input_tokens=10)
    world.ask("Which quarter?", goal_id=mine)
    world.ask("Which vendor?", goal_id=theirs)
    approved_by_me = world.create_approval("wire_funds", risk="high", detail="d",
                                          requested_by="user:alice")
    other = world.create_approval("delete_files", risk="medium", detail="d",
                                  requested_by="user:bob")
    return {"mine": mine, "theirs": theirs, "requested": approved_by_me, "other": other}


def test_compose_for_a_person_who_may_decide(tmp_path):
    from maverick.daily_digest import compose_digest

    world = _world(tmp_path)
    ids = _seed(world)
    digest = compose_digest(world, "user:alice", can_decide=True)
    assert [a["id"] for a in digest["approvals"]] == [ids["requested"], ids["other"]]
    assert [a["waiting_on"] for a in digest["approvals"]] == ["someone else", "you"]
    assert [r["id"] for r in digest["results"]] == [ids["mine"]]
    assert [q["question"] for q in digest["questions"]] == ["Which quarter?"]
    assert digest["spending"]["dollars"] == pytest.approx(1.25)
    assert digest["counts"] == {"approvals": 2, "results": 1, "questions": 1, "expiring_keys": 0}
    assert digest["empty"] is False


def test_compose_for_a_viewer_and_an_administrator(tmp_path):
    from maverick.daily_digest import compose_digest

    world = _world(tmp_path)
    ids = _seed(world)
    viewer = compose_digest(world, "user:alice", can_decide=False)
    assert [a["id"] for a in viewer["approvals"]] == [ids["requested"]]
    assert viewer["approvals"][0]["waiting_on"] == "someone else"
    admin = compose_digest(world, "user:root", can_decide=True, sees_everything=True)
    assert {r["id"] for r in admin["results"]} == {ids["mine"], ids["theirs"]}
    assert len(admin["questions"]) == 2
    nobody = compose_digest(world, "user:carol", can_decide=False)
    assert nobody["empty"] is True


def test_spending_this_month_follows_the_persons_time_zone(tmp_path):
    from maverick.daily_digest import compose_digest, month_start

    world = _world(tmp_path)
    goal = world.create_goal("Old", owner="user:alice")
    world.set_goal_status(goal, "done")
    # 1 Oct 2026 00:30 in Auckland is still 30 Sep in UTC.
    now = datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc).timestamp()
    old = world.start_episode(goal)
    world.end_episode(old, summary="s", outcome="ok", cost_dollars=3.0)
    with world._writing() as conn:  # backdate the run to 30 Sep 10:00 UTC
        conn.execute("UPDATE episodes SET started_at = ? WHERE id = ?",
                     (datetime(2026, 9, 30, 10, tzinfo=timezone.utc).timestamp(), old))
    utc = compose_digest(world, "user:alice", can_decide=False, now=now, time_zone="UTC",
                         spend_limit=2.0)
    assert utc["spending"]["dollars"] == pytest.approx(3.0) and utc["spending"]["over_limit"]
    nz = compose_digest(world, "user:alice", can_decide=False, now=now,
                        time_zone="Pacific/Auckland", spend_limit=2.0)
    assert nz["spending"]["dollars"] == 0 and nz["spending"]["month_label"] == "October 2026"
    assert datetime.fromtimestamp(month_start(now, "Pacific/Auckland"),
                                  tz=ZoneInfo("Pacific/Auckland")).day == 1


def test_spend_limit_comes_from_config(tmp_path):
    from maverick.config import get_my_work
    from maverick.daily_digest import compose_digest

    (tmp_path / "config.toml").write_text(
        "[my_work]\ndigest_enable = true\ndigest_hour = 7\nmonthly_spend_limit_dollars = 42\n")
    section = get_my_work()
    assert section["digest_enable"] is True and section["digest_hour"] == 7
    assert section["digest_poll_seconds"] == 300
    digest = compose_digest(_world(tmp_path), "user:a", can_decide=False)
    assert digest["spending"]["limit_dollars"] == 42


def test_render_text_and_html(tmp_path):
    from maverick.daily_digest import compose_digest, render_html, render_text

    world = _world(tmp_path)
    _seed(world)
    world.ask("<b>Which</b> quarter?", goal_id=world.create_goal("X", owner="user:alice"))
    digest = compose_digest(world, "user:alice", can_decide=True, spend_limit=10)
    text = render_text(digest, dashboard_url="https://lw.test/")
    assert "waiting for your decision" in text and "https://lw.test/me" in text
    assert "$1.25 of your $10.00 monthly limit" in text
    html = render_html(digest, dashboard_url="https://lw.test/")
    assert "&lt;b&gt;Which&lt;/b&gt;" in html and "<b>Which</b>" not in html
    empty = compose_digest(world, "user:nobody", can_decide=False)
    assert "Nothing is waiting" in render_text(empty)


def test_due_now_respects_hour_time_zone_and_opt_out():
    from maverick.daily_digest import due_now

    # 07:30 UTC = 08:30 in Berlin (CEST): due in Berlin, not yet in Reykjavik (UTC).
    now = datetime(2026, 7, 1, 7, 30, tzinfo=timezone.utc).timestamp()
    berlin = {"time_zone": "Europe/Berlin", "digest_hour": 8, "digest_enabled": True}
    london = {"time_zone": "Atlantic/Reykjavik", "digest_hour": 8, "digest_enabled": True}
    assert due_now(berlin, now=now) == (True, "2026-07-01")
    assert due_now(london, now=now) == (False, "2026-07-01")
    assert due_now({**berlin, "last_digest_date": "2026-07-01"}, now=now)[0] is False
    assert due_now({**berlin, "digest_enabled": False}, now=now)[0] is False
    late = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc).timestamp()
    assert due_now(berlin, now=late)[0] is True  # a paused scheduler still sends today


def test_run_cycle_sends_once_skips_empty_and_audits(tmp_path, recorded, monkeypatch):
    from maverick import channel_identities, daily_digest, user_preferences

    world = _world(tmp_path)
    _seed(world)
    sent = []
    daily_digest.register_digest_sender("teams", lambda p, d, links, prefs: sent.append(("teams", p, d["counts"])) or True)
    monkeypatch.setattr(daily_digest, "_send_slack",
                        lambda p, d, links, prefs: sent.append(("slack", p, d["counts"])) or True)
    try:
        channel_identities.link("slack", "U1", "user:alice", actor="admin")
        channel_identities.link("teams", "T-1", "user:alice", actor="admin")
        channel_identities.link("slack", "U2", "user:carol", actor="admin")  # nothing waiting
        user_preferences.set_preferences("user:dave", digest_enabled=False)
        now = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc).timestamp()
        people = ["user:alice", "user:carol", "user:dave", "user:erin"]
        summary = daily_digest.run_digest_cycle(
            world, principals=people, can_decide=lambda p: p == "user:alice", now=now)
        assert summary == {"checked": 4, "sent": 1, "skipped_empty": 1,
                           "skipped_unlinked": 1, "failed": 0}
        assert sorted(s[0] for s in sent) == ["slack", "teams"]
        assert all(s[1] == "user:alice" for s in sent)
        notes = [p for k, p in recorded if k == "digest_sent"]
        assert {n["channel"] for n in notes} == {"slack", "teams"}
        assert all(n["recipient"] == "user:alice" and n["ok"] and n["approvals"] == 2 for n in notes)
        assert not any("wire" in str(n).lower() for n in notes)  # no content in the audit
        again = daily_digest.run_digest_cycle(
            world, principals=people, can_decide=lambda p: True, now=now + 60)
        assert again["sent"] == 0 and len(sent) == 2  # once a day
        user_preferences.set_preferences("user:carol", send_when_empty=True)
        user_preferences.mark_digest_sent("user:carol", "")
        third = daily_digest.run_digest_cycle(
            world, principals=["user:carol"], can_decide=lambda p: False, now=now + 120)
        assert third["sent"] == 1 and sent[-1][1] == "user:carol"
    finally:
        daily_digest.register_digest_sender("teams", None)


def test_email_digest_uses_linked_address_or_profile_email(tmp_path, recorded, monkeypatch):
    from maverick import daily_digest, notifications, user_preferences

    world = _world(tmp_path)
    _seed(world)
    mails = []
    monkeypatch.setattr(notifications, "send_email",
                        lambda to, subject, text, html_body=None, cfg=None: mails.append((to, subject, html_body is not None)) or True)
    user_preferences.set_preferences("user:alice", email="alice@example.com")
    prefs = user_preferences.get_preferences("user:alice")
    digest = daily_digest.compose_digest(world, "user:alice", can_decide=True)
    outcomes = daily_digest.send_digest("user:alice", digest, links=[], prefs=prefs)
    assert outcomes == [{"channel": "email", "ok": True}]
    assert mails[0][0] == "alice@example.com" and mails[0][2] is True
    assert "summary" in mails[0][1].lower()
    outcomes = daily_digest.send_digest(
        "user:alice", digest, links=[{"channel": "email", "external_id": "work@example.com"}],
        prefs={"email": ""})
    assert mails[-1][0] == "work@example.com" and outcomes[0]["ok"]


def test_slack_digest_sends_a_dm_with_blocks(tmp_path, monkeypatch):
    from maverick import daily_digest, slack_interactive

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-not-a-real-token")
    dms = []
    monkeypatch.setattr(slack_interactive, "send_dm",
                        lambda token, user, text, blocks=None: dms.append((token, user, blocks)) or True)
    digest = daily_digest.compose_digest(_world(tmp_path), "user:a", can_decide=False)
    ok = daily_digest._send_slack("user:a", digest, [{"channel": "slack", "external_id": "U7"}], {})
    assert ok and dms[0][1] == "U7" and dms[0][2][0]["type"] == "header"
    monkeypatch.delenv("SLACK_BOT_TOKEN")
    assert daily_digest._send_slack("user:a", digest, [{"channel": "slack", "external_id": "U7"}], {}) is False


def test_expiring_access_keys_section_is_optional(tmp_path, monkeypatch):
    import sys
    import types

    from maverick import daily_digest
    from maverick.slack_interactive import digest_blocks

    world = _world(tmp_path)
    now = 1_800_000_000.0
    assert daily_digest.compose_digest(world, "user:a", can_decide=False, now=now)["expiring_keys"] == []

    class Key:
        def __init__(self, label, owner, days):
            self.id, self.label, self.owner, self.prefix = label, label, owner, "lw_" + label[:3]
            self.expires_at = now + days * 86400

        def usable(self, at):
            return at < self.expires_at

    keys = [Key("deploy", "user:a", 2), Key("later", "user:a", 20), Key("bobs", "user:b", 1)]
    fake = types.ModuleType("maverick_dashboard.api_keys")
    fake.list_keys = lambda owner=None: [k for k in keys if owner is None or k.owner == owner]
    fake.expiring_soon = lambda rows, within_days=7, now=None: [
        k for k in rows if k.usable(now) and k.expires_at <= now + within_days * 86400]
    monkeypatch.setitem(sys.modules, "maverick_dashboard.api_keys", fake)
    mine = daily_digest.compose_digest(world, "user:a", can_decide=False, now=now)
    assert [k["label"] for k in mine["expiring_keys"]] == ["deploy"]
    assert mine["expiring_keys"][0]["days_left"] == 2 and mine["counts"]["expiring_keys"] == 1
    assert mine["empty"] is False
    everyone = daily_digest.compose_digest(world, "user:root", can_decide=True,
                                           sees_everything=True, now=now)
    assert [k["label"] for k in everyone["expiring_keys"]] == ["bobs", "deploy"]
    text = daily_digest.render_text(mine)
    assert "access key(s) stop working within 7 days" in text and "deploy (in 2 day(s))" in text
    assert "deploy" in daily_digest.render_html(mine)
    assert "stop working within 7 days" in digest_blocks(mine)[1]["text"]["text"]
    fake.list_keys = lambda owner=None: (_ for _ in ()).throw(RuntimeError("store down"))
    assert daily_digest.compose_digest(world, "user:a", can_decide=False, now=now)["expiring_keys"] == []
