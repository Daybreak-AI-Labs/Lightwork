"""Practice mode keeps practised work apart and lets nothing leave Lightwork (ONB-4)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    from maverick import config

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    (tmp_path / "config.toml").write_text("", encoding="utf-8")
    config.reset_config_cache()
    yield tmp_path
    config.reset_config_cache()


def test_practice_is_off_unless_a_context_turns_it_on(home):
    from maverick import practice

    assert practice.active() is False
    with practice.scope():
        assert practice.active() is True
    assert practice.active() is False


def test_practised_tasks_use_a_separate_world_database(home, monkeypatch):
    from maverick import practice, world_model

    live = world_model.default_db_path()
    with practice.scope():
        practised = world_model.default_db_path()
        assert practised != live and "practice" in str(practised)
        world = world_model.world_for_tenant("acme")
        assert "practice" in str(world.path)
    assert "practice" not in str(world_model.world_for_tenant("acme").path)
    # A test or embedder that points the world elsewhere gets a practice file beside it.
    monkeypatch.setattr(world_model, "DEFAULT_DB", home / "custom.db")
    with practice.scope():
        assert world_model.default_db_path() == home / "custom.practice.db"


def test_only_local_reads_and_coordination_run_in_practice(home):
    from maverick import practice
    from maverick.agent import Agent

    fake = SimpleNamespace(ctx=SimpleNamespace(goal_id=7))
    assert Agent._practice_denial(fake, "send_email", {"to": "pat@example.com"}) is None  # live: no gate
    with practice.scope():
        for tool in ("read_file", "list_dir", "ask_user", "spawn_subagent"):
            assert Agent._practice_denial(fake, tool, {}) is None
        for tool in ("send_email", "slack", "send_payment", "shell", "http_fetch", "web_search",
                     "write_file", "gl_read_journal", "some_new_connector"):
            refusal = Agent._practice_denial(fake, tool, {"to": "pat@example.com"})
            assert refusal and "did not run" in refusal and "Nothing was sent" in refusal
    held = practice.outbox()
    assert [row["name"] for row in held][:2] == ["some_new_connector", "gl_read_journal"]
    assert all(row["goal_id"] == 7 and row["kind"] == "tool" for row in held)


def test_alerts_email_webhooks_and_approval_cards_are_recorded_not_sent(home, monkeypatch):
    import smtplib

    from maverick import (
        approval_notify,
        notifications,
        personal_notification_delivery,
        practice,
        webhooks,
    )

    def boom(*args, **kwargs):  # any real transport is a leak
        raise AssertionError("a practice delivery reached a real transport")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    monkeypatch.setattr(smtplib, "SMTP_SSL", boom)
    monkeypatch.setattr(webhooks, "_post", boom)
    monkeypatch.setattr(webhooks, "_post_result", boom)
    monkeypatch.setattr(notifications, "_dispatch", boom)
    monkeypatch.setattr(approval_notify, "install_announcers", boom)
    with practice.scope():
        assert notifications.send_email("pat@example.com", "Invoice paid", "Body") is False
        assert notifications.notify("Your task finished", title="Lightwork", async_dispatch=False) == 0
        assert webhooks.fire("goal.finished", {"goal": 1}, urls=["https://hooks.example.com/x"]) == 0
        assert approval_notify.approval_created(5) is None
        assert personal_notification_delivery.dispatch("user:pat", {"items": [{"id": 1}]},
                                                       {"mode": "all"}) is None
    kinds = [row["kind"] for row in practice.outbox()]
    assert kinds == ["personal message", "approval card", "webhook", "alert", "email"]


def test_practice_is_unavailable_when_turned_off_or_on_another_database(home, monkeypatch):
    from maverick import config, practice, world_model_backends

    assert practice.unavailable_reason() == ""
    monkeypatch.setattr(world_model_backends, "is_postgres_configured", lambda: True)
    assert "built-in database" in practice.unavailable_reason()
    monkeypatch.setattr(world_model_backends, "is_postgres_configured", lambda: False)
    from maverick import runner

    class Queue:
        def submit(self, *args, **kwargs):
            return "queued"

    monkeypatch.setattr(runner, "_dispatcher", Queue())
    assert "separate workers" in practice.unavailable_reason()
    monkeypatch.undo()
    (home / "config.toml").write_text("[practice]\nenable = false\n", encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(home / "mh"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(home / "config.toml"))
    config.reset_config_cache()
    assert practice.enabled() is False
    assert practice.unavailable_reason() == "Practice mode is turned off in Settings."
