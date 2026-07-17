"""Shared cross-process lock + atomic write helper (maverick.file_lock).

These back the file-race fixes across the state stores: atomic_write_text must
never leave a torn file for a concurrent reader, and cross_process_lock must
serialize a read-modify-write so concurrent writers don't lose updates.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest
from maverick.file_lock import (
    atomic_read_bytes,
    atomic_read_text,
    atomic_write_bytes,
    atomic_write_text,
    atomic_write_text_chunks,
    cross_process_lock,
    ensure_private_file,
    private_path_is_restricted,
)


def test_atomic_write_replaces_whole_file(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_text(p, json.dumps({"a": 1}))
    assert json.loads(p.read_text()) == {"a": 1}
    atomic_write_text(p, json.dumps({"a": 2}))
    assert json.loads(p.read_text()) == {"a": 2}


def test_atomic_write_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "s.txt"
    atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_atomic_write_supports_non_bmp_windows_path(tmp_path):
    p = tmp_path / "rocket-U0001f680U0001f680" / "state.json"
    atomic_write_text(p, '{"ok": true}')
    assert json.loads(p.read_text())["ok"] is True


def test_atomic_write_bytes_round_trips_privately_without_temp_files(tmp_path):
    p = tmp_path / "payload.bin"
    payload = bytes(range(256)) + b"\x00\xffaudit"
    atomic_write_bytes(p, payload)
    assert atomic_read_bytes(p) == payload
    assert private_path_is_restricted(p)
    assert [f.name for f in tmp_path.iterdir()] == ["payload.bin"]


def test_atomic_write_text_chunks_streams_privately(tmp_path):
    p = tmp_path / "export.jsonl"
    count = atomic_write_text_chunks(p, (f'{{"n":{i}}}\n' for i in range(3)))
    assert count == 3
    assert p.read_text(encoding="utf-8").splitlines() == [
        '{"n":0}',
        '{"n":1}',
        '{"n":2}',
    ]
    assert private_path_is_restricted(p)
    assert [f.name for f in tmp_path.iterdir()] == ["export.jsonl"]


def test_atomic_write_replaces_symlink_not_referent(tmp_path):
    referent = tmp_path / "referent.txt"
    link = tmp_path / "state.txt"
    referent.write_text("do-not-overwrite", encoding="utf-8")
    try:
        link.symlink_to(referent)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    atomic_write_text(link, "new-state")
    assert referent.read_text(encoding="utf-8") == "do-not-overwrite"
    assert not link.is_symlink()
    assert link.read_text(encoding="utf-8") == "new-state"


def test_atomic_write_sets_mode_0600(tmp_path):
    p = tmp_path / "s.txt"
    atomic_write_text(p, "x")
    assert private_path_is_restricted(p)


def test_ensure_private_file_hardens_existing_file(tmp_path):
    p = tmp_path / "legacy-secret.txt"
    p.write_text("secret", encoding="utf-8")
    if os.name != "nt":
        p.chmod(0o644)
    ensure_private_file(p)
    assert private_path_is_restricted(p)
    assert p.read_text(encoding="utf-8") == "secret"


def test_ensure_private_file_rejects_non_regular_path(tmp_path):
    with pytest.raises(PermissionError, match="not a regular file"):
        ensure_private_file(tmp_path)


def test_ensure_private_file_rejects_symlink(tmp_path):
    referent = tmp_path / "referent.txt"
    link = tmp_path / "secret-link.txt"
    referent.write_text("secret", encoding="utf-8")
    try:
        link.symlink_to(referent)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    with pytest.raises(PermissionError, match="not a regular file"):
        ensure_private_file(link)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    p = tmp_path / "s.txt"
    atomic_write_text(p, "x")
    # Only the target file remains -- the unique temp was replaced into place.
    assert [f.name for f in tmp_path.iterdir()] == ["s.txt"]


def test_atomic_write_creates_private_empty_temp_before_writing(tmp_path, monkeypatch):
    import maverick.file_lock as file_lock

    seen: list[tuple[int, bool]] = []
    real_create = file_lock._secure_mkstemp

    def _record_create(directory, *, prefix, suffix, mode):
        fd, path = real_create(
            directory,
            prefix=prefix,
            suffix=suffix,
            mode=mode,
        )
        seen.append((os.stat(path).st_size, file_lock.private_path_is_restricted(path)))
        return fd, path

    monkeypatch.setattr(file_lock, "_secure_mkstemp", _record_create)
    file_lock.atomic_write_text(tmp_path / "secret.txt", "sensitive")
    assert seen == [(0, True)]


def test_atomic_write_no_torn_read_under_concurrency(tmp_path):
    p = tmp_path / "s.json"
    atomic_write_text(p, json.dumps({"n": 0}))
    errors: list[Exception] = []
    successful_reads = 0
    stop = threading.Event()

    def writer():
        for i in range(300):
            atomic_write_text(p, json.dumps({"n": i, "pad": "y" * 200}))

    def reader():
        nonlocal successful_reads
        while not stop.is_set():
            try:
                json.loads(p.read_text())
                successful_reads += 1
            except PermissionError as e:
                # Windows opens do not request FILE_SHARE_DELETE. A reader can
                # lose the race with MoveFileEx and receive a transient access
                # denial; successful reads must still be complete documents.
                if os.name != "nt":
                    errors.append(e)
            except (ValueError, OSError) as e:
                errors.append(e)

    rt = threading.Thread(target=reader)
    wt = threading.Thread(target=writer)
    rt.start()
    wt.start()
    wt.join()
    stop.set()
    rt.join()
    assert not errors, errors[:3]
    assert successful_reads > 0
    assert json.loads(p.read_text())["n"] == 299


@pytest.mark.skipif(os.name != "nt", reason="Windows rename sharing semantics")
def test_atomic_read_retries_transient_windows_access_denial(
    tmp_path, monkeypatch,
):
    p = tmp_path / "state.json"
    p.write_text('{"ok": true}', encoding="utf-8")
    real_read = type(p).read_text
    attempts = 0

    def _flaky_read(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("simulated MoveFileEx race")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(type(p), "read_text", _flaky_read)
    assert json.loads(atomic_read_text(p))["ok"] is True
    assert attempts == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows rename sharing semantics")
def test_atomic_write_waits_for_real_windows_sharing_denial(tmp_path):
    """A real handle without FILE_SHARE_DELETE pins the old destination.

    The atomic publisher must retry until that handle closes, rather than
    exposing a transient Windows sharing violation to normal callers.
    """
    import ctypes
    from ctypes import wintypes

    p = tmp_path / "shared.txt"
    atomic_write_text(p, "old")

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = create_file(
        str(p),
        0x80000000,  # GENERIC_READ
        0x00000001 | 0x00000002,  # share READ|WRITE, deliberately not DELETE
        None,
        3,  # OPEN_EXISTING
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    assert handle != invalid_handle, ctypes.WinError(ctypes.get_last_error())

    started = threading.Event()
    errors: list[BaseException] = []

    def writer():
        started.set()
        try:
            atomic_write_text(p, "new")
        except BaseException as exc:  # surfaced below with thread context
            errors.append(exc)

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(timeout=2)
    try:
        time.sleep(0.15)
        assert thread.is_alive(), "write unexpectedly bypassed sharing denial"
    finally:
        assert close_handle(handle)
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert not errors
    assert p.read_text(encoding="utf-8") == "new"


def test_cross_process_lock_serializes_writers(tmp_path):
    """The lock must serialize a load-modify-save so concurrent increments of a
    shared counter don't lose updates."""
    p = tmp_path / "counter.json"
    atomic_write_text(p, json.dumps({"c": 0}))
    n, per = 12, 50

    def worker():
        for _ in range(per):
            with cross_process_lock(p):
                cur = json.loads(p.read_text())["c"]
                atomic_write_text(p, json.dumps({"c": cur + 1}))

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert json.loads(p.read_text())["c"] == n * per


