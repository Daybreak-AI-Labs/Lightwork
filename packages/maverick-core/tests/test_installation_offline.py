"""Offline repair trust regressions; no native inspector or provider executes."""
import contextlib
import json

import pytest
from maverick import installation_offline as offline


def metadata(tmp_path, **changes):
    release = dict(version="1.2.3", signature="signed", runtime_sha256="a" * 64, installer_sha256="b" * 64)
    release.update(changes)
    (tmp_path / "downloads.json").write_text(json.dumps({"public_key": "pinned", "new": release}))


def test_wrong_version_refused_before_inspection(tmp_path, monkeypatch):
    metadata(tmp_path, version="1.2.2")
    monkeypatch.setattr(offline.recovery.workspace_recovery, "version", lambda: "1.2.3")
    monkeypatch.setattr(offline.recovery, "repair_capability", lambda **kwargs: pytest.fail("must reject first"))
    with pytest.raises(offline.recovery.MaintenanceError, match="exact installed version"):
        offline.review(str(tmp_path), public_key="pinned")


def test_wrong_key_refused_before_inspection(tmp_path):
    metadata(tmp_path)
    with pytest.raises(offline.recovery.MaintenanceError, match="cannot change"):
        offline.review(str(tmp_path), public_key="different")


def test_changed_cached_package_cannot_authorize(tmp_path, monkeypatch):
    monkeypatch.setattr(offline.recovery, "_locked", contextlib.nullcontext)
    monkeypatch.setattr(offline, "_directory", lambda identifier: tmp_path)
    monkeypatch.setattr(offline.recovery, "_read", lambda path: {"kind": "offline-repair-review", "expires_at": 99999999999, "release": {"installer_sha256": "b" * 64}})
    monkeypatch.setattr(offline.backup, "_sha256", lambda path: "c" * 64)
    monkeypatch.setattr(offline.recovery, "authorize_repair", lambda **kwargs: pytest.fail("changed bytes cannot authorize"))
    with pytest.raises(offline.recovery.MaintenanceError, match="installer changed"):
        offline.authorize("a" * 32, confirm=True)
