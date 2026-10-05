"""Every approval check is recorded without content, for a later threshold preview."""
from __future__ import annotations

import pytest
from maverick import approval_checks, config, world_model
from maverick.safety import consent
from maverick.world_model import WorldModel

DAY = 86_400.0
COMMAND = "rm -rf /srv/payroll/2026-q3"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_CONSENT_MODE", raising=False)
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    (tmp_path / "config.toml").write_text("")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(approval_checks, "_pruned_at", {})
    config.reset_config_cache()
    world = WorldModel(tmp_path / "world.db")
    yield world
    world.close()
    config.reset_config_cache()


def _all(world):
    return approval_checks.checks_since(world, 0)


@pytest.mark.parametrize(("mode", "outcome"), [("auto-approve", "allowed"), ("auto-deny", "refused")])
def test_the_gate_records_each_decision_without_content(world, monkeypatch, mode, outcome):
    monkeypatch.setenv("MAVERICK_CONSENT_MODE", mode)
    goal_id = world.create_goal("Payroll cleanup", owner="user:a")
    world.set_goal_domain(goal_id, "finance_close")
    from maverick.logging_config import reset_goal_context, set_goal_context

    tokens = set_goal_context(goal_id=goal_id)
    try:
        consent.require_consent("shell", risk="high", scope="/srv/payroll", detail=COMMAND)
    finally:
        reset_goal_context(tokens)
    (row,) = _all(world)
    assert row["risk"] == "high" and row["action_kind"] == "shell"
    assert row["agent"] == "finance_close" and row["outcome"] == outcome
    stored = world._read_all("SELECT * FROM approval_checks", ())
    text = repr([dict(r) for r in stored])
    for secret in (COMMAND, "/srv/payroll", "Payroll cleanup", "user:a"):
        assert secret not in text


def test_a_person_being_asked_reads_as_asked():
    assert approval_checks.outcome_for(True, "dashboard") == "asked"
    assert approval_checks.outcome_for(False, "prompt") == "asked"
    assert approval_checks.outcome_for(False, "no-decision") == "asked"
    assert approval_checks.outcome_for(True, "ledger") == "allowed"
    assert approval_checks.outcome_for(False, "non-tty-deny") == "refused"


def test_free_text_never_lands_in_the_kind_or_agent(world):
    approval_checks.record_check(world, risk="HIGH", action="send this email to bob",
                                 outcome="allowed", agent="Bob's private task")
    (row,) = _all(world)
    assert row == {**row, "risk": "high", "action_kind": "other", "agent": ""}
    with pytest.raises(ValueError):
        approval_checks.record_check(world, risk="low", action="shell", outcome="maybe")


def test_recording_can_be_turned_off(world, monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text("[approval]\nrecord_checks = false\n")
    config.reset_config_cache()
    monkeypatch.setenv("MAVERICK_CONSENT_MODE", "auto-approve")
    consent.require_consent("shell", risk="low")
    assert _all(world) == []


def test_a_recording_failure_never_changes_the_decision(world, monkeypatch):
    monkeypatch.setenv("MAVERICK_CONSENT_MODE", "auto-approve")

    def broken(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(approval_checks, "record_check", broken)
    assert consent.require_consent("shell", risk="low").granted is True


def test_old_rows_are_pruned_in_bounded_batches(world):
    now = 1_000 * DAY
    for age in range(5):
        approval_checks.record_check(world, risk="low", action="shell", outcome="allowed",
                                     now=now - (100 + age) * DAY)
    approval_checks.record_check(world, risk="low", action="shell", outcome="asked", now=now - DAY)
    assert approval_checks.prune(world, now=now, batch=2) == 2
    assert approval_checks.prune(world, now=now, batch=10) == 3
    assert approval_checks.prune(world, now=now) == 0
    assert [r["outcome"] for r in _all(world)] == ["asked"]


def test_retention_defaults_to_90_days_and_follows_the_setting(world, tmp_path):
    assert approval_checks.retention_days() == 90
    assert approval_checks.enabled() is True
    (tmp_path / "config.toml").write_text("[approval]\ncheck_retention_days = 30\n")
    config.reset_config_cache()
    assert approval_checks.retention_days() == 30
    (tmp_path / "config.toml").write_text("[approval]\ncheck_retention_days = 0\n")
    config.reset_config_cache()
    assert approval_checks.retention_days() == 90


def test_the_read_function_returns_this_workspace_since_a_time(world, monkeypatch):
    approval_checks.record_check(world, risk="low", action="shell", outcome="allowed", now=10.0)
    approval_checks.record_check(world, risk="high", action="email.send", outcome="asked", now=20.0)
    monkeypatch.setattr(approval_checks, "_workspace", lambda: "other")
    approval_checks.record_check(world, risk="high", action="shell", outcome="refused", now=30.0)
    assert [r["outcome"] for r in approval_checks.checks_since(world, 0)] == ["refused"]
    monkeypatch.setattr(approval_checks, "_workspace", lambda: "")
    rows = approval_checks.checks_since(world, 15.0)
    assert rows == [{"checked_at": 20.0, "risk": "high", "action_kind": "email.send",
                     "agent": "", "outcome": "asked"}]
