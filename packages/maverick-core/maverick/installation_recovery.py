"""Reviewed Windows upgrade/rollback transactions over a whole local home.

The authenticated recovery server grants one bounded operation. A separate
retained runtime acquires the control-plane lock after that server exits;
only then does it snapshot or replace state. It never interprets budget ledgers.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote

from . import (
    backup,
    config,
    control_plane_lease,
    file_lock,
    paths,
    release_identity_transition,
    workspace_recovery,
)
from .windows_installer_verification import inspect_signed_msi, verify_minisign

_ID = re.compile(r"^[0-9a-f]{32}$")
_TOKEN = re.compile(r"^([0-9a-f]{32}):([0-9a-f]{64})$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
MAX_EXPANDED_BYTES = 8 * 1024**3
MAX_TICKETS = 20
MAX_RETIREMENT_HISTORY = 100


class MaintenanceError(RuntimeError):
    """Maintenance has not established the authority or consistency it needs."""


def _home() -> Path:
    return backup._filesystem_path(paths.maverick_home()).absolute()


def _store() -> Path:
    return file_lock.ensure_private_directory(_home() / "backups" / "installation-recovery")


def _target() -> str:
    return hashlib.sha256(str(_home()).encode()).hexdigest()


def _key() -> bytes:
    path = _store() / "operator.key"
    if not path.exists():
        file_lock.atomic_create_text(path, secrets.token_hex(32))
    return backup._decode_operator_key(file_lock.atomic_read_text(path))


def _scope():
    return backup.recovery_scope(_home(), "installation:" + _target(), _key(),
                                 max_expanded_bytes=MAX_EXPANDED_BYTES, whole_home=True)


def _directory(identifier: str) -> Path:
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise MaintenanceError("Select an installation recovery point from this computer.")
    return _store() / identifier


def _write(path: Path, record: dict) -> None:
    file_lock.atomic_write_text(path, json.dumps(backup._authenticate_for_write(record, _key())))


def _read(path: Path) -> dict:
    if path.stat().st_size > 128 * 1024:
        raise MaintenanceError("Installation recovery metadata exceeds its size limit.")
    record = json.loads(file_lock.atomic_read_text(path))
    with _scope():
        backup._verify_authentication(record, allow_unsigned=False, label="installation recovery")
    if record.get("schema") != 1 or record.get("target") != _target():
        raise MaintenanceError("This installation recovery point belongs to a different data home.")
    return record


@contextmanager
def _locked():
    with file_lock.cross_process_lock(_store() / "operations", strict=True):
        yield


@contextmanager
def _quiesced():
    # Reusing a web server's lease would not establish quiescence.
    if control_plane_lease.current() is not None:
        raise MaintenanceError("Pause Lightwork from the desktop maintenance controls first.")
    lease = control_plane_lease.acquire(role="installation-maintenance")
    if lease is None:
        raise MaintenanceError("Installation recovery requires the single control plane lock.")
    try:
        yield
    finally:
        control_plane_lease.release()


def _shared_blockers(global_configuration: dict) -> list[str]:
    """Tenant overlays cannot remove the shared administration's authority."""
    reasons = []
    global_kms = global_configuration.get("kms") or {}
    if not isinstance(global_kms, dict) or global_kms.get("provider", "local") != "local":
        reasons.append("Shared administration uses a remote key service and requires coordinated recovery.")
    # Tenant settings can shadow deployment-global settings. The shared state
    # is still part of a whole-home restore, even if no tenant directory exists.
    for section, defaults in (("world_model", {"", "sqlite"}), ("governed_records", {"", "local"}),
                              ("memory", {"", "local", "sqlite", "auto"}), ("queue", {""})):
        value = global_configuration.get(section) or {}
        if not isinstance(value, dict) or str(value.get("backend") or "").lower() not in defaults:
            reasons.append("Shared " + section.replace("_", " ") + " uses an external authority and requires coordinated recovery.")
    knowledge = global_configuration.get("knowledge") or {}
    if not isinstance(knowledge, dict) or str(knowledge.get("store") or "sqlite") != "sqlite" or knowledge.get("path"):
        reasons.append("Shared knowledge storage is outside the default local data home and requires coordinated recovery.")
    dispatch = global_configuration.get("grpc_dispatch") or {}
    if not isinstance(dispatch, dict) or dispatch.get("target"):
        reasons.append("Shared remote workers require coordinated shutdown and recovery.")
    for section in ("redis", "qdrant", "pgvector"):
        if global_configuration.get(section):
            reasons.append("Shared " + section + " storage requires its own verified recovery process.")
    return reasons


