"""CLI acknowledgement means checked local state, never completed shutdown."""
import json

import pytest
from click.testing import CliRunner
from maverick import killswitch
from maverick.cli import main


def test_halt_and_clear_machine_acknowledgements_check_actual_local_state(monkeypatch, tmp_path):
    path = tmp_path / "owned-HALT"
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(path))
    for command, present in (("halt", True), ("unhalt", False), ("unhalt", False)):
        result = CliRunner().invoke(main, [command, "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == {"command": command, "acknowledged": True,
            "halt_file_present": present, "scope": "local", "work_stopped": False}
        assert path.exists() is present


@pytest.mark.parametrize("command", ["halt", "unhalt"])
def test_state_read_failure_cannot_be_reported_as_an_acknowledgement(monkeypatch, tmp_path, command):
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "owned-HALT"))
    def fail(*args):
        raise OSError("synthetic observation failure")
    monkeypatch.setattr(killswitch, "_halt_file_generation", fail)
    result = CliRunner().invoke(main, [command, "--json"])
    assert result.exit_code != 0 and '"acknowledged": true' not in result.output


@pytest.mark.parametrize("command, observed", [("halt", None), ("unhalt", (1,))])
def test_concurrently_changed_state_is_unconfirmed(monkeypatch, tmp_path, command, observed):
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "owned-HALT"))
    monkeypatch.setattr(killswitch, "_halt_file_generation", lambda *args: observed)
    result = CliRunner().invoke(main, [command, "--json"])
    assert result.exit_code != 0 and "not confirmed" in result.output
