"""A crashed first install must resume only its exact approved files."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from maverick_installer import bridge, wizard


@pytest.fixture
def first_install(tmp_path, monkeypatch):
    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".env")
    return tmp_path


def _crash_receipt(home, *, config="[safety]\nprofile = 'strict'\n", env="ANTHROPIC_API_KEY=test\n"):
    wizard._pending_setup_path().write_text(
        json.dumps({"version": 1, "config": config, "env": env}), encoding="utf-8"
    )
    (home / ".env").write_text(env, encoding="utf-8")
    return config, env


def test_first_install_receipt_precedes_writes_and_is_removed(first_install, monkeypatch):
    original = wizard._write_wizard_file
    seen = []

    def recording_write(path, body):
        seen.append((path, wizard._pending_setup_path().is_file()))
        original(path, body)

    monkeypatch.setattr(wizard, "_write_wizard_file", recording_write)
    wizard._publish_wizard_configuration(
        "[safety]\nprofile = 'strict'\n", "ANTHROPIC_API_KEY=test\n", protect_existing=True
    )
    assert seen == [(wizard.ENV_FILE, True), (wizard.CONFIG_FILE, True)]
    assert not wizard._pending_setup_path().exists()
    assert wizard.CONFIG_FILE.is_file() and wizard.ENV_FILE.is_file()


def test_first_install_ordinary_write_failure_rolls_back(first_install, monkeypatch):
    original = wizard._write_wizard_file

    def fail_config(path, body):
        if path == wizard.CONFIG_FILE:
            raise OSError("synthetic disk failure")
        original(path, body)

    monkeypatch.setattr(wizard, "_write_wizard_file", fail_config)
    with pytest.raises(OSError, match="synthetic disk failure"):
        wizard._publish_wizard_configuration(
            "[safety]\nprofile = 'strict'\n", "ANTHROPIC_API_KEY=test\n", protect_existing=True
        )
    assert not wizard.ENV_FILE.exists()
    assert not wizard.CONFIG_FILE.exists()
    assert not wizard._pending_setup_path().exists()


@pytest.mark.parametrize("start", [wizard.run, wizard.run_fast, wizard.run_consumer, wizard.run_express])
def test_approved_first_install_resumes_without_questions(first_install, monkeypatch, start):
    config, env = _crash_receipt(first_install)
    monkeypatch.setattr(wizard, "preflight", lambda: pytest.fail("setup asked new questions"))
    assert start() == 0
    assert wizard.CONFIG_FILE.read_text(encoding="utf-8") == config
    assert wizard.ENV_FILE.read_text(encoding="utf-8") == env
    assert not wizard._pending_setup_path().exists()


def test_crash_after_both_files_only_clears_receipt(first_install):
    config, env = _crash_receipt(first_install)
    wizard.CONFIG_FILE.write_text(config, encoding="utf-8")
    assert wizard.recover_pending_setup()
    assert wizard.CONFIG_FILE.read_text(encoding="utf-8") == config
    assert wizard.ENV_FILE.read_text(encoding="utf-8") == env
    assert not wizard._pending_setup_path().exists()


def test_crash_before_either_file_replays_exact_approved_setup(first_install):
    config = "[safety]\nprofile = 'strict'\n"
    env = "ANTHROPIC_API_KEY=test\n"
    wizard._pending_setup_path().write_text(
        json.dumps({"version": 1, "config": config, "env": env}), encoding="utf-8"
    )
    assert wizard.recover_pending_setup()
    assert wizard.CONFIG_FILE.read_text(encoding="utf-8") == config
    assert wizard.ENV_FILE.read_text(encoding="utf-8") == env
    assert not wizard._pending_setup_path().exists()


def test_keyless_first_install_does_not_create_key_file(first_install):
    config = "[safety]\nprofile = 'strict'\n"
    wizard._pending_setup_path().write_text(
        json.dumps({"version": 1, "config": config, "env": None}), encoding="utf-8"
    )
    assert wizard.recover_pending_setup()
    assert wizard.CONFIG_FILE.read_text(encoding="utf-8") == config
    assert not wizard.ENV_FILE.exists()


def test_damaged_receipt_refuses_without_creating_setup(first_install):
    wizard._pending_setup_path().write_text("{damaged", encoding="utf-8")
    with pytest.raises(RuntimeError, match="needs assistance"):
        wizard.recover_pending_setup()
    assert not wizard.CONFIG_FILE.exists()
    assert not wizard.ENV_FILE.exists()
    assert wizard._pending_setup_path().is_file()


def test_changed_key_file_is_preserved_and_recovery_refused(first_install):
    _crash_receipt(first_install)
    wizard.ENV_FILE.write_text("ANTHROPIC_API_KEY=changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="needs assistance"):
        wizard.recover_pending_setup()
    assert wizard.ENV_FILE.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=changed\n"
    assert not wizard.CONFIG_FILE.exists()
    assert wizard._pending_setup_path().exists()


def test_bridge_first_install_recovers_after_crash(first_install, monkeypatch):
    config, env = _crash_receipt(first_install)
    sent = []
    monkeypatch.setattr(bridge, "_recv", lambda: "")
    monkeypatch.setattr(bridge, "_send", sent.append)
    bridge.run()
    assert len(sent) == 1 and sent[0]["id"] == "__done__"
    assert "ANTHROPIC_API_KEY=test" not in json.dumps(sent)
    assert wizard.CONFIG_FILE.read_text(encoding="utf-8") == config
    assert wizard.ENV_FILE.read_text(encoding="utf-8") == env


def test_bridge_protocol_resumes_with_one_json_step(tmp_path):
    home = tmp_path / "lightwork"
    home.mkdir()
    (home / ".config.toml.lightwork-pending.json").write_text(
        json.dumps({"version": 1, "config": "[safety]\nprofile = 'strict'\n", "env": "ANTHROPIC_API_KEY=test\n"}),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["MAVERICK_HOME"] = str(home)
    environment.pop("MAVERICK_CONFIG", None)
    result = subprocess.run(
        [sys.executable, "-m", "maverick_installer.bridge"],
        input="\n", text=True, capture_output=True, env=environment, check=False, timeout=10,
    )
    assert result.returncode == 0
    lines = result.stdout.strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["id"] == "__done__"
    assert "ANTHROPIC_API_KEY=test" not in result.stdout
    assert (home / "config.toml").is_file() and (home / ".env").is_file()


def test_bridge_protocol_reports_changed_key_without_exposing_it(tmp_path):
    home = tmp_path / "lightwork"
    home.mkdir()
    (home / ".config.toml.lightwork-pending.json").write_text(
        json.dumps({"version": 1, "config": "[safety]\nprofile = 'strict'\n", "env": "ANTHROPIC_API_KEY=test\n"}),
        encoding="utf-8",
    )
    (home / ".env").write_text("ANTHROPIC_API_KEY=changed\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["MAVERICK_HOME"] = str(home)
    environment.pop("MAVERICK_CONFIG", None)
    result = subprocess.run(
        [sys.executable, "-m", "maverick_installer.bridge"],
        input="\n", text=True, capture_output=True, env=environment, check=False, timeout=10,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout.strip())["id"] == "__error__"
    assert "needs assistance" in result.stdout
    assert "ANTHROPIC_API_KEY=changed" not in result.stdout
    assert not (home / "config.toml").exists()
