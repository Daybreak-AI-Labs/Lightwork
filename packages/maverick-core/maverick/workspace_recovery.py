"""Tenant-bound local recovery, with authenticated archives and explicit review.

No caller-selected filesystem paths or unsigned/force restoration are exposed.
Live restore is reserved for the separately started, isolated recovery server.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import importlib.util
import io
import json
import os
import re
import secrets
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import backup, file_lock
from .paths import current_tenant_id_strict, data_dir

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_SNAPSHOTS = 200
MAX_COMPARISON_BYTES = 64 * 1024 * 1024
MAX_CUSTODY_UPLOAD_BYTES = 4 * 1024 * 1024
MAX_CUSTODY_REPORT_BYTES = 64 * 1024
_ID = re.compile(r"^[0-9a-f]{32}$")


class RecoveryError(RuntimeError):
    """Recovery cannot safely complete the requested step."""


def version() -> str:
    return importlib.metadata.version("maverick-agent")


def application_identity() -> str:
    """Exact frozen executable, or source bytes of the installed cohort.

    A release number alone is insufficient: two development builds can share
    a version while disagreeing on accounting or other on-disk formats.
    """
    if getattr(sys, "frozen", False):
        return "binary-sha256:" + backup._sha256(Path(sys.executable))
    digest = hashlib.sha256()
    for name in ("maverick", "maverick_dashboard", "maverick_shield", "maverick_channels",
                 "maverick_evolve", "maverick_knowledge", "maverick_mcp", "maverick_installer"):
        spec = importlib.util.find_spec(name)
        digest.update(name.encode() + b"\0")
        if spec is None or not spec.origin:
            digest.update(b"absent\0")
            continue
        root = Path(spec.origin).parent
        for source in sorted(root.rglob("*.py")):
            digest.update(source.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(source.read_bytes()).digest())
    return "source-sha256:" + digest.hexdigest()


def _root() -> Path:
    if not current_tenant_id_strict():
        raise RecoveryError("Select a named workspace before using recovery. Shared administration files are excluded.")
    return file_lock.ensure_private_directory(backup._filesystem_path(data_dir())).absolute()


def _store() -> Path:
    return file_lock.ensure_private_directory(_root() / "backups" / "recovery")


def target() -> str:
    return hashlib.sha256(json.dumps([str(_root()), current_tenant_id_strict()], separators=(",", ":")).encode()).hexdigest()


@contextmanager
def _locked():
    with file_lock.cross_process_lock(_store() / "operations", strict=True):
        yield


def _key() -> bytes:
    injected = os.environ.get("MAVERICK_BACKUP_SIGNING_KEY", "")
    if injected.strip():
        return backup._decode_operator_key(injected)
    path = _store() / "operator.key"
    if not path.exists():
        raise RecoveryError("Create or import a recovery key first, and keep a separate safe copy.")
    file_lock.ensure_private_file(path)
    return backup._decode_operator_key(file_lock.atomic_read_text(path))


def configure_key(*, imported: str | None = None) -> dict:
    with _locked():
        if os.environ.get("MAVERICK_BACKUP_SIGNING_KEY", "").strip() or (_store() / "operator.key").exists():
            raise RecoveryError("A recovery key already exists. Replacing it would strand earlier snapshots.")
        key = backup._decode_operator_key(imported) if imported is not None else secrets.token_bytes(32)
        file_lock.atomic_create_text(_store() / "operator.key", key.hex() + "\n")
        # Returned only by an explicit, authenticated download. Never log it or
        # include it in the archive whose authenticity it establishes.
        return {"key": key.hex(), "key_id": backup._key_id(key)}


def export_key() -> str:
    with _locked():
        if os.environ.get("MAVERICK_BACKUP_SIGNING_KEY", "").strip():
            raise RecoveryError("The deployment custodian manages this recovery key; it cannot be downloaded from the app.")
        return _key().hex()


def encryption_custody() -> dict:
    """Report the custody boundary without generating, reading or exposing keys."""
    from . import crypto_at_rest
    from .config import load_config

    sources = [crypto_at_rest._KEY_PATH] if crypto_at_rest._KEY_PATH.is_file() else []
    sources.extend(crypto_at_rest._keyring_dir().glob("*.key"))
    kms = load_config().get("kms") or {}
    external = bool(os.environ.get("MAVERICK_ENCRYPTION_KEY") or os.environ.get("MAVERICK_KMS_KEK")
                    or not isinstance(kms, dict) or kms.get("provider", "local") != "local")
    return {"local_key_files": len(sources), "local_export_available": bool(sources),
            "external_key_custody": external,
            "detail": "Encrypted drafts and other sealed files require their matching encryption keys. Local primary and rotation keys may be shared by several workspaces and are exported separately. Wrapped tenant keys also require their original key-encryption key. Environment and remote key-service keys are not exported."}


def export_encryption_keys() -> bytes:
    """Explicit administrator escrow; never installs or rotates shared keys."""
    from . import crypto_at_rest

    with _locked(), backup._temporary_directory(prefix="lightwork-key-escrow-") as temporary:
        signing_key = _key()
        if encryption_custody()["local_key_files"] > 512:
            raise RecoveryError("The local encryption keyring exceeds the in-app export limit.")
        directory = file_lock.ensure_private_directory(Path(temporary) / "keys")
        try:
            paths = crypto_at_rest.backup_key_material(directory)
        except crypto_at_rest.EncryptionUnavailable as exc:
            raise RecoveryError("No readable local encryption keys are available. Keep the original deployment key or key-service custody separately.") from exc
        output = io.BytesIO()
        manifest = {"schema": 1, "kind": "local-encryption-key-escrow", "workspace": current_tenant_id_strict(),
                    "shared_key_material": True, "created_at": time.time(), "files": {}}
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for path in paths:
                if path.stat().st_size > 4096:
                    raise RecoveryError("Local encryption key material has an unexpected size.")
                payload = file_lock.atomic_read_bytes(path)
                manifest["files"][path.name] = {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
                archive.writestr("keys/" + path.name, payload)
            archive.writestr("manifest.json", json.dumps(backup._authenticate_for_write(manifest, signing_key)))
            archive.writestr("README.txt", "Sensitive plaintext encryption keys. Store in your offline vault or secrets manager, separately from snapshots. These keys may unlock several workspaces. This is a custody export, not a workspace archive. Restore shared keys only through your deployment administrator's reviewed key-custody procedure; the app never overwrites shared keys. Environment and remote key-service keys are excluded.\n")
        return output.getvalue()


def restore_blockers() -> list[str]:
    from .config import config_source_errors, load_config
    from .control_plane_lease import override_enabled
    from .governed_records import configured_governed_records_backend
    from .grpc_dispatcher import configured_target
    from .world_model_backends import is_postgres_configured

    cfg = load_config()
    if config_source_errors(include_tenant=True):
        return ["Deployment settings are unreadable; recovery cannot establish local ownership."]
    blockers = []
    if is_postgres_configured():
        blockers.append("PostgreSQL is managed separately. Use its verified database recovery process before changing local files.")
    if configured_governed_records_backend() != "local":
        blockers.append("Governance records use a shared storage authority and require its coordinated recovery process.")
    queue = cfg.get("queue") or {}
    if not isinstance(queue, dict) or queue.get("backend") or configured_target():
        blockers.append("Independently managed workers are configured. Their shutdown and storage recovery require a coordinated administrator procedure.")
    memory = cfg.get("memory") or {}
    if not isinstance(memory, dict) or str(memory.get("backend") or "local") not in {"local", "sqlite", "auto"}:
        blockers.append("An external memory store is configured and is outside this local snapshot.")
    knowledge = cfg.get("knowledge") or {}
    if not isinstance(knowledge, dict) or str(knowledge.get("store") or "sqlite") != "sqlite" or knowledge.get("path"):
        blockers.append("Knowledge storage is outside the default local workspace and needs a coordinated recovery procedure.")
    for name in ("redis", "qdrant", "pgvector"):
        if cfg.get(name):
            blockers.append("An external " + name + " store requires its own verified recovery process.")
    if any(os.environ.get(name, "").strip() for name in ("MAVERICK_QUEUE_REDIS_DSN", "MAVERICK_PG_DSN", "MAVERICK_KNOWLEDGE_DSN", "QDRANT_URL", "REDIS_URL")):
        blockers.append("An external store connection is configured and cannot be restored by this local operation.")
    if override_enabled():
        blockers.append("The single control plane lock is disabled. Exclusive local ownership cannot be established.")
    return blockers


def _archive(identifier: str) -> Path:
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
        raise RecoveryError("Select a snapshot from this workspace.")
    path = _store() / (identifier + ".tgz")
    if not path.is_file():
        raise RecoveryError("This snapshot is no longer available.")
    file_lock.ensure_private_file(path)
    if path.stat().st_size > MAX_UPLOAD_BYTES:
        raise RecoveryError("This snapshot exceeds the in-app archive limit.")
    return path


def _metadata(*, kind: str) -> dict:
    return {"schema": 1, "tenant_id": current_tenant_id_strict(), "application_version": version(),
            "application_identity": application_identity(),
            "kind": kind, "coverage": "tenant-local-files", "consistency": "per-file and SQLite online snapshots"}


def _compatible(manifest: dict) -> None:
    context = manifest.get("recovery")
    if not isinstance(context, dict) or context.get("schema") != 1:
        raise RecoveryError("This archive lacks a verified workspace/version binding. Use the legacy administrator recovery process.")
    if context.get("tenant_id") != current_tenant_id_strict():
        raise RecoveryError("The snapshot belongs to a different workspace.")
    if context.get("application_version") != version():
        raise RecoveryError("The snapshot requires a different application version. Cross-version restore needs a reviewed migration plan.")
    if context.get("application_identity") != application_identity():
        raise RecoveryError("The snapshot requires a different application build. Restore with its exact build or a reviewed compatibility migration.")


def _inspect(identifier: str) -> dict:
    path = _archive(identifier)
    with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
        manifest = backup.verify_backup(path)
    compatibility = "compatible"
    try:
        _compatible(manifest)
    except RecoveryError as exc:
        compatibility = str(exc)
    files = manifest["files"]
    context = manifest.get("recovery")
    context = context if isinstance(context, dict) else {}
    result = {"id": identifier, "sha256": backup._sha256(path), "bytes": path.stat().st_size,
            "created_at": manifest.get("created_at"), "file_count": len(files),
            "expanded_bytes": sum(row["size"] for row in files.values()),
            "tenant": context.get("tenant_id"),
            "application_version": context.get("application_version"),
            "authenticated": True, "payload_verified": True, "compatibility": compatibility,
            "contains_encryption_key": any(name.startswith("keys/") for name in files),
            "contains_drafts": any(name.startswith("draft-recovery/") for name in files)}
    file_lock.atomic_write_text(_store() / (identifier + ".json"), json.dumps(backup._authenticate_for_write(result, _key())))
    return result


def inspect(identifier: str) -> dict:
    with _locked():
        return _inspect(identifier)


def _comparison_path(identifier: str) -> Path:
    _archive(identifier)  # Validate the server-resolved snapshot identity.
    return _store() / (identifier + ".comparison.json")


def _comparison_page(report: dict, *, offset: int, limit: int) -> dict:
    if type(offset) is not int or not 0 <= offset <= backup.MAX_ARCHIVE_MEMBERS * 2 or type(limit) is not int or not 1 <= limit <= 100:
        raise RecoveryError("The comparison page is outside the supported bounds.")
    return {**{key: value for key, value in report.items() if key not in {"items", "authentication", "actor"}},
            "items": report["items"][offset:offset + limit], "offset": offset, "limit": limit,
            "has_more": offset + limit < report["total_changes"]}


def compare_snapshot(identifier: str, *, actor: str, isolated: bool) -> dict:
    """Keep the latest signed comparison per snapshot; never change live state."""
    if not actor:
        raise RecoveryError("A current administrator identity is required for comparison.")
    with _locked():
        selected = _inspect(identifier)
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            comparison = backup.compare_backup(_archive(identifier))
        if backup._sha256(_archive(identifier)) != selected["sha256"]:
            raise RecoveryError("The snapshot changed during comparison. Verify it again.")
        report = {"schema": 1, "kind": "workspace-recovery-comparison", "report_id": secrets.token_hex(16),
                  "target": target(), "tenant": current_tenant_id_strict(), "actor": actor,
                  "snapshot": {key: selected[key] for key in ("id", "sha256", "application_version", "compatibility")},
                  "application_version": version(), "application_identity": application_identity(),
                  "compared_at": time.time(), "isolated": isolated is True, "restore_authorized": False,
                  "consistency": "per-file and SQLite online snapshots; live state can change after capture",
                  "coverage": "local file metadata only; no payload, business-invariant, schema-compatibility or external-effect validation",
                  **comparison}
        signed = backup._authenticate_for_write(report, _key())
        encoded = json.dumps(signed, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_COMPARISON_BYTES:
            raise RecoveryError("The comparison exceeds the in-app evidence size limit. Existing comparison evidence was retained.")
        file_lock.atomic_write_text(_comparison_path(identifier), encoded)
        return _comparison_page(signed, offset=0, limit=50)


def _read_comparison(identifier: str, report_id: str, *, actor: str) -> dict:
    path = _comparison_path(identifier)
    try:
        if path.stat().st_size > MAX_COMPARISON_BYTES:
            raise RecoveryError("The saved comparison exceeds the evidence size limit.")
        report = json.loads(file_lock.atomic_read_text(path))
        if not isinstance(report, dict):
            raise RecoveryError("The saved comparison is malformed. Compare the snapshot again.")
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            backup._verify_authentication(report, allow_unsigned=False, label="recovery comparison")
    except (OSError, ValueError, RecursionError, backup.BackupError) as exc:
        raise RecoveryError("The signed comparison is unavailable. Compare the snapshot again.") from exc
    if (not isinstance(report.get("snapshot"), dict) or not isinstance(report.get("items"), list)
            or type(report.get("total_changes")) is not int or report["total_changes"] != len(report["items"])
            or type(report.get("protected_changes")) is not int or not 0 <= report["protected_changes"] <= report["total_changes"]
            or any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
                   or item.get("change") not in ("added", "changed", "deleted") or type(item.get("protected")) is not bool
                   for item in report["items"])):
        raise RecoveryError("The saved comparison is malformed. Compare the snapshot again.")
    if (report.get("schema") != 1 or report.get("kind") != "workspace-recovery-comparison"
            or not isinstance(report_id, str) or not _ID.fullmatch(report_id) or not hmac.compare_digest(str(report.get("report_id", "")), report_id)
            or report.get("target") != target() or report.get("tenant") != current_tenant_id_strict()
            or not actor or report.get("actor") != actor or report.get("application_identity") != application_identity()
            or report.get("restore_authorized") is not False
            or report.get("snapshot", {}).get("id") != identifier
            or report.get("snapshot", {}).get("sha256") != backup._sha256(_archive(identifier))):
        raise RecoveryError("The snapshot, account, build or comparison changed. Compare it again.")
    return report


def comparison_page(identifier: str, report_id: str, *, actor: str, offset: int = 0, limit: int = 50) -> dict:
    with _locked():
        return _comparison_page(_read_comparison(identifier, report_id, actor=actor), offset=offset, limit=limit)


def export_comparison(identifier: str, report_id: str, *, actor: str) -> bytes:
    with _locked():
        report = _read_comparison(identifier, report_id, actor=actor)
        return (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _create(kind: str) -> dict:
    if len(list(_store().glob("*.tgz"))) >= MAX_SNAPSHOTS:
        raise RecoveryError("The local snapshot limit is reached. Export and retire snapshots under your retention policy first.")
    identifier = secrets.token_hex(16)
    path = _store() / (identifier + ".tgz")
    with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
        backup.create_backup(path, recovery_metadata=_metadata(kind=kind))
    try:
        result = _inspect(identifier)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return result


def create() -> dict:
    with _locked():
        return _create("snapshot")


def list_snapshots() -> list[dict]:
    with _locked():
        rows = []
        for path in sorted(_store().glob("*.tgz"), key=lambda item: item.name)[:MAX_SNAPSHOTS]:
            identifier = path.stem
            if not _ID.fullmatch(identifier):
                continue
            # Listing does not claim full verification. Inspect always checks
            # the current signed manifest and all current payload bytes.
            row = {"id": identifier, "bytes": path.stat().st_size}
            try:
                cached = json.loads(file_lock.atomic_read_text(_store() / (identifier + ".json")))
                with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
                    backup._verify_authentication(cached, allow_unsigned=False, label="snapshot index")
                row["created_at"] = cached.get("created_at")
            except (OSError, ValueError, backup.BackupError, RecoveryError):
                pass
            rows.append(row)
        return rows


def retire(identifier: str, expected_sha256: str) -> dict:
    with _locked():
        selected = _inspect(identifier)
        if not hmac.compare_digest(selected["sha256"], expected_sha256):
            raise RecoveryError("The snapshot changed. Verify it again before retiring it.")
        for name in ("review.json", "last-restore.json"):
            path = _store() / name
            if path.exists():
                record = json.loads(file_lock.atomic_read_text(path))
                with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
                    backup._verify_authentication(record, allow_unsigned=False, label="recovery retention record")
                if identifier in {record.get("snapshot"), record.get("safety_snapshot")}:
                    raise RecoveryError("This snapshot is retained for the current or most recent restore.")
        comparison_path = _comparison_path(identifier)
        drill_path = _store() / (identifier + ".drill.json")
        _archive(identifier).unlink()
        (_store() / (identifier + ".json")).unlink(missing_ok=True)
        comparison_path.unlink(missing_ok=True)
        drill_path.unlink(missing_ok=True)
        (_store() / (identifier + ".custody-test.json")).unlink(missing_ok=True)
        return {"retired": True, "id": identifier}


def import_archive(staged: Path) -> dict:
    with _locked():
        if len(list(_store().glob("*.tgz"))) >= MAX_SNAPSHOTS:
            raise RecoveryError("The local snapshot limit is reached.")
        if staged.stat().st_size > MAX_UPLOAD_BYTES:
            raise RecoveryError("The archive is too large for in-app recovery.")
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            manifest = backup.verify_backup(staged)
            # Foreign archives must not populate another tenant's recovery UI.
            context = manifest.get("recovery")
            if not isinstance(context, dict):
                raise RecoveryError("The archive lacks a verified workspace binding.")
            if context.get("tenant_id") != current_tenant_id_strict():
                raise RecoveryError("The snapshot belongs to a different workspace.")
        identifier = secrets.token_hex(16)
        destination = _store() / (identifier + ".tgz")
        backup._copy_verified_file(staged, destination)
        return _inspect(identifier)


def download_path(identifier: str) -> Path:
    with _locked():
        _inspect(identifier)
        return _archive(identifier)


def _last_restore() -> dict | None:
    path = _store() / "last-restore.json"
    if not path.exists():
        return None
    try:
        record = json.loads(file_lock.atomic_read_text(path))
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key()):
            backup._verify_authentication(record, allow_unsigned=False, label="restore receipt")
        return {name: record.get(name) for name in ("state", "snapshot", "safety_snapshot", "completed_at", "elapsed_seconds")}
    except (OSError, ValueError, backup.BackupError, RecoveryError):
        return {"state": "receipt_unavailable"}


def status() -> dict:
    key_id = None
    key_error = None
    try:
        key_id = backup._key_id(_key())
    except (RecoveryError, backup.BackupError, OSError) as exc:
        key_error = str(exc)
    return {"tenant": current_tenant_id_strict(), "target": target(), "application_version": version(),
            "key_id": key_id, "key_error": key_error, "max_archive_bytes": MAX_UPLOAD_BYTES, "max_expanded_bytes": MAX_EXPANDED_BYTES,
            "snapshots": list_snapshots(), "restore_blockers": restore_blockers(), "encryption_custody": encryption_custody(), "last_restore": _last_restore(),
            "coverage": ["Files in the selected workspace, including governance/evidence records, local SQLite databases, audit files and encrypted editor drafts.",
                         "Each SQLite database is copied consistently. Ordinary live snapshots are not a simultaneous transaction across every file."],
            "excluded": ["Other workspaces and shared administration/configuration outside the selected workspace.",
                         "PostgreSQL, remote memory stores, external-provider state, object-storage buckets and independently managed workers.",
                         "Shared encryption keys outside this workspace, and environment/HSM/KMS keys. Keep their custody and restore procedures separately; encrypted data is unusable without the matching key.",
                         "Recovery archives and their signing key. Keep exported archives and a separate safe key copy outside this computer."],
            "verification_scope": "File hashes, sizes and local SQLite integrity; application-level decryption, external stores and cross-file business invariants are not certified.",
            "live_restore_available": False}


def drill(identifier: str, *, actor: str = "local-operator") -> dict:
    if not actor:
        raise RecoveryError("A current operator identity is required for a restore drill.")
    with _locked():
        selected = _inspect(identifier)
        if selected["compatibility"] != "compatible":
            raise RecoveryError(selected["compatibility"])
        started = time.perf_counter()
        with backup._temporary_directory(prefix="lightwork-recovery-drill-") as temporary:
            destination = file_lock.ensure_private_directory(Path(temporary) / "workspace")
            with backup.recovery_scope(destination, current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
                backup.restore_backup(_archive(identifier), replace_existing=True)
            report = {"schema": 1, "kind": "workspace-restore-drill", "report_id": secrets.token_hex(16),
                      "snapshot": identifier, "archive_sha256": selected["sha256"],
                      "snapshot_created_at": selected["created_at"], "target": target(),
                      "tenant": current_tenant_id_strict(), "actor": actor,
                      "application_version": version(), "application_identity": application_identity(),
                      "completed_at": time.time(), "restore_authorized": False,
                      "file_count": selected["file_count"], "restored": True, "post_restore_verified": True,
                      "elapsed_seconds": round(time.perf_counter() - started, 6),
                      "live_workspace_changed": False, "external_stores_tested": False,
                      "verification_scope": "archived file hashes, sizes and SQLite integrity; no application decryption, cross-file business invariants or external-system acceptance"}
        if backup._sha256(_archive(identifier)) != selected["sha256"]:
            raise RecoveryError("The snapshot changed during the restore drill. Verify it again.")
        signed = backup._authenticate_for_write(report, _key())
        encoded = json.dumps(signed, allow_nan=False)
        file_lock.atomic_write_text(_store() / (identifier + ".drill.json"), encoded)
        file_lock.atomic_write_text(_store() / "last-drill.json", encoded)
        return report


def _read_drill(identifier: str, *, actor: str) -> dict | None:
    archive = _archive(identifier)
    path = _store() / (identifier + ".drill.json")
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 64 * 1024:
            raise RecoveryError("The saved drill report exceeds its evidence size limit.")
        report = json.loads(file_lock.atomic_read_text(path))
        if not isinstance(report, dict):
            raise RecoveryError("The saved drill report is malformed. Run a new temporary restore drill.")
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            backup._verify_authentication(report, allow_unsigned=False, label="restore drill report")
    except (OSError, ValueError, RecursionError, backup.BackupError) as exc:
        raise RecoveryError("The signed drill report is unavailable. Run a new temporary restore drill.") from exc
    if (any(not isinstance(report.get(field), str) or not report[field]
            for field in ("report_id", "application_identity", "application_version", "verification_scope"))
            or not _ID.fullmatch(report["report_id"])
            or type(report.get("snapshot_created_at")) not in {int, float} or report["snapshot_created_at"] <= 0
            or type(report.get("completed_at")) not in {int, float} or report["completed_at"] <= 0
            or type(report.get("elapsed_seconds")) not in {int, float} or report["elapsed_seconds"] < 0
            or type(report.get("file_count")) is not int or report["file_count"] < 0
            or report.get("restored") is not True or report.get("post_restore_verified") is not True
            or report.get("live_workspace_changed") is not False or report.get("external_stores_tested") is not False):
        raise RecoveryError("The saved drill report is malformed. Run a new temporary restore drill.")
    if (report.get("schema") != 1 or report.get("kind") != "workspace-restore-drill"
            or report.get("target") != target() or report.get("tenant") != current_tenant_id_strict()
            or not actor or report.get("actor") != actor or report.get("snapshot") != identifier
            or report.get("archive_sha256") != backup._sha256(archive) or report.get("restore_authorized") is not False):
        raise RecoveryError("The snapshot, account or drill report changed. Run a new temporary restore drill.")
    return report


def drill_report(identifier: str, *, actor: str) -> dict:
    with _locked():
        report = _read_drill(identifier, actor=actor)
        if report is None:
            return {"state": "not_recorded", "snapshot": identifier}
        return {**{key: value for key, value in report.items() if key not in {"authentication", "actor"}},
                "state": "recorded", "current_build_matches": report["application_identity"] == application_identity()}


def export_drill_report(identifier: str, report_id: str, *, actor: str) -> bytes:
    with _locked():
        report = _read_drill(identifier, actor=actor)
        if report is None or not isinstance(report_id, str) or not hmac.compare_digest(str(report.get("report_id", "")), report_id):
            raise RecoveryError("The saved drill report changed. Load the selected snapshot's report again.")
        return (json.dumps(report, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _custody_environment(directory: Path, tenant: str) -> dict[str, str]:
    """A fresh child's settings never inherit operator credentials or aliases."""
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
               "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "LANG", "LC_ALL", "TZ"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    home = file_lock.ensure_private_directory(directory / "home")
    temporary = file_lock.ensure_private_directory(directory / "temporary")
    profile = file_lock.ensure_private_directory(directory / "profile")
    appdata = file_lock.ensure_private_directory(profile / "AppData")
    file_lock.atomic_create_text(home / "config.toml", "# Isolated recovery rehearsal; no provider configuration.\n")
    paths = [str(Path(sys.executable).parent)]
    if os.name == "nt":
        paths.append(str(Path(os.environ["SystemRoot"]) / "System32"))
    environment.update(PATH=os.pathsep.join(paths), HOME=str(profile), USERPROFILE=str(profile),
                       APPDATA=str(appdata), LOCALAPPDATA=str(appdata), TEMP=str(temporary), TMP=str(temporary),
                       TMPDIR=str(temporary), MAVERICK_HOME=str(home), MAVERICK_CONFIG=str(home / "config.toml"),
                       MAVERICK_CLIENT_ID=tenant, PYTHONUTF8="1", PYINSTALLER_RESET_ENVIRONMENT="1", NO_COLOR="1")
    return environment


