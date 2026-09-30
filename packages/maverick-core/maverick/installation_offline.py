"""Explicitly selected, exact-release offline repair using the online trust checks."""
from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from pathlib import Path

from . import backup, file_lock, installation_recovery as recovery
from .installation_handoff import _release


def _root() -> Path:
    return file_lock.ensure_private_directory(recovery._store() / "offline-reviews")


def _directory(identifier: str) -> Path:
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise recovery.MaintenanceError("Select and verify the offline package again.")
    return _root() / identifier


def _review(identifier: str) -> tuple[Path, dict]:
    directory = _directory(identifier)
    row = recovery._read(directory / "review.json")
    if row.get("kind") != "offline-repair-review" or row.get("used") or row.get("expires_at", 0) <= time.time():
        raise recovery.MaintenanceError("The offline package review expired or was already used. Select it again.")
    if backup._sha256(directory / "new.msi") != row["release"]["installer_sha256"]:
        raise recovery.MaintenanceError("The selected offline installer changed. Select and verify it again.")
    return directory, row


def _cleanup(directory: Path) -> None:
    # Only the bounded, private files created by this module; no selected user files.
    for name in ("new.msi", "review.json", "inspect-installer.ps1"):
        path = directory / name
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise recovery.MaintenanceError("The private offline cache needs administrator inspection.")
            path.unlink()
    directory.rmdir()


def review(folder: str, *, public_key: str) -> dict:
    """Copy and verify local bytes. Never download, install, reserve, or pause work."""
    if not isinstance(folder, str) or not folder or len(folder) > 4096:
        raise recovery.MaintenanceError("Select the local folder containing new.msi and downloads.json.")
    selected = backup._filesystem_path(Path(folder).absolute())
    metadata_path = backup._filesystem_path(selected / "downloads.json")
    if not metadata_path.is_file() or metadata_path.stat().st_size > 65536:
        raise recovery.MaintenanceError("The offline release needs bounded downloads.json metadata next to new.msi.")
    try:
        metadata = json.loads(file_lock.atomic_read_text(metadata_path))
    except (ValueError, OSError) as exc:
        raise recovery.MaintenanceError("The offline release metadata is unreadable.") from exc
    if not isinstance(metadata, dict) or metadata.get("identity_transition") is not None or not hmac.compare_digest(str(metadata.get("public_key", "")), public_key):
        raise recovery.MaintenanceError("Offline repair cannot change the installed update key or publisher identity.")
    release = _release(metadata.get("new"))
    if release["version"] != recovery.workspace_recovery.version():
        raise recovery.MaintenanceError("Offline repair requires the exact installed version; upgrades and downgrades are not repair.")
    capability = recovery.repair_capability(expected_runtime_sha256=release["runtime_sha256"], expected_installer_sha256=release["installer_sha256"], expected_public_key=public_key, expected_version=release["version"])
    with recovery._locked():
        entries = list(_root().iterdir())
        for entry in entries:
            if re.fullmatch(r"[0-9a-f]{32}", entry.name) and entry.is_dir() and not entry.is_symlink():
                row = recovery._read(entry / "review.json")
                if row.get("kind") == "offline-repair-review" and (row.get("used") or row.get("expires_at", 0) <= time.time()):
                    _cleanup(entry)
        if len(list(_root().iterdir())) >= 4:
            raise recovery.MaintenanceError("Four offline package reviews are retained. Use a current review or wait for its 30-minute expiry before selecting another.")
        identifier = secrets.token_hex(16)
        directory = file_lock.ensure_private_directory(_directory(identifier))
        try:
            recovery._copy_retained_source(selected / "new.msi", directory / "new.msi", release["installer_sha256"])
            verified = recovery._installer(directory, {"public_key": public_key, "new": release}, "new")
            if verified["sha256"] != release["installer_sha256"] or any(verified[key] != capability[key] for key in ("product_code", "package_code", "publisher_thumbprint")):
                raise recovery.MaintenanceError("The offline package does not match the installed Windows product and package.")
            row = {"schema": 1, "target": recovery._target(), "id": identifier, "kind": "offline-repair-review", "release": release, "public_key": public_key, "expires_at": time.time() + 1800, "used": False}
            recovery._write(directory / "review.json", row)
        except BaseException:
            _cleanup(directory)
            raise
    return {"review": identifier, "version": release["version"], "installer_sha256": verified["sha256"], "publisher_thumbprint": verified["publisher_thumbprint"], "size_bytes": (directory / "new.msi").stat().st_size, "expires_at": row["expires_at"], "runtime_role": capability["runtime_role"], "verified": True}


def authorize(identifier: str, *, confirm: bool) -> dict:
    if confirm is not True:
        raise recovery.MaintenanceError("Review the exact offline package and explicitly confirm repairing this installation.")
    with recovery._locked():
        source, row = _review(identifier)
    release = row["release"]
    grant = recovery.authorize_repair(actor="native-offline-repair", confirm_all_workspaces=True, expected_runtime_sha256=release["runtime_sha256"], expected_installer_sha256=release["installer_sha256"], expected_public_key=row["public_key"], expected_version=release["version"])
    with recovery._locked():
        source, row = _review(identifier)
        target = Path(grant["directory"])
        recovery._copy_retained_source(source / "new.msi", target / "new.msi", release["installer_sha256"])
        file_lock.atomic_write_text(target / "downloads.json", json.dumps({"public_key": row["public_key"], "identity_transition": None, "new": release}, allow_nan=False))
        row["used"] = True
        recovery._write(source / "review.json", row)
    # Existing prepare rechecks Minisign, Authenticode, publisher/product/package,
    # exact runtime identity and unchanged workspace guards before any installer.
    return grant
