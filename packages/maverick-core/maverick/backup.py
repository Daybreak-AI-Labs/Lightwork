"""Authenticated, bounded backup / transactional restore for one client.

Backups snapshot the active tenant/client data root. SQLite databases use the
online backup API; every payload file is content-hashed; and ``manifest.json``
is authenticated with an operator-custodied HMAC-SHA256 key supplied through
``MAVERICK_BACKUP_SIGNING_KEY`` (exactly 32 bytes encoded as hex or base64).
The HMAC is the authenticity boundary. Payload SHA-256 values alone are only
self-consistency checks and are never described or treated as authentication.

Unsigned legacy archives have an intentionally awkward compatibility path:
callers must pass ``allow_unsigned=True`` to ``read_manifest`` / ``restore_backup``
(and may create one with the same explicit flag). The CLI has no unsigned
override. ``force=True`` only overrides client/schema compatibility and never
bypasses signature verification.

Restore scans tar streams without ``getmembers``/``extractall``, enforces hard
member/path/type/per-file/expanded-size limits, writes only into a private
staging tree, and authenticates/verifies the complete payload before touching
live state. Application is journaled: originals plus SQLite sidecars are saved
first, and any failure rolls the entire file set back. An interrupted restore
is recovered from its private journal before the next restore begins.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import stat
import tarfile
import tempfile
import time
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO
from urllib.parse import quote

from . import file_lock

log = logging.getLogger(__name__)

MANIFEST = "manifest.json"
SCHEMA = 2
_AUTH_ALGORITHM = "HMAC-SHA256"
_SIGNING_KEY_ENV = "MAVERICK_BACKUP_SIGNING_KEY"
_SKIP_SUFFIXES = ("-wal", "-shm", ".tmp")
_RESTORE_TX_ROOT = ".restore-transactions"
_RESTORE_LOCK_TARGET = ".restore-transaction"
_JOURNAL = "journal.json"
_DB_SIDECARS = ("-wal", "-shm", "-journal")

# Generous production ceilings, but finite on every attacker-controlled axis.
MAX_ARCHIVE_MEMBERS = 100_000
MAX_ARCHIVE_FILE_BYTES = 8 * 1024**3
MAX_ARCHIVE_EXPANDED_BYTES = 256 * 1024**3
MAX_ARCHIVE_COMPRESSED_BYTES = 64 * 1024**3
MAX_MANIFEST_BYTES = 16 * 1024**2
MAX_ARCHIVE_PATH_BYTES = 1024
MAX_COMPONENT_UNITS = 255
_COPY_CHUNK = 1 << 20
_RECOVERY_SCOPE: ContextVar[tuple[Path, str | None, bytes] | None] = ContextVar("backup_recovery_scope", default=None)
_RECOVERY_EXPANDED_LIMIT: ContextVar[int | None] = ContextVar("backup_recovery_expanded_limit", default=None)
_RECOVERY_WHOLE_HOME: ContextVar[bool] = ContextVar("backup_recovery_whole_home", default=False)
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_FORBIDDEN = frozenset('<>:"/\\|?*')
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CLOCK$", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³",
}


class BackupError(RuntimeError):
    """A backup cannot be created or a restore cannot be safely applied."""


def _filesystem_path(path: str | Path) -> Path:
    return Path(file_lock._windows_extended_path(path)) if os.name == "nt" else Path(path)


@contextmanager
def _temporary_directory(*, prefix: str):
    temporary = tempfile.TemporaryDirectory(prefix=prefix)
    # Cleanup must use the same long-path spelling as staging; otherwise
    # Windows can create the payload but fail to remove it after verification.
    temporary.name = str(_filesystem_path(temporary.name))
    try:
        yield Path(temporary.name)
    finally:
        temporary.cleanup()


def _client_root() -> Path:
    scope = _RECOVERY_SCOPE.get()
    if scope is not None:
        return _filesystem_path(scope[0])
    from .paths import data_dir

    return _filesystem_path(data_dir())


def _client_id() -> str | None:
    scope = _RECOVERY_SCOPE.get()
    if scope is not None:
        return scope[1]
    from .client import client_id

    return client_id()


def data_backups_dir() -> Path:
    from .paths import data_dir

    return data_dir("backups")


def _is_alias(path: Path, info: os.stat_result) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return (
        stat.S_ISLNK(info.st_mode)
        or bool(getattr(info, "st_file_attributes", 0) & _REPARSE_ATTRIBUTE)
        or bool(callable(is_junction) and is_junction())
    )


def _transaction_root(root: Path) -> Path:
    path = root / _RESTORE_TX_ROOT
    # The journal preserves full payload paths under originals/. Windows needs
    # its extended filesystem spelling for hashed draft paths in that subtree.
    return Path(file_lock._windows_extended_path(path)) if os.name == "nt" else path


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_nlink


def _canonical_body(document: dict[str, Any]) -> bytes:
    body = {k: v for k, v in document.items() if k != "authentication"}
    try:
        return json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BackupError("backup metadata is not canonical JSON") from exc


def _decode_operator_key(raw: str) -> bytes:
    value = raw.strip()
    if len(value) == 64:
        try:
            decoded = bytes.fromhex(value)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return decoded
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if len(decoded) != 32:
        raise BackupError(
            f"{_SIGNING_KEY_ENV} must be exactly 32 bytes encoded as hex or base64"
        )
    return decoded


def _operator_key(*, required: bool) -> bytes | None:
    scope = _RECOVERY_SCOPE.get()
    if scope is not None:
        return scope[2]
    raw = os.environ.get(_SIGNING_KEY_ENV, "")
    if not raw.strip():
        if required:
            raise BackupError(
                f"authenticated backup requires {_SIGNING_KEY_ENV}; refusing "
                "an unsigned archive (use allow_unsigned=True only for explicit "
                "legacy recovery)"
            )
        return None
    return _decode_operator_key(raw)


@contextmanager
def recovery_scope(root: Path, client: str | None, key: bytes, *, max_expanded_bytes: int | None = None, whole_home: bool = False):
    """Pin a server-resolved recovery target/key without process-env mutation.

    The recovery center is responsible for target authorization and quiescence.
    Ordinary backup CLI callers keep the existing operator environment contract.
    """
    if len(key) != 32:
        raise BackupError("recovery signing key must contain exactly 32 bytes")
    if max_expanded_bytes is not None and (type(max_expanded_bytes) is not int or max_expanded_bytes < 1):
        raise BackupError("recovery archive size limit must be a positive integer")
    token = _RECOVERY_SCOPE.set((Path(root), client, bytes(key)))
    limit_token = _RECOVERY_EXPANDED_LIMIT.set(max_expanded_bytes)
    home_token = _RECOVERY_WHOLE_HOME.set(whole_home)
    try:
        yield
    finally:
        _RECOVERY_SCOPE.reset(token)
        _RECOVERY_EXPANDED_LIMIT.reset(limit_token)
        _RECOVERY_WHOLE_HOME.reset(home_token)


def _expanded_limit() -> int:
    requested = _RECOVERY_EXPANDED_LIMIT.get()
    return min(MAX_ARCHIVE_EXPANDED_BYTES, requested) if requested is not None else MAX_ARCHIVE_EXPANDED_BYTES


def _key_id(key: bytes) -> str:
    return hashlib.sha256(key).hexdigest()[:16]


def _authenticate_for_write(
    document: dict[str, Any], key: bytes | None,
) -> dict[str, Any]:
    result = dict(document)
    if key is None:
        result["authentication"] = {"algorithm": "none"}
        return result
    signature = hmac.new(key, _canonical_body(result), hashlib.sha256).hexdigest()
    result["authentication"] = {
        "algorithm": _AUTH_ALGORITHM,
        "key_id": _key_id(key),
        "signature": signature,
    }
    return result


def _verify_authentication(
    document: dict[str, Any], *, allow_unsigned: bool, label: str,
) -> bytes | None:
    auth = document.get("authentication")
    if auth is None:
        if allow_unsigned:
            log.warning("accepting explicitly-authorized unsigned legacy %s", label)
            return None
        raise BackupError(f"{label} is unsigned; authenticity cannot be established")
    if not isinstance(auth, dict):
        raise BackupError(f"{label} authentication metadata is malformed")
    algorithm = auth.get("algorithm")
    if algorithm == "none":
        if set(auth) != {"algorithm"}:
            raise BackupError(f"{label} unsigned metadata is ambiguous")
        if not allow_unsigned:
            raise BackupError(f"{label} is unsigned; authenticity cannot be established")
        log.warning("accepting explicitly-authorized unsigned %s", label)
        return None
    if algorithm != _AUTH_ALGORITHM:
        raise BackupError(f"{label} uses an unsupported authentication algorithm")
    if set(auth) != {"algorithm", "key_id", "signature"}:
        raise BackupError(f"{label} authentication metadata is ambiguous")
    key = _operator_key(required=True)
    assert key is not None
    claimed_key_id = auth.get("key_id")
    signature = auth.get("signature")
    if claimed_key_id != _key_id(key):
        raise BackupError(f"{label} was not signed by the configured operator key")
    if not isinstance(signature, str) or len(signature) != 64:
        raise BackupError(f"{label} signature is malformed")
    expected = hmac.new(key, _canonical_body(document), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        raise BackupError(f"{label} signature verification failed")
    return key


def _safe_relative_path(value: str, *, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\0" in value or "\\" in value:
        raise BackupError(f"unsafe {label}: {value!r}")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BackupError(f"unsafe {label}: {value!r}") from exc
    if len(encoded) > MAX_ARCHIVE_PATH_BYTES:
        raise BackupError(f"{label} is too long")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != value
        or any(p in {"", ".", ".."} for p in path.parts)
    ):
        raise BackupError(f"unsafe {label}: {value!r}")
    for component in path.parts:
        if (
            component.endswith((".", " "))
            or any(ord(c) < 0x20 or ord(c) == 0x7F for c in component)
            or any(c in _WINDOWS_FORBIDDEN for c in component)
        ):
            raise BackupError(f"unsafe {label}: {value!r}")
        basename = component.split(".", 1)[0].rstrip(" ").upper()
        if basename in _WINDOWS_RESERVED:
            raise BackupError(f"unsafe {label}: {value!r}")
        if (
            len(component.encode("utf-8")) > MAX_COMPONENT_UNITS
            or len(component.encode("utf-16-le")) // 2 > MAX_COMPONENT_UNITS
        ):
            raise BackupError(f"{label} component is too long")
    return path


def _is_reserved_restore_path(relative: PurePosixPath) -> bool:
    return (
        relative.parts[0] in {"backups", _RESTORE_TX_ROOT}
        or relative.as_posix() == f"{_RESTORE_LOCK_TARGET}.lock"
        or (_RECOVERY_WHOLE_HOME.get() and (
            relative.parts[0] in {"control-plane.lock", "control-plane-holder.json"}
            or (len(relative.parts) >= 3 and relative.parts[0] == "tenants"
                and relative.parts[2] in {"backups", _RESTORE_TX_ROOT, f"{_RESTORE_LOCK_TARGET}.lock"})))
    )


class _ArchiveBudget:
    def __init__(self) -> None:
        self.members = 0
        self.expanded = 0
        self.names: set[str] = set()
        self.portable_names: dict[str, str] = {}

    def observe(self, member: tarfile.TarInfo) -> str | None:
        self.members += 1
        if self.members > MAX_ARCHIVE_MEMBERS:
            raise BackupError("backup exceeds the archive member-count limit")
        name = member.name
        if not isinstance(name, str) or name in self.names:
            raise BackupError(f"duplicate or malformed archive member: {name!r}")
        self.names.add(name)
        folded = unicodedata.normalize("NFC", name).casefold()
        prior = self.portable_names.get(folded)
        if prior is not None and prior != name:
            raise BackupError(
                f"archive members collide on a case-insensitive host: {prior!r}, {name!r}"
            )
        self.portable_names[folded] = name

        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}:
            raise BackupError(f"unsupported archive member type: {name!r}")

        if name == MANIFEST:
            if not member.isfile() or member.issym() or member.islnk():
                raise BackupError("manifest.json must be one regular file")
            rel: str | None = None
        elif name == "data":
            if not member.isdir():
                raise BackupError("data archive root must be a directory")
            rel = None
        elif name.startswith("data/"):
            rel = name[len("data/"):]
            safe_rel = _safe_relative_path(rel, label="archive member path")
            if _is_reserved_restore_path(safe_rel):
                raise BackupError(f"archive member targets reserved restore state: {name!r}")
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise BackupError(f"unsupported archive member type: {name!r}")
        else:
            raise BackupError(f"unexpected archive member: {name!r}")

        if member.isdir():
            if member.size not in {0, None}:
                raise BackupError(f"directory member has a payload: {name!r}")
            return rel
        if not isinstance(member.size, int) or member.size < 0:
            raise BackupError(f"archive member has an invalid size: {name!r}")
        if member.size > MAX_ARCHIVE_FILE_BYTES:
            raise BackupError(f"archive member exceeds the per-file limit: {name!r}")
        if name == MANIFEST and member.size > MAX_MANIFEST_BYTES:
            raise BackupError("manifest.json exceeds the manifest-size limit")
        self.expanded += member.size
        if self.expanded > _expanded_limit():
            raise BackupError("backup exceeds the total expanded-size limit")
        return rel


@contextmanager
def _verified_archive(path: str | Path):
    archive = _filesystem_path(path)
    try:
        before = archive.lstat()
    except OSError as exc:
        raise BackupError(f"unreadable backup {str(path)!r}: {exc}") from exc
    if (
        _is_alias(archive, before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size > MAX_ARCHIVE_COMPRESSED_BYTES
    ):
        raise BackupError("backup path must be one bounded, single-link regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(str(archive), flags)
    except OSError as exc:
        raise BackupError(f"backup could not be opened safely: {exc}") from exc
    try:
        opened = os.fstat(fd)
        after = archive.lstat()
        if (
            _is_alias(archive, after)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or after.st_nlink != 1
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
        ):
            raise BackupError("backup path identity changed while opening")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            yield stream
    finally:
        if fd >= 0:
            os.close(fd)


def _read_exact(member_stream: BinaryIO, size: int, *, limit: int) -> bytes:
    if size > limit:
        raise BackupError("archive member exceeds its read limit")
    data = member_stream.read(size + 1)
    if not isinstance(data, bytes) or len(data) != size:
        raise BackupError("archive member is truncated or overlong")
    return data


def _parse_manifest(data: bytes) -> dict[str, Any]:
    def _reject_constant(value: str):
        raise ValueError(f"non-finite JSON constant {value}")

    try:
        manifest = json.loads(
            data.decode("utf-8"),
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise BackupError("manifest.json is not valid bounded JSON") from exc
    if not isinstance(manifest, dict):
        raise BackupError("manifest.json must contain an object")
    return manifest


def _read_manifest_stream(
    stream: BinaryIO, *, allow_unsigned: bool,
) -> tuple[dict[str, Any], bytes | None]:
    manifest: dict[str, Any] | None = None
    budget = _ArchiveBudget()
    try:
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for member in archive:
                budget.observe(member)
                if member.name != MANIFEST:
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BackupError("backup has no readable manifest.json")
                manifest = _parse_manifest(
                    _read_exact(extracted, member.size, limit=MAX_MANIFEST_BYTES)
                )
    except BackupError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"unreadable backup archive: {exc}") from exc
    if manifest is None:
        raise BackupError("backup has no manifest.json")
    key = _verify_authentication(
        manifest,
        allow_unsigned=allow_unsigned,
        label="backup manifest",
    )
    _manifest_files(manifest, allow_legacy_unsigned=allow_unsigned and key is None)
    return manifest, key


def _manifest_files(
    manifest: dict[str, Any], *, allow_legacy_unsigned: bool,
) -> dict[str, tuple[str, int | None]]:
    schema = manifest.get("schema")
    if schema != SCHEMA and not (allow_legacy_unsigned and schema == 1):
        raise BackupError(f"unsupported backup manifest schema: {schema!r}")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict) or len(raw_files) > MAX_ARCHIVE_MEMBERS:
        raise BackupError("backup manifest files map is missing or oversized")
    if not raw_files:
        raise BackupError("backup manifest contains no payload files")
    expected: dict[str, tuple[str, int | None]] = {}
    portable: dict[str, str] = {}
    total = 0
    for raw_rel, record in raw_files.items():
        rel = _safe_relative_path(raw_rel, label="manifest file path").as_posix()
        if _is_reserved_restore_path(PurePosixPath(rel)):
            raise BackupError(f"manifest targets reserved restore state: {rel}")
        folded = unicodedata.normalize("NFC", rel).casefold()
        if folded in portable and portable[folded] != rel:
            raise BackupError("manifest file paths collide on a portable host")
        portable[folded] = rel
        if isinstance(record, str) and allow_legacy_unsigned and schema == 1:
            digest, size = record, None
        elif isinstance(record, dict):
            digest, size = record.get("sha256"), record.get("size")
            if not isinstance(size, int) or size < 0 or size > MAX_ARCHIVE_FILE_BYTES:
                raise BackupError(f"invalid manifest size for {rel}")
            total += size
        else:
            raise BackupError(f"invalid manifest record for {rel}")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise BackupError(f"invalid manifest SHA-256 for {rel}")
        expected[rel] = (digest, size)
    if total > _expanded_limit():
        raise BackupError("manifest declares too much expanded data")
    return expected


def read_manifest(
    tarball: str | Path, *, allow_unsigned: bool = False,
) -> dict[str, Any]:
    """Return a bounded, authenticated manifest.

    ``allow_unsigned=True`` is an explicit compatibility path for legacy
    recovery. It verifies structure and payload hashes during restore but does
    not and cannot authenticate archive provenance.
    """
    with _verified_archive(tarball) as stream:
        manifest, _ = _read_manifest_stream(stream, allow_unsigned=allow_unsigned)
        return manifest


def _new_private_empty(path: Path) -> None:
    try:
        file_lock.atomic_create_bytes(path, b"")
    except FileExistsError as exc:
        raise BackupError(f"refusing an existing or planted path: {path}") from exc
    except OSError as exc:
        raise BackupError(f"could not create private staging file: {path}") from exc


def _open_private_writer(path: Path) -> tuple[int, os.stat_result]:
    try:
        before = path.lstat()
    except OSError as exc:
        raise BackupError(f"private staging path disappeared: {path}") from exc
    if (
        _is_alias(path, before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or not file_lock.private_path_is_restricted(path)
    ):
        raise BackupError(f"staging path is not a private regular file: {path}")
    flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(path), flags)
    try:
        opened = os.fstat(fd)
        after = path.lstat()
        if (
            _is_alias(path, after)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or after.st_nlink != 1
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after)
        ):
            raise BackupError(f"staging path identity changed: {path}")
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def _stream_to_new_private(
    reader: BinaryIO,
    destination: Path,
    *,
    expected_size: int,
    precreated: bool = False,
) -> tuple[str, int]:
    if expected_size < 0 or expected_size > MAX_ARCHIVE_FILE_BYTES:
        raise BackupError("file exceeds the backup per-file limit")
    file_lock.ensure_private_directory(destination.parent)
    if not precreated:
        _new_private_empty(destination)
    digest = hashlib.sha256()
    remaining = expected_size
    fd = -1
    try:
        fd, _ = _open_private_writer(destination)
        with os.fdopen(fd, "wb") as output:
            fd = -1
            while remaining:
                chunk = reader.read(min(_COPY_CHUNK, remaining))
                if not isinstance(chunk, bytes) or not chunk:
                    raise BackupError("source file is truncated during streaming copy")
                if len(chunk) > remaining:
                    raise BackupError("source file exceeded its declared size")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            if reader.read(1) not in {b"", None}:
                raise BackupError("source file grew during streaming copy")
            output.flush()
            os.fsync(output.fileno())
        if not file_lock.private_path_is_restricted(destination):
            raise BackupError("streamed file did not retain private permissions")
        return digest.hexdigest(), expected_size
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            destination.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _copy_verified_file(
    source: Path,
    destination: Path,
    *,
    destination_precreated: bool = False,
) -> tuple[str, int]:
    source, destination = _filesystem_path(source), _filesystem_path(destination)
    try:
        before = source.lstat()
    except OSError as exc:
        raise BackupError(f"source file could not be inspected: {source}") from exc
    if _is_alias(source, before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise BackupError(f"source is not a single-link regular file: {source}")
    if before.st_size > MAX_ARCHIVE_FILE_BYTES:
        raise BackupError(f"source file exceeds the per-file limit: {source}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(str(source), flags)
    try:
        opened = os.fstat(fd)
        after_open = source.lstat()
        if (
            _is_alias(source, after_open)
            or opened.st_nlink != 1
            or after_open.st_nlink != 1
            or _identity(before) != _identity(opened)
            or _identity(opened) != _identity(after_open)
        ):
            raise BackupError(f"source file identity changed: {source}")
        with os.fdopen(fd, "rb") as reader:
            fd = -1
            result = _stream_to_new_private(
                reader,
                destination,
                expected_size=opened.st_size,
                precreated=destination_precreated,
            )
        final = source.lstat()
        if (
            _identity(after_open) != _identity(final)
            or final.st_size != opened.st_size
            or getattr(final, "st_mtime_ns", None) != getattr(after_open, "st_mtime_ns", None)
        ):
            destination.unlink(missing_ok=True)
            raise BackupError(f"source file changed during backup: {source}")
        return result
    finally:
        if fd >= 0:
            os.close(fd)


def _consistent_db_copy(source: Path, destination: Path) -> tuple[str, int]:
    file_lock.ensure_private_directory(destination.parent)
    _new_private_empty(destination)
    try:
        src = sqlite3.connect(str(source))
        try:
            dst = sqlite3.connect(str(destination))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        file_lock.ensure_private_file(destination)
        size = destination.stat().st_size
        if size > MAX_ARCHIVE_FILE_BYTES:
            raise BackupError(f"database exceeds the per-file limit: {source}")
        return _sha256(destination), size
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(_filesystem_path(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(_COPY_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _world_schema_version(root: Path) -> int | None:
    db = root / "world.db"
    if not db.exists():
        return None
    try:
        connection = sqlite3.connect(str(db))
        try:
            row = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
            return int(row[0]) if row and row[0] is not None else None
        finally:
            connection.close()
    except sqlite3.Error:
        return None


def _excluded_from_snapshot(relative: Path) -> bool:
    return (
        not relative.parts
        or relative.parts[0] in {"backups", _RESTORE_TX_ROOT}
        or relative.name == f"{_RESTORE_LOCK_TARGET}.lock"
        or relative.name.endswith(_SKIP_SUFFIXES)
        or _is_reserved_restore_path(PurePosixPath(relative.as_posix()))
    )


def _is_sqlite_file(path: Path) -> bool:
    # Consent, A2A and other stores use .sqlite3/.sqlite, and extensions are not
    # a database format boundary. Header recognition also covers custom names.
    # Conventional names retain fail-closed validation if their header is bad.
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return True
    try:
        with path.open("rb") as stream:
            return stream.read(16) == b"SQLite format 3\x00"
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise BackupError("could not inspect a possible SQLite database") from exc


def _stage(root: Path, stage: Path) -> dict[str, dict[str, Any]]:
    root, stage = _filesystem_path(root), _filesystem_path(stage)
    files: dict[str, dict[str, Any]] = {}
    total = 0
    count = 0
    for source in sorted(root.rglob("*")):
        relative = source.relative_to(root)
        if _excluded_from_snapshot(relative):
            continue
        try:
            info = source.lstat()
        except OSError as exc:
            raise BackupError(f"backup source could not be inspected: {source}") from exc
        if _is_alias(source, info):
            raise BackupError(f"backup source contains an unsupported alias: {relative}")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupError(f"backup source is not a regular file: {relative}")
        if total + info.st_size > _expanded_limit():
            raise BackupError("live data exceeds configured backup archive limits")
        file_lock.ensure_private_file(source)
        rel = _safe_relative_path(relative.as_posix(), label="backup source path").as_posix()
        destination = stage / Path(*PurePosixPath(rel).parts)
        if _is_sqlite_file(source):
            digest, size = _consistent_db_copy(source, destination)
        else:
            digest, size = _copy_verified_file(source, destination)
        count += 1
        total += size
        if count > MAX_ARCHIVE_MEMBERS or total > _expanded_limit():
            raise BackupError("live data exceeds configured backup archive limits")
        files[rel] = {"sha256": digest, "size": size}
    directory_count = sum(1 for path in stage.rglob("*") if path.is_dir())
    if count + directory_count + 2 > MAX_ARCHIVE_MEMBERS:
        raise BackupError("staged backup exceeds the archive member-count limit")
    return files


def _unique_private_empty(parent: Path, prefix: str) -> Path:
    parent = _filesystem_path(parent)
    for _ in range(100):
        candidate = parent / f".{prefix}-{secrets.token_hex(16)}.tmp"
        try:
            file_lock.atomic_create_bytes(candidate, b"")
            return candidate
        except FileExistsError:
            continue
    raise BackupError("could not allocate a unique private staging path")


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def create_backup(
    out: str | Path | None = None,
    *,
    allow_unsigned: bool = False,
    recovery_metadata: dict[str, Any] | None = None,
) -> Path:
    """Create one private, atomically-published backup archive.

    Authenticated creation is the default and requires
    ``MAVERICK_BACKUP_SIGNING_KEY``. ``allow_unsigned=True`` exists only for
    explicit legacy interoperability; such an archive is labelled unsigned.
    Existing destination paths are refused rather than overwritten.
    """
    root = _client_root()
    if not root.exists():
        raise BackupError(f"no data root to back up at {root}")
    file_lock.ensure_private_directory(root)
    key = _operator_key(required=not allow_unsigned)
    client_identifier = _client_id() or "unbound"
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    default_destination = out is None
    if default_destination:
        out = data_backups_dir() / (
            f"maverick-{client_identifier}-{timestamp}-{secrets.token_hex(4)}.tgz"
        )
    public_destination = Path(out).expanduser()
    destination = _filesystem_path(public_destination)
    try:
        if default_destination:
            file_lock.ensure_private_directory(destination.parent)
        else:
            # A caller-selected parent may contain unrelated files. Verify its
            # ACL instead of silently revoking collaborators' access to it.
            file_lock.prepare_private_directory(destination.parent)
    except OSError as exc:
        raise BackupError("backup destination directory must be private") from exc
    if os.path.lexists(destination):
        raise BackupError(f"refusing existing backup destination: {destination}")

    with _temporary_directory(prefix="mvk-backup-") as temporary:
        temp_root = file_lock.ensure_private_directory(temporary)
        stage = file_lock.ensure_private_directory(temp_root / "data")
        schema_version = _world_schema_version(root)
        files = _stage(root, stage)
        if not files:
            raise BackupError(f"no data files to back up at {root}")
        manifest = _authenticate_for_write({
            "schema": SCHEMA,
            "client_id": _client_id(),
            "created_at": time.time(),
            "world_schema_version": schema_version,
            "files": files,
            **({"recovery": recovery_metadata} if recovery_metadata is not None else {}),
        }, key)
        manifest_path = temp_root / MANIFEST
        file_lock.atomic_create_text(
            manifest_path,
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        )

        staged_archive = _unique_private_empty(destination.parent, destination.name)
        try:
            fd, _ = _open_private_writer(staged_archive)
            with os.fdopen(fd, "wb") as output:
                with tarfile.open(fileobj=output, mode="w:gz") as archive:
                    archive.add(manifest_path, arcname=MANIFEST, recursive=False)
                    archive.add(stage, arcname="data")
                output.flush()
                os.fsync(output.fileno())

            # Validate the generated artifact through the same bounded/authenticated
            # reader used by restore before it becomes the visible DR artifact.
            with _verified_archive(staged_archive) as stream:
                _read_manifest_stream(stream, allow_unsigned=allow_unsigned)
            if os.path.lexists(destination):
                raise BackupError(f"backup destination was planted: {destination}")
            os.replace(staged_archive, destination)
            _fsync_directory(destination.parent)
            file_lock.ensure_private_file(destination)
        except BaseException:
            staged_archive.unlink(missing_ok=True)
            raise

    log.info(
        "backup written: %s (%d files, client=%s, authenticated=%s)",
        destination,
        len(files),
        client_identifier,
        key is not None,
    )
    return public_destination


def _extract_payload_stream(
    stream: BinaryIO,
    stage: Path,
    *,
    expected: dict[str, tuple[str, int | None]],
) -> None:
    budget = _ArchiveBudget()
    seen: set[str] = set()
    try:
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for member in archive:
                rel = budget.observe(member)
                if rel is None or member.isdir():
                    continue
                record = expected.get(rel)
                if record is None:
                    raise BackupError(
                        f"backup payload {rel} is not in the manifest "
                        "(backup is corrupt or tampered)"
                    )
                if rel in seen:
                    raise BackupError(f"duplicate backup payload path: {rel}")
                seen.add(rel)
                expected_digest, expected_size = record
                if expected_size is not None and member.size != expected_size:
                    raise BackupError(
                        f"backup integrity check failed for {rel} (size mismatch)"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BackupError(f"backup payload is unreadable: {rel}")
                destination = stage / Path(*PurePosixPath(rel).parts)
                digest, size = _stream_to_new_private(
                    extracted,
                    destination,
                    expected_size=member.size,
                )
                if digest != expected_digest or (
                    expected_size is not None and size != expected_size
                ):
                    raise BackupError(
                        f"backup integrity check failed for {rel} "
                        "(SHA-256 mismatch — backup is corrupt or truncated)"
                    )
    except BackupError:
        raise
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BackupError(f"unreadable backup payload: {exc}") from exc
    missing = set(expected) - seen
    if missing:
        sample = sorted(missing)[0]
        raise BackupError(f"manifest payload is missing from archive: {sample}")


def _live_path(root: Path, rel: str) -> Path:
    safe = _safe_relative_path(rel, label="restore path")
    current = root
    for component in safe.parts[:-1]:
        current = current / component
        if os.path.lexists(current):
            info = current.lstat()
            if _is_alias(current, info) or not stat.S_ISDIR(info.st_mode):
                raise BackupError(f"unsafe restore parent: {current}")
        file_lock.ensure_private_directory(current)
    return root / Path(*safe.parts)


def _validate_optional_live_file(path: Path) -> bool:
    if not os.path.lexists(path):
        return False
    info = path.lstat()
    if _is_alias(path, info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise BackupError(f"refusing planted or aliased live path: {path}")
    file_lock.ensure_private_file(path)
    return True


def _journal_document(body: dict[str, Any], key: bytes | None) -> dict[str, Any]:
    return _authenticate_for_write(body, key)


def _write_journal(tx: Path, body: dict[str, Any], key: bytes | None) -> None:
    document = _journal_document(body, key)
    file_lock.atomic_write_text(
        tx / _JOURNAL,
        json.dumps(document, sort_keys=True, separators=(",", ":")),
    )


def _load_journal(
    tx: Path, *, allow_unsigned: bool,
) -> tuple[dict[str, Any], bytes | None]:
    path = tx / _JOURNAL
    try:
        file_lock.ensure_private_file(path)
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise BackupError("restore journal is oversized")
        document = _parse_manifest(file_lock.atomic_read_bytes(path))
    except OSError as exc:
        raise BackupError(f"restore journal could not be read: {tx}") from exc
    key = _verify_authentication(
        document,
        allow_unsigned=allow_unsigned,
        label="restore journal",
    )
    return document, key


def _remove_private_tree(path: Path) -> None:
    if not os.path.lexists(path):
        return
    info = path.lstat()
    if _is_alias(path, info):
        path.unlink()
        return
    if stat.S_ISDIR(info.st_mode):
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            _remove_private_tree(child)
        path.rmdir()
    else:
        path.unlink()


def _capture_original(
    root: Path,
    tx: Path,
    rel: str,
) -> dict[str, Any]:
    destination = _live_path(root, rel)
    existed = _validate_optional_live_file(destination)
    if existed:
        original = tx / "originals" / Path(*PurePosixPath(rel).parts)
        _copy_verified_file(destination, original)
    sidecars: list[str] = []
    if _is_sqlite_file(destination):
        for suffix in _DB_SIDECARS:
            sidecar = Path(str(destination) + suffix)
            if _validate_optional_live_file(sidecar):
                side_rel = rel + suffix
                original_sidecar = tx / "originals" / Path(*PurePosixPath(side_rel).parts)
                _copy_verified_file(sidecar, original_sidecar)
                sidecars.append(suffix)
    return {"rel": rel, "existed": existed, "sidecars": sidecars}


def _publish_staged_file(source: Path, destination: Path) -> None:
    file_lock.ensure_private_directory(destination.parent)
    if os.path.lexists(destination):
        _validate_optional_live_file(destination)
    # A restored draft already has a long hashed filename. Repeating that name
    # in the temporary path exceeds Windows path limits on ordinary profiles.
    # The random suffix supplies uniqueness without copying the payload name.
    temporary = _unique_private_empty(destination.parent, "restore")
    try:
        _copy_verified_file(
            source,
            temporary,
            destination_precreated=True,
        )
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        file_lock.ensure_private_file(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _validate_journal_entries(document: dict[str, Any]) -> list[dict[str, Any]]:
    if document.get("journal_schema") != 1:
        raise BackupError("restore journal schema is unsupported")
    entries = document.get("entries")
    if not isinstance(entries, list) or len(entries) > MAX_ARCHIVE_MEMBERS:
        raise BackupError("restore journal entries are malformed")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("restore journal entry is malformed")
        rel = _safe_relative_path(entry.get("rel"), label="journal path").as_posix()
        if rel in seen or not isinstance(entry.get("existed"), bool):
            raise BackupError("restore journal contains duplicate/malformed state")
        seen.add(rel)
        sidecars = entry.get("sidecars")
        if (
            not isinstance(sidecars, list)
            or len(sidecars) > len(_DB_SIDECARS)
            or any(s not in _DB_SIDECARS for s in sidecars)
            or len(set(sidecars)) != len(sidecars)
        ):
            raise BackupError("restore journal sidecar state is malformed")
        validated.append({"rel": rel, "existed": entry["existed"], "sidecars": sidecars})
    return validated


def _rollback(root: Path, tx: Path, document: dict[str, Any]) -> None:
    entries = _validate_journal_entries(document)
    for entry in reversed(entries):
        rel = entry["rel"]
        destination = _live_path(root, rel)
        if _is_sqlite_file(destination) or entry["sidecars"]:
            for suffix in _DB_SIDECARS:
                sidecar = Path(str(destination) + suffix)
                if os.path.lexists(sidecar):
                    _validate_optional_live_file(sidecar)
                    sidecar.unlink()
        if entry["existed"]:
            original = tx / "originals" / Path(*PurePosixPath(rel).parts)
            _validate_optional_live_file(original)
            _publish_staged_file(original, destination)
        elif os.path.lexists(destination):
            _validate_optional_live_file(destination)
            destination.unlink()
        for suffix in entry["sidecars"]:
            side_rel = rel + suffix
            original_sidecar = tx / "originals" / Path(*PurePosixPath(side_rel).parts)
            _validate_optional_live_file(original_sidecar)
            _publish_staged_file(
                original_sidecar,
                Path(str(destination) + suffix),
            )


def _recover_transactions(root: Path, *, allow_unsigned: bool) -> None:
    tx_root = _transaction_root(root)
    if not os.path.lexists(tx_root):
        return
    file_lock.ensure_private_directory(tx_root)
    transactions = sorted(tx_root.iterdir())
    if len(transactions) > 32:
        raise BackupError("too many incomplete restore transactions")
    for tx in transactions:
        info = tx.lstat()
        if _is_alias(tx, info) or not stat.S_ISDIR(info.st_mode):
            raise BackupError(f"invalid restore transaction path: {tx}")
        file_lock.ensure_private_directory(tx)
        journal = tx / _JOURNAL
        if not journal.exists():
            _remove_private_tree(tx)
            continue
        document, _ = _load_journal(tx, allow_unsigned=allow_unsigned)
        state = document.get("state")
        if state in {"prepared", "applying"}:
            _rollback(root, tx, document)
        elif state not in {"preparing", "committed"}:
            raise BackupError(f"unknown restore journal state: {state!r}")
        _remove_private_tree(tx)
    if tx_root.exists() and not any(tx_root.iterdir()):
        tx_root.rmdir()


def _replacement_targets(root: Path, expected: dict) -> set[str]:
    targets = set(expected)
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if _excluded_from_snapshot(relative):
            continue
        info = path.lstat()
        if _is_alias(path, info):
            raise BackupError("live recovery target contains an alias")
        if not stat.S_ISDIR(info.st_mode):
            targets.add(_safe_relative_path(relative.as_posix(), label="live recovery path").as_posix())
        if len(targets) > MAX_ARCHIVE_MEMBERS:
            raise BackupError("live recovery target exceeds file limit")
    return targets


def _verify_restored(root: Path, expected: dict) -> None:
    for rel, (digest, size) in expected.items():
        restored = _live_path(root, rel)
        if _sha256(restored) != digest or (size is not None and restored.stat().st_size != size):
            raise BackupError("post-restore file verification failed")
        if _is_sqlite_file(restored):
            uri = ("file:" + quote(str(_filesystem_path(restored)), safe=":")
                   if os.name == "nt" else restored.resolve().as_uri())
            connection = sqlite3.connect(uri + "?mode=ro", uri=True)
            try:
                if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise BackupError("post-restore database integrity check failed")
            except sqlite3.DatabaseError as exc:
                raise BackupError("post-restore database integrity check failed") from exc
            finally:
                connection.close()


def _apply_transaction(
    root: Path,
    stage: Path,
    expected: dict[str, tuple[str, int | None]],
    *,
    key: bytes | None,
    replace_existing: bool = False,
) -> None:
    tx_root = file_lock.ensure_private_directory(_transaction_root(root))
    tx = file_lock.ensure_private_directory(tx_root / secrets.token_hex(16))
    body: dict[str, Any] = {
        "journal_schema": 1,
        "state": "preparing",
        "entries": [],
    }
    _write_journal(tx, body, key)
    committed = False
    try:
        targets = _replacement_targets(root, expected) if replace_existing else set(expected)
        entries = [_capture_original(root, tx, rel) for rel in sorted(targets)]
        body["entries"] = entries
        body["state"] = "prepared"
        _write_journal(tx, body, key)
        body["state"] = "applying"
        _write_journal(tx, body, key)
        for entry in entries:
            rel = entry["rel"]
            source = stage / Path(*PurePosixPath(rel).parts)
            destination = _live_path(root, rel)
            if _is_sqlite_file(destination) or _is_sqlite_file(source):
                for suffix in _DB_SIDECARS:
                    sidecar = Path(str(destination) + suffix)
                    if os.path.lexists(sidecar):
                        _validate_optional_live_file(sidecar)
                        sidecar.unlink()
            if rel in expected:
                _publish_staged_file(source, destination)
            elif destination.exists():
                destination.unlink()
        if replace_existing:
            # Validate before committing the journal so any failed check restores
            # the originals, including files created after the chosen snapshot.
            _verify_restored(root, expected)
        committed_body = dict(body)
        committed_body["state"] = "committed"
        _write_journal(tx, committed_body, key)
        body = committed_body
        committed = True
    except BaseException as original:
        try:
            if body.get("state") == "preparing":
                _remove_private_tree(tx)
            elif not committed and body.get("state") in {"prepared", "applying", "committed"}:
                _rollback(root, tx, body)
                _remove_private_tree(tx)
        except BaseException as rollback_error:
            raise BackupError(
                f"restore failed and rollback is incomplete; journal retained at {tx}: "
                f"{rollback_error}"
            ) from original
        raise
    else:
        _remove_private_tree(tx)
    finally:
        if tx_root.exists() and not any(tx_root.iterdir()):
            tx_root.rmdir()


def restore_backup(
    tarball: str | Path,
    *,
    force: bool = False,
    allow_unsigned: bool = False,
    replace_existing: bool = False,
) -> Path:
    """Authenticate, verify and transactionally restore one backup.

    ``force`` does not bypass signature verification. ``allow_unsigned`` is the
    explicit legacy path and provides integrity/bounds checks, not provenance.
    """
    with _verified_archive(tarball) as stream:
        manifest, key = _read_manifest_stream(stream, allow_unsigned=allow_unsigned)
        backup_client = manifest.get("client_id")
        live_client = _client_id()
        if not force and backup_client != live_client:
            raise BackupError(
                f"backup is for client {backup_client!r} but this deployment is "
                f"{live_client!r}; refusing (pass force=True to override)"
            )
        from .world_model import SCHEMA_VERSION

        backup_schema = manifest.get("world_schema_version")
        if (
            not force
            and isinstance(backup_schema, int)
            and backup_schema > SCHEMA_VERSION
        ):
            raise BackupError(
                f"backup world schema v{backup_schema} is newer than this "
                f"binary's v{SCHEMA_VERSION}; upgrade Lightwork first, or pass "
                "force=True to override"
            )
        expected = _manifest_files(
            manifest,
            allow_legacy_unsigned=allow_unsigned and key is None,
        )
        root = _client_root()
        file_lock.ensure_private_directory(root)
        with _temporary_directory(prefix="mvk-restore-") as temporary:
            stage = file_lock.ensure_private_directory(Path(temporary) / "data")
            stream.seek(0)
            _extract_payload_stream(stream, stage, expected=expected)
            lock_target = root / _RESTORE_LOCK_TARGET
            with file_lock.cross_process_lock(lock_target, strict=True):
                _recover_transactions(root, allow_unsigned=allow_unsigned)
                _apply_transaction(
                    root,
                    stage,
                    expected,
                    key=key,
                    replace_existing=replace_existing,
                )
    log.info(
        "restore complete into %s (from client=%s, %d files verified, authenticated=%s)",
        root,
        backup_client,
        len(expected),
        key is not None,
    )
    return root


def verify_backup(tarball: str | Path) -> dict[str, Any]:
    """Authenticate and verify every payload byte without touching live state."""
    with _verified_archive(tarball) as stream:
        manifest, _ = _read_manifest_stream(stream, allow_unsigned=False)
        expected = _manifest_files(manifest, allow_legacy_unsigned=False)
        with _temporary_directory(prefix="mvk-backup-verify-") as temporary:
            stage = file_lock.ensure_private_directory(Path(temporary) / "data")
            stream.seek(0)
            _extract_payload_stream(stream, stage, expected=expected)
    return manifest


def _accounting_file(relative: str, path: Path) -> bool:
    parts = PurePosixPath(relative).parts
    lowered = tuple(part.casefold() for part in parts)
    name = lowered[-1]
    # Custom database names are not a format boundary. Only ordinary lock
    # sidecars may be ignored, never SQLite payloads whose name ends in .lock.
    if _is_sqlite_file(path):
        return True
    if name.endswith(".lock"):
        return False
    return (
        bool({"usage", "llm_gateway_team_budget", "mcp_sidecar"}.intersection(lowered[:-1]))
        or name in {"provider_spend.json", "tenant_registry.json"}
        or name.startswith("budget_receipts.")
    )


def assert_accounting_unchanged(tarball: str | Path) -> None:
    """Refuse automated live recovery that could rewind spend or reservations.

    Call within the selected recovery scope after stopping normal work, before
    writing a live restore or replacing application binaries. Both sides use
    the same online SQLite snapshot representation, so WAL checkpointing alone
    is not drift. All databases are protected conservatively: an older binary
    cannot safely interpret or merge newer accounting/checkpoint schemas.
    Snapshot creation, export and isolated drills do not invoke this guard.
    """
    with _verified_archive(tarball) as stream:
        manifest, _ = _read_manifest_stream(stream, allow_unsigned=False)
        if manifest.get("client_id") != _client_id():
            raise BackupError("The accounting snapshot belongs to a different recovery scope.")
        expected = _manifest_files(manifest, allow_legacy_unsigned=False)
        with _temporary_directory(prefix="lightwork-accounting-check-") as temporary:
            selected = file_lock.ensure_private_directory(temporary / "selected")
            current = file_lock.ensure_private_directory(temporary / "current")
            stream.seek(0)
            _extract_payload_stream(stream, selected, expected=expected)
            live = _stage(_client_root(), current)
            protected = {
                name for name in expected
                if _accounting_file(name, selected / Path(*PurePosixPath(name).parts))
            } | {
                name for name in live
                if _accounting_file(name, current / Path(*PurePosixPath(name).parts))
            }
            changed = sorted(name for name in protected if expected.get(name) != (
                (live[name]["sha256"], live[name]["size"]) if name in live else None
            ))
            if changed:
                raise BackupError(
                    "Automatic restore is unavailable because spending, reservations, budget controls "
                    "or database state differs from the selected snapshot. Administrator accounting "
                    "reconciliation is required before restoring; newer records must not be erased "
                    "or passed to an older incompatible reader. Retain the current accounting "
                    "and export the snapshot for administrator review. Protected files: " + ", ".join(changed[:5])
                )


__all__ = [
    "BackupError",
    "assert_accounting_unchanged",
    "create_backup",
    "restore_backup",
    "read_manifest",
    "data_backups_dir",
]