def _validate_custody_result(result: dict) -> None:
    flags = {"fresh_process": True, "coverage_complete": False, "live_workspace_changed": False, "external_stores_tested": False}
    if (not isinstance(result, dict) or result.get("schema") != 1
            or any(result.get(key) is not value for key, value in flags.items())
            or result.get("key_source") != "uploaded-custody-copy"
            or not isinstance(result.get("application_identity"), str)
            or any(type(result.get(key)) is not int or not 0 <= result[key] <= 10_000_000
                   for key in ("files_scanned", "sqlite_columns_scanned", "unexamined_files"))
            or not isinstance(result.get("checks"), list) or not 1 <= len(result["checks"]) <= 20
            or not isinstance(result.get("limits"), list) or not 1 <= len(result["limits"]) <= 20
            or any(not isinstance(item, str) or not 1 <= len(item) <= 2000 for item in result["limits"])):
        raise RecoveryError("The retained-key test report is malformed. Run a new temporary test.")
    names = set()
    totals = {"detected": 0, "verified": 0, "failed": 0, "unsupported": 0}
    for row in result["checks"]:
        if (not isinstance(row, dict) or not isinstance(row.get("format"), str) or not 1 <= len(row["format"]) <= 100
                or row["format"] in names or set(row) != {"format", *totals}
                or any(type(row.get(key)) is not int or not 0 <= row[key] <= 10_000_000 for key in totals)
                or row["detected"] != row["verified"] + row["failed"] + row["unsupported"]):
            raise RecoveryError("The retained-key test counts are malformed. Run a new temporary test.")
        names.add(row["format"])
        for key in totals:
            totals[key] += row[key]
    expected = ("failed" if totals["failed"] else "incomplete" if totals["unsupported"]
                else "verified_supported_formats" if totals["verified"] else "no_encrypted_records")
    if result.get("outcome") != expected:
        raise RecoveryError("The retained-key test outcome is malformed. Run a new temporary test.")


