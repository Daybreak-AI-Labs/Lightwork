"""The daily summary scheduler: config-gated, leased, backs off, stops cleanly."""
from __future__ import annotations

import threading

import pytest


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from maverick import config, world_model

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_DAILY_DIGEST", raising=False)
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    import maverick.audit as audit

    monkeypatch.setattr(audit, "record", lambda kind, **p: True)
    config.reset_config_cache()
    yield
    from maverick_dashboard import daily_digest_scheduler as sched

    sched.stop_daily_digest_scheduler()
    config.reset_config_cache()


def test_lease_is_exclusive_renewable_and_expires():
    from maverick_dashboard.daily_digest_scheduler import acquire_lease, release_lease

    assert acquire_lease("a", now=100.0, ttl_seconds=60) is True
    assert acquire_lease("b", now=120.0, ttl_seconds=60) is False
    assert acquire_lease("a", now=150.0, ttl_seconds=60) is True  # renew
    assert acquire_lease("b", now=211.0, ttl_seconds=60) is True  # a's lease lapsed
    release_lease("a")  # not the holder: nothing happens
    assert acquire_lease("c", now=212.0, ttl_seconds=60) is False
    release_lease("b")
    assert acquire_lease("c", now=213.0, ttl_seconds=60) is True


def test_cycle_is_off_by_default_and_leased(tmp_path, monkeypatch):
    from maverick import config
    from maverick_dashboard import daily_digest_scheduler as sched

    assert sched.scheduler_cycle("h1") is None
    assert sched.start_daily_digest_scheduler() is False
    (tmp_path / "config.toml").write_text("[my_work]\ndigest_enable = true\n")
    config.reset_config_cache()
    called = []
    monkeypatch.setattr("maverick.daily_digest.run_digest_cycle",
                        lambda world, **kw: called.append(sorted(kw["principals"])) or {"sent": 0})
    monkeypatch.setattr(sched, "_recipients", lambda: ["user:b", "user:a"])
    assert sched.scheduler_cycle("h1", now=1000.0) == {"sent": 0}
    assert called == [["user:a", "user:b"]]
    assert sched.scheduler_cycle("h2", now=1001.0) is None  # h1 holds the lease


def test_recipients_union_roster_links_and_preferences(monkeypatch):
    from maverick import channel_identities, user_preferences
    from maverick_dashboard import daily_digest_scheduler as sched
    from maverick_dashboard import rbac

    monkeypatch.setattr(rbac, "list_users", lambda: {"user:role": "viewer"})
    channel_identities.link("slack", "U1", "user:linked", actor="admin")
    user_preferences.set_preferences("user:prefs", digest_hour=9)
    assert sched._recipients() == ["user:linked", "user:prefs", "user:role"]


def test_start_and_stop_thread(tmp_path, monkeypatch):
    from maverick import config
    from maverick_dashboard import daily_digest_scheduler as sched

    (tmp_path / "config.toml").write_text("[my_work]\ndigest_enable = true\ndigest_poll_seconds = 30\n")
    config.reset_config_cache()
    ran = threading.Event()
    monkeypatch.setattr(sched, "scheduler_cycle", lambda holder, now=None: ran.set() or {"sent": 0})
    assert sched.start_daily_digest_scheduler() is True
    assert sched.start_daily_digest_scheduler() is False  # already running
    assert ran.wait(5)
    assert sched.stop_daily_digest_scheduler(timeout=5) is True
    assert sched.stop_daily_digest_scheduler() is False


def test_failed_cycle_backs_off(monkeypatch):
    from maverick_dashboard import daily_digest_scheduler as sched

    waits = []
    stop = threading.Event()

    class Stop:
        def is_set(self):
            return len(waits) >= 3

        def wait(self, seconds):
            waits.append(seconds)

    def boom(holder, now=None):
        raise RuntimeError("down")

    monkeypatch.setattr(sched, "scheduler_cycle", boom)
    monkeypatch.setattr(sched, "release_lease", lambda holder: None)
    sched._scheduler_loop(Stop(), "h", 30.0)
    assert waits == [60.0, 120.0, 240.0]
    assert not stop.is_set()
