"""Container mounts must not include the operator's private data."""
import os

import pytest
from maverick.sandbox.workspace_guard import validate_container_workspace


def test_rejects_workspace_containing_lightwork_data(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "state"))
    with pytest.raises(ValueError, match="dedicated workspace"):
        validate_container_workspace(tmp_path)
    with pytest.raises(ValueError, match="dedicated workspace"):
        validate_container_workspace(tmp_path / "state" / "audit")
    validate_container_workspace(tmp_path / "state" / "workspace")
    validate_container_workspace(tmp_path / "project")


@pytest.mark.skipif(os.name == "nt", reason="creating symlinks requires Windows privileges")
def test_rejects_resolved_private_directory(tmp_path, monkeypatch):
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    private = tmp_path / "private"
    private.mkdir()
    (home / ".ssh").symlink_to(private, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("MAVERICK_HOME", str(home / ".maverick"))
    with pytest.raises(ValueError, match="dedicated workspace"):
        validate_container_workspace(private)
