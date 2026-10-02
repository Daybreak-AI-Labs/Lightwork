"""Pre-upgrade event work cannot cross either local or remote start boundary."""
from __future__ import annotations

import pytest
from maverick import event_admission
from maverick.event_admission import LegacyEventGoalHeld
from maverick.world_model import WorldModel


def _imap_binding(monkeypatch):
    from maverick_dashboard import event_poll, event_triggers_store, triggers_store

    trigger = {
        "name": "orders", "created": 1.0, "tenant": "", "owner": "user:alice",
        "source": "imap_email", "config": {
            "host": "mail.example.test", "username": "bot@example.test",
            "password_env": "SYNTHETIC_MAIL_PASSWORD",  # pragma: allowlist secret
            "trusted_authserv_id": "mx.example.test",
            "allowed_senders": "alice@example.test",
        },
        "params": {}, "template": "orders-template", "template_revision": "saved-revision",
        "flow": "", "flow_owner": "", "flow_revision": "",
    }
    current = [trigger]
    monkeypatch.setattr(
        event_triggers_store, "get_trigger",
        lambda name, *, tenant=None, strict=False: current[0] if name == "orders" else None,
    )
    monkeypatch.setattr(triggers_store, "bound_template", lambda _trigger: object())
    return current, event_poll._trigger_key(trigger)


def _admit_imap(world, generation, suffix):
    goal, _state = event_admission.admit(
        world, f"verified-email-{suffix}", f"Email {suffix}", "Verified sender",
        owner="user:alice", origin="orders", trigger_key=generation,
        origin_kind="event_v2",
    )
    return goal


def test_legacy_event_goal_is_held_at_both_execution_claims(tmp_path):
    with WorldModel(tmp_path / "world.db") as world:
        local = world.create_goal("old local")
        world.record_goal_origin(local, "event", "orders")
        with pytest.raises(LegacyEventGoalHeld):
            world.set_goal_status(local, "active")
        assert world.get_goal(local).status == "blocked"

        remote = world.create_goal("old remote", owner="user:alice")
        world.record_goal_origin(remote, "event", "orders")
        assert not world.claim_goal_for_run(remote, expected_owner="user:bob")
        assert world.get_goal(remote).status == "pending"
        assert not world.claim_goal_for_run(remote, expected_owner="user:alice")
        assert world.get_goal(remote).status == "blocked"


def test_current_and_unrelated_goal_origins_still_start(tmp_path, monkeypatch):
    _current, generation = _imap_binding(monkeypatch)
    with WorldModel(tmp_path / "world.db") as world:
        current = _admit_imap(world, generation, "remote")
        assert world.claim_goal_for_run(current)
        assert world.get_goal(current).status == "active"

        local = _admit_imap(world, generation, "local")
        world.set_goal_status(local, "active")
        assert world.get_goal(local).status == "active"

        scheduled = world.create_goal("scheduled")
        world.record_goal_origin(scheduled, "schedule", "daily")
        world.set_goal_status(scheduled, "active")
        assert world.get_goal(scheduled).status == "active"


def test_current_event_without_durable_receipt_is_held_at_both_execution_claims(
    tmp_path, monkeypatch,
):
    _imap_binding(monkeypatch)
    with WorldModel(tmp_path / "world.db") as world:
        local = world.create_goal("missing receipt", owner="user:alice")
        world.record_goal_origin(local, "event_v2", "orders")
        with pytest.raises(LegacyEventGoalHeld):
            world.set_goal_status(local, "active")
        assert world.get_goal(local).status == "blocked"

        remote = world.create_goal("missing remote receipt", owner="user:alice")
        world.record_goal_origin(remote, "event_v2", "orders")
        assert not world.claim_goal_for_run(remote, expected_owner="user:alice")
        assert world.get_goal(remote).status == "blocked"


def test_pending_imap_template_goal_is_held_after_sender_policy_or_trigger_changes(
    tmp_path, monkeypatch,
):
    current, generation = _imap_binding(monkeypatch)
    with WorldModel(tmp_path / "world.db") as world:
        changed = _admit_imap(world, generation, "changed")
        current[0] = {
            **current[0],
            "config": {**current[0]["config"], "allowed_senders": "bob@example.test"},
        }
        with pytest.raises(LegacyEventGoalHeld):
            world.set_goal_status(changed, "active")
        assert world.get_goal(changed).status == "blocked"

        current[0] = None
        deleted = _admit_imap(world, generation, "deleted")
        assert not world.claim_goal_for_run(deleted, expected_owner="user:alice")
        assert world.get_goal(deleted).status == "blocked"


def test_already_active_legacy_event_is_not_retroactively_blocked(tmp_path):
    with WorldModel(tmp_path / "world.db") as world:
        goal = world.create_goal("already started")
        world.set_goal_status(goal, "active")
        world.record_goal_origin(goal, "event", "orders")
        world.set_goal_status(goal, "running")
        assert world.get_goal(goal).status == "running"
