"""A setup retry must leave an existing customer's choices and keys intact."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from maverick_installer import bridge, wizard


@pytest.fixture
def saved_setup(tmp_path, monkeypatch):
    home = tmp_path / "lightwork"
    home.mkdir()
    monkeypatch.setattr(wizard, "CONFIG_DIR", home)
    monkeypatch.setattr(wizard, "CONFIG_FILE", home / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", home / ".env")
    config = b"[feature_locks]\ncomputer_use = true\n[governance]\nprofile = 'custom'\n"
    secrets = b"ANTHROPIC_API_KEY=test\nCUSTOM_SECRET=test\n"
    wizard.CONFIG_FILE.write_bytes(config)
    wizard.ENV_FILE.write_bytes(secrets)
    return home, config, secrets


@pytest.mark.parametrize(
    "start",
    [
        lambda: wizard.run(),
        lambda: wizard.run(fast=True),
        lambda: wizard.run(resume=True),
        wizard.run_fast,
        wizard.run_consumer,
        wizard.run_express,
    ],
    ids=["standard", "fast-flag", "resume-flag", "fast-direct", "consumer-direct", "express-direct"],
)
def test_setup_retry_keeps_saved_choices_and_keys(saved_setup, monkeypatch, capsys, start):
    home, config, secrets = saved_setup

    def no_questions(*_args, **_kwargs):
        raise AssertionError("an existing setup must stop before the wizard asks questions")

    monkeypatch.setattr(wizard, "preflight", no_questions)
    monkeypatch.setattr(wizard, "pick_mode", no_questions)
    assert start() == 1
    assert wizard.CONFIG_FILE.read_bytes() == config
    assert wizard.ENV_FILE.read_bytes() == secrets
    assert not (home / "config.toml.bak").exists()
    assert not (home / ".env.bak").exists()
    notice = capsys.readouterr().out
    assert "saved setup files" in notice
    assert "Lightwork" in notice and "Settings" in notice


@pytest.mark.parametrize("existing", ["config.toml", ".env"])
def test_publish_rechecks_existing_files_while_locked(tmp_path, monkeypatch, existing):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    current = tmp_path / existing
    current.write_bytes(b"keep these bytes\n")

    with pytest.raises(FileExistsError, match="Nothing was replaced"):
        wizard._publish_wizard_configuration(
            "[safety]\nprofile = 'strict'\n",
            "ANTHROPIC_API_KEY=replacement\n",
            protect_existing=True,
        )

    assert current.read_bytes() == b"keep these bytes\n"
    assert not (tmp_path / "config.toml.bak").exists()
    assert not (tmp_path / ".env.bak").exists()
    assert not (tmp_path / (".env" if existing == "config.toml" else "config.toml")).exists()


def test_unreadable_setup_path_is_not_assumed_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    original_lstat = Path.lstat

    def unreadable(path, *args, **kwargs):
        if path == wizard.CONFIG_FILE:
            raise PermissionError("setup path is not readable")
        return original_lstat(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "lstat", unreadable)
        assert wizard._existing_setup() is True


def test_desktop_bridge_keeps_existing_setup(saved_setup, monkeypatch):
    home, config, secrets = saved_setup
    sent = []
    received = []

    def receive():
        received.append(True)
        if len(received) > 1:
            raise AssertionError("the desktop bridge must not ask setup questions")
        return ""

    monkeypatch.setattr(bridge, "_recv", receive)
    monkeypatch.setattr(bridge, "_send", sent.append)
    monkeypatch.setattr(bridge, "write_consumer_config", lambda **_kwargs: pytest.fail("setup was replaced"))
    with pytest.raises(FileExistsError, match="Nothing was changed") as stopped:
        bridge.run()

    assert len(received) == 1
    assert sent == []
    assert "Settings" in str(stopped.value)
    assert wizard.CONFIG_FILE.read_bytes() == config
    assert wizard.ENV_FILE.read_bytes() == secrets
    assert not (home / "config.toml.bak").exists()
    assert not (home / ".env.bak").exists()


def test_desktop_bridge_protocol_reports_existing_key_file(tmp_path):
    home = tmp_path / "lightwork"
    home.mkdir()
    keys = home / ".env"
    keys.write_text("CUSTOM_SECRET=keep-me\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["MAVERICK_HOME"] = str(home)
    environment.pop("MAVERICK_CONFIG", None)

    result = subprocess.run(
        [sys.executable, "-m", "maverick_installer.bridge"],
        input="\n",
        text=True,
        capture_output=True,
        env=environment,
        check=False,
        timeout=10,
    )

    assert result.returncode == 1
    step = json.loads(result.stdout.strip())
    assert step["id"] == "__error__"
    assert "Nothing was changed" in step["question"]
    assert "CUSTOM_SECRET" not in result.stdout
    assert keys.read_text(encoding="utf-8") == "CUSTOM_SECRET=keep-me\n"
    assert not (home / "config.toml").exists()