def blockers() -> list[str]:
    """All tenants matter because one installed runtime serves the whole home."""
    reasons = []
    # Loading settings also admits wizard-persisted environment values. Check
    # injected key custody after that load, including in a fresh helper process.
    global_configuration = config.load_global_config()
    if config.config_path().absolute() != paths.maverick_home().absolute() / "config.toml" or os.environ.get(config.CONFIG_OVERLAY_ENV):
        reasons.append("Settings outside this local data home require a coordinated administrator recovery process.")
    if any(os.environ.get(prefix + suffix) for prefix in ("MAVERICK_", "LIGHTWORK_") for suffix in ("ENCRYPTION_KEY", "KMS_KEK")):
        reasons.append("Environment-managed encryption keys require separate coordinated recovery.")
    reasons.extend(_shared_blockers(global_configuration))
    tenants = _home() / "tenants"
    directories = list(tenants.iterdir()) if tenants.exists() else []
    if len(directories) > 200:
        reasons.append("This installation exceeds the local recovery limit of 200 workspaces.")
        return reasons
    token = paths.set_tenant(None)
    try:
        reasons.extend(workspace_recovery.restore_blockers())
        for directory in directories:
            file_lock.ensure_private_directory(directory)
            tenant = unquote(directory.name, errors="strict")
            if paths._tenant_segment(paths.canonical_tenant_id(tenant)) != directory.name:
                raise MaintenanceError("A workspace directory has no unambiguous portable identity.")
            with paths.tenant_scope(tenant=tenant):
                reasons.extend(workspace_recovery.restore_blockers())
                kms = config.load_config().get("kms") or {}
                if not isinstance(kms, dict) or kms.get("provider", "local") != "local":
                    reasons.append("A workspace uses a remote key service and requires coordinated recovery.")
    finally:
        paths.reset_tenant(token)
    return list(dict.fromkeys(reasons))


def _runtime() -> Path:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        raise MaintenanceError("Installation recovery requires the packaged Windows application.")
    return Path(sys.executable).absolute()