def test_cross_process_lock_creates_sidecar(tmp_path):
    p = tmp_path / "x.json"
    with cross_process_lock(p):
        pass
    lock_path = tmp_path / "x.json.lock"
    assert lock_path.exists()
    assert private_path_is_restricted(lock_path)
    # The lock sidecar is separate from the target (os.replace swaps the inode).
    assert not p.exists()


def test_cross_process_lock_is_reentrant_for_same_thread(tmp_path):
    p = tmp_path / "nested.json"
    with cross_process_lock(p), cross_process_lock(p):
        atomic_write_text(p, "nested")
    assert p.read_text() == "nested"


def test_strict_cross_process_lock_uses_platform_backend(tmp_path):
    p = tmp_path / "strict.json"
    with cross_process_lock(p, strict=True):
        atomic_write_text(p, "strict")
    assert p.read_text() == "strict"


def test_strict_nested_lock_refuses_degraded_outer_scope(tmp_path):
    import maverick.file_lock as file_lock

    p = tmp_path / "degraded.json"
    lock_path = p.parent / (p.name + ".lock")
    canonical = file_lock._canonical_lock_path(lock_path)
    prior = getattr(file_lock._THREAD_LOCK_STATE, "held", None)
    file_lock._THREAD_LOCK_STATE.held = {canonical: (1, False)}
    try:
        with pytest.raises(RuntimeError, match="backend is unavailable"):
            with cross_process_lock(p, strict=True):
                pytest.fail("strict lock unexpectedly inherited a degraded scope")
    finally:
        if prior is None:
            delattr(file_lock._THREAD_LOCK_STATE, "held")
        else:
            file_lock._THREAD_LOCK_STATE.held = prior


