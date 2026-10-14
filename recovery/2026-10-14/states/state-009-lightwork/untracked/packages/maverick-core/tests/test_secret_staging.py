"""The production init helper regularizes Kubernetes Secret projections."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from maverick import secret_staging

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX custody modes")


def _projection(tmp_path: Path, files: dict[str, bytes]) -> Path:
    source = tmp_path / "projected"
    source.mkdir(mode=0o750)
    snapshot = source / "..2026_08_03_00_00_00"
    snapshot.mkdir(mode=0o750)
    for name, payload in files.items():
        path = snapshot / name
        path.write_bytes(payload)
        path.chmod(0o440)
        (source / name).symlink_to(Path("..data") / name)
    (source / "..data").symlink_to(snapshot.name)
    return source


def test_projected_snapshot_becomes_owner_only_regular_files(
    monkeypatch, tmp_path,
) -> None:
    source = _projection(
        tmp_path,
        {"VAULT_TOKEN": b"vault-token", "MAVERICK_PG_DSN": b"postgres-dsn"},
    )
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o770)
    staging.chmod(0o777)  # kubelet-owned emptyDir mount roots may retain this
    destination = staging / "custody"
    monkeypatch.setattr(secret_staging, "_source_is_read_only", lambda _path: True)

    assert secret_staging.stage_projected_secrets(source, destination) == 2

    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    for name, expected in {
        "VAULT_TOKEN": b"vault-token",
        "MAVERICK_PG_DSN": b"postgres-dsn",
    }.items():
        path = destination / name
        assert path.is_file() and not path.is_symlink()
        assert path.read_bytes() == expected
        assert stat.S_IMODE(path.stat().st_mode) == 0o400


def test_staging_refuses_unsafe_secret_names(monkeypatch, tmp_path) -> None:
    source = _projection(tmp_path, {"not-provider-compatible.txt": b"secret"})
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o770)
    monkeypatch.setattr(secret_staging, "_source_is_read_only", lambda _path: True)

    with pytest.raises(secret_staging.SecretStagingError, match="name.*unsafe"):
        secret_staging.stage_projected_secrets(source, staging / "custody")
    assert not (staging / "custody").exists()


def test_staging_refuses_a_writable_projection(monkeypatch, tmp_path) -> None:
    source = _projection(tmp_path, {"VAULT_TOKEN": b"secret"})
    staging = tmp_path / "staging"
    staging.mkdir(mode=0o770)
    monkeypatch.setattr(secret_staging, "_source_is_read_only", lambda _path: False)

    with pytest.raises(secret_staging.SecretStagingError, match="not mounted read-only"):
        secret_staging.stage_projected_secrets(source, staging / "custody")


def test_staging_refuses_existing_destination(monkeypatch, tmp_path) -> None:
    source = _projection(tmp_path, {"VAULT_TOKEN": b"secret"})
    destination = tmp_path / "staging" / "custody"
    destination.mkdir(parents=True)
    monkeypatch.setattr(secret_staging, "_source_is_read_only", lambda _path: True)

    with pytest.raises(secret_staging.SecretStagingError, match="already exists"):
        secret_staging.stage_projected_secrets(source, destination)