def _native_channel(runtime: Path, *, expected_version: str | None = None) -> dict:
    native = runtime.parent / "maverick-desktop.exe"
    digest = backup._sha256(native)
    result = subprocess.run([str(native), "--maintenance-channel"], capture_output=True, text=True,
                            encoding="utf-8", timeout=30, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        channel = json.loads(result.stdout) if result.returncode == 0 and len(result.stdout) <= 16384 else {}
        public_key = channel.get("public_key")
        if (not isinstance(public_key, str) or not public_key or len(public_key) > 4096
                or channel.get("version") != (expected_version or workspace_recovery.version())
                or channel.get("packaged_runtime") is not True or backup._sha256(native) != digest):
            raise ValueError("unbound channel")
    except (TypeError, ValueError) as exc:
        raise MaintenanceError("The installed native application's release identity could not be established.") from exc
    return {"native_sha256": digest, "public_key": public_key}


def _admit_local() -> None:
    reasons = blockers()
    if reasons:
        raise MaintenanceError(" ".join(reasons))


def _transition_source(record):
    return {"version": record["old_version"], "runtime_sha256": record["old_runtime_sha256"],
            "native_sha256": record["native_sha256"], "public_key": record["public_key"]}


def _verified_transition(value, record, *, admit=True):
    if admit and not config.signing_identity_transition_enabled():
        raise MaintenanceError("Signing identity transitions are disabled. A global administrator must enable this trust capability in Settings before reviewing a bridge release.")
    try:
        return release_identity_transition.verify(value, installed=_transition_source(record), admit=admit)
    except release_identity_transition.IdentityTransitionError as exc:
        raise MaintenanceError(str(exc)) from exc


def review_identity_transition(value, *, isolated: bool) -> dict:
    if not isolated:
        raise MaintenanceError("Review signing identity transitions in the isolated Workspace recovery mode.")
    runtime = _runtime()
    source = {"old_version": workspace_recovery.version(), "old_runtime_sha256": backup._sha256(runtime),
              **_native_channel(runtime)}
    return release_identity_transition.summary(_verified_transition(value, source))


def _check_transition_installers(record, old, new, *, admit=False):
    envelope = record.get("identity_transition")
    if envelope is None:
        if old.get("publisher_thumbprint") != new.get("publisher_thumbprint"):
            raise MaintenanceError("The installer publisher changed. Review an authenticated exact-release identity transition in Workspace recovery first.")
        return
    if release_identity_transition.digest(envelope) != record.get("transition_sha256"):
        raise MaintenanceError("The reviewed signing identity transition changed.")
    if not admit and not record.get("transition_admitted_at"):
        raise MaintenanceError("This signing identity transition has no completed admission receipt.")
    verified = _verified_transition(envelope, record, admit=admit)
    try:
        release_identity_transition.verify_installers(verified, old=old, new=new)
    except release_identity_transition.IdentityTransitionError as exc:
        raise MaintenanceError(str(exc)) from exc


def authorize(*, actor: str, isolated: bool, confirm_all_workspaces: bool,
              rollback_id: str | None = None, identity_transition=None, transition_sha256: str | None = None,
              confirm_identity_transition: bool = False) -> dict:
    if not isolated or confirm_all_workspaces is not True or not actor:
        raise MaintenanceError("A global administrator must explicitly review all local workspaces in recovery mode.")
    runtime = _runtime()
    with _locked():
        assert_normal_start_allowed()
        _admit_local()
        if rollback_id:
            if identity_transition is not None or transition_sha256 is not None:
                raise MaintenanceError("Rollback uses only its originally admitted exact identity pair.")
            directory = _directory(rollback_id)
            record = _read(directory / "record.json")
            if record.get("status") != "upgraded" or backup._sha256(runtime) != record.get("new_runtime_sha256"):
                raise MaintenanceError("Rollback requires the matching completed upgrade on this computer.")
            _verify_runtime(record, "new")
        else:
            if _usable_point_count() >= MAX_TICKETS:
                raise MaintenanceError("The installation recovery store is full. Review and retire unused authorizations or obsolete recovery points in Workspace recovery.")
            identifier = secrets.token_hex(16)
            directory = file_lock.ensure_private_directory(_directory(identifier))
            record = {"schema": 1, "target": _target(), "id": identifier, "status": "authorized",
                      "old_version": workspace_recovery.version(), "old_runtime_sha256": backup._sha256(runtime),
                      "installed_runtime": str(runtime), "created_at": time.time(), **_native_channel(runtime)}
            if identity_transition is not None:
                if confirm_identity_transition is not True:
                    raise MaintenanceError("Explicitly confirm the reviewed change to installation signing trust.")
                envelope = _verified_transition(identity_transition, record)
                if release_identity_transition.digest(envelope) != transition_sha256:
                    raise MaintenanceError("Review and confirm the exact signing identity transition before authorizing it.")
                record.update(identity_transition=envelope, transition_sha256=transition_sha256)
            elif transition_sha256 is not None:
                raise MaintenanceError("The reviewed signing identity transition is missing.")
        nonce = secrets.token_hex(32)
        record.update(actor=actor, operation="rollback" if rollback_id else "upgrade",
                      grant_hash=hashlib.sha256(nonce.encode()).hexdigest(), expires_at=time.time() + 900)
        _write(directory / "record.json", record)
        return {"id": record["id"], "authorization": record["id"] + ":" + nonce,
                "old_version": record["old_version"], "expires_at": record["expires_at"],
                "scope": "all local workspaces and shared administration files"}


def _grant(token: str) -> tuple[Path, dict]:
    match = _TOKEN.fullmatch(token) if isinstance(token, str) else None
    if match is None:
        raise MaintenanceError("Copy the current maintenance authorization from Workspace recovery.")
    directory = _directory(match[1])
    if not (directory / "record.json").exists():
        raise MaintenanceError("This maintenance authorization expired or was retired. Review it again in Workspace recovery.")
    record = _read(directory / "record.json")
    if record.get("expires_at", 0) < time.time() or not hmac.compare_digest(
            record.get("grant_hash", ""), hashlib.sha256(match[2].encode()).hexdigest()):
        raise MaintenanceError("This maintenance authorization expired or was already used. Review it again in Workspace recovery.")
    return directory, record


def inspect_grant(token: str) -> dict:
    with _locked():
        directory, record = _grant(token)
        return {"id": record["id"], "directory": str(directory), "old_version": record["old_version"],
                "operation": record["operation"], "helper": str(directory / "maverick.exe"),
                "identity_transition": record.get("identity_transition")}


def _installer(directory: Path, metadata: dict, label: str) -> dict:
    release = metadata.get(label) or {}
    if not isinstance(release, dict) or not _VERSION.fullmatch(str(release.get("version", ""))):
        raise MaintenanceError("The installer release identity is incomplete.")
    path = directory / (label + ".msi")
    digest = verify_minisign(path, metadata.get("public_key", ""), release.get("signature", ""))
    properties = inspect_signed_msi(path, version=release["version"], scratch=directory)
    return {**release, "sha256": digest, "product_code": properties["ProductCode"],
            "publisher_thumbprint": properties["PublisherThumbprint"]}


def _snapshot(directory: Path, name: str, runtime_sha256: str) -> dict:
    destination = directory / (name + ".tgz")
    with _scope():
        backup.create_backup(destination, recovery_metadata={"schema": 1, "kind": "installation-" + name,
                              "target": _target(), "runtime_sha256": runtime_sha256,
                              "coverage": "all-local-workspaces-and-shared-administration"})
        manifest = backup.verify_backup(destination)
    return {"name": destination.name, "sha256": backup._sha256(destination), "files": len(manifest["files"])}


def prepare(token: str) -> dict:
    """Called by the native shell only after stopping its owned recovery server."""
    with _locked(), _quiesced():
        directory, record = _grant(token)
        assert_normal_start_allowed()
        _admit_local()
        current = backup._sha256(_runtime())
        if record["operation"] == "upgrade":
            if record["status"] != "authorized" or current != record["old_runtime_sha256"]:
                raise MaintenanceError("The reviewed source application changed.")
            metadata_path = directory / "downloads.json"
            if metadata_path.stat().st_size > 64 * 1024:
                raise MaintenanceError("The installer metadata exceeds its limit.")
            metadata = json.loads(file_lock.atomic_read_text(metadata_path))
            if not hmac.compare_digest(str(metadata.get("public_key", "")), record["public_key"]):
                raise MaintenanceError("The staged verification key does not match the installed native application.")
            if metadata.get("identity_transition") != record.get("identity_transition"):
                raise MaintenanceError("The staged signing identity transition does not match the administrator's review.")
            if backup._sha256(Path(record["installed_runtime"]).parent / "maverick-desktop.exe") != record["native_sha256"]:
                raise MaintenanceError("The native application changed after authorization.")
            old = _installer(directory, metadata, "old")
            new = _installer(directory, metadata, "new")
            _check_transition_installers(record, old, new, admit=True)
            if old["version"] != record["old_version"] or tuple(map(int, new["version"].split("."))) <= tuple(map(int, old["version"].split("."))):
                raise MaintenanceError("The retained installers do not match the reviewed upgrade.")
            for release in (old, new):
                if not re.fullmatch(r"[0-9a-f]{64}", str(release.get("runtime_sha256", ""))):
                    raise MaintenanceError("The release lacks an exact runtime identity.")
            if old["runtime_sha256"] != current:
                raise MaintenanceError("The retained release does not match the installed application build.")
            for name in ("maverick.exe", "maverick-desktop.exe"):
                source = Path(record["installed_runtime"]).parent / name
                # Keep the helper and its native recovery UI outside MSI ownership.
                file_lock.atomic_write_bytes(directory / name, source.read_bytes())
            if backup._sha256(directory / "maverick.exe") != current:
                raise MaintenanceError("The retained application helper changed during copy.")
            record.update(old=old, new=new,
                          new_runtime_sha256=new["runtime_sha256"],
                          before=_snapshot(directory, "before", current))
            if record.get("identity_transition") is not None:
                record["transition_admitted_at"] = time.time()
        else:
            if record["status"] != "upgraded" or current != record["new_runtime_sha256"]:
                raise MaintenanceError("The installed application no longer matches this completed upgrade.")
            _verify_runtime(record, "new")
            _check_transition_installers(record, record["old"], record["new"])
            with _scope():
                archive = directory / "before.tgz"
                if backup._sha256(archive) != record["before"]["sha256"]:
                    raise MaintenanceError("The pre-upgrade snapshot changed.")
                # Refuse before creating a startup block or touching either
                # installer. Reverting real spend would reopen exhausted caps.
                backup.assert_accounting_unchanged(archive)
            record["safety"] = _snapshot(directory, "safety-" + secrets.token_hex(8), current)
        record.update(status="prepared", prepared_at=time.time())
        _write(directory / "record.json", record)
        _write(_store() / "active.json", {"schema": 1, "target": _target(), "id": record["id"]})
        return {"id": record["id"], "helper": str(directory / "maverick.exe"), "operation": record["operation"]}


def _msiexec(directory: Path, action: str, name: str) -> None:
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "msiexec.exe"
    from .windows_installer_verification import installer_path
    record = _read(directory / "record.json")
    arguments = [str(executable), action, installer_path(directory / (name + ".msi")), "/passive", "/norestart",
                 "/L*v", installer_path(directory / (action.strip("/") + "-" + name + ".log"))]
    if action == "/i":
        arguments.append("INSTALLDIR=" + installer_path(Path(record["installed_runtime"]).parent))
    result = subprocess.run(arguments,
                            check=False, timeout=1800, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode not in ((0, 3010, 1605) if action == "/x" else (0, 3010)):
        raise MaintenanceError("Windows Installer could not finish. The recovery point and installation logs have been kept.")


def _verify_runtime(record: dict, label: str) -> None:
    path = Path(record["installed_runtime"])
    if not path.is_file() or backup._sha256(path) != record[label + "_runtime_sha256"]:
        raise MaintenanceError("The installed application does not match the verified release. Workspace restoration has not been marked complete.")
    if record.get("identity_transition") is not None:
        side = record["identity_transition"]["policy"]["from" if label == "old" else "to"]
        native = _native_channel(path, expected_version=side["version"])
        if any(native.get(name) != side[name] for name in ("native_sha256", "public_key")):
            raise MaintenanceError("The installed desktop signing identity does not match the reviewed release. Recovery remains blocked.")
    else:
        native = _native_channel(path, expected_version=record[label]["version"])
        if (native.get("public_key") != record["public_key"]
                or label == "old" and native.get("native_sha256") != record["native_sha256"]):
            raise MaintenanceError("The installed desktop signing identity changed without an admitted bridge. Recovery remains blocked; obtain the administrator's reviewed transition release.")


def _verify_completed_state(directory: Path, record: dict, outcome: str) -> None:
    _verify_runtime(record, "old" if outcome == "rolled_back" else "new")
    with _scope():
        archive = directory / "before.tgz"
        if backup._sha256(archive) != record["before"]["sha256"]:
            raise MaintenanceError("The completed operation's snapshot changed.")
        manifest = backup.verify_backup(archive)
        expected = {name: (row["sha256"], row["size"]) for name, row in manifest["files"].items()}
        if backup._replacement_targets(_home(), expected) != set(expected):
            raise MaintenanceError("Local files changed after the completion receipt. The startup block remains in place.")
        # A committed SQLite WAL may be checkpointed when a reader closes,
        # without changing logical data. Compare through the same online
        # snapshot representation used for the archive, then integrity-check
        # that representation. Non-database bytes remain exact hash checks.
        with backup._temporary_directory(prefix="lightwork-completion-check-") as temporary:
            stage = file_lock.ensure_private_directory(temporary / "data")
            observed = backup._stage(_home(), stage)
            if set(observed) != set(expected):
                raise MaintenanceError("Local files changed after the completion receipt.")
            backup._verify_restored(stage, expected)


def execute(token: str) -> dict:
    """Run from the retained old binary after the native shell exits.

    Success means exact installed runtime AND matching local state. A failure
    retains the startup block, signed receipt, installers and safety snapshots.
    """
    with _locked(), _quiesced():
        directory, record = _grant(token)
        active = _read(_store() / "active.json")
        if active.get("id") != record["id"]:
            raise MaintenanceError("Another reviewed operation owns installation recovery. Its startup block has been preserved.")
        if record["status"] != "prepared" or backup._sha256(_runtime()) != record["old_runtime_sha256"]:
            raise MaintenanceError("Only the reviewed retained application can complete this maintenance operation.")
        _admit_local()
        record.update(status="applying", grant_hash="", started_at=time.time())
        _write(directory / "record.json", record)
        try:
            metadata = {"public_key": record["public_key"], "old": record["old"], "new": record["new"]}
            installers = {}
            for label in ("old", "new"):
                verified = _installer(directory, metadata, label)
                if verified["sha256"] != record[label]["sha256"]:
                    raise MaintenanceError("A retained installer changed after review.")
                installers[label] = verified
            _check_transition_installers(record, installers["old"], installers["new"])
            if record.get("finalization") in {"upgraded", "rolled_back"}:
                outcome = record["finalization"]
                _verify_completed_state(directory, record, outcome)
            elif record["operation"] == "upgrade":
                _msiexec(directory, "/i", "new")
                _verify_runtime(record, "new")
                outcome = "upgraded"
            else:
                with _scope():
                    archive = directory / "before.tgz"
                    if backup._sha256(archive) != record["before"]["sha256"]:
                        raise MaintenanceError("The pre-upgrade snapshot changed.")
                    manifest = backup.verify_backup(archive)
                    binding = manifest.get("recovery", {})
                    if binding.get("target") != _target() or binding.get("runtime_sha256") != record["old_runtime_sha256"]:
                        raise MaintenanceError("The pre-upgrade snapshot does not match this application and data home.")
                    # Recheck under the fresh lease immediately before MSI;
                    # accounting may have changed after the prepared review.
                    backup.assert_accounting_unchanged(archive)
                    # Downgrade through explicit reviewed uninstall/install; never
                    # disable the general MSI downgrade guard or ledger guards.
                    _msiexec(directory, "/x", "new")
                    _msiexec(directory, "/i", "old")
                    _verify_runtime(record, "old")
                    backup.restore_backup(archive, replace_existing=True)
                outcome = "rolled_back"
            record.update(status=outcome, completed_at=time.time())
            _write(directory / "record.json", record)
            (_store() / "active.json").unlink()
            return {"id": record["id"], "status": outcome}
        except Exception as exc:
            record.update(status="incomplete", failed_at=time.time())
            record["error"] = str(exc)[:2000]
            _write(directory / "record.json", record)
            raise


def logs(identifier: str) -> dict:
    """Bounded local-only diagnostics for a fixed reviewed operation."""
    with _locked():
        directory = _directory(identifier)
        record = _read(directory / "record.json")
        sections = ["Operation: " + str(record.get("operation")), "State: " + str(record.get("status"))]
        if record.get("error"):
            sections.append(str(record["error"])[:2000])
        for name in ("i-new.log", "x-new.log", "i-old.log"):
            path = directory / name
            if not path.exists():
                continue
            file_lock.ensure_private_file(path)
            with path.open("rb") as source:
                unicode_log = source.read(2) == b"\xff\xfe"
                start = max(0, path.stat().st_size - 16000)
                source.seek(start - start % 2 if unicode_log else start)
                payload = source.read(16000)
            text = payload.decode("utf-16-le" if unicode_log else "utf-8", errors="replace")
            text = "".join(char for char in text if char in "\n\r\t" or ord(char) >= 32)
            sections.append(name + " (most recent output)\n" + text)
        return {"id": identifier, "details": "\n\n".join(sections)}


def assert_normal_start_allowed() -> None:
    # Do not create state or keys during ordinary startup.
    marker = _home() / "backups" / "installation-recovery" / "active.json"
    if marker.exists():
        raise MaintenanceError("Installation recovery is unfinished. Open Install & updates in Lightwork Desktop to finish recovery before starting workspace work.")


def resume(identifier: str) -> str:
    """Native, explicitly confirmed retry of an already authorized operation.

    No new operation, target, version or snapshot may be selected by this path.
    Normal HTTP routes never expose this local OS-user recovery primitive.
    """
    with _locked():
        directory = _directory(identifier)
        record = _read(directory / "record.json")
        active = _read(_store() / "active.json")
        if active.get("id") != identifier or record.get("status") not in {"prepared", "applying", "incomplete", "upgraded", "rolled_back"}:
            raise MaintenanceError("This installation has no unfinished reviewed operation to resume.")
        if record.get("status") in {"upgraded", "rolled_back"}:
            # A crash between the signed completion receipt and marker removal
            # is recoverable only after rechecking exact binary and local state.
            record["finalization"] = record["status"]
        nonce = secrets.token_hex(32)
        record.update(status="prepared", grant_hash=hashlib.sha256(nonce.encode()).hexdigest(), expires_at=time.time() + 900)
        _write(directory / "record.json", record)
        return identifier + ":" + nonce


def _retired_directory(identifier: str) -> Path:
    return _directory(identifier).with_name("retired-" + identifier)


def _records() -> list[tuple[Path, dict]]:
    records = []
    for path in _store().glob("*/record.json"):
        record = _read(path)
        expected = {_directory(record.get("id"))}
        if record.get("status") == "retired":
            expected.add(_retired_directory(record["id"]))
        if path.parent not in expected:
            raise MaintenanceError("An installation recovery point has a mismatched identity.")
        records.append((path, record))
    return records


def _usable_point_count() -> int:
    return sum(record.get("status") != "retired" or record.get("cleanup_pending", False)
               for _, record in _records())


def _record_sha256(record: dict) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _installed_identity() -> tuple[Path, str] | None:
    try:
        runtime = _runtime()
        return runtime.resolve(), backup._sha256(runtime)
    except (MaintenanceError, OSError):
        return None


def _retirement_blocker(record: dict, active_id: str | None, installed: tuple[Path, str] | None) -> str | None:
    if active_id is not None or record.get("status") in {"prepared", "applying", "incomplete"}:
        return "An installation operation is unfinished. Finish it before retiring recovery points."
    if record.get("status") in {"authorized", "rolled_back", "retired"}:
        return None
    if record.get("status") == "upgraded":
        try:
            if not re.fullmatch(r"[0-9a-f]{64}", str(record.get("new_runtime_sha256", ""))):
                return "The installed application identity could not be established. Keep this recovery point."
            if installed is None or installed[0] != Path(record["installed_runtime"]).resolve():
                return "The installed application identity could not be established. Keep this recovery point."
            if installed[1] == record.get("new_runtime_sha256"):
                return "This recovery point protects rollback of the installed application. Keep it until a later application update."
            return None
        except (MaintenanceError, OSError, KeyError):
            return "The installed application identity could not be established. Keep this recovery point."
    return "This recovery point is not eligible for retirement."


def _retirement_files(directory: Path) -> list[Path]:
    # Installation points contain flat, private files only. Never recursively
    # delete a user-selected directory or follow an alias into another tree.
    store = file_lock.require_private_directory(_store()).resolve()
    file_lock.require_private_directory(directory)
    if directory.resolve() != directory or directory.parent.resolve() != store:
        raise MaintenanceError("The recovery point is outside its private installation store.")
    files = list(directory.iterdir())
    if len(files) > 128:
        raise MaintenanceError("This recovery point contains an unexpected number of files.")
    for path in files:
        info = path.lstat()
        if file_lock._unsafe_lock_alias(path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise MaintenanceError("This recovery point contains an unexpected directory or file alias; no files were retired.")
    return files


def _prune_retirement_history() -> None:
    retired = [(path, record) for path, record in _records()
               if record.get("status") == "retired" and not record.get("cleanup_pending")]
    retired.sort(key=lambda item: (item[1]["retired_at"], item[1]["id"]), reverse=True)
    for path, _ in retired[MAX_RETIREMENT_HISTORY:]:
        files = _retirement_files(path.parent)
        if files != [path]:
            raise MaintenanceError("A retired recovery point has unexpected remaining files. Its receipt was kept.")
        path.unlink()
        path.parent.rmdir()


def retire(identifier: str, *, expected_sha256: str, actor: str, confirm: bool) -> dict:
    """Retire only an explicitly reviewed, inactive local installation point."""
    if not actor or confirm is not True:
        raise MaintenanceError("Review and explicitly confirm this installation recovery retirement.")
    with _locked():
        directory = _directory(identifier)
        if not directory.exists():
            directory = _retired_directory(identifier)
        record = _read(directory / "record.json")
        if record.get("id") != identifier:
            raise MaintenanceError("The installation recovery point changed. Refresh and review it again.")
        digest = record.get("retirement_sha256") if record.get("status") == "retired" else _record_sha256(record)
        if not isinstance(expected_sha256, str) or not hmac.compare_digest(digest, expected_sha256):
            raise MaintenanceError("The installation recovery point changed. Refresh and review it again.")
        marker = _store() / "active.json"
        active_id = _read(marker).get("id") if marker.exists() else None
        reason = _retirement_blocker(record, active_id, _installed_identity() if record.get("status") == "upgraded" else None)
        if reason:
            raise MaintenanceError(reason)
        _retirement_files(directory)
        if record.get("status") != "retired":
            record = ({key: record.get(key) for key in ("schema", "target", "id", "old_version", "created_at", "completed_at")}
                      | {"previous_status": record["status"], "new_version": record.get("new", {}).get("version")})
            record.update(status="retired", retirement_sha256=digest, retired_by=actor,
                          retired_at=time.time(), cleanup_pending=True)
            # Persist revocation before deleting any retained payload. A crash
            # can retry this exact retirement, but cannot revive an old grant.
            _write(directory / "record.json", record)
        destination = _retired_directory(identifier)
        if directory != destination:
            if destination.exists() or destination.resolve().parent != _store().resolve():
                raise MaintenanceError("The retired recovery directory is not available. Its signed receipt was kept.")
            # Native downloads never create the point directory. Moving the
            # revoked point prevents a delayed download from writing new bytes
            # at its old path after cleanup has reported completion.
            directory.rename(destination)
            directory = destination
        for path in _retirement_files(directory):
            if path.name != "record.json":
                path.unlink()
        record["cleanup_pending"] = False
        _write(directory / "record.json", record)
        _prune_retirement_history()
        return {"id": identifier, "retired": True}


def status() -> dict:
    store = _home() / "backups" / "installation-recovery"
    records, history = [], []
    if store.exists():
        with _locked():
            active_id = _read(store / "active.json").get("id") if (store / "active.json").exists() else None
            saved = _records()
            installed = _installed_identity() if any(record.get("status") == "upgraded" for _, record in saved) else None
            for _, record in saved:
                row = {key: record.get(key) for key in ("id", "status", "old_version", "created_at", "completed_at")}
                if record.get("status") == "retired" and not record.get("cleanup_pending"):
                    history.append(row | {key: record.get(key) for key in ("retired_at", "retired_by", "previous_status", "new_version")})
                    continue
                reason = _retirement_blocker(record, active_id, installed)
                records.append(row | {"new_version": record.get("new", {}).get("version"),
                                      "pending_finalization": record.get("id") == active_id,
                                      "can_retire": reason is None, "retirement_blocker": reason,
                                      "record_sha256": record.get("retirement_sha256") or _record_sha256(record),
                                      "cleanup_pending": record.get("cleanup_pending", False)})
    records.sort(key=lambda row: (not row["pending_finalization"], -row["created_at"]))
    history.sort(key=lambda row: row["retired_at"], reverse=True)
    return {"recovery_points": records, "retirement_history": history[:MAX_RETIREMENT_HISTORY],
            "retirement_history_limit": MAX_RETIREMENT_HISTORY, "point_limit": MAX_TICKETS,
            "incomplete": (store / "active.json").exists(),
            "coverage": "All local workspaces and shared administration. External stores and services are excluded."}