def test_key_custody(identifier: str, custody_path: Path | bytes, *, expected_sha256: str, actor: str) -> dict:
    """Rehearse using only a separately supplied key copy in a fresh process."""
    if not actor:
        raise RecoveryError("A current administrator identity is required to test retained keys.")
    with _locked():
        selected = _inspect(identifier)
        if selected["compatibility"] != "compatible" or selected["sha256"] != expected_sha256:
            raise RecoveryError("The snapshot or application changed. Verify the selected snapshot again.")
        if isinstance(custody_path, bytes):
            custody = custody_path
        else:
            if not 0 < custody_path.stat().st_size <= MAX_CUSTODY_UPLOAD_BYTES:
                raise RecoveryError("Select a separately retained encryption-key ZIP of at most 4 MiB.")
            custody = file_lock.atomic_read_bytes(custody_path)
        if not 0 < len(custody) <= MAX_CUSTODY_UPLOAD_BYTES:
            raise RecoveryError("The retained key copy exceeds the upload limit.")
        started = time.perf_counter()
        identity = application_identity()
        tenant = current_tenant_id_strict()
        with backup._temporary_directory(prefix="lw-custody-") as temporary:
            directory = file_lock.ensure_private_directory(Path(temporary))
            environment = _custody_environment(directory, tenant)
            file_lock.atomic_create_bytes(directory / "snapshot.tgz", file_lock.atomic_read_bytes(_archive(identifier)))
            file_lock.atomic_create_bytes(directory / "custody.zip", custody)
            file_lock.atomic_create_text(directory / "recovery.key", _key().hex())
            request = {"schema": 1, "tenant": tenant, "archive_sha256": selected["sha256"],
                       "custody_sha256": hashlib.sha256(custody).hexdigest(),
                       "application_identity": identity, "application_version": version()}
            file_lock.atomic_create_text(directory / "request.json", json.dumps(request))
            command = ([sys.executable, "--recovery-rehearsal-worker", str(directory)] if getattr(sys, "frozen", False)
                       else [sys.executable, "-m", "maverick.recovery_rehearsal", str(directory)])
            try:
                completed = subprocess.run(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired as exc:
                raise RecoveryError("The temporary retained-key test exceeded its time limit. Previous evidence was kept.") from exc
            path = directory / "result.json"
            if completed.returncode or not path.is_file() or path.stat().st_size > MAX_CUSTODY_REPORT_BYTES:
                raise RecoveryError("The temporary test could not verify this snapshot and retained key copy. Previous evidence was kept.")
            try:
                result = json.loads(file_lock.atomic_read_text(path))
            except (ValueError, RecursionError) as exc:
                raise RecoveryError("The temporary retained-key test returned an unreadable report. Previous evidence was kept.") from exc
            _validate_custody_result(result)
            if result["application_identity"] != identity:
                raise RecoveryError("The temporary test used a different application build. Previous evidence was kept.")
        # Successful cleanup is part of completion; retain no uploaded keys.
        if backup._sha256(_archive(identifier)) != expected_sha256 or application_identity() != identity:
            raise RecoveryError("The snapshot or application changed during the temporary test. Previous evidence was kept.")
        report = {**result, "kind": "workspace-custody-test", "report_id": secrets.token_hex(16),
                  "snapshot": identifier, "archive_sha256": selected["sha256"], "custody_sha256": request["custody_sha256"],
                  "snapshot_created_at": selected["created_at"], "target": target(), "tenant": tenant, "actor": actor,
                  "application_version": version(), "completed_at": time.time(), "elapsed_seconds": round(time.perf_counter() - started, 6),
                  "restore_authorized": False}
        signed = backup._authenticate_for_write(report, _key())
        encoded = json.dumps(signed, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_CUSTODY_REPORT_BYTES:
            raise RecoveryError("The retained-key report exceeds its evidence size limit. Previous evidence was kept.")
        file_lock.atomic_write_text(_store() / (identifier + ".custody-test.json"), encoded)
        return {**{key: value for key, value in report.items() if key != "actor"}, "state": "recorded", "current_build_matches": True}


def _read_custody_test(identifier: str, *, actor: str) -> dict | None:
    archive = _archive(identifier)
    path = _store() / (identifier + ".custody-test.json")
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_CUSTODY_REPORT_BYTES:
            raise RecoveryError("The saved retained-key report exceeds its evidence size limit.")
        report = json.loads(file_lock.atomic_read_text(path))
        if not isinstance(report, dict):
            raise RecoveryError("The retained-key test report is malformed. Run a new temporary test.")
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key()):
            backup._verify_authentication(report, allow_unsigned=False, label="retained-key test report")
    except (OSError, ValueError, RecursionError, backup.BackupError) as exc:
        raise RecoveryError("The signed retained-key test is unavailable. Run a new temporary test.") from exc
    _validate_custody_result(report)
    if (any(not isinstance(report.get(field), str) or len(report[field]) != 64
            or any(char not in "0123456789abcdef" for char in report[field])
            for field in ("archive_sha256", "custody_sha256"))
            or not isinstance(report.get("application_version"), str)
            or type(report.get("completed_at")) not in {int, float} or report["completed_at"] <= 0
            or type(report.get("snapshot_created_at")) not in {int, float} or report["snapshot_created_at"] <= 0
            or type(report.get("elapsed_seconds")) not in {int, float} or report["elapsed_seconds"] < 0):
        raise RecoveryError("The retained-key test metadata is malformed. Run a new temporary test.")
    if (report.get("kind") != "workspace-custody-test" or not isinstance(report.get("report_id"), str)
            or not _ID.fullmatch(report["report_id"]) or report.get("target") != target()
            or report.get("tenant") != current_tenant_id_strict() or not actor or report.get("actor") != actor
            or report.get("snapshot") != identifier or report.get("archive_sha256") != backup._sha256(archive)
            or report.get("restore_authorized") is not False):
        raise RecoveryError("The snapshot, account or retained-key report changed. Run a new temporary test.")
    return report


def custody_test_report(identifier: str, *, actor: str) -> dict:
    with _locked():
        report = _read_custody_test(identifier, actor=actor)
        if report is None:
            return {"state": "not_recorded", "snapshot": identifier}
        return {**{key: value for key, value in report.items() if key not in {"authentication", "actor"}},
                "state": "recorded", "current_build_matches": report["application_identity"] == application_identity()}


def export_custody_test(identifier: str, report_id: str, *, actor: str) -> bytes:
    with _locked():
        report = _read_custody_test(identifier, actor=actor)
        if report is None or not isinstance(report_id, str) or not _ID.fullmatch(report_id) or not hmac.compare_digest(report["report_id"], report_id):
            raise RecoveryError("The retained-key test changed. Load the selected snapshot's evidence again.")
        return (json.dumps(report, allow_nan=False, indent=2) + "\n").encode("utf-8")


def prepare_restore(identifier: str, *, isolated: bool, actor: str) -> dict:
    with _locked():
        if not isolated:
            raise RecoveryError("Open local recovery mode from the desktop app before preparing a live restore.")
        blockers = restore_blockers()
        if blockers:
            raise RecoveryError(" ".join(blockers))
        selected = _inspect(identifier)
        if selected["compatibility"] != "compatible":
            raise RecoveryError(selected["compatibility"])
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            backup.assert_accounting_unchanged(_archive(identifier))
        safety = _create("before-restore")
        review = {"review": secrets.token_hex(16), "target": target(), "snapshot": identifier,
                  "actor": actor,
                  "archive_sha256": selected["sha256"], "safety_snapshot": safety["id"],
                  "expires_at": time.time() + 300, "application_version": version()}
        file_lock.atomic_write_text(_store() / "review.json", json.dumps(backup._authenticate_for_write(review, _key())))
        return {**review, "file_count": selected["file_count"], "tenant": current_tenant_id_strict()}


def apply_restore(review_id: str, expected_target: str, *, isolated: bool, actor: str) -> dict:
    with _locked():
        if not isolated or restore_blockers():
            raise RecoveryError("Restore requires isolated local recovery mode with no external stores or independent workers.")
        review_path = _store() / "review.json"
        try:
            review = json.loads(file_lock.atomic_read_text(review_path))
            with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
                backup._verify_authentication(review, allow_unsigned=False, label="recovery review")
        except (OSError, ValueError, backup.BackupError) as exc:
            raise RecoveryError("The restore review is unavailable. Prepare a new review.") from exc
        if (not hmac.compare_digest(str(review.get("review", "")), review_id)
                or review.get("target") != target() or not hmac.compare_digest(target(), expected_target)
                or review.get("actor") != actor
                or review.get("application_version") != version() or review.get("expires_at", 0) < time.time()):
            raise RecoveryError("The workspace, version or restore review changed. Prepare a new review.")
        selected = _inspect(review["snapshot"])
        if selected["sha256"] != review["archive_sha256"] or selected["compatibility"] != "compatible":
            raise RecoveryError("The selected snapshot changed after review.")
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            backup.assert_accounting_unchanged(_archive(review["snapshot"]))
        _inspect(review["safety_snapshot"])
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            safety_manifest = backup.read_manifest(_archive(review["safety_snapshot"]))
        with backup._temporary_directory(prefix="lightwork-restore-drift-") as temporary, backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            stage = file_lock.ensure_private_directory(Path(temporary) / "data")
            if backup._stage(_root(), stage, include_keys=backup._archive_includes_keys(safety_manifest["files"])) != safety_manifest["files"]:
                raise RecoveryError("Workspace files changed after review. Prepare a new safety snapshot before restoring.")
        # Consume before applying. A retry never repeats a possibly completed
        # restore; the independent pre-restore archive remains downloadable.
        pending = {"state": "completion_unconfirmed", "snapshot": review["snapshot"],
                   "safety_snapshot": review["safety_snapshot"], "actor": actor}
        file_lock.atomic_write_text(_store() / "last-restore.json", json.dumps(backup._authenticate_for_write(pending, _key())))
        review_path.unlink()
        started = time.perf_counter()
        with backup.recovery_scope(_root(), current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
            backup.restore_backup(_archive(review["snapshot"]), replace_existing=True)
        report = {"state": "completed", "snapshot": review["snapshot"], "safety_snapshot": review["safety_snapshot"],
                  "actor": actor, "completed_at": time.time(),
                  "restored": True, "post_restore_verified": True, "restart_required": True,
                  "elapsed_seconds": round(time.perf_counter() - started, 6), "external_stores_restored": False}
        file_lock.atomic_write_text(_store() / "last-restore.json", json.dumps(backup._authenticate_for_write(report, _key())))
        return report
