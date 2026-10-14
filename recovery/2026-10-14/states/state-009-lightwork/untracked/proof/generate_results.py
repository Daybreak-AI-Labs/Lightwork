#!/usr/bin/env python3
"""Generate source-content-bound local fixture results with retained raw output.

This runner deliberately records failures, skips, and missing prerequisites as
observed.  It does not convert a fixture result into competitive-capability,
customer-ROI, production-security, or release evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "maverick-core"))

from maverick import proof_provenance  # noqa: E402


@dataclass(frozen=True)
class Harness:
    id: str
    title: str
    script: str
    prerequisites: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CommandOutcome:
    started_at: float
    completed_at: float
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    output_limit_exceeded: bool = False
    termination_reason: str | None = None


@dataclass
class _CaptureState:
    buffers: dict[str, bytearray] = field(
        default_factory=lambda: {"stdout": bytearray(), "stderr": bytearray()}
    )
    completed_streams: set[str] = field(default_factory=set)
    overflowed_streams: set[str] = field(default_factory=set)
    reader_errors: list[Exception] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    limit_event: threading.Event = field(default_factory=threading.Event)


HARNESSES = (
    Harness(
        "platform-source-invariants",
        "Platform source-invariant scoreboard",
        "proof/run_proof.py",
        ("Python 3.10-3.12 and the source checkout's core package",),
        (
            "Exercises named catalog and enforcement-code invariants with local fixtures.",
            "Does not call a live connector or qualify a deployment.",
        ),
    ),
    Harness(
        "self-learning-fixture",
        "Self-learning fixture checks",
        "proof/self_harness_proof.py",
        ("Python 3.10-3.12 and writable temporary storage",),
        (
            "Uses fixed seeded inputs and an injected scorer.",
            "Does not prove useful learning or robustness to real-world outcome manipulation.",
        ),
    ),
    Harness(
        "dgm-code-rung-fixture",
        "Governed DGM code-rung fixture",
        "proof/dgm_code_rung_proof.py",
        (
            "Python 3.10-3.12 and cryptography for Ed25519 checks.",
            "A configured non-host, no-egress, bounded evaluator that returns the "
            "controller-authenticated evidence contract; no bundled backend currently "
            "satisfies that production-relevant evaluator prerequisite.",
        ),
        (
            "The patch proposer is stubbed with a supplied diff.",
            "A configured evaluator result would still be local regression evidence, not "
            "autonomous-improvement or production-safe-promotion proof.",
        ),
    ),
    Harness(
        "swebench-shaped-fixture",
        "SWE-bench-shaped governance fixture",
        "proof/swebench_governed_proof.py",
        ("Python 3.10-3.12 and cryptography for local signatures",),
        (
            "Uses a self-seeded toy repository, an oracle patch, and trusted host execution.",
            "Does not run SWE-bench Verified or produce an agent capability score.",
        ),
    ),
    Harness(
        "dgm-uplift-fixture",
        "Governed DGM-uplift fixture",
        "proof/dgm_uplift_proof.py",
        ("Python 3.10-3.12, cryptography, pytest, and writable temporary storage",),
        (
            "Uses a toy solver, self-seeded instances, oracle-authored patches, and host pytest.",
            "Does not establish autonomous improvement or external-task generalization.",
        ),
    ),
    Harness(
        "adapter-rung-fixture",
        "Governed adapter-rung fixture",
        "proof/adapter_rung_proof.py",
        (
            "Python 3.10-3.12 and cryptography for local signatures.",
            "The fixture's generated public key is temporarily installed through the "
            "process-level deployment trust registry for each promotion transaction.",
        ),
        (
            "The trainer writes a deterministic LoRA-shaped artifact and the evaluator is a "
            "deterministic stub.",
            "The locally generated signer and trust root are co-located fixture material, not "
            "independent approval or production key custody.",
            "Does not establish training quality, model uplift, tenant-data safety, or serving "
            "behavior.",
        ),
    ),
)

PROOF_HARNESS_TIMEOUT_SECONDS = 900
PROOF_RAW_STREAM_MAX_BYTES = 4 * 1024 * 1024
PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES = 24 * 1024 * 1024
PROOF_RESULTS_MAX_BYTES = 16 * 1024 * 1024
PROOF_MANIFEST_MAX_BYTES = 4 * 1024 * 1024
PROOF_GENERATION_MAX_BYTES = 4 * 1024 * 1024
PROOF_ARTIFACT_TREE_MAX_BYTES = 64 * 1024 * 1024
PROOF_SOURCE_FILE_MAX_BYTES = 4 * 1024 * 1024
PROOF_SOURCE_TREE_MAX_BYTES = 32 * 1024 * 1024

ISOLATED_BOOTSTRAP = "proof/isolated_harness_bootstrap.py"
_ISOLATED_PYTHON_OPTIONS = (
    "-I",
    "-S",
    "-B",
    "-X",
    "utf8",
    "-W",
    "default",
)
_CANONICAL_GENERATOR = (
    "python",
    *_ISOLATED_PYTHON_OPTIONS,
    "proof/generate_results.py",
)
_APPROVED_GENERATOR_FLAGS = {"--require-pass"}
_HARNESS_INTERPRETER_PREFIX = ("python", *_ISOLATED_PYTHON_OPTIONS)
_RUNTIME_IMPORT_PATHS_SCHEMA = "lightwork-proof-runtime-import-paths.v1"
_RUNTIME_IMPORT_PATHS_FILE = "runtime-import-paths.json"
_GENERATION_SCHEMA = "lightwork-proof-generation.v2"
_GENERATION_FILE = "proof/results/generation.json"
_ISOLATED_IGNORED_PYTHON_ENV = (
    "PYTHONBREAKPOINT",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "PYTHONIOENCODING",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
    "PYTHONSAFEPATH",
    "PYTHONUTF8",
    "PYTHONWARNINGS",
)
_REQUIRED_BOUND_SOURCE_PATHS = (
    "packages/maverick-core/maverick/proof_provenance.py",
    "proof/generate_results.py",
    ISOLATED_BOOTSTRAP,
    *(harness.script for harness in HARNESSES),
)
_MATERIALIZED_PROOF_FILES = frozenset(
    {
        "generate_results.py",
        "isolated_harness_bootstrap.py",
        *(Path(harness.script).name for harness in HARNESSES),
    }
)


def _lexical_path(root: Path, path: Path) -> Path:
    candidate = Path(os.path.abspath(path if path.is_absolute() else root / path))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"proof path must remain lexically inside repository: {path}") from exc
    return candidate


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, FileNotFoundError):
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _reject_linked_ancestors(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    cursor = root
    for part in relative.parts:
        cursor /= part
        if not cursor.exists() and not cursor.is_symlink():
            continue
        if cursor.is_symlink() or _is_reparse_point(cursor):
            raise ValueError(f"proof path contains a symlink or reparse point: {relative}")


def _require_regular_source(root: Path, relative: str) -> Path:
    path = _lexical_path(root, Path(relative))
    _reject_linked_ancestors(root, path)
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"required proof source is missing: {relative}") from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f"required proof source is not a regular file: {relative}")
    return path


def _prepare_fixed_artifact_paths(
    root: Path,
    output: Path | None,
    evidence_dir: Path | None,
) -> tuple[Path, Path]:
    fixed_output = _lexical_path(root, Path("proof/RESULTS.md"))
    fixed_evidence = _lexical_path(root, Path("proof/results"))
    requested_output = _lexical_path(root, output or fixed_output)
    requested_evidence = _lexical_path(root, evidence_dir or fixed_evidence)
    if requested_output != fixed_output or requested_evidence != fixed_evidence:
        raise ValueError("proof outputs and exclusions are fixed repository-relative paths")
    for path in (
        fixed_output,
        fixed_evidence,
        fixed_evidence / "manifest.json",
        root / _GENERATION_FILE,
        fixed_evidence / "raw",
        *(
            fixed_evidence / "raw" / f"{harness.id}.{stream}.txt"
            for harness in HARNESSES
            for stream in ("stdout", "stderr")
        ),
    ):
        _reject_linked_ancestors(root, path)
    proof_dir = root / "proof"
    if _directory_identity(proof_dir) != _directory_identity(fixed_output.parent):
        raise ValueError("proof output parent identity drifted")
    return fixed_output, fixed_evidence


def _directory_identity(path: Path) -> tuple[int, int]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"proof output directory disappeared: {path}") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or _is_reparse_point(path)
    ):
        raise ValueError(f"proof output parent is not a regular directory: {path}")
    return metadata.st_dev, metadata.st_ino


def _ensure_regular_directory(root: Path, path: Path) -> None:
    target = _lexical_path(root, path)
    cursor = root
    for part in target.relative_to(root).parts:
        cursor /= part
        if not cursor.exists() and not cursor.is_symlink():
            cursor.mkdir(mode=0o700)
        _directory_identity(cursor)


def _write_new_file(path: Path, payload: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _regular_file_record(
    path: Path,
    *,
    relative: str,
    maximum_bytes: int,
) -> dict[str, object]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or metadata.st_nlink != 1
        or _is_reparse_point(path)
    ):
        raise RuntimeError(f"proof execution path is not a regular file: {relative}")
    if metadata.st_size < 0 or metadata.st_size > maximum_bytes:
        raise RuntimeError(f"proof execution path exceeds its byte limit: {relative}")
    identity_names = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_nlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or any(
                getattr(opened, name) != getattr(metadata, name)
                for name in identity_names
            )
        ):
            raise RuntimeError(f"proof execution path changed before read: {relative}")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum_bytes + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum_bytes:
                raise RuntimeError(
                    f"proof execution path exceeds its byte limit: {relative}"
                )
        after_open = os.fstat(descriptor)
        if any(
            getattr(after_open, name) != getattr(opened, name)
            for name in identity_names
        ):
            raise RuntimeError(f"proof execution path changed during read: {relative}")
    finally:
        os.close(descriptor)
    after = path.lstat()
    if (
        path.is_symlink()
        or _is_reparse_point(path)
        or any(getattr(after, name) != getattr(metadata, name) for name in identity_names)
    ):
        raise RuntimeError(f"proof execution path changed during read: {relative}")
    payload = b"".join(chunks)
    if len(payload) != metadata.st_size:
        raise RuntimeError(f"proof execution path length changed during read: {relative}")
    return {
        "path": relative,
        "bytes": len(payload),
        "sha256": proof_provenance.sha256_bytes(payload),
        "mode": "100755"
        if os.name != "nt" and metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        else "100644",
    }


def _materialized_proof_tree_records(root: Path) -> list[dict[str, object]]:
    proof_dir = root / "proof"
    _directory_identity(proof_dir)
    observed: set[str] = set()
    records: list[dict[str, object]] = []
    total_bytes = 0
    expected = set(_MATERIALIZED_PROOF_FILES)
    entry_count = 0
    with os.scandir(proof_dir) as entries:
        for entry in entries:
            if entry.name not in expected:
                raise RuntimeError(
                    f"materialized proof tree contains an unexpected entry: {entry.name}"
                )
            entry_count += 1
            if entry_count > len(expected):
                raise RuntimeError("materialized proof tree exceeds its hard entry ceiling")
            observed.add(entry.name)
            path = proof_dir / entry.name
            record = _regular_file_record(
                path,
                relative=f"proof/{entry.name}",
                maximum_bytes=PROOF_SOURCE_FILE_MAX_BYTES,
            )
            records.append(record)
            total_bytes += int(record["bytes"])
            if total_bytes > PROOF_SOURCE_TREE_MAX_BYTES:
                raise RuntimeError("materialized proof tree exceeds its source byte limit")
    if observed != expected:
        raise RuntimeError(
            "materialized proof tree is not exact: "
            f"missing={sorted(expected - observed)}"
        )
    return sorted(records, key=lambda item: str(item["path"]))


def _materialized_tree_token(root: Path) -> str:
    return str(proof_provenance.materialized_tree_identity(root)["sha256"])


def _make_materialized_tree_read_only(root: Path) -> None:
    proof_provenance.make_materialized_tree_read_only(root)


def _validate_local_provenance_module(root: Path) -> None:
    expected = _require_regular_source(
        root,
        "packages/maverick-core/maverick/proof_provenance.py",
    )
    actual_raw = getattr(proof_provenance, "__file__", None)
    if not isinstance(actual_raw, str):
        raise RuntimeError("proof provenance module has no source path")
    actual = Path(actual_raw).resolve(strict=True)
    if actual != expected.resolve(strict=True):
        raise RuntimeError("proof generator imported provenance from another checkout")


def _validate_canonical_worktree(root: Path) -> None:
    discovered = proof_provenance.discover_repository_root(root)
    if discovered != root:
        raise RuntimeError("canonical proof root is not the discovered Git worktree root")
    control = root / ".git"
    try:
        metadata = control.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError("canonical proof worktree has no local .git directory") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or control.is_symlink()
        or _is_reparse_point(control)
    ):
        raise RuntimeError(
            "canonical proof generation rejects linked-worktree and redirected .git controls"
        )


def _atomic_write_bytes(root: Path, path: Path, payload: bytes) -> None:
    target = _lexical_path(root, path)
    _reject_linked_ancestors(root, target)
    parent_identity = _directory_identity(target.parent)
    use_directory_handle = (
        os.open in os.supports_dir_fd
        and os.replace in os.supports_dir_fd
        and os.unlink in os.supports_dir_fd
        and hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
    )
    if use_directory_handle:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_descriptor = os.open(target.parent, directory_flags)
        temporary_name = f".{target.name}.{secrets.token_hex(16)}.tmp"
        descriptor = -1
        try:
            directory_metadata = os.fstat(directory_descriptor)
            if (directory_metadata.st_dev, directory_metadata.st_ino) != parent_identity:
                raise ValueError("proof output parent changed before publication")
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_descriptor,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temporary_name,
                target.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            os.fsync(directory_descriptor)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            os.close(directory_descriptor)
        _reject_linked_ancestors(root, target)
        if _directory_identity(target.parent) != parent_identity:
            raise ValueError("proof output parent changed during publication")
        return

    # Windows has no dir_fd variants for replace/unlink.  Revalidate both the
    # complete no-reparse chain and the parent identity immediately before and
    # after the replace, so any junction/symlink swap invalidates generation.
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _reject_linked_ancestors(root, target)
        if _directory_identity(target.parent) != parent_identity:
            raise ValueError("proof output parent changed before publication")
        os.replace(temporary, target)
        _reject_linked_ancestors(root, target)
        if _directory_identity(target.parent) != parent_identity:
            raise ValueError("proof output parent changed during publication")
    finally:
        if (
            temporary.exists()
            and not temporary.is_symlink()
            and _directory_identity(temporary.parent) == parent_identity
        ):
            temporary.unlink()


def _canonical_generator_command(flags: Sequence[str]) -> list[str]:
    normalized = [str(flag) for flag in flags]
    if len(normalized) != len(set(normalized)) or any(
        flag not in _APPROVED_GENERATOR_FLAGS for flag in normalized
    ):
        raise ValueError("generator command contains an unapproved or duplicate flag")
    return [*_CANONICAL_GENERATOR, *sorted(normalized)]


def _canonical_cli_invocation(raw_argv: Sequence[str]) -> bool:
    script = (_ROOT / "proof" / "generate_results.py").resolve()
    original = list(getattr(sys, "orig_argv", ()))
    script_index = 1 + len(_ISOLATED_PYTHON_OPTIONS)
    if (
        len(original) != script_index + 1 + len(raw_argv)
        or tuple(original[1:script_index]) != _ISOLATED_PYTHON_OPTIONS
        or original[script_index + 1 :] != list(raw_argv)
        or sys.argv[1:] != list(raw_argv)
        or not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.safe_path
        or not sys.flags.utf8_mode
        or not sys.dont_write_bytecode
        or "default" not in sys.warnoptions
    ):
        return False
    try:
        if (
            original[script_index] != "proof/generate_results.py"
            or sys.argv[0] != "proof/generate_results.py"
        ):
            return False
        invoked_script = Path(original[script_index]).resolve()
        argv_script = Path(sys.argv[0]).resolve()
        invoked_token = Path(original[0])
        if invoked_token.is_absolute():
            invoked_python = invoked_token.resolve(strict=True)
        else:
            discovered = shutil.which(original[0])
            if discovered is None:
                return False
            invoked_python = Path(discovered).resolve(strict=True)
        runtime_python = Path(sys.executable).resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if invoked_python != runtime_python:
        return False
    repository_root = _ROOT.resolve()
    if Path.cwd().resolve() != repository_root:
        return False
    try:
        runtime_python.relative_to(repository_root)
    except ValueError:
        pass
    else:
        return False
    return invoked_script == script and argv_script == script


def _observed_generator_command() -> dict[str, object]:
    original = list(getattr(sys, "orig_argv", ()))
    if not original or not all(isinstance(token, str) and token for token in original):
        raise RuntimeError("Python did not retain an exact generator argv")
    return proof_provenance.command_record(original)


def _runtime_import_path_records() -> list[dict[str, str]]:
    return proof_provenance.runtime_import_path_records()


def _runtime_executable_record(root: Path) -> dict[str, object]:
    executable = Path(sys.executable).resolve(strict=True)
    metadata = executable.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or executable.is_symlink()
        or _is_reparse_point(executable)
    ):
        raise RuntimeError("Python runtime executable is not a regular file")
    digest = hashlib.sha256()
    with executable.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    outside_repository = True
    try:
        executable.relative_to(root)
    except ValueError:
        pass
    else:
        outside_repository = False
    return {
        "path": str(executable),
        "sha256": digest.hexdigest(),
        "version": sys.version,
        "implementation": sys.implementation.name,
        "outside_repository": outside_repository,
        "digest_scope": "exact Python executable bytes and runtime version",
    }


def _write_runtime_import_control(
    temporary_root: Path,
    paths: Sequence[str],
    *,
    admissions: Sequence[Mapping[str, str]] = (),
) -> dict[str, object]:
    control = temporary_root / _RUNTIME_IMPORT_PATHS_FILE
    record = {
        "schema": _RUNTIME_IMPORT_PATHS_SCHEMA,
        "paths": list(paths),
    }
    payload = proof_provenance.canonical_json_bytes(record)
    descriptor = os.open(
        control,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_BINARY", 0),
        0o600,
    )
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    control.chmod(0o444)
    return {
        **record,
        "admissions": [dict(item) for item in admissions],
        "sha256": proof_provenance.sha256_bytes(payload),
        "digest_scope": "exact canonical JSON runtime import-path control bytes",
    }


def _validate_runtime_import_control(
    temporary_root: Path,
    expected: Mapping[str, object],
) -> None:
    control = temporary_root / _RUNTIME_IMPORT_PATHS_FILE
    metadata = control.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or control.is_symlink()
        or metadata.st_nlink != 1
        or _is_reparse_point(control)
    ):
        raise RuntimeError("runtime import-path control changed type during execution")
    payload = control.read_bytes()
    expected_payload = proof_provenance.canonical_json_bytes(
        {
            "schema": expected["schema"],
            "paths": expected["paths"],
        }
    )
    if (
        payload != expected_payload
        or proof_provenance.sha256_bytes(payload) != expected.get("sha256")
    ):
        raise RuntimeError("runtime import-path control changed during execution")


def _drain_bounded_stream(name: str, pipe, state: _CaptureState) -> None:
    try:
        while True:
            remaining = PROOF_RAW_STREAM_MAX_BYTES - len(state.buffers[name])
            chunk = pipe.read(min(64 * 1024, remaining + 1))
            if not chunk:
                break
            retained = chunk[:remaining]
            if retained:
                state.buffers[name].extend(retained)
            if len(chunk) > remaining:
                with state.lock:
                    state.overflowed_streams.add(name)
                state.limit_event.set()
                break
    except Exception as exc:  # pragma: no cover - platform pipe failure
        with state.lock:
            state.reader_errors.append(exc)
        state.limit_event.set()
    finally:
        with state.lock:
            state.completed_streams.add(name)
        try:
            pipe.close()
        except OSError:
            pass


def _wait_for_bounded_process(
    process: subprocess.Popen[bytes],
    state: _CaptureState,
) -> tuple[bool, str | None]:
    deadline = time.monotonic() + PROOF_HARNESS_TIMEOUT_SECONDS
    exit_observed_at: float | None = None
    while True:
        with state.lock:
            streams_complete = len(state.completed_streams) == 2
            overflows = sorted(state.overflowed_streams)
            errors = bool(state.reader_errors)
        if overflows or errors:
            reason = (
                "output_limit:" + ",".join(overflows)
                if overflows
                else "output_reader_failure"
            )
            return False, reason
        now = time.monotonic()
        if now >= deadline:
            return True, "timeout"
        if process.poll() is not None:
            if streams_complete:
                return False, None
            if exit_observed_at is None:
                exit_observed_at = now
            elif now - exit_observed_at >= 0.25:
                return False, "descendant_pipe_drain_timeout"
        state.limit_event.wait(timeout=min(0.02, max(0.0, deadline - now)))


def _finish_bounded_process(
    process: subprocess.Popen[bytes],
    readers: Sequence[threading.Thread],
    state: _CaptureState,
) -> int:
    try:
        exit_code = process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("proof harness process did not terminate") from exc
    for reader in readers:
        reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        for pipe in (process.stdout, process.stderr):
            if pipe is None:
                continue
            try:
                pipe.close()
            except OSError:
                pass
        for reader in readers:
            reader.join(timeout=1)
    if any(reader.is_alive() for reader in readers):
        raise RuntimeError("proof harness output readers did not terminate")
    with state.lock:
        errors = list(state.reader_errors)
        overflows = sorted(state.overflowed_streams)
    if errors and not overflows:
        raise RuntimeError("proof harness output reader failed") from errors[0]
    return exit_code


def _run(argv: Sequence[str], *, cwd: Path, env: dict[str, str]) -> CommandOutcome:
    started = time.time()
    with proof_provenance.process_tree_controller() as tree:
        process = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            **tree.popen_kwargs,
        )
        if process.stdout is None or process.stderr is None:  # pragma: no cover - invariant
            process.kill()
            raise RuntimeError("proof harness pipes were not created")
        tree.attach(process)
        state = _CaptureState()
        readers = [
            threading.Thread(
                target=_drain_bounded_stream,
                args=(name, pipe, state),
                name=f"proof-{name}-reader",
                daemon=True,
            )
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in readers:
            reader.start()

        timed_out, termination_reason = _wait_for_bounded_process(process, state)
        if termination_reason is not None:
            seal = tree.seal(process, terminate=True)
            if seal.control_failure is not None:
                termination_reason = (
                    f"process_tree_control_failure:{seal.control_failure}"
                )
        exit_code = _finish_bounded_process(process, readers, state)
        if termination_reason is None:
            seal = tree.seal(process)
            if seal.control_failure is not None:
                termination_reason = (
                    f"process_tree_control_failure:{seal.control_failure}"
                )
            elif seal.descendant_survived:
                termination_reason = "descendant_process_survived"
    with state.lock:
        overflows = sorted(state.overflowed_streams)
    finished = time.time()
    return CommandOutcome(
        started_at=started,
        completed_at=finished,
        exit_code=exit_code,
        stdout=bytes(state.buffers["stdout"]),
        stderr=bytes(state.buffers["stderr"]),
        timed_out=timed_out,
        output_limit_exceeded=bool(overflows),
        termination_reason=termination_reason,
    )


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _source_exclusions(root: Path) -> list[str]:
    return proof_provenance.proof_source_exclusions(
        root,
        generated_paths=(root / "proof/RESULTS.md", root / "proof/results"),
    )


def _execution_environment(
    root: Path,
    runtime_home: Path,
    *,
    parent_environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str], dict[str, object]]:
    environment, fixed, provenance = (
        proof_provenance.fixture_execution_environment(
            root,
            runtime_home,
            parent_environment=parent_environment,
        )
    )
    return environment, fixed, provenance


def _raw_record(
    staged_raw_dir: Path,
    harness: Harness,
    logical_argv: Sequence[str],
    execution_argv: Sequence[str],
    outcome: CommandOutcome,
) -> dict[str, object]:
    if outcome.timed_out and outcome.output_limit_exceeded:
        raise RuntimeError("proof runner reported contradictory termination flags")
    control_failure = bool(
        outcome.termination_reason
        and outcome.termination_reason.startswith("process_tree_control_failure:")
    )
    if (
        outcome.timed_out
        and outcome.termination_reason != "timeout"
        and not control_failure
    ) or (not outcome.timed_out and outcome.termination_reason == "timeout"):
        raise RuntimeError("proof runner timeout fields are inconsistent")
    output_reason = bool(
        outcome.termination_reason
        and outcome.termination_reason.startswith("output_limit:")
    )
    if (
        outcome.output_limit_exceeded and not output_reason and not control_failure
    ) or (not outcome.output_limit_exceeded and output_reason):
        raise RuntimeError("proof runner output-limit fields are inconsistent")
    stdout_name = f"{harness.id}.stdout.txt"
    stderr_name = f"{harness.id}.stderr.txt"
    stdout_path = staged_raw_dir / stdout_name
    stderr_path = staged_raw_dir / stderr_name
    _write_new_file(stdout_path, outcome.stdout)
    _write_new_file(stderr_path, outcome.stderr)
    stdout_digest = proof_provenance.sha256_bytes(outcome.stdout)
    stderr_digest = proof_provenance.sha256_bytes(outcome.stderr)
    skip_marker_observed = any(
        line.lstrip().startswith(b"[SKIP]")
        for payload in (outcome.stdout, outcome.stderr)
        for line in payload.splitlines()
    )
    machine_failure = (
        outcome.timed_out
        or outcome.output_limit_exceeded
        or outcome.termination_reason is not None
    )
    if not machine_failure and outcome.exit_code == 0 and not skip_marker_observed:
        status = "passed"
    elif not machine_failure and outcome.exit_code == 2 and skip_marker_observed:
        status = "skipped"
    else:
        status = "failed"
    digest_payload = {
        "exit_code": outcome.exit_code,
        "status": status,
        "timed_out": outcome.timed_out,
        "output_limit_exceeded": outcome.output_limit_exceeded,
        "termination_reason": outcome.termination_reason,
        "stdout_sha256": stdout_digest,
        "stderr_sha256": stderr_digest,
    }
    return {
        "id": harness.id,
        "title": harness.title,
        "command": proof_provenance.command_record(logical_argv),
        "execution_command": proof_provenance.command_record(execution_argv),
        "started_at": outcome.started_at,
        "started_at_utc": proof_provenance.utc_timestamp(outcome.started_at),
        "completed_at": outcome.completed_at,
        "completed_at_utc": proof_provenance.utc_timestamp(outcome.completed_at),
        "duration_seconds": max(0.0, outcome.completed_at - outcome.started_at),
        "exit_code": outcome.exit_code,
        "status": status,
        "skip_marker_observed": skip_marker_observed,
        "timed_out": outcome.timed_out,
        "output_limit_exceeded": outcome.output_limit_exceeded,
        "termination_reason": outcome.termination_reason,
        "execution_limits": {
            "timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
            "stdout_max_bytes": PROOF_RAW_STREAM_MAX_BYTES,
            "stderr_max_bytes": PROOF_RAW_STREAM_MAX_BYTES,
        },
        "stdout": {
            "path": f"proof/results/raw/{stdout_name}",
            "bytes": len(outcome.stdout),
            "sha256": stdout_digest,
        },
        "stderr": {
            "path": f"proof/results/raw/{stderr_name}",
            "bytes": len(outcome.stderr),
            "sha256": stderr_digest,
        },
        "raw_output_sha256": proof_provenance.sha256_bytes(
            proof_provenance.canonical_json_bytes(digest_payload)
        ),
        "raw_output_digest_scope": (
            "status, machine termination fields, exit code, and exact retained bounded "
            "stdout/stderr byte digests"
        ),
        "prerequisites": list(harness.prerequisites),
        "limitations": list(harness.limitations),
    }


def _markdown_inline(value: object) -> str:
    text = "".join(
        character
        if character >= " " and character not in {"\x7f", "\r", "\n"}
        else f"\\x{ord(character):02x}"
        for character in str(value)
    )
    run = maximum = 0
    for character in text:
        if character == "`":
            run += 1
            maximum = max(maximum, run)
        else:
            run = 0
    fence = "`" * max(1, maximum + 1)
    padding = " " if text.startswith(("`", " ")) or text.endswith(("`", " ")) else ""
    return f"{fence}{padding}{text}{padding}{fence}"


def _markdown_fenced_text(text: str) -> list[str]:
    run = maximum = 0
    for character in text:
        if character == "`":
            run += 1
            maximum = max(maximum, run)
        else:
            run = 0
    fence = "`" * max(3, maximum + 1)
    return [f"{fence}text", text, fence]


def render_results_markdown(
    manifest: Mapping[str, object],
    raw_outputs: Mapping[str, Mapping[str, bytes]],
    *,
    manifest_path: str,
    manifest_sha256: str,
) -> str:
    """Purely render RESULTS.md from one manifest and its exact retained bytes."""
    source = manifest["source"]
    runtime = manifest["runtime"]
    execution_environment = manifest["execution_environment"]
    execution_limits = manifest.get("execution_limits", {})
    runs = manifest["runs"]
    assert isinstance(source, dict)
    assert isinstance(runtime, dict)
    assert isinstance(execution_environment, dict)
    assert isinstance(execution_limits, dict)
    assert isinstance(runs, list)
    source_content = source.get("source_content")
    if not isinstance(source_content, dict):
        source_content = {}
    run_ids = [str(run["id"]) for run in runs]
    if len(run_ids) != len(set(run_ids)) or set(raw_outputs) != set(run_ids):
        raise ValueError("raw output inventory does not exactly match proof runs")
    status_counts = {
        status: sum(1 for run in runs if run.get("status") == status)
        for status in ("passed", "failed", "skipped")
    }
    if sum(status_counts.values()) != len(runs):
        raise ValueError("proof run status inventory is malformed")
    state = "dirty" if source.get("dirty") else "clean"
    source_stable = manifest.get("source_stable") is True
    if manifest.get("evidence_valid") is True:
        evidence_status = "source-content-bound local fixture execution"
    elif not source_stable:
        evidence_status = "INVALID mixed-source execution; source changed while harnesses ran"
    else:
        evidence_status = "INVALID non-canonical or injected generator execution"
    git_runtime = source.get("source_content_git_diagnostics", {}).get("runtime", {})
    observed_command = manifest.get("generator_command")
    observed_display = (
        observed_command.get("display", "unavailable")
        if isinstance(observed_command, dict)
        else "unavailable"
    )
    lines = [
        "# Lightwork — Source-Content-Bound Local Fixture Results",
        "",
        f"> **Evidence status: {evidence_status}.** These results are "
        "bound to the stable Git-clean source-content identity, commands, timestamps, "
        "runtime/dependency inventory, and retained stdout/stderr digests below. The "
        "execution-base revision and worktree digest are retained as diagnostics.",
        ">",
        "> **Not production-release evidence.** This self-authored local run does not qualify "
        "a release, deployment, live identity/KMS/database/SIEM/provider integration, "
        "competitive capability, customer ROI, compliance, or penetration-test coverage.",
        "> Fixture, oracle, trusted-host, and stubbed seams remain disclosed per harness.",
        "",
        "## Bound execution provenance",
        "",
        f"- Assessed source revision: {_markdown_inline(source.get('revision') or 'unavailable')}.",
        f"- Stable source content: {_markdown_inline(source_content.get('schema') or 'unavailable')}; "
        f"SHA-256 {_markdown_inline(source_content.get('sha256') or 'unavailable')} across "
        f"{source_content.get('path_count', 'unavailable')} paths.",
        f"- Worktree state: {_markdown_inline(state)}; SHA-256 "
        f"{_markdown_inline(source.get('worktree_state_sha256') or 'unavailable')}.",
        f"- Source stability: {_markdown_inline('stable' if source_stable else 'changed during execution')}; "
        "the start and completion source records must match before this artifact is usable.",
        f"- Evidence validity: {_markdown_inline('valid' if manifest.get('evidence_valid') else 'invalid')}; "
        "only the canonical isolated CLI with the built-in runner and live timestamp is valid.",
        f"- Generation timestamp: {_markdown_inline(manifest['generated_at_utc'])}.",
        "- Required canonical generator command: "
        f"{_markdown_inline(manifest['canonical_generator_command']['display'])}.",
        f"- Observed generator command: {_markdown_inline(observed_display)}.",
        f"- Python: {_markdown_inline(runtime.get('python_executable'))} "
        f"({_markdown_inline(runtime.get('python_version'))}, "
        f"{_markdown_inline(runtime.get('python_implementation'))}).",
        f"- Platform: {_markdown_inline(runtime.get('platform'))} / "
        f"{_markdown_inline(runtime.get('machine'))}.",
        f"- Dependencies: {runtime.get('dependency_count')} visible distributions; SHA-256 "
        f"{_markdown_inline(runtime.get('dependencies_sha256'))}. Exact versions are retained in "
        "`proof/results/manifest.json`.",
        f"- Fixture child environment: policy "
        f"{_markdown_inline(execution_environment['policy']['id'])}; policy SHA-256 "
        f"{_markdown_inline(execution_environment['policy_sha256'])}; exact admitted-value SHA-256 "
        f"{_markdown_inline(execution_environment['environment_sha256'])} across "
        f"{len(execution_environment['admitted_names'])} admitted names. Discarded "
        "credential/configuration values are not serialized.",
        "- Interpreter boundary: top-level harnesses use isolated no-site startup and "
        "directly admitted runtime package directories without processing `.pth` files. "
        "Nested harness subprocesses remain a recorded trusted-host limitation.",
        "- Execution bounds: each harness is limited to "
        f"{_markdown_inline(execution_limits.get('harness_timeout_seconds', 'unavailable'))} "
        "seconds and each raw stream to "
        f"{_markdown_inline(execution_limits.get('raw_stream_max_bytes', 'unavailable'))} "
        "bytes; aggregate retained raw output is limited to "
        f"{_markdown_inline(execution_limits.get('raw_output_total_max_bytes', 'unavailable'))} "
        "bytes.",
        "- Git source collector: trusted-host boundary; executable SHA-256 "
        f"{_markdown_inline(git_runtime.get('sha256', 'unavailable'))}; the binary is resolved "
        "once and retained by absolute path, but its hash-to-execution binding is not atomic.",
        f"- Evidence manifest: {_markdown_inline(manifest_path)}; SHA-256 "
        f"{_markdown_inline(manifest_sha256)}.",
        f"- Result: {status_counts['passed']}/{len(runs)} harness commands passed; "
        f"{status_counts['failed']} failed and {status_counts['skipped']} skipped. "
        "Only exit 0 with no skip marker passes; exit 2 with an expected `[SKIP]` "
        "line is skipped; every other combination fails.",
        "",
    ]
    for number, run in enumerate(runs, 1):
        retained = raw_outputs[str(run["id"])]
        if set(retained) != {"stdout", "stderr"} or not all(
            isinstance(retained[name], bytes) for name in ("stdout", "stderr")
        ):
            raise ValueError(f"raw output streams are malformed: {run['id']}")
        stdout_text = (
            retained["stdout"]
            .decode("utf-8", errors="replace")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .rstrip("\n")
        )
        stderr_text = (
            retained["stderr"]
            .decode("utf-8", errors="replace")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .rstrip("\n")
        )
        lines.extend(
            [
                f"## {number}. {_markdown_inline(run['title'])}",
                "",
                f"- Exact command: {_markdown_inline(run['command']['display'])}.",
                f"- Executed: {_markdown_inline(run['started_at_utc'])} to "
                f"{_markdown_inline(run['completed_at_utc'])}; exit code "
                f"{_markdown_inline(run['exit_code'])}; status {_markdown_inline(run['status'])}.",
                "- Machine termination: timeout "
                f"{_markdown_inline(run.get('timed_out', False))}; output limit exceeded "
                f"{_markdown_inline(run.get('output_limit_exceeded', False))}; reason "
                f"{_markdown_inline(run.get('termination_reason') or 'none')}.",
                f"- Raw stdout: {_markdown_inline(run['stdout']['path'])} — SHA-256 "
                f"{_markdown_inline(run['stdout']['sha256'])} ({run['stdout']['bytes']} bytes).",
                f"- Raw stderr: {_markdown_inline(run['stderr']['path'])} — SHA-256 "
                f"{_markdown_inline(run['stderr']['sha256'])} ({run['stderr']['bytes']} bytes).",
                f"- Combined raw-output digest: {_markdown_inline(run['raw_output_sha256'])}.",
                "- Prerequisites:",
                *(f"  - {_markdown_inline(item)}" for item in run["prerequisites"]),
                "- Limitations:",
                *(f"  - {_markdown_inline(item)}" for item in run["limitations"]),
                "",
                "Normalized UTF-8/LF display of stdout:",
                "",
                *_markdown_fenced_text(stdout_text),
                "",
            ]
        )
        if retained["stderr"]:
            lines.extend(
                [
                    "Normalized UTF-8/LF display of stderr:",
                    "",
                    *_markdown_fenced_text(stderr_text),
                    "",
                ]
            )
    lines.extend(
        [
            "## What this execution does not claim",
            "",
            "- No competitive capability benchmark or external-task generalization.",
            "- No useful self-improvement or customer ROI result.",
            "- No production deployment, security, compliance, or release qualification.",
            "- A fixture PASS applies only to the named bounded checks; a SKIP or missing "
            "prerequisite is not a PASS.",
            "",
        ]
    )
    return "\n".join(lines)


def _generation_record(
    manifest_payload: bytes,
    results_payload: bytes,
    raw_outputs: Mapping[str, Mapping[str, bytes]],
) -> dict[str, object]:
    retained: list[dict[str, object]] = []
    raw_total = 0
    for harness in HARNESSES:
        streams = raw_outputs[harness.id]
        for stream in ("stdout", "stderr"):
            payload = streams[stream]
            if len(payload) > PROOF_RAW_STREAM_MAX_BYTES:
                raise RuntimeError("retained raw proof stream exceeds its hard byte limit")
            raw_total += len(payload)
            retained.append(
                {
                    "path": f"proof/results/raw/{harness.id}.{stream}.txt",
                    "bytes": len(payload),
                    "sha256": proof_provenance.sha256_bytes(payload),
                }
            )
    if raw_total > PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES:
        raise RuntimeError("retained raw proof outputs exceed their aggregate byte limit")
    if len(manifest_payload) > PROOF_MANIFEST_MAX_BYTES:
        raise RuntimeError("proof manifest exceeds its hard byte limit")
    if len(results_payload) > PROOF_RESULTS_MAX_BYTES:
        raise RuntimeError("proof RESULTS exceeds its hard byte limit")
    return {
        "schema": _GENERATION_SCHEMA,
        "manifest": {
            "path": "proof/results/manifest.json",
            "bytes": len(manifest_payload),
            "sha256": proof_provenance.sha256_bytes(manifest_payload),
        },
        "results": {
            "path": "proof/RESULTS.md",
            "bytes": len(results_payload),
            "sha256": proof_provenance.sha256_bytes(results_payload),
        },
        "raw_outputs": retained,
        "limits": {
            "harness_timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
            "raw_stream_max_bytes": PROOF_RAW_STREAM_MAX_BYTES,
            "raw_output_total_max_bytes": PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES,
            "results_max_bytes": PROOF_RESULTS_MAX_BYTES,
            "manifest_max_bytes": PROOF_MANIFEST_MAX_BYTES,
            "generation_max_bytes": PROOF_GENERATION_MAX_BYTES,
            "artifact_tree_max_bytes": PROOF_ARTIFACT_TREE_MAX_BYTES,
        },
        "observed": {
            "raw_output_total_bytes": raw_total,
            "manifest_bytes": len(manifest_payload),
            "results_bytes": len(results_payload),
        },
        "publication": (
            "staged complete artifact set; raw streams, manifest, and RESULTS publish first; "
            "this generation record publishes last and is the fail-closed commit marker"
        ),
    }


def _artifact_file_paths() -> set[str]:
    return {
        "proof/RESULTS.md",
        "proof/results/manifest.json",
        _GENERATION_FILE,
        *(
            f"proof/results/raw/{harness.id}.{stream}.txt"
            for harness in HARNESSES
            for stream in ("stdout", "stderr")
        ),
    }


def _validate_staged_artifact_tree(staged_root: Path) -> None:
    expected_files = _artifact_file_paths()
    expected_directories = {"proof", "proof/results", "proof/results/raw"}
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    observed_bytes = 0
    observed_raw_bytes = 0
    observed_entries = 0
    maximum_entries = len(expected_files) + len(expected_directories)

    def visit(directory: Path, prefix: str) -> None:
        nonlocal observed_bytes, observed_entries, observed_raw_bytes
        with os.scandir(directory) as entries:
            for entry in entries:
                observed_entries += 1
                if observed_entries > maximum_entries:
                    raise RuntimeError(
                        "staged proof artifact tree exceeds its hard entry ceiling"
                    )
                path = directory / entry.name
                relative = f"{prefix}/{entry.name}" if prefix else entry.name
                metadata = path.lstat()
                if path.is_symlink() or _is_reparse_point(path):
                    raise RuntimeError(
                        f"staged proof artifact contains a link: {relative}"
                    )
                if stat.S_ISDIR(metadata.st_mode):
                    if relative not in expected_directories:
                        raise RuntimeError(
                            f"staged proof artifact contains an unexpected directory: {relative}"
                        )
                    observed_directories.add(relative)
                    visit(path, relative)
                elif stat.S_ISREG(metadata.st_mode):
                    if relative not in expected_files:
                        raise RuntimeError(
                            f"staged proof artifact contains an unexpected file: {relative}"
                        )
                    if metadata.st_nlink != 1:
                        raise RuntimeError(
                            f"staged proof artifact is not single-link: {relative}"
                        )
                    observed_files.add(relative)
                    size = metadata.st_size
                    if relative.startswith("proof/results/raw/"):
                        maximum = PROOF_RAW_STREAM_MAX_BYTES
                        observed_raw_bytes += size
                    elif relative == "proof/RESULTS.md":
                        maximum = PROOF_RESULTS_MAX_BYTES
                    elif relative == "proof/results/manifest.json":
                        maximum = PROOF_MANIFEST_MAX_BYTES
                    else:
                        maximum = PROOF_GENERATION_MAX_BYTES
                    if size > maximum:
                        raise RuntimeError(
                            f"staged proof artifact exceeds its hard byte limit: {relative}"
                        )
                    observed_bytes += size
                else:
                    raise RuntimeError(
                        f"staged proof artifact has unsupported type: {relative}"
                    )

    visit(staged_root, "")
    if observed_files != expected_files or observed_directories != expected_directories:
        raise RuntimeError("staged proof artifact tree is not exact")
    if observed_bytes > PROOF_ARTIFACT_TREE_MAX_BYTES:
        raise RuntimeError("staged proof artifact tree exceeds its aggregate byte limit")
    if observed_raw_bytes > PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES:
        raise RuntimeError("staged raw proof outputs exceed their aggregate byte limit")


def _validate_existing_artifact_namespace(
    root: Path,
    output: Path,
    evidence_dir: Path,
) -> None:
    if output.exists() or output.is_symlink():
        _regular_file_record(
            output,
            relative="proof/RESULTS.md",
            maximum_bytes=PROOF_RESULTS_MAX_BYTES,
        )
    if not evidence_dir.exists() and not evidence_dir.is_symlink():
        return
    _directory_identity(evidence_dir)
    allowed_top = {"manifest.json", "generation.json", "raw"}
    top: set[str] = set()
    top_count = 0
    with os.scandir(evidence_dir) as entries:
        for entry in entries:
            if entry.name not in allowed_top:
                raise RuntimeError(
                    "proof results contain an unexpected stale artifact: "
                    f"{entry.name}"
                )
            top_count += 1
            if top_count > len(allowed_top):
                raise RuntimeError("proof results exceed the hard entry ceiling")
            top.add(entry.name)
    for name in top - {"raw"}:
        maximum = (
            PROOF_MANIFEST_MAX_BYTES
            if name == "manifest.json"
            else PROOF_GENERATION_MAX_BYTES
        )
        _regular_file_record(
            evidence_dir / name,
            relative=f"proof/results/{name}",
            maximum_bytes=maximum,
        )
    raw_dir = evidence_dir / "raw"
    if not raw_dir.exists() and not raw_dir.is_symlink():
        return
    _directory_identity(raw_dir)
    expected_raw = {
        f"{harness.id}.{stream}.txt"
        for harness in HARNESSES
        for stream in ("stdout", "stderr")
    }
    observed_raw: set[str] = set()
    raw_count = 0
    with os.scandir(raw_dir) as entries:
        for entry in entries:
            if entry.name not in expected_raw:
                raise RuntimeError(
                    "proof raw results contain an unexpected stale artifact: "
                    f"{entry.name}"
                )
            raw_count += 1
            if raw_count > len(expected_raw):
                raise RuntimeError("proof raw results exceed the hard entry ceiling")
            observed_raw.add(entry.name)
    for name in observed_raw:
        _regular_file_record(
            raw_dir / name,
            relative=f"proof/results/raw/{name}",
            maximum_bytes=PROOF_RAW_STREAM_MAX_BYTES,
        )


def _publish_staged_artifacts(
    root: Path,
    staged_root: Path,
    output: Path,
    evidence_dir: Path,
) -> None:
    _validate_staged_artifact_tree(staged_root)
    _validate_existing_artifact_namespace(root, output, evidence_dir)
    raw_dir = evidence_dir / "raw"
    _ensure_regular_directory(root, evidence_dir)
    _ensure_regular_directory(root, raw_dir)
    staged_proof = staged_root / "proof"
    for harness in HARNESSES:
        for stream in ("stdout", "stderr"):
            name = f"{harness.id}.{stream}.txt"
            _atomic_write_bytes(
                root,
                raw_dir / name,
                (staged_proof / "results" / "raw" / name).read_bytes(),
            )
    _atomic_write_bytes(
        root,
        evidence_dir / "manifest.json",
        (staged_proof / "results" / "manifest.json").read_bytes(),
    )
    _atomic_write_bytes(root, output, (staged_proof / "RESULTS.md").read_bytes())
    # Publish the commit marker last.  Any interruption before this replace leaves
    # either no marker or an older marker that cannot match the mixed live files.
    _atomic_write_bytes(
        root,
        root / _GENERATION_FILE,
        (staged_proof / "results" / "generation.json").read_bytes(),
    )


def generate(
    *,
    root: Path = _ROOT,
    output: Path | None = None,
    evidence_dir: Path | None = None,
    generator_flags: Sequence[str] = (),
    runner: Callable[..., CommandOutcome] = _run,
    generated_at: float | None = None,
    _cli_argv: Sequence[str] | None = None,
) -> dict[str, object]:
    root = root.resolve()
    output, evidence_dir = _prepare_fixed_artifact_paths(root, output, evidence_dir)
    canonical_cli_verified = _cli_argv is not None and _canonical_cli_invocation(_cli_argv)
    if canonical_cli_verified:
        _validate_local_provenance_module(root)
        _validate_canonical_worktree(root)
    for relative in _REQUIRED_BOUND_SOURCE_PATHS:
        _require_regular_source(root, relative)
    exclusions = _source_exclusions(root)
    runtime_import_admissions = _runtime_import_path_records()
    runtime_import_paths = [item["path"] for item in runtime_import_admissions]
    runtime_executable = _runtime_executable_record(root)
    original_sys_path = list(sys.path)
    try:
        sys.path[:0] = [
            path for path in runtime_import_paths if path not in sys.path
        ]
        runtime = proof_provenance.runtime_provenance()
    finally:
        sys.path[:] = original_sys_path
    timestamp = float(generated_at if generated_at is not None else time.time())
    exact_generator = _canonical_generator_command(generator_flags)
    observed_generator = _observed_generator_command() if canonical_cli_verified else None
    records: list[dict[str, object]] = []
    raw_outputs: dict[str, dict[str, bytes]] = {}
    with tempfile.TemporaryDirectory(prefix="lightwork-proof-results-") as temporary:
        temporary_root = Path(temporary)
        materialized_root = temporary_root / "source"
        staged_root = temporary_root / "staged-artifacts"
        staged_raw_dir = staged_root / "proof" / "results" / "raw"
        staged_raw_dir.mkdir(parents=True)
        runtime_imports = _write_runtime_import_control(
            temporary_root,
            runtime_import_paths,
            admissions=runtime_import_admissions,
        )
        materialized_identity = proof_provenance.materialize_repository_source(
            root,
            materialized_root,
            exclude_paths=exclusions,
            required_regular_paths=_REQUIRED_BOUND_SOURCE_PATHS,
            symlink_materialization_policy=(
                proof_provenance.SYMLINK_MATERIALIZATION_POLICY
            ),
        )
        semantic_execution_identity = (
            proof_provenance.semantic_execution_tree_identity(
                materialized_identity,
                symlink_materialization_policy=(
                    proof_provenance.SYMLINK_MATERIALIZATION_POLICY
                ),
            )
        )
        proof_tree_records = _materialized_proof_tree_records(materialized_root)
        execution_tree_identity = proof_provenance.materialized_tree_identity(
            materialized_root
        )
        _make_materialized_tree_read_only(materialized_root)
        if (
            proof_provenance.materialized_tree_identity(materialized_root)
            != execution_tree_identity
            or _materialized_proof_tree_records(materialized_root)
            != proof_tree_records
        ):
            raise RuntimeError("making the materialized source read-only changed its identity")
        source = proof_provenance.repository_provenance(
            root,
            exclude_paths=exclusions,
        )
        if (
            source.get("available") is not True
            or proof_provenance.source_content_identity(source) != materialized_identity
        ):
            raise RuntimeError("source changed while the isolated proof tree was materialized")
        execution_env, environment_overrides, execution_environment = _execution_environment(
            materialized_root,
            temporary_root / "runtime-home",
        )
        raw_output_total_bytes = 0
        for harness in HARNESSES:
            _validate_runtime_import_control(temporary_root, runtime_imports)
            if (
                proof_provenance.materialized_tree_identity(materialized_root)
                != execution_tree_identity
                or _materialized_proof_tree_records(materialized_root)
                != proof_tree_records
            ):
                raise RuntimeError("materialized bound source changed before harness execution")
            logical_argv = [
                *_HARNESS_INTERPRETER_PREFIX,
                ISOLATED_BOOTSTRAP,
                harness.script,
            ]
            execution_argv = [str(runtime_executable["path"]), *logical_argv[1:]]
            outcome = runner(execution_argv, cwd=materialized_root, env=execution_env)
            _validate_runtime_import_control(temporary_root, runtime_imports)
            if (
                proof_provenance.materialized_tree_identity(materialized_root)
                != execution_tree_identity
                or _materialized_proof_tree_records(materialized_root)
                != proof_tree_records
            ):
                raise RuntimeError("materialized bound source changed during harness execution")
            if (
                len(outcome.stdout) > PROOF_RAW_STREAM_MAX_BYTES
                or len(outcome.stderr) > PROOF_RAW_STREAM_MAX_BYTES
            ):
                raise RuntimeError("proof runner returned an over-limit raw output stream")
            raw_output_total_bytes += len(outcome.stdout) + len(outcome.stderr)
            if raw_output_total_bytes > PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES:
                raise RuntimeError("proof runner exceeded the aggregate raw output limit")
            raw_outputs[harness.id] = {
                "stdout": outcome.stdout,
                "stderr": outcome.stderr,
            }
            records.append(
                _raw_record(
                    staged_raw_dir,
                    harness,
                    logical_argv,
                    execution_argv,
                    outcome,
                )
            )
        _validate_runtime_import_control(temporary_root, runtime_imports)
        source_at_completion = proof_provenance.repository_provenance(
            root,
            exclude_paths=exclusions,
        )
        source_stable = source_at_completion == source
        canonical_execution = (
            runner is _run
            and generated_at is None
            and canonical_cli_verified
            and runtime_executable["outside_repository"] is True
        )
        evidence_valid = (
            canonical_execution
            and source.get("available") is True
            and source_stable
        )

        manifest: dict[str, object] = {
            "kind": "lightwork-source-content-bound-fixture-results",
            "version": 2,
            "generated_at": timestamp,
            "generated_at_utc": proof_provenance.utc_timestamp(timestamp),
            "canonical_generator_command": proof_provenance.command_record(exact_generator),
            "generator_command": observed_generator,
            "execution_mode": {
                "id": (
                    "canonical-isolated-cli-v2"
                    if canonical_execution
                    else "injected-or-noncanonical-test-v2"
                ),
                "canonical_cli_verified": canonical_execution,
                "runner_injected": runner is not _run,
                "timestamp_injected": generated_at is not None,
            },
            "evidence_valid": evidence_valid,
            "evidence_valid_scope": (
                "internally consistent source-content-bound local fixture execution only"
            ),
            "production_release_evidence": False,
            "execution_limits": {
                "harness_timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
                "raw_stream_max_bytes": PROOF_RAW_STREAM_MAX_BYTES,
                "raw_output_total_max_bytes": PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES,
                "results_max_bytes": PROOF_RESULTS_MAX_BYTES,
                "manifest_max_bytes": PROOF_MANIFEST_MAX_BYTES,
                "generation_max_bytes": PROOF_GENERATION_MAX_BYTES,
                "artifact_tree_max_bytes": PROOF_ARTIFACT_TREE_MAX_BYTES,
                "source_proof_file_max_bytes": PROOF_SOURCE_FILE_MAX_BYTES,
                "source_proof_tree_max_bytes": PROOF_SOURCE_TREE_MAX_BYTES,
                "process_tree_termination": (
                    "Linux subreaper PID/start-time lifecycle seal; Windows suspended-spawn "
                    "kill-on-close Job Object lifecycle seal"
                ),
                "raw_output_total_bytes": raw_output_total_bytes,
            },
            "environment_overrides": environment_overrides,
            "execution_environment": execution_environment,
            "interpreter_boundary": {
                "entrypoint_argv_prefix": list(_HARNESS_INTERPRETER_PREFIX),
                "runtime_executable": runtime_executable,
                "entrypoint_site_initialization": "disabled with -S",
                "runtime_imports": runtime_imports,
                "runtime_paths_added_without_pth_processing": True,
                "isolated_entrypoint_ignored_environment_names": list(
                    _ISOLATED_IGNORED_PYTHON_ENV
                ),
                "descendant_subprocess_isolation_complete": False,
                "descendant_subprocess_boundary": (
                    "trusted-host seam; lifecycle sealing covers descendants, but same-UID "
                    "hostile code can attack the verifier, and harness-created Python/pytest/"
                    "solver subprocesses are not all routed through the isolated bootstrap"
                ),
                "runtime_binary_hash_to_execution_atomic": False,
                "runtime_binary_boundary": (
                    "trusted-host executable; exact bytes are hashed before execution, but the "
                    "operating system path lookup/open is not handle-pinned across every launch"
                ),
            },
            "source_collection_boundary": {
                "git_binary_trust": "trusted-host",
                "git_binary": source.get("source_content_git_diagnostics", {}).get("runtime"),
                "git_binary_hash_to_execution_atomic": False,
                "worktree_control": "local regular .git directory; indirection rejected",
                "global_and_system_git_configuration": "suppressed",
            },
            "source_execution_tree": {
                "local_identity_schema": (
                    proof_provenance.MATERIALIZED_TREE_IDENTITY_SCHEMA
                ),
                "symlink_materialization_policy": (
                    proof_provenance.SYMLINK_MATERIALIZATION_POLICY
                ),
                "semantic_identity": semantic_execution_identity,
                "local_materialized_tree_identity_enforced": True,
                "local_materialized_tree_identity_serialized": False,
                "read_only_transition_identity_preserved": True,
                "required_regular_paths": list(_REQUIRED_BOUND_SOURCE_PATHS),
                "required_regular_paths_sha256": proof_provenance.sha256_bytes(
                    proof_provenance.canonical_json_bytes(
                        list(_REQUIRED_BOUND_SOURCE_PATHS)
                    )
                ),
                "exact_proof_paths": sorted(
                    f"proof/{name}" for name in _MATERIALIZED_PROOF_FILES
                ),
                "exact_proof_paths_sha256": proof_provenance.sha256_bytes(
                    proof_provenance.canonical_json_bytes(
                        sorted(f"proof/{name}" for name in _MATERIALIZED_PROOF_FILES)
                    )
                ),
                "mutation_checks": "before and after every harness",
            },
            "source": source,
            "source_at_completion": source_at_completion,
            "source_stable": source_stable,
            "runtime": runtime,
            "runs": records,
            "all_commands_passed": all(record["status"] == "passed" for record in records),
            "evidence_boundary": [
                "source-content-bound local regression and prerequisite evidence only",
                "fixture/stub/oracle/trusted-host limitations remain authoritative",
                "not competitive capability, customer ROI, production, or release evidence",
                "fixture children inherit only the recorded system allow-list; ambient app, "
                "provider, Python-path, and pytest injection variables are discarded",
                "isolated no-site startup covers each top-level harness only; nested host "
                "subprocesses remain an explicit trusted-host limitation",
            ],
        }
        manifest_payload = (
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        manifest_sha256 = proof_provenance.sha256_bytes(manifest_payload)
        results_payload = render_results_markdown(
            manifest,
            raw_outputs,
            manifest_path="proof/results/manifest.json",
            manifest_sha256=manifest_sha256,
        ).encode("utf-8")
        generation = _generation_record(manifest_payload, results_payload, raw_outputs)
        generation_payload = (
            json.dumps(
                generation,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        if len(generation_payload) > PROOF_GENERATION_MAX_BYTES:
            raise RuntimeError("proof generation marker exceeds its hard byte limit")
        staged_tree_bytes = (
            len(manifest_payload)
            + len(results_payload)
            + len(generation_payload)
            + raw_output_total_bytes
        )
        if staged_tree_bytes > PROOF_ARTIFACT_TREE_MAX_BYTES:
            raise RuntimeError("proof artifact tree exceeds its aggregate byte limit")
        _write_new_file(staged_root / "proof" / "results" / "manifest.json", manifest_payload)
        _write_new_file(staged_root / "proof" / "RESULTS.md", results_payload)
        _write_new_file(staged_root / _GENERATION_FILE, generation_payload)
        _validate_staged_artifact_tree(staged_root)
        source_before_publication = proof_provenance.repository_provenance(
            root,
            exclude_paths=exclusions,
        )
        if source_before_publication != source_at_completion:
            raise RuntimeError("source changed before staged proof artifacts could publish")
        _publish_staged_artifacts(root, staged_root, output, evidence_dir)
        manifest["manifest_path"] = "proof/results/manifest.json"
        manifest["manifest_sha256"] = manifest_sha256
        manifest["results_path"] = "proof/RESULTS.md"
        manifest["results_sha256"] = proof_provenance.sha256_bytes(results_payload)
        manifest["generation_path"] = _GENERATION_FILE
        manifest["generation_sha256"] = proof_provenance.sha256_bytes(generation_payload)
        return manifest


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_ROOT / "proof" / "RESULTS.md")
    parser.add_argument(
        "--evidence-dir", type=Path, default=_ROOT / "proof" / "results"
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help=(
            "exit 1 after writing evidence when evidence is invalid or any harness "
            "fails, skips, times out, or exceeds an output limit"
        ),
    )
    args = parser.parse_args(raw_argv)
    expected_argv = ["--require-pass"] if args.require_pass else []
    if raw_argv != expected_argv:
        parser.error(
            "canonical generation accepts only an optional trailing --require-pass"
        )
    canonical_cli = argv is None and _canonical_cli_invocation(raw_argv)
    if argv is None and not canonical_cli:
        parser.error(
            "invoke with: " + " ".join(_canonical_generator_command(expected_argv))
        )
    manifest = generate(
        output=args.output,
        evidence_dir=args.evidence_dir,
        generator_flags=("--require-pass",) if args.require_pass else (),
        _cli_argv=raw_argv if canonical_cli else None,
    )
    print(f"wrote {args.output}")
    print(f"evidence manifest SHA-256: {manifest['manifest_sha256']}")
    if args.require_pass and (
        not manifest["all_commands_passed"] or not manifest["evidence_valid"]
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
