"""Local sign-in and a role list that failed to save, or went missing.

A first roster write that fails must not strand a fresh install, and a lost
roster must not lock out the administrators set up in configuration: they are
always administrators, so they must be able to sign in and start a new list.
"""
from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

LOCAL = "http://127.0.0.1:8765"
REMOTE = "https://lightwork.example"
OWNER = "owner@example.test"
OWNER_PASS = "Synthetic owner passphrase for roster tests"  # pragma: allowlist secret
COLLEAGUE = "sam@example.test"
GOOD = "harbor violet tram ledger"  # pragma: allowlist secret - synthetic fixture passphrase


@pytest.fixture
def home(monkeypatch, tmp_path):
    """A fresh installation: invitations sign people in locally, nobody has claimed it."""
    from maverick import client as client_binding
    from maverick import config, world_model

    for name in tuple(os.environ):
        if name.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text('[client]\nid="roster-sign-in"\n', encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    client_binding.reset_client_cache()
    from maverick_dashboard import local_passphrases

    local_passphrases.reset_attempt_limit()
    yield tmp_path
    local_passphrases.reset_attempt_limit()
    config.reset_config_cache()
    client_binding.reset_client_cache()


def _on_this_computer():
    from maverick_dashboard.app import app

    return TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50001),
                      headers={"Origin": LOCAL}, follow_redirects=False)


def _elsewhere():
    from maverick_dashboard.app import app

    return TestClient(app, base_url=REMOTE, client=("192.0.2.40", 50002),
                      headers={"Origin": REMOTE}, follow_redirects=False)


def _claim(owner):
    return owner.post("/auth/local-owner", data={
        "email": OWNER, "password": OWNER_PASS, "password2": OWNER_PASS, "confirm": "yes"})


def _break_writes_of(monkeypatch, name):
    """Make every write of ``name`` fail until ``fault["armed"] = False``."""
    from maverick import file_lock

    real = file_lock.atomic_write_text
    fault = {"armed": True, "failed": 0}

    def flaky(path, *args, **kwargs):
        if fault["armed"] and Path(path).name == name:
            fault["failed"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(file_lock, "atomic_write_text", flaky)
    return fault


def test_owner_setup_survives_a_failed_first_roster_write(home, monkeypatch):
    from maverick_dashboard import local_owner, rbac

    fault = _break_writes_of(monkeypatch, "dashboard-users.json")
    owner = _on_this_computer()
    first = _claim(owner)
    assert first.status_code == 503, first.text
    assert fault["failed"] == 1
    assert json.loads((home / "dashboard-owner.json").read_text(encoding="utf-8"))["status"] == "pending"
    assert not (home / "dashboard-users.json").exists()
    assert not (home / "dashboard-users.json.present").exists()

    fault["armed"] = False
    again = owner.post("/auth/local-login", data={"email": OWNER, "password": OWNER_PASS})
    assert again.status_code == 303, again.text
    assert rbac.get_stored_role("user:" + OWNER) == "admin"
    assert local_owner._read()["status"] == "ready"
    assert owner.get("/users", headers={"Accept": "text/html"}).status_code == 200
