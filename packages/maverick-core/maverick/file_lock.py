"""Cross-process serialization + atomic writes for small JSON/TOML state files.

Many modules persist a tiny state file with a *read-modify-write*: load the
whole dict, mutate one key, write it all back. Two correctness hazards recur:

  - **Torn read.** A bare ``write_text`` / ``open(..., "w")`` truncates the
    file in place; a concurrent reader sees a half-written file and its
    ``json.load`` raises -> the state is treated as empty or the caller
    crashes. ``atomic_write_text`` writes a *unique* temp file and ``os.replace``
    it into position, so a successful reader only ever sees the old or the new
    whole file. On Windows, a reader racing the rename can receive a transient
    sharing/access error; :func:`atomic_read_text` and
    :func:`atomic_read_bytes` apply the matching bounded retry.

  - **Lost update.** ``os.replace`` makes each write atomic but does NOT stop
    two processes from both loading the same totals and the second clobbering
    the first. A ``threading.Lock`` only serializes threads *within one
    process* — the dashboard, ``serve``, a cron ``dream``, and a webhook can be
    four separate processes against the same tenant data dir.
    ``cross_process_lock`` adds an advisory ``flock`` on a stable sidecar so the
    whole load-modify-save is serialized across processes too.

This mirrors the proven discipline in :mod:`maverick.quotas`
(``_cross_process_lock`` + unique ``mkstemp`` + ``os.replace``); it is factored
out here so the dozen other state stores can adopt it without copy-pasting the
platform-specific locking dance. It uses ``flock`` on POSIX and a byte-range
lock on Windows, plus bounded in-process lock stripes on both platforms.
"""
from __future__ import annotations

import logging
import math
import os
import re
import secrets
import stat
import tempfile
import threading
import time
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = [
    "cross_process_lock",
    "atomic_read_text",
    "atomic_read_bytes",
    "atomic_write_text",
    "atomic_write_text_chunks",
    "atomic_write_bytes",
    "ensure_private_directory",
    "ensure_private_file",
    "harden_path_permissions",
    "private_path_is_restricted",
]


# A Windows byte-range lock is process-scoped enough that competing threads can
# otherwise receive ``EDEADLK`` instead of waiting for each other.  A fixed set
# of local stripes serializes same-process contenders without creating an
# attacker-controlled, unbounded mapping of path -> Lock objects.  POSIX also
# benefits because ``flock`` semantics can vary for multiple descriptors in one
# process.
_LOCAL_LOCK_STRIPES = tuple(threading.RLock() for _ in range(64))
_THREAD_LOCK_STATE = threading.local()


