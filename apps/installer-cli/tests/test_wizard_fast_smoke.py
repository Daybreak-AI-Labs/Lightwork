"""`maverick init --fast` must not report success over a failed smoke test.

run_fast() called smoke_test() and discarded its bool, so the one command whose
whole promise is "trust this without answering questions" printed "Fast setup
finished" and exited 0 on a broken install. An installer that cannot fail is an
installer whose success carries no information.
"""
from __future__ import annotations

from pathlib import Path


def test_wizard_paths_honor_maverick_home(monkeypatch, tmp_path):
    from maverick_installer import wizard

    target = tmp_path / "isolated-home"
    monkeypatch.setenv("MAVERICK_HOME", str(target))
    monkeypatch.delenv("MAVERICK_CONFIG", raising=False)

    config_dir, config_file, env_file = wizard._resolve_config_paths()
    assert config_dir == target
    assert config_file == target / "config.toml"
    assert env_file == target / ".env"


def test_explicit_config_path_wins_and_keeps_env_adjacent(monkeypatch, tmp_path):
    from maverick_installer import wizard

    target = tmp_path / "operator" / "lightwork.toml"
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "ignored"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(target))

    config_dir, config_file, env_file = wizard._resolve_config_paths()
    assert config_dir == target.parent
    assert config_file == target
    assert env_file == target.parent / ".env"


def _stub(monkeypatch, tmp_path: Path, *, smoke: bool):
    from maverick_installer import wizard

    monkeypatch.setattr(wizard, "CONFIG_DIR", tmp_path / ".maverick")
    monkeypatch.setattr(wizard, "CONFIG_FILE", tmp_path / ".maverick" / "config.toml")
    monkeypatch.setattr(wizard, "ENV_FILE", tmp_path / ".maverick" / ".env")
    monkeypatch.setattr(wizard, "preflight", lambda: True)
    monkeypatch.setattr(wizard, "welcome", lambda: None)
    monkeypatch.setattr(wizard, "_docker_available", lambda: False)
    monkeypatch.setattr(wizard, "smoke_test", lambda: smoke)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return wizard


def test_fast_setup_fails_when_the_smoke_test_fails(monkeypatch, tmp_path):
    wizard = _stub(monkeypatch, tmp_path, smoke=False)
    assert wizard.run_fast() == 1


def test_fast_setup_succeeds_when_the_smoke_test_passes(monkeypatch, tmp_path):
    """Control: rejection only means something if acceptance also works."""
    wizard = _stub(monkeypatch, tmp_path, smoke=True)
    assert wizard.run_fast() == 0


def test_the_config_is_still_written_before_the_smoke_test_verdict(
        monkeypatch, tmp_path):
    """A failed smoke test must not leave the user with nothing to fix.

    write_config runs first deliberately: the operator needs the file on disk to
    inspect and correct. The failure is in the exit code and the message, not in
    withholding the artifact.
    """
    wizard = _stub(monkeypatch, tmp_path, smoke=False)
    assert wizard.run_fast() == 1
    assert (tmp_path / ".maverick" / "config.toml").is_file()