def test_cross_process_lock_serializes_processes(tmp_path):
    p = tmp_path / "process-counter.json"
    atomic_write_text(p, json.dumps({"c": 0}))
    code = """
import json
import sys
from pathlib import Path
from maverick.file_lock import atomic_write_text, cross_process_lock

path = Path(sys.argv[1])
for _ in range(20):
    with cross_process_lock(path):
        current = json.loads(path.read_text())["c"]
        atomic_write_text(path, json.dumps({"c": current + 1}))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(p)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(4)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
    assert json.loads(p.read_text())["c"] == 80


def test_cross_process_lock_fails_closed_when_dir_unwritable(tmp_path):
    # An impossible lock dir must raise rather than silently running an
    # unserialized security-sensitive read-modify-write.
    bogus = tmp_path / "missing-parent-file" / "child.json"
    # Create a *file* where a directory is expected so mkdir/open fail.
    (tmp_path / "missing-parent-file").write_text("not a dir")
    with pytest.raises(OSError), cross_process_lock(bogus):
        pytest.fail("lock acquisition unexpectedly succeeded")
    assert not os.path.exists(str(bogus) + ".lock")


def test_cross_process_lock_rejects_dangling_sidecar_symlink(tmp_path):
    target = tmp_path / "state.json"
    lock_path = tmp_path / "state.json.lock"
    outside = tmp_path / "outside" / "created-by-followed-link"
    try:
        lock_path.symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise

    with pytest.raises((OSError, PermissionError)):
        with cross_process_lock(target, strict=True):
            pytest.fail("a dangling lock alias was followed")

    # In particular, Windows O_CREAT must not follow the dangling sidecar and
    # create its outside referent before post-open validation can run.
    assert not outside.exists()
    assert lock_path.is_symlink()


def test_cross_process_lock_rejects_hardlinked_sidecar(tmp_path):
    target = tmp_path / "state.json"
    lock_path = tmp_path / "state.json.lock"
    outside = tmp_path / "outside-lock"
    outside.write_bytes(b"do-not-lock-or-mutate")
    try:
        lock_path.hardlink_to(outside)
    except OSError:
        pytest.skip("hardlinks are unavailable on this host")

    with pytest.raises(PermissionError, match="single-link regular file"):
        with cross_process_lock(target, strict=True):
            pytest.fail("a hardlinked lock sidecar was accepted")

    assert outside.read_bytes() == b"do-not-lock-or-mutate"