def _canonical_lock_path(path: Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _local_lock_for(path: Path) -> threading.RLock:
    canonical = _canonical_lock_path(path)
    return _LOCAL_LOCK_STRIPES[hash(canonical) % len(_LOCAL_LOCK_STRIPES)]


def _windows_set_private_dacl(path: Path) -> None:
    """Install a protected owner/SYSTEM/Administrators-only Windows DACL."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    set_security = advapi32.SetFileSecurityW
    set_security.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    set_security.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    # P = protected DACL (no inherited broad Users/Everyone entry). OW is the
    # file owner, SY is LocalSystem, and BA is Built-in Administrators.
    descriptor = ctypes.c_void_p()
    sddl = "D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security_information = 0x00000004 | 0x80000000
        if not set_security(str(path), security_information, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.LocalFree(descriptor)


def _windows_secure_mkstemp(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
) -> tuple[int, str]:
    """Create a new temp file with the private DACL in the create syscall.

    ``tempfile.mkstemp`` is race-free with respect to the filename, but on
    Windows it initially inherits the parent DACL. Applying a private DACL in a
    second call still lets an inherited writer retain a handle opened between
    those calls. ``CreateFileW`` accepts a security descriptor at creation, so
    there is no permissive intermediate object.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    descriptor = ctypes.c_void_p()
    sddl = "D:P(A;;FA;;;OW)(A;;FA;;;SY)(A;;FA;;;BA)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = SECURITY_ATTRIBUTES(
        ctypes.sizeof(SECURITY_ATTRIBUTES),
        descriptor,
        False,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    try:
        for _ in range(100):
            candidate = directory / f"{prefix}{secrets.token_hex(16)}{suffix}"
            handle = create_file(
                str(candidate),
                0xC0010000,  # GENERIC_READ | GENERIC_WRITE | DELETE
                0,  # no sharing while the private payload is being written
                ctypes.byref(attributes),
                1,  # CREATE_NEW (never follow/overwrite a planted path)
                0x00000080,  # FILE_ATTRIBUTE_NORMAL
                None,
            )
            if handle != invalid_handle:
                try:
                    flags = os.O_RDWR | getattr(os, "O_BINARY", 0)
                    flags |= getattr(os, "O_NOINHERIT", 0)
                    fd = msvcrt.open_osfhandle(handle, flags)
                except BaseException:
                    kernel32.CloseHandle(handle)
                    try:
                        candidate.unlink()
                    except OSError:
                        pass
                    raise
                return fd, str(candidate)
            error = ctypes.get_last_error()
            if error not in {80, 183}:  # ERROR_FILE_EXISTS / ALREADY_EXISTS
                raise ctypes.WinError(error)
    finally:
        kernel32.LocalFree(descriptor)
    raise FileExistsError("unable to allocate a unique secure temporary file")


def _secure_mkstemp(
    directory: Path,
    *,
    prefix: str,
    suffix: str,
    mode: int,
) -> tuple[int, str]:
    if os.name == "nt":
        fd, path = _windows_secure_mkstemp(
            directory,
            prefix=prefix,
            suffix=suffix,
        )
    else:
        fd, path = tempfile.mkstemp(
            dir=str(directory),
            prefix=prefix,
            suffix=suffix,
        )
    try:
        # On Windows the DACL is already private; chmod only applies the
        # requested DOS read-only posture. On POSIX this applies the exact mode.
        os.chmod(path, mode)
        if os.name == "nt" and not private_path_is_restricted(path, mode):
            raise PermissionError(
                "filesystem did not preserve the private create-time ACL"
            )
    except BaseException:
        os.close(fd)
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return fd, path


def _windows_private_sddl(path: Path) -> tuple[str, str]:
    """Return ``(DACL SDDL, owner SID)`` for a Windows path."""
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = advapi32.GetNamedSecurityInfoW
    get_info.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    get_info.restype = wintypes.DWORD
    to_sddl = advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW
    to_sddl.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    to_sddl.restype = wintypes.BOOL
    sid_to_text = advapi32.ConvertSidToStringSidW
    sid_to_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    sid_to_text.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    result = get_info(
        str(path),
        1,  # SE_FILE_OBJECT
        0x00000001 | 0x00000004,  # OWNER + DACL
        ctypes.byref(owner),
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if result:
        raise OSError(result, "GetNamedSecurityInfoW failed")
    sddl_text = ctypes.c_wchar_p()
    owner_text = ctypes.c_wchar_p()
    try:
        if not to_sddl(descriptor, 1, 0x00000004, ctypes.byref(sddl_text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not sid_to_text(owner, ctypes.byref(owner_text)):
            raise ctypes.WinError(ctypes.get_last_error())
        return str(sddl_text.value or ""), str(owner_text.value or "")
    finally:
        if sddl_text:
            kernel32.LocalFree(sddl_text)
        if owner_text:
            kernel32.LocalFree(owner_text)
        if descriptor:
            kernel32.LocalFree(descriptor)


def harden_path_permissions(path: str | Path, mode: int = 0o600) -> None:
    """Apply real private permissions on POSIX and Windows.

    ``os.chmod(0o600)`` only toggles the DOS read-only bit on Windows and can
    leave sensitive files readable by inherited ``Users``/``Everyone`` ACEs.
    Windows therefore receives a protected DACL; failure is surfaced rather
    than silently claiming the file is private.
    """
    path = Path(path)
    os.chmod(path, mode)
    if os.name == "nt":
        _windows_set_private_dacl(path)


def ensure_private_directory(path: str | Path) -> Path:
    """Create/tighten a directory to an owner-only security boundary."""
    path = Path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    harden_path_permissions(path, 0o700)
    if not private_path_is_restricted(path, 0o700):
        raise PermissionError(f"directory could not be made private: {path}")
    return path


def ensure_private_file(path: str | Path, mode: int = 0o600) -> Path:
    """Tighten and verify an existing sensitive regular file.

    Atomic writers create new files privately, but deployments may already
    contain state written by an older release under inherited/broad ACLs. A
    security-sensitive reader calls this before opening such a file so an
    upgrade protects existing state too. Symlinks and non-regular files are
    refused rather than following an attacker-selected object while changing
    permissions.
    """
    path = Path(path)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PermissionError(f"sensitive state path is not a regular file: {path}")
    harden_path_permissions(path, mode)
    if not private_path_is_restricted(path, mode):
        raise PermissionError(f"file could not be made private: {path}")
    return path


def private_path_is_restricted(path: str | Path, mode: int = 0o600) -> bool:
    """Verify the effective owner-only posture used by security tests/health."""
    path = Path(path)
    if os.name != "nt":
        return (path.stat().st_mode & 0o777) == (mode & 0o777)
    try:
        sddl, owner_sid = _windows_private_sddl(path)
    except (OSError, ValueError):
        return False
    if not sddl.startswith("D:P"):
        return False
    allowed = {
        "OW", "SY", "BA", owner_sid,
        "S-1-3-4", "S-1-5-18", "S-1-5-32-544",
    }
    aces = re.findall(r"\(([^()]*)\)", sddl)
    principals = {ace.rsplit(";", 1)[-1] for ace in aces if ";" in ace}
    return bool(principals & {"OW", owner_sid, "S-1-3-4"}) and principals <= allowed


def _replace_with_retry(source: str | Path, target: str | Path) -> None:
    """Replace atomically, tolerating brief Windows sharing violations."""
    if os.name != "nt":
        os.replace(source, target)
        # The file contents were fsync'd before rename; persist the directory
        # entry too so replay/policy state survives an abrupt power loss.
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        parent_fd = os.open(str(Path(target).parent), flags)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        return

    # Python readers on Windows do not request FILE_SHARE_DELETE, so a short
    # read can make MoveFileEx/``os.replace`` fail with ACCESS_DENIED.  Retry
    # with a bounded deadline: this covers normal antivirus/indexer/read races
    # but still fails closed when another process pins the destination.
    deadline = time.monotonic() + 5.0
    delay = 0.002
    import ctypes
    from ctypes import wintypes

    move_file = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    while True:
        if move_file(
            str(source),
            str(target),
            0x00000001 | 0x00000008,  # REPLACE_EXISTING | WRITE_THROUGH
        ):
            return
        error = ctypes.get_last_error()
        if error not in {5, 32, 33} or time.monotonic() >= deadline:
            raise ctypes.WinError(error)
        time.sleep(delay)
        delay = min(delay * 1.5, 0.05)


def _windows_replace_open_fd(fd: int, target: str | Path) -> None:
    """Rename the still-open source object, binding publication to its handle.

    A path-based rename after closing the temp lets a writer on the parent
    directory substitute a different object at the random temp name. Windows'
    ``FileRenameInfo`` renames the object referenced by the verified handle, so
    source-path replacement cannot change the bytes that get published.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    # Do not resolve the final component: replacing an existing symlink must
    # replace the link itself, never follow it and overwrite its referent.
    destination = os.path.abspath(os.fspath(target))
    encoded_destination = destination.encode("utf-16-le")
    utf16_units = len(encoded_destination) // 2

    class FILE_RENAME_INFO(ctypes.Structure):
        _fields_ = [
            ("ReplaceIfExists", wintypes.BOOL),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            # Non-BMP characters occupy two UTF-16 code units on Windows.
            ("FileName", ctypes.c_wchar * (utf16_units + 1)),
        ]

    info = FILE_RENAME_INFO()
    info.ReplaceIfExists = True
    info.RootDirectory = None
    info.FileNameLength = len(encoded_destination)
    info.FileName = destination

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    set_info = kernel32.SetFileInformationByHandle
    set_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    set_info.restype = wintypes.BOOL
    flush = kernel32.FlushFileBuffers
    flush.argtypes = [wintypes.HANDLE]
    flush.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(fd)
    deadline = time.monotonic() + 5.0
    delay = 0.002
    while not set_info(handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
        error = ctypes.get_last_error()
        if error not in {5, 32, 33} or time.monotonic() >= deadline:
            raise ctypes.WinError(error)
        time.sleep(delay)
        delay = min(delay * 1.5, 0.05)
    if not flush(handle):
        raise ctypes.WinError(ctypes.get_last_error())


def _read_with_windows_retry(read, *, retry_seconds: float):
    try:
        retry_seconds = float(retry_seconds)
    except (TypeError, ValueError):
        raise ValueError("retry_seconds must be a finite number from 0 to 5") from None
    if not math.isfinite(retry_seconds) or not 0.0 <= retry_seconds <= 5.0:
        raise ValueError("retry_seconds must be a finite number from 0 to 5")
    if os.name != "nt":
        return read()
    deadline = time.monotonic() + retry_seconds
    delay = 0.002
    while True:
        try:
            return read()
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(delay)
            delay = min(delay * 1.5, 0.05)


def atomic_read_text(
    path: str | Path,
    *,
    encoding: str = "utf-8",
    retry_seconds: float = 1.0,
) -> str:
    """Read an atomically-published text file across Windows rename races."""
    path = Path(path)
    return _read_with_windows_retry(
        lambda: path.read_text(encoding=encoding),
        retry_seconds=retry_seconds,
    )


def atomic_read_bytes(
    path: str | Path,
    *,
    retry_seconds: float = 1.0,
) -> bytes:
    """Read an atomically-published binary file across Windows rename races."""
    path = Path(path)
    return _read_with_windows_retry(
        path.read_bytes,
        retry_seconds=retry_seconds,
    )


@contextmanager
def cross_process_lock(target: str | Path, *, strict: bool = False):
    """Advisory exclusive lock serializing a read-modify-write of ``target``.

    Keyed on a stable ``<name>.lock`` sidecar next to the file — never the file
    itself, since ``os.replace`` swaps the target's inode out from under any
    handle held on it. Lock creation/acquisition failures propagate instead of
    silently disabling serialization. ``strict=True`` additionally refuses to
    degrade to a process-local lock on filesystems without a working OS lock.
    """
    target = Path(target)
    lock_path = target.parent / (target.name + ".lock")
    canonical = _canonical_lock_path(lock_path)
    with _local_lock_for(lock_path):
        held: dict[str, tuple[int, bool]] = getattr(
            _THREAD_LOCK_STATE, "held", {},
        )
        _THREAD_LOCK_STATE.held = held
        if canonical in held:
            # msvcrt byte-range locks are not re-entrant, even from the same
            # thread. The outer scope already owns both the local stripe and
            # OS lock, so only maintain the recursion count here. A strict
            # nested caller must not inherit a degraded process-local lock.
            count, process_safe = held[canonical]
            if strict and not process_safe:
                raise RuntimeError(
                    "cross-process lock backend is unavailable for strict state"
                )
            held[canonical] = (count + 1, process_safe)
            try:
                yield
            finally:
                held[canonical] = (count, process_safe)
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        # Failure to create/open the coordination primitive must not silently
        # disable serialization for security-sensitive read-modify-write state.
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        backend: str | None = None
        try:
            try:
                import fcntl
            except ImportError:
                fcntl = None  # type: ignore[assignment]
            if fcntl is not None:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    backend = "fcntl"
                except OSError as exc:
                    # flock is genuinely unavailable on this platform/filesystem
                    # (a non-POSIX or network/overlay FS that answers LOCK_EX
                    # with ENOLCK/EOPNOTSUPP). Cross-process serialization is
                    # impossible there; the in-process lock held above
                    # (``_local_lock_for``) still serializes this process, so
                    # degrade to it rather than make every audit write and
                    # security-state read-modify-write fail on such a platform.
                    # A lock-FILE creation failure (the ``os.open`` above) still
                    # propagates -- only acquisition on an already-open fd
                    # degrades, so ``test_cross_process_lock_fails_closed_when_
                    # dir_unwritable`` stays fail-closed.
                    if strict:
                        raise RuntimeError(
                            "cross-process lock backend is unavailable for strict state"
                        ) from exc
                    log.debug(
                        "cross_process_lock: flock unavailable for %s (%s); "
                        "relying on the in-process lock only", lock_path, exc,
                    )
                    backend = None
            elif os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                backend = "msvcrt"
            else:  # pragma: no cover - supported platforms have one backend
                raise RuntimeError(
                    "no cross-process lock backend is available"
                ) from None
            harden_path_permissions(lock_path)
            held[canonical] = (1, backend is not None)
            try:
                yield
            finally:
                held.pop(canonical, None)
        finally:
            if backend == "fcntl":
                try:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass
            elif backend == "msvcrt":
                try:
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except (ImportError, OSError):
                    pass
            os.close(fd)


def atomic_write_text(path: str | Path, text: str, *, mode: int = 0o600,
                      encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically: a *unique* temp file in the same
    directory, then ``os.replace`` into position.

    A reader concurrent with the write sees either the old whole file or the new
    whole file — never a truncated one. The temp name is unique (``mkstemp``) so
    two concurrent writers don't collide on a shared ``.tmp`` (one ``os.replace``
    would otherwise move the temp out from under the other). The temp is cleaned
    up on any failure so a crashed write leaves no stray files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _secure_mkstemp(
        path.parent,
        prefix=f".{path.name}-",
        suffix=".tmp",
        mode=mode,
    )
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = -1  # ownership transferred to the file object
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
            if os.name == "nt":
                _windows_replace_open_fd(f.fileno(), path)
            else:
                _replace_with_retry(tmp, path)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text_chunks(
    path: str | Path,
    chunks: Iterable[str],
    *,
    mode: int = 0o600,
    encoding: str = "utf-8",
) -> int:
    """Atomically stream text chunks into a privately-created destination.

    This is the bounded-memory counterpart to :func:`atomic_write_text` for
    exports that may be large. The unpublished temp receives its final POSIX
    mode or Windows DACL at creation; only after every chunk is flushed and
    fsync'd is the verified handle published over ``path``. Returns the number
    of chunks written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _secure_mkstemp(
        path.parent,
        prefix=f".{path.name}-",
        suffix=".tmp",
        mode=mode,
    )
    count = 0
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            fd = -1
            for chunk in chunks:
                f.write(chunk)
                count += 1
            f.flush()
            os.fsync(f.fileno())
            if os.name == "nt":
                _windows_replace_open_fd(f.fileno(), path)
            else:
                _replace_with_retry(tmp, path)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return count


def atomic_write_bytes(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    """Byte-for-byte sibling of :func:`atomic_write_text` for binary payloads
    (e.g. an at-rest-sealed blob): unique temp in the same directory, then
    ``os.replace`` into position; the temp is cleaned up on any failure."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _secure_mkstemp(
        path.parent,
        prefix=f".{path.name}-",
        suffix=".tmp",
        mode=mode,
    )
    try:
        with os.fdopen(fd, "wb") as f:
            fd = -1
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            if os.name == "nt":
                _windows_replace_open_fd(f.fileno(), path)
            else:
                _replace_with_retry(tmp, path)
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
