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
        _archive(identifier).unlink()
        (_store() / (identifier + ".json")).unlink(missing_ok=True)
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


def drill(identifier: str) -> dict:
    with _locked():
        selected = _inspect(identifier)
        if selected["compatibility"] != "compatible":
            raise RecoveryError(selected["compatibility"])
        started = time.perf_counter()
        with backup._temporary_directory(prefix="lightwork-recovery-drill-") as temporary:
            destination = file_lock.ensure_private_directory(Path(temporary) / "workspace")
            with backup.recovery_scope(destination, current_tenant_id_strict(), _key(), max_expanded_bytes=MAX_EXPANDED_BYTES):
                backup.restore_backup(_archive(identifier), replace_existing=True)
            report = {"snapshot": identifier, "archive_sha256": selected["sha256"],
                      "file_count": selected["file_count"], "restored": True, "post_restore_verified": True,
                      "elapsed_seconds": round(time.perf_counter() - started, 6),
                      "live_workspace_changed": False, "external_stores_tested": False}
        file_lock.atomic_write_text(_store() / "last-drill.json", json.dumps(report))
        return report


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
            if backup._stage(_root(), stage) != safety_manifest["files"]:
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
