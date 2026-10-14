"""Regularize a read-only Kubernetes Secret projection for strict custody.

Kubernetes updates Secret volumes through an atomic symlink tree.  The runtime
secret reader deliberately refuses every symlink, so the production pod uses
this one-shot init-container helper to copy one immutable ``..data`` snapshot
into an in-memory ``emptyDir``.  The application sees only owner-readable
regular files and never mounts the projected source.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

_SAFE_SECRET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}")
_MAX_SECRET_FILES = 128
_MAX_SECRET_BYTES = 64 * 1024
_MAX_TOTAL_BYTES = 1024 * 1024


class SecretStagingError(RuntimeError):
    """The projected Secret could not be converted into private custody."""


def _trusted_groups() -> set[int]:
    groups = set(os.getgroups()) if hasattr(os, "getgroups") else set()
    getegid = getattr(os, "getegid", None)
    if callable(getegid):
        groups.add(getegid())
    return groups


def _require_linux_identity(info: os.stat_result, label: str) -> None:
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid) or not hasattr(info, "st_uid"):
        raise SecretStagingError("secret staging requires POSIX identity metadata")
    if info.st_uid not in {0, geteuid()}:
        raise SecretStagingError(f"{label} has an untrusted owner")


def _require_readable_secret_file(info: os.stat_result, name: str) -> None:
    _require_linux_identity(info, f"projected secret {name!r}")
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise SecretStagingError(
            f"projected secret {name!r} is not a single-link regular file"
        )
    permissions = info.st_mode & 0o777
    if permissions & 0o007 or permissions & 0o022:
        raise SecretStagingError(
            f"projected secret {name!r} is writable or readable outside its service group"
        )
    geteuid = os.geteuid
    readable = bool(permissions & 0o400) if info.st_uid == geteuid() else False
    if info.st_gid in _trusted_groups() and permissions & 0o040:
        readable = True
    if info.st_uid == 0 and geteuid() == 0 and permissions & 0o400:
        readable = True
    if not readable:
        raise SecretStagingError(
            f"projected secret {name!r} is not readable by the service identity"
        )


def _source_is_read_only(path: Path) -> bool:
    readonly_flag = getattr(os, "ST_RDONLY", 1)
    try:
        return bool(os.statvfs(path).f_flag & readonly_flag)
    except OSError as exc:
        raise SecretStagingError("projected secret mount flags are unavailable") from exc


def _read_snapshot_file(snapshot_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        before = os.stat(name, dir_fd=snapshot_fd, follow_symlinks=False)
        fd = os.open(name, flags, dir_fd=snapshot_fd)
    except OSError as exc:
        raise SecretStagingError(f"projected secret {name!r} could not be opened") from exc
    try:
        opened = os.fstat(fd)
        if (
            before.st_dev,
            before.st_ino,
            stat.S_IFMT(before.st_mode),
            before.st_nlink,
            before.st_size,
        ) != (
            opened.st_dev,
            opened.st_ino,
            stat.S_IFMT(opened.st_mode),
            opened.st_nlink,
            opened.st_size,
        ):
            raise SecretStagingError(f"projected secret {name!r} changed before read")
        _require_readable_secret_file(opened, name)
        if opened.st_size > _MAX_SECRET_BYTES:
            raise SecretStagingError(f"projected secret {name!r} exceeds 64 KiB")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(16 * 1024, _MAX_SECRET_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_SECRET_BYTES:
                raise SecretStagingError(f"projected secret {name!r} exceeds 64 KiB")
        after = os.fstat(fd)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
            opened.st_nlink,
            opened.st_size,
            getattr(opened, "st_mtime_ns", None),
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            getattr(after, "st_mtime_ns", None),
        ):
            raise SecretStagingError(f"projected secret {name!r} changed during read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_private_file(directory: Path, name: str, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(os.fspath(directory / name), flags, 0o400)
    except OSError as exc:
        raise SecretStagingError(f"private secret {name!r} could not be created") from exc
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SecretStagingError(f"private secret {name!r} could not be written")
            view = view[written:]
        os.fsync(fd)
        os.fchmod(fd, 0o400)
    finally:
        os.close(fd)


def stage_projected_secrets(source: str | Path, destination: str | Path) -> int:
    """Copy one projected ``..data`` generation into private regular files."""
    source_path = Path(source)
    destination_path = Path(destination)
    if not source_path.is_absolute() or not destination_path.is_absolute():
        raise SecretStagingError("secret staging paths must be absolute")
    if destination_path.exists() or destination_path.is_symlink():
        raise SecretStagingError("private secret destination already exists")
    if not _source_is_read_only(source_path):
        raise SecretStagingError("projected secret source is not mounted read-only")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        source_fd = os.open(os.fspath(source_path), directory_flags)
    except OSError as exc:
        raise SecretStagingError("projected secret source is unavailable") from exc
    snapshot_fd: int | None = None
    temporary: Path | None = None
    try:
        source_info = os.fstat(source_fd)
        _require_linux_identity(source_info, "projected secret source")
        try:
            data_link = os.stat("..data", dir_fd=source_fd, follow_symlinks=False)
            target_name = os.readlink("..data", dir_fd=source_fd)
        except OSError as exc:
            raise SecretStagingError("projected secret has no readable ..data link") from exc
        if not stat.S_ISLNK(data_link.st_mode):
            raise SecretStagingError("projected secret ..data entry is not a symlink")
        if (
            not target_name
            or target_name in {".", ".."}
            or "/" in target_name
            or "\\" in target_name
            or not target_name.startswith("..")
        ):
            raise SecretStagingError("projected secret ..data target is unsafe")
        try:
            snapshot_fd = os.open(target_name, directory_flags, dir_fd=source_fd)
        except OSError as exc:
            raise SecretStagingError("projected secret snapshot is unavailable") from exc
        snapshot_info = os.fstat(snapshot_fd)
        _require_linux_identity(snapshot_info, "projected secret snapshot")
        if not stat.S_ISDIR(snapshot_info.st_mode):
            raise SecretStagingError("projected secret snapshot is not a directory")

        names = sorted(os.listdir(snapshot_fd))
        if not names or len(names) > _MAX_SECRET_FILES:
            raise SecretStagingError("projected secret has an invalid file count")
        for name in names:
            if _SAFE_SECRET_NAME.fullmatch(name) is None:
                raise SecretStagingError(f"projected secret name {name!r} is unsafe")

        parent = destination_path.parent
        parent_info = parent.lstat()
        _require_linux_identity(parent_info, "private secret staging parent")
        if not stat.S_ISDIR(parent_info.st_mode):
            raise SecretStagingError("private secret staging parent is not a directory")
        # The emptyDir mount root is owned by kubelet and may retain broad
        # traversal bits. It is mounted only into this sequential init
        # container, so there is no concurrent pod process at this path. The
        # generated child is still atomically published service-owned 0700 and is
        # the only subPath the application container ever receives.
        temporary = Path(tempfile.mkdtemp(prefix=".custody-", dir=parent))
        os.chmod(temporary, 0o700)

        total = 0
        for name in names:
            payload = _read_snapshot_file(snapshot_fd, name)
            total += len(payload)
            if total > _MAX_TOTAL_BYTES:
                raise SecretStagingError("projected secret exceeds the 1 MiB total limit")
            _write_private_file(temporary, name, payload)
        directory_fd = os.open(os.fspath(temporary), directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rename(temporary, destination_path)
        temporary = None
        return len(names)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
        if snapshot_fd is not None:
            os.close(snapshot_fd)
        os.close(source_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        count = stage_projected_secrets(args.source, args.destination)
    except (OSError, SecretStagingError) as exc:
        parser.exit(1, f"secret staging refused: {exc}\n")
    print(f"staged {count} secret files into private custody")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the init container
    raise SystemExit(main())
