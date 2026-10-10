"""Undo window for dashboard approvals.

The world model can return a just-decided approval to pending; the consent
consumer waits out ``[approval] undo_grace_seconds`` before acting so an
undo never races an action that already started.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def wm(tmp_path, monkeypatch):
    import maverick.world_model as world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    w = world_model.WorldModel(tmp_path / "world.db")
    monkeypatch.setattr(world_model, "open_world", lambda *a, **k: w)
    yield w
    try:
        w.close()
    except Exception:  # the consent consumer may already have closed it
        pass


def _decide(wm, aid, status, who="u:bob"):
    event = wm.decide_approval_audited(aid, status, decided_by=who)
    assert event is not None
    assert wm.mark_approval_audit_delivered(event.event_id)
    return event


def test_undo_outcomes_and_round_bumped_vote_identity(wm):
    aid = wm.create_approval("wire-funds", risk="high", undoable=True)
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "not_decided"
    assert wm.undo_approval_decision(999, undone_by="u:bob", within_seconds=30) == "not_found"

    first = _decide(wm, aid, "approved")
    assert wm.get_approval(aid).status == "approved"
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=0) == "too_late"
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "undone"
    row = wm.get_approval(aid)
    assert row.status == "pending" and row.decided_at is None and row.decided_by is None
    assert wm.approval_state(aid)["approved_count"] == 0

    # The same supervisor casting the same vote again is a new decision.
    second_id = wm.approval_audit_event_id(aid, "approved", "u:bob")
    assert second_id != first.event_id
    second = _decide(wm, aid, "approved")
    assert second.event_id == second_id and second.delivered_at is None
    assert wm.get_approval(aid).status == "approved"
    assert wm.get_approval_audit_event(first.event_id).final_status is None


def test_undo_round_ids_are_stable_and_distinct():
    from maverick.world_model import _approval_audit_event_id

    base = _approval_audit_event_id("t", 7, "approved", "u:bob")
    assert base == _approval_audit_event_id("t", 7, "approved", "u:bob", 0)
    later = _approval_audit_event_id("t", 7, "approved", "u:bob", 1)
    assert later != base and later != _approval_audit_event_id("t", 7, "approved", "u:bob", 2)


def test_grace_reader_defaults_and_bounds(monkeypatch):
    from maverick.safety import consent

    monkeypatch.delenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", raising=False)
    monkeypatch.setattr("maverick.config.load_config", dict)
    assert consent.approval_undo_grace_seconds() == 30.0
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "-4")
    assert consent.approval_undo_grace_seconds() == 0.0
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "junk")
    assert consent.approval_undo_grace_seconds() == 30.0
    monkeypatch.setenv("MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT", "off")
    assert consent.approval_requires_acknowledgement() is False
    monkeypatch.setenv("MAVERICK_APPROVAL_REQUIRE_ACKNOWLEDGEMENT", "yes")
    assert consent.approval_requires_acknowledgement() is True


def _wait_for_pending(wm):
    for _ in range(100):
        rows = wm.pending_approvals()
        if rows:
            return rows[0].id
        time.sleep(0.05)
    raise AssertionError("consent never queued the approval")


def test_consumer_waits_out_the_undo_window(wm, monkeypatch):
    from maverick.safety import consent

    monkeypatch.setenv("MAVERICK_CONSENT_DASHBOARD_TIMEOUT", "15")
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "1.5")
    decided_at = {}

    def approve():
        aid = _wait_for_pending(wm)
        _decide(wm, aid, "approved")
        decided_at["t"] = time.time()

    t = threading.Thread(target=approve, daemon=True)
    t.start()
    decision = consent._decide_via_dashboard("rm-rf", "high", "/tmp/x", "d", "test")
    t.join()
    assert decision is not None and decision.granted and decision.source == "dashboard"
    assert decision.actor == "u:bob"
    assert time.time() - decided_at["t"] >= 1.4


def test_undo_during_the_window_keeps_the_consumer_waiting(wm, monkeypatch, tmp_path):
    from maverick.safety import consent

    monkeypatch.setenv("MAVERICK_CONSENT_DASHBOARD_TIMEOUT", "2.5")
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "5")

    def approve_then_undo():
        aid = _wait_for_pending(wm)
        _decide(wm, aid, "approved")
        time.sleep(0.4)
        assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=5) == "undone"

    t = threading.Thread(target=approve_then_undo, daemon=True)
    t.start()
    started = time.monotonic()
    decision = consent._decide_via_dashboard("rm-rf", "high", "/tmp/x", "d", "test")
    t.join()
    assert decision is None                      # nobody decided in time
    assert time.monotonic() - started >= 2.4
    import maverick.world_model as world_model

    fresh = world_model.WorldModel(tmp_path / "world.db")   # consent closed ``wm``
    assert fresh.pending_approvals()[0].status == "pending"
    fresh.close()


def test_zero_grace_acts_at_once(wm, monkeypatch):
    from maverick.safety import consent

    monkeypatch.setenv("MAVERICK_CONSENT_DASHBOARD_TIMEOUT", "15")
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "0")
    decided_at = {}

    def deny():
        aid = _wait_for_pending(wm)
        _decide(wm, aid, "denied")
        decided_at["t"] = time.time()

    t = threading.Thread(target=deny, daemon=True)
    t.start()
    decision = consent._decide_via_dashboard("rm-rf", "high", "/tmp/x", "d", "test")
    t.join()
    assert decision is not None and not decision.granted
    assert time.time() - decided_at["t"] < 1.5


def _park(wm):
    from maverick.safety import consent

    aid, _card = consent._park_approval(wm, "rm-rf", "high", "/tmp/x", "d", "test", None)
    return aid


def test_a_request_nobody_waits_on_cannot_be_undone(wm):
    # Settings, codebase merges, data-use checks and agent credentials act on
    # "approved" at once. Undoing one would show a request as waiting again
    # after its effect already happened.
    aid = wm.create_approval("codebase_merge", risk="high", provenance="codebase")
    _decide(wm, aid, "approved")
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "final"
    assert wm.get_approval(aid).status == "approved"


@pytest.mark.parametrize("by", ["system:task-stopped", "system:no-decision"])
def test_a_system_withdrawal_cannot_be_undone(wm, by):
    from maverick.safety import consent

    aid = _park(wm)
    consent._withdraw_approval(wm, aid, by)
    assert wm.get_approval(aid).status == "denied"
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "final"
    assert wm.get_approval(aid).status == "denied"


def test_a_recovery_denial_cannot_be_undone(wm):
    aid = _park(wm)
    with wm._writing() as conn:   # the statement workspace_recovery runs after a restore
        conn.execute(
            "UPDATE approvals SET status='denied', decided_at=?, decided_by=?, "
            "claimed_by=NULL, claimed_at=NULL WHERE status IN ('pending','approved')",
            (time.time(), "system:bootstrap-recovery"),
        )
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "final"


def test_an_undo_after_the_consumer_acted_is_refused_whatever_the_window(wm, monkeypatch, tmp_path):
    # The waiting task and the page that offers Undo can read different
    # windows (a setting changed mid-wait, or another process's environment).
    # Once the task has acted, no window reopens the decision.
    from maverick.safety import consent

    monkeypatch.setenv("MAVERICK_CONSENT_DASHBOARD_TIMEOUT", "15")
    monkeypatch.setenv("MAVERICK_APPROVAL_UNDO_GRACE_SECONDS", "0")
    seen = {}

    def approve():
        seen["aid"] = _wait_for_pending(wm)
        _decide(wm, seen["aid"], "approved")

    t = threading.Thread(target=approve, daemon=True)
    t.start()
    decision = consent._decide_via_dashboard("rm-rf", "high", "/tmp/x", "d", "test")
    t.join()
    assert decision is not None and decision.granted
    import maverick.world_model as world_model

    fresh = world_model.WorldModel(tmp_path / "world.db")   # consent closed ``wm``
    try:
        assert fresh.undo_approval_decision(seen["aid"], undone_by="u:bob", within_seconds=300) == "too_late"
        assert fresh.get_approval(seen["aid"]).status == "approved"
    finally:
        fresh.close()


def test_an_undo_tells_webhook_subscribers(wm, monkeypatch):
    from maverick import webhook_events

    sent = []
    monkeypatch.setattr(webhook_events, "emit", lambda event, **kw: sent.append((event, kw)))
    aid = _park(wm)
    _decide(wm, aid, "approved")
    assert wm.undo_approval_decision(aid, undone_by="u:bob", within_seconds=30) == "undone"
    (undone,) = [kw for event, kw in sent if event == "approval.undone"]
    assert undone["record_id"] == str(aid)
    assert undone["payload"]["previous_status"] == "approved" and undone["payload"]["undone_by"] == "u:bob"
    assert "approval.undone" in webhook_events.EVENT_TYPES
