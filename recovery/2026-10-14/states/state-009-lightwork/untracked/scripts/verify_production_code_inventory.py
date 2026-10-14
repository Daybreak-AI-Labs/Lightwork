#!/usr/bin/env python3
"""Generate and verify the release-bound inventory derived from source code.

The production repository contains many more surfaces than the first admitted
profile.  This gate inventories both the code that exists and the smaller
production subset, so a count in documentation cannot silently become release
authority.  The committed JSON is deterministic: it copies fixed values from
the retained proof manifest only after verifying that its portable source-content
identity still matches the current source; revision/worktree values remain
diagnostics, and the verifier never samples the current clock.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Any

try:  # Python 3.10 uses the compatibility dependency.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


SCHEMA = "lightwork.production-code-inventory.v1"
REPOSITORY = "Daybreak-AI-Labs/Lightwork"
DEFAULT_PATH = Path("release/production-code-inventory.json")
MAX_INVENTORY_BYTES = 4 * 1024 * 1024
MAX_SOURCE_FILE_BYTES = 32 * 1024 * 1024
MAX_TOML_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 32
PROOF_HARNESS_TIMEOUT_SECONDS = 900
MAX_PROOF_RAW_FILE_BYTES = 4 * 1024 * 1024
MAX_PROOF_RAW_TOTAL_BYTES = 24 * 1024 * 1024
MAX_PROOF_RESULTS_BYTES = 16 * 1024 * 1024
MAX_PROOF_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PROOF_GENERATION_BYTES = 4 * 1024 * 1024
MAX_PROOF_TREE_BYTES = 64 * 1024 * 1024
MAX_PROOF_SOURCE_FILE_BYTES = 4 * 1024 * 1024
MAX_PROOF_SOURCE_TREE_BYTES = 32 * 1024 * 1024
MAX_PROOF_HARNESS_IDS = 64
MAX_PROOF_RESULTS_ENTRIES = 3
MAX_PROOF_RAW_ENTRIES = MAX_PROOF_HARNESS_IDS * 2
MAX_RUNTIME_PROBE_STDOUT_BYTES = 4 * 1024 * 1024
MAX_RUNTIME_PROBE_STDERR_BYTES = 64 * 1024
SOURCE_TEXT_DIGEST_MODE = "utf8-lf-sha256"
CANONICAL_JSON_DIGEST_MODE = "canonical-json-sha256"
EXACT_BYTE_DIGEST_MODE = "exact-byte-sha256"
DIGEST_CONTRACT = {
    "source_text": SOURCE_TEXT_DIGEST_MODE,
    "source_aggregate": CANONICAL_JSON_DIGEST_MODE,
    "retained_proof_artifact": EXACT_BYTE_DIGEST_MODE,
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_PROOF_HARNESS_CONTRACT = (
    {
        "id": "platform-source-invariants",
        "title": "Platform source-invariant scoreboard",
        "script": "proof/run_proof.py",
        "prerequisites": (
            "Python 3.10-3.12 and the source checkout's core package",
        ),
        "limitations": (
            "Exercises named catalog and enforcement-code invariants with local fixtures.",
            "Does not call a live connector or qualify a deployment.",
        ),
    },
    {
        "id": "self-learning-fixture",
        "title": "Self-learning fixture checks",
        "script": "proof/self_harness_proof.py",
        "prerequisites": (
            "Python 3.10-3.12 and writable temporary storage",
        ),
        "limitations": (
            "Uses fixed seeded inputs and an injected scorer.",
            "Does not prove useful learning or robustness to real-world outcome manipulation.",
        ),
    },
    {
        "id": "dgm-code-rung-fixture",
        "title": "Governed DGM code-rung fixture",
        "script": "proof/dgm_code_rung_proof.py",
        "prerequisites": (
            "Python 3.10-3.12 and cryptography for Ed25519 checks.",
            "A configured non-host, no-egress, bounded evaluator that returns the "
            "controller-authenticated evidence contract; no bundled backend currently "
            "satisfies that production-relevant evaluator prerequisite.",
        ),
        "limitations": (
            "The patch proposer is stubbed with a supplied diff.",
            "A configured evaluator result would still be local regression evidence, not "
            "autonomous-improvement or production-safe-promotion proof.",
        ),
    },
    {
        "id": "swebench-shaped-fixture",
        "title": "SWE-bench-shaped governance fixture",
        "script": "proof/swebench_governed_proof.py",
        "prerequisites": (
            "Python 3.10-3.12 and cryptography for local signatures",
        ),
        "limitations": (
            "Uses a self-seeded toy repository, an oracle patch, and trusted host execution.",
            "Does not run SWE-bench Verified or produce an agent capability score.",
        ),
    },
    {
        "id": "dgm-uplift-fixture",
        "title": "Governed DGM-uplift fixture",
        "script": "proof/dgm_uplift_proof.py",
        "prerequisites": (
            "Python 3.10-3.12, cryptography, pytest, and writable temporary storage",
        ),
        "limitations": (
            "Uses a toy solver, self-seeded instances, oracle-authored patches, and host pytest.",
            "Does not establish autonomous improvement or external-task generalization.",
        ),
    },
    {
        "id": "adapter-rung-fixture",
        "title": "Governed adapter-rung fixture",
        "script": "proof/adapter_rung_proof.py",
        "prerequisites": (
            "Python 3.10-3.12 and cryptography for local signatures.",
            "The fixture's generated public key is temporarily installed through the "
            "process-level deployment trust registry for each promotion transaction.",
        ),
        "limitations": (
            "The trainer writes a deterministic LoRA-shaped artifact and the evaluator is a "
            "deterministic stub.",
            "The locally generated signer and trust root are co-located fixture material, not "
            "independent approval or production key custody.",
            "Does not establish training quality, model uplift, tenant-data safety, or serving "
            "behavior.",
        ),
    },
)
_PROOF_HARNESS_SCRIPTS = tuple(
    (str(item["id"]), str(item["script"])) for item in _PROOF_HARNESS_CONTRACT
)
_RUNTIME_PROBE_SYSTEM_ENV_ALLOWLIST = (
    "COMSPEC",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "WINDIR",
)
_RUNTIME_PROBE_POLICY = {
    "startup_argv_prefix": [
        "python",
        "-I",
        "-S",
        "-B",
        "-X",
        "pycache_prefix=<fresh-controlled-empty-external-directory>",
        "-X",
        "utf8",
        "-W",
        "default",
    ],
    "site_initialization": "disabled",
    "pth_processing": False,
    "bytecode_cache_reads": (
        "redirected to a fresh controlled empty directory outside source and dependency "
        "trees with -X pycache_prefix while -B prevents writes"
    ),
    "pycache_prefix_empty_before_and_after": True,
    "pycache_prefix_path_serialized": False,
    "materialized_source_access": "read-only full source tree",
    "materialized_source_mutation_check": "before and after runtime probe, including failure",
    "source_import_paths": [
        "packages/maverick-core",
        "packages/maverick-dashboard",
        "packages/maverick-mcp",
        "packages/maverick-channels",
    ],
    "dependency_import_paths": (
        "one private cache-free snapshot of captured reviewed dependency files; absolute "
        "host paths are not serialized"
    ),
    "venv_system_site_packages": (
        "honor pyvenv.cfg include-system-site-packages; admit base-prefix packages only "
        "when enabled"
    ),
    "parent_environment_allowlist": list(_RUNTIME_PROBE_SYSTEM_ENV_ALLOWLIST),
    "fixed_environment_names": ["MAVERICK_CONFIG", "MAVERICK_HOME", "TEMP", "TMP"],
    "trusted_host_boundaries": [
        "Python executable and explicitly admitted dependency directories",
        "admitted operating-system PATH and dynamic-loader search",
    ],
    "stdout_limit_bytes": MAX_RUNTIME_PROBE_STDOUT_BYTES,
    "stderr_limit_bytes": MAX_RUNTIME_PROBE_STDERR_BYTES,
}
_RUNTIME_PROBE_SOURCE_ORIGINS = {
    "dashboard_app": "packages/maverick-dashboard/maverick_dashboard/app.py",
    "dashboard_endpoint_policy": (
        "packages/maverick-dashboard/maverick_dashboard/endpoint_policy.py"
    ),
    "enterprise_connectors": (
        "packages/maverick-core/maverick/tools/enterprise_connectors.py"
    ),
    "mcp_server": "packages/maverick-mcp/maverick_mcp/server.py",
}
_TOOL_COUNT_CLAIM = re.compile(
    r"(?<![\w])(?P<count>\d+)\+?\s+"
    r"(?:(?:[`*_]*[A-Za-z][\w*-]*[`*_]*)\s+){0,3}tools?\b",
    flags=re.IGNORECASE,
)


class ProductionCodeInventoryError(RuntimeError):
    """The source-derived inventory is unavailable, malformed, or stale."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _check_depth(value: object, *, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ProductionCodeInventoryError("production code inventory is too deeply nested")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProductionCodeInventoryError("inventory object keys must be strings")
            _check_depth(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth=depth + 1)


def _decode_json(raw: bytes, *, label: str) -> object:
    if len(raw) > MAX_INVENTORY_BYTES:
        raise ProductionCodeInventoryError(f"{label} exceeds the bounded size")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProductionCodeInventoryError(f"{label} is not strict UTF-8 JSON") from exc
    _check_depth(value)
    return value


def _load_json(path: Path, *, label: str) -> object:
    raw = _bounded_read_path(path, maximum_bytes=MAX_INVENTORY_BYTES, label=label)
    return _decode_json(raw, label=label)


def _read_toml(path: Path, *, label: str) -> dict[str, Any]:
    try:
        raw = _bounded_read_path(path, maximum_bytes=MAX_TOML_BYTES, label=label)
        value = tomllib.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ProductionCodeInventoryError(f"cannot parse {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ProductionCodeInventoryError(f"{label} root must be a table")
    return value


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_exact_file(path: Path) -> str:
    return _sha256_bytes(
        _bounded_read_path(
            path,
            maximum_bytes=MAX_SOURCE_FILE_BYTES,
            label="exact-byte file",
        )
    )


def _sha256_source_text(path: Path) -> str:
    """Hash reviewed UTF-8 source in checkout-independent LF form."""

    try:
        text = _bounded_read_path(
            path,
            maximum_bytes=MAX_SOURCE_FILE_BYTES,
            label="source text",
        ).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionCodeInventoryError(
            f"cannot hash source as UTF-8 text: {path}"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return _sha256_bytes(normalized.encode("utf-8"))


def _read_utf8_source(path: Path, *, label: str) -> str:
    try:
        return _bounded_read_path(
            path,
            maximum_bytes=MAX_SOURCE_FILE_BYTES,
            label=label,
        ).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionCodeInventoryError(f"{label} is not strict UTF-8") from exc


def _canonical_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProductionCodeInventoryError("inventory value is not canonical JSON") from exc
    return encoded.encode("utf-8")


def _digest_rows(rows: Iterable[object]) -> str:
    return _sha256_bytes(_canonical_bytes(list(rows)))


def _repo_file(root: Path, relative: str | Path) -> Path:
    path, _metadata, _parents = _safe_entry_metadata(
        root,
        relative,
        directory=False,
        label="required source file",
    )
    return path


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(getattr(metadata, "st_nlink", 1)),
        int(getattr(metadata, "st_file_attributes", 0)),
    )


def _directory_identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(bool(marker and getattr(metadata, "st_file_attributes", 0) & marker)),
    )


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _safe_directory_chain(
    path: Path,
    *,
    label: str,
) -> list[tuple[Path, tuple[int, int, int, int]]]:
    absolute = _absolute_lexical(path)
    chain = [*reversed(absolute.parents), absolute]
    identities: list[tuple[Path, tuple[int, int, int, int]]] = []
    for directory in chain:
        try:
            metadata = directory.lstat()
        except OSError as exc:
            raise ProductionCodeInventoryError(f"{label} ancestor is unavailable") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
        ):
            raise ProductionCodeInventoryError(
                f"{label} path contains a symlink, reparse point, or non-directory ancestor"
            )
        identities.append((directory, _directory_identity(metadata)))
    return identities


def _relative_parts(relative: str | Path, *, label: str) -> tuple[str, ...]:
    raw = os.fspath(relative)
    if not raw or "\x00" in raw:
        raise ProductionCodeInventoryError(f"{label} path is non-canonical")
    value = Path(raw)
    if (
        value.is_absolute()
        or value.drive
        or any(part in {"", ".", ".."} for part in value.parts)
        or os.name == "nt"
        and any(":" in part for part in value.parts)
    ):
        raise ProductionCodeInventoryError(f"{label} path escapes its root")
    return value.parts


def _safe_entry_metadata(
    root: Path,
    relative: str | Path,
    *,
    directory: bool,
    label: str,
) -> tuple[
    Path,
    os.stat_result,
    list[tuple[Path, tuple[int, int, int, int]]],
]:
    root = _absolute_lexical(root)
    parts = _relative_parts(relative, label=label)
    parent_identities = _safe_directory_chain(root, label=label)
    current = root
    try:
        for part in parts[:-1]:
            current /= part
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
            ):
                raise ProductionCodeInventoryError(f"{label} has an unsafe ancestor")
            parent_identities.append((current, _directory_identity(metadata)))
        path = current / parts[-1]
        leaf = path.lstat()
    except FileNotFoundError as exc:
        raise ProductionCodeInventoryError(f"{label} is missing: {Path(relative).as_posix()}") from exc
    except OSError as exc:
        raise ProductionCodeInventoryError(f"{label} is unavailable") from exc
    if stat.S_ISLNK(leaf.st_mode) or _is_reparse_point(leaf):
        raise ProductionCodeInventoryError(f"{label} is a symlink or reparse point")
    if directory:
        if not stat.S_ISDIR(leaf.st_mode):
            raise ProductionCodeInventoryError(f"{label} is not a directory")
    elif not stat.S_ISREG(leaf.st_mode) or getattr(leaf, "st_nlink", 1) != 1:
        raise ProductionCodeInventoryError(
            f"{label} is not a single-link regular file"
        )
    return path, leaf, parent_identities


def _recheck_directory_identities(
    identities: Sequence[tuple[Path, tuple[int, int, int, int]]],
    *,
    label: str,
) -> None:
    try:
        for directory, expected in identities:
            metadata = directory.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
                or _directory_identity(metadata) != expected
            ):
                raise ProductionCodeInventoryError(
                    f"{label} ancestor changed during the operation"
                )
    except OSError as exc:
        raise ProductionCodeInventoryError(
            f"{label} ancestor changed during the operation"
        ) from exc


def _bounded_read_file(
    root: Path,
    relative: str | Path,
    *,
    maximum_bytes: int,
    label: str,
) -> bytes:
    if maximum_bytes < 0:
        raise ProductionCodeInventoryError(f"{label} has an invalid byte limit")
    path, before, parents = _safe_entry_metadata(
        root,
        relative,
        directory=False,
        label=label,
    )
    if before.st_size < 0 or before.st_size > maximum_bytes:
        raise ProductionCodeInventoryError(f"{label} exceeds its bounded size")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProductionCodeInventoryError(f"cannot safely open {label}") from exc
    try:
        try:
            opened = os.fstat(descriptor)
        except OSError as exc:
            raise ProductionCodeInventoryError(f"cannot inspect opened {label}") from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or getattr(opened, "st_nlink", 1) != 1
            or _is_reparse_point(opened)
            or _file_identity(opened) != _file_identity(before)
        ):
            raise ProductionCodeInventoryError(f"{label} changed before it was opened")
        chunks: list[bytes] = []
        observed = 0
        while True:
            try:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, maximum_bytes + 1 - observed),
                )
            except OSError as exc:
                raise ProductionCodeInventoryError(f"cannot safely read {label}") from exc
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum_bytes:
                raise ProductionCodeInventoryError(f"{label} exceeds its bounded size")
        try:
            after_open = os.fstat(descriptor)
        except OSError as exc:
            raise ProductionCodeInventoryError(
                f"cannot verify opened {label} after reading"
            ) from exc
        if _file_identity(after_open) != _file_identity(opened):
            raise ProductionCodeInventoryError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ProductionCodeInventoryError(f"{label} changed while it was read") from exc
    if _file_identity(after) != _file_identity(before) or observed != after.st_size:
        raise ProductionCodeInventoryError(f"{label} changed while it was read")
    _recheck_directory_identities(parents, label=label)
    return b"".join(chunks)


def _bounded_read_path(path: Path, *, maximum_bytes: int, label: str) -> bytes:
    absolute = _absolute_lexical(path)
    if absolute.name == "":
        raise ProductionCodeInventoryError(f"{label} path is not a file")
    return _bounded_read_file(
        absolute.parent,
        absolute.name,
        maximum_bytes=maximum_bytes,
        label=label,
    )


def _proof_lexical_path(root: Path, relative: str | Path) -> Path:
    parts = _relative_parts(relative, label="proof artifact")
    return _absolute_lexical(root).joinpath(*parts)


def _proof_entry_metadata(
    root: Path,
    relative: str | Path,
    *,
    directory: bool,
) -> tuple[Path, os.stat_result]:
    path, leaf, _parents = _safe_entry_metadata(
        root,
        relative,
        directory=directory,
        label="proof artifact",
    )
    return path, leaf


def _read_exact_proof_file(
    root: Path,
    relative: str | Path,
    *,
    maximum_bytes: int,
) -> bytes:
    return _bounded_read_file(
        root,
        relative,
        maximum_bytes=maximum_bytes,
        label="proof artifact",
    )


def _bounded_harness_ids(harness_ids: Iterable[str]) -> tuple[str, ...]:
    identifiers = tuple(islice(iter(harness_ids), MAX_PROOF_HARNESS_IDS + 1))
    if len(identifiers) > MAX_PROOF_HARNESS_IDS:
        raise ProductionCodeInventoryError("proof harness namespace exceeds its entry ceiling")
    if (
        not identifiers
        or len(set(identifiers)) != len(identifiers)
        or any(
            not isinstance(identity, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", identity) is None
            for identity in identifiers
        )
    ):
        raise ProductionCodeInventoryError("proof harness namespace is malformed")
    return identifiers


def _validate_exact_directory_namespace(
    root: Path,
    relative: str,
    *,
    expected: Mapping[str, str],
    maximum_entries: int,
    label: str,
) -> None:
    if len(expected) > maximum_entries:
        raise ProductionCodeInventoryError(f"{label} expected namespace exceeds its entry ceiling")
    directory, before, parents = _safe_entry_metadata(
        root,
        relative,
        directory=True,
        label=label,
    )
    missing = set(expected)
    count = 0
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > maximum_entries:
                    raise ProductionCodeInventoryError(
                        f"{label} namespace exceeds its entry ceiling"
                    )
                expected_kind = expected.get(entry.name)
                if expected_kind is None:
                    raise ProductionCodeInventoryError(
                        f"{label} contains missing or unexpected entries"
                    )
                try:
                    # Windows DirEntry.stat may report st_dev/st_ino/st_nlink as
                    # zero.  The bounded child path lstat carries link-count
                    # authority and still avoids following the entry.
                    metadata = (directory / entry.name).lstat()
                except OSError as exc:
                    raise ProductionCodeInventoryError(
                        f"{label} entry cannot be inspected"
                    ) from exc
                if entry.is_symlink() or _is_reparse_point(metadata):
                    raise ProductionCodeInventoryError(
                        f"{label} contains a symlink or reparse point"
                    )
                if expected_kind == "directory":
                    valid = stat.S_ISDIR(metadata.st_mode)
                else:
                    valid = stat.S_ISREG(metadata.st_mode) and getattr(
                        metadata,
                        "st_nlink",
                        1,
                    ) == 1
                if not valid:
                    suffix = (
                        "directory"
                        if expected_kind == "directory"
                        else "single-link regular file"
                    )
                    raise ProductionCodeInventoryError(
                        f"{label} entry is not a {suffix}: {entry.name}"
                    )
                missing.discard(entry.name)
    except OSError as exc:
        raise ProductionCodeInventoryError(f"{label} namespace cannot be enumerated") from exc
    if missing:
        raise ProductionCodeInventoryError(f"{label} contains missing or unexpected entries")
    try:
        after = directory.lstat()
    except OSError as exc:
        raise ProductionCodeInventoryError(f"{label} changed while it was enumerated") from exc
    if _directory_identity(after) != _directory_identity(before):
        raise ProductionCodeInventoryError(f"{label} changed while it was enumerated")
    _recheck_directory_identities(parents, label=label)


def _read_exact_proof_tree(
    root: Path,
    harness_ids: Iterable[str],
) -> dict[str, bytes]:
    identifiers = _bounded_harness_ids(harness_ids)
    expected_raw = tuple(
        f"{identity}.{stream}.txt"
        for identity in identifiers
        for stream in ("stdout", "stderr")
    )
    _validate_exact_directory_namespace(
        root,
        "proof/results",
        expected={
            "generation.json": "file",
            "manifest.json": "file",
            "raw": "directory",
        },
        maximum_entries=MAX_PROOF_RESULTS_ENTRIES,
        label="proof results tree",
    )
    _validate_exact_directory_namespace(
        root,
        "proof/results/raw",
        expected=dict.fromkeys(expected_raw, "file"),
        maximum_entries=MAX_PROOF_RAW_ENTRIES,
        label="proof raw-output tree",
    )
    payloads = {
        "proof/results/manifest.json": _read_exact_proof_file(
            root,
            "proof/results/manifest.json",
            maximum_bytes=MAX_PROOF_MANIFEST_BYTES,
        ),
        "proof/results/generation.json": _read_exact_proof_file(
            root,
            "proof/results/generation.json",
            maximum_bytes=MAX_PROOF_GENERATION_BYTES,
        ),
        "proof/RESULTS.md": _read_exact_proof_file(
            root,
            "proof/RESULTS.md",
            maximum_bytes=MAX_PROOF_RESULTS_BYTES,
        ),
    }
    raw_total = 0
    for name in expected_raw:
        relative = f"proof/results/raw/{name}"
        payload = _read_exact_proof_file(
            root,
            relative,
            maximum_bytes=MAX_PROOF_RAW_FILE_BYTES,
        )
        payloads[relative] = payload
        raw_total += len(payload)
        if raw_total > MAX_PROOF_RAW_TOTAL_BYTES:
            raise ProductionCodeInventoryError(
                "proof raw-output tree exceeds the bounded size"
            )
    if sum(map(len, payloads.values())) > MAX_PROOF_TREE_BYTES:
        raise ProductionCodeInventoryError("proof artifact tree exceeds the bounded size")
    return payloads


def _validate_exact_proof_tree(root: Path, harness_ids: Iterable[str]) -> None:
    _read_exact_proof_tree(root, harness_ids)


def _proof_artifact_identity(
    root: Path,
    harness_ids: Iterable[str],
) -> dict[str, object]:
    payloads = _read_exact_proof_tree(root, harness_ids)
    rows = [
        {
            "path": relative,
            "bytes": len(payload),
            "sha256": _sha256_bytes(payload),
        }
        for relative, payload in sorted(payloads.items())
    ]
    return {
        "schema": "lightwork.inventory-proof-artifact-snapshot.v1",
        "path_count": len(rows),
        "byte_count": sum(int(row["bytes"]) for row in rows),
        "sha256": _digest_rows(rows),
    }


def _write_snapshot_file(root: Path, relative: str, payload: bytes) -> None:
    parts = _relative_parts(relative, label="proof artifact snapshot")
    current = _absolute_lexical(root)
    for part in parts[:-1]:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
            ):
                raise ProductionCodeInventoryError(
                    "proof artifact snapshot has an unsafe directory"
                ) from None
    path = current / parts[-1]
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ProductionCodeInventoryError(
            "proof artifact snapshot file cannot be created safely"
        ) from exc
    try:
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except OSError as exc:
                raise ProductionCodeInventoryError(
                    "proof artifact snapshot write failed"
                ) from exc
            if written <= 0:  # pragma: no cover - operating-system invariant
                raise ProductionCodeInventoryError(
                    "proof artifact snapshot write did not progress"
                )
            view = view[written:]
        try:
            os.fsync(descriptor)
            metadata = os.fstat(descriptor)
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "proof artifact snapshot file could not be synchronized"
            ) from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or getattr(metadata, "st_nlink", 1) != 1
            or metadata.st_size != len(payload)
        ):
            raise ProductionCodeInventoryError(
                "proof artifact snapshot file identity is unsafe"
            )
    finally:
        os.close(descriptor)


def _capture_proof_artifact_snapshot(
    live_root: Path,
    destination: Path,
    harness_ids: Iterable[str],
) -> dict[str, object]:
    identifiers = _bounded_harness_ids(harness_ids)
    payloads = _read_exact_proof_tree(live_root, identifiers)
    try:
        destination.mkdir(mode=0o700)
    except OSError as exc:
        raise ProductionCodeInventoryError(
            "proof artifact snapshot destination cannot be created"
        ) from exc
    for relative, payload in sorted(payloads.items()):
        _write_snapshot_file(destination, relative, payload)
    captured = _proof_artifact_identity(destination, identifiers)
    expected = {
        "schema": "lightwork.inventory-proof-artifact-snapshot.v1",
        "path_count": len(payloads),
        "byte_count": sum(map(len, payloads.values())),
        "sha256": _digest_rows(
            {
                "path": relative,
                "bytes": len(payload),
                "sha256": _sha256_bytes(payload),
            }
            for relative, payload in sorted(payloads.items())
        ),
    }
    if captured != expected:
        raise ProductionCodeInventoryError(
            "proof artifact snapshot changed while it was materialized"
        )
    return captured


def _parse_python(path: Path) -> ast.Module:
    try:
        source = _bounded_read_path(
            path,
            maximum_bytes=MAX_SOURCE_FILE_BYTES,
            label="Python source",
        ).decode("utf-8", errors="strict")
        return ast.parse(source, filename=str(path))
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionCodeInventoryError(f"cannot parse Python source: {path}") from exc


def _surface_document(root: Path) -> tuple[dict[str, Any], str]:
    path = _repo_file(root, "release/production-surface-manifest.json")
    value = _load_json(path, label="production surface manifest")
    if not isinstance(value, dict):
        raise ProductionCodeInventoryError("production surface manifest root must be an object")
    return value, _sha256_source_text(path)


def _tests_inventory(root: Path, workspace: Mapping[str, Any]) -> dict[str, Any]:
    pytest_table = workspace.get("tool", {}).get("pytest", {}).get("ini_options", {})
    testpaths = pytest_table.get("testpaths")
    if (
        not isinstance(testpaths, list)
        or not testpaths
        or any(not isinstance(item, str) or not item for item in testpaths)
    ):
        raise ProductionCodeInventoryError("pytest testpaths must be explicit strings")

    files: list[Path] = []
    for relative in testpaths:
        base, _metadata, _parents = _safe_entry_metadata(
            root,
            relative,
            directory=True,
            label="pytest testpath",
        )
        files.extend(path for path in base.rglob("test_*.py") if path.is_file())
    files = sorted(set(files), key=lambda item: item.relative_to(root).as_posix())

    rows: list[dict[str, object]] = []
    root_counts: Counter[str] = Counter()
    function_count = 0
    async_function_count = 0
    for path in files:
        relative = path.relative_to(root).as_posix()
        tree = _parse_python(path)
        declared = 0
        declared_async = 0
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                declared += 1
                if isinstance(node, ast.AsyncFunctionDef):
                    declared_async += 1
        function_count += declared
        async_function_count += declared_async
        root_counts[relative.split("/", 1)[0]] += 1
        rows.append(
            {
                "path": relative,
                "sha256": _sha256_source_text(path),
                "digest_mode": SOURCE_TEXT_DIGEST_MODE,
                "declared_test_functions": declared,
            }
        )
    if not rows or function_count <= 0:
        raise ProductionCodeInventoryError("test inventory is empty")
    return {
        "testpaths": list(testpaths),
        "file_count": len(rows),
        "declared_test_function_count": function_count,
        "declared_async_test_function_count": async_function_count,
        "file_count_by_root": dict(sorted(root_counts.items())),
        "source_sha256": _digest_rows(rows),
        "source_digest_mode": CANONICAL_JSON_DIGEST_MODE,
        "files": rows,
        "count_semantics": (
            "AST declarations named test_; parametrized case expansion and runtime skips are "
            "reported by test execution and the separate pytest-exception contract"
        ),
    }


def _workspace_packages(
    root: Path,
    workspace: Mapping[str, Any],
    surface: Mapping[str, Any],
) -> dict[str, Any]:
    members = workspace.get("tool", {}).get("uv", {}).get("workspace", {}).get("members")
    if (
        not isinstance(members, list)
        or not members
        or any(not isinstance(item, str) or not item for item in members)
        or len(set(members)) != len(members)
    ):
        raise ProductionCodeInventoryError("uv workspace member inventory is malformed")
    surfaces = surface.get("surfaces")
    if not isinstance(surfaces, list):
        raise ProductionCodeInventoryError("production surface rows are malformed")
    package_status: dict[str, tuple[str, bool]] = {}
    for row in surfaces:
        if not isinstance(row, dict) or row.get("category") != "package":
            continue
        surface_id = row.get("id")
        if not isinstance(surface_id, str) or not surface_id.startswith("package:"):
            raise ProductionCodeInventoryError("package surface identity is malformed")
        package_status[surface_id.removeprefix("package:")] = (
            str(row.get("status")),
            bool(row.get("included")),
        )

    rows: list[dict[str, object]] = []
    for member in members:
        project_file = _repo_file(root, Path(member) / "pyproject.toml")
        project = _read_toml(project_file, label=f"workspace package {member}").get("project")
        if not isinstance(project, dict):
            raise ProductionCodeInventoryError(f"workspace package has no project table: {member}")
        name = project.get("name")
        version = project.get("version")
        if not isinstance(name, str) or not name or not isinstance(version, str) or not version:
            raise ProductionCodeInventoryError(f"workspace package identity is incomplete: {member}")
        status, included = package_status.get(name, ("unclassified", False))
        rows.append(
            {
                "path": member,
                "distribution": name,
                "version": version,
                "production_status": status,
                "production_included": included,
                "pyproject_sha256": _sha256_source_text(project_file),
                "pyproject_digest_mode": SOURCE_TEXT_DIGEST_MODE,
            }
        )
    return {
        "workspace_member_count": len(rows),
        "production_included_count": sum(bool(row["production_included"]) for row in rows),
        "source_sha256": _digest_rows(rows),
        "source_digest_mode": CANONICAL_JSON_DIGEST_MODE,
        "workspace_members": rows,
    }


def _venv_includes_system_site_packages() -> bool:
    try:
        prefix = Path(sys.prefix).resolve(strict=True)
        base_prefix = Path(sys.base_prefix).resolve(strict=True)
    except OSError as exc:
        raise ProductionCodeInventoryError(
            "runtime interpreter prefixes cannot be resolved"
        ) from exc
    if prefix == base_prefix:
        return True
    configuration = prefix / "pyvenv.cfg"
    raw = _bounded_read_path(
        configuration,
        maximum_bytes=64 * 1024,
        label="runtime virtual environment policy",
    )
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionCodeInventoryError(
            "runtime virtual environment policy is not UTF-8"
        ) from exc
    values: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip().casefold() == "include-system-site-packages":
            values.append(value.strip().casefold())
    if not values:
        return False
    if len(set(values)) != 1 or values[0] not in {"true", "false"}:
        raise ProductionCodeInventoryError(
            "runtime virtual environment system-site-packages policy is malformed"
        )
    return values[0] == "true"


def _runtime_probe_import_paths(root: Path) -> list[str]:
    root = _absolute_lexical(root)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    candidates: list[Path] = []
    prefixes = [Path(sys.prefix)]
    if Path(sys.prefix).resolve() == Path(sys.base_prefix).resolve() or (
        _venv_includes_system_site_packages()
    ):
        prefixes.append(Path(sys.base_prefix))
    for prefix in prefixes:
        if os.name == "nt":
            candidates.append(prefix / "Lib" / "site-packages")
        else:
            candidates.extend(
                (
                    prefix / "lib" / version / "site-packages",
                    prefix / "lib" / version / "dist-packages",
                    prefix / "lib64" / version / "site-packages",
                )
            )
    admitted: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except ValueError:
            pass
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "runtime dependency path cannot be resolved"
            ) from exc
        else:
            # Ignored in-repository virtual environments are mutable source-adjacent
            # state and cannot qualify as inventory dependencies.
            continue
        metadata = resolved.lstat()
        if resolved.is_symlink() or _is_reparse_point(metadata):
            raise ProductionCodeInventoryError(
                "runtime dependency path is a symlink or reparse point"
            )
        value = str(resolved)
        if value not in seen:
            seen.add(value)
            admitted.append(value)
    if not admitted:
        raise ProductionCodeInventoryError(
            "no external runtime dependency directory is available for the isolated probe"
        )
    return admitted


def _runtime_probe_environment(raw_home: str) -> dict[str, str]:
    environment = {
        name: os.environ[name]
        for name in _RUNTIME_PROBE_SYSTEM_ENV_ALLOWLIST
        if name in os.environ
    }
    environment.update(
        {
            "MAVERICK_HOME": raw_home,
            "MAVERICK_CONFIG": str(Path(raw_home) / "config.toml"),
            "TEMP": raw_home,
            "TMP": raw_home,
        }
    )
    return environment


def _validate_empty_runtime_pycache_prefix(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ProductionCodeInventoryError(
            "runtime probe pycache prefix is missing"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
    ):
        raise ProductionCodeInventoryError(
            "runtime probe pycache prefix is not a regular directory"
        )
    with os.scandir(path) as entries:
        if next(entries, None) is not None:
            raise ProductionCodeInventoryError(
                "runtime probe pycache prefix is not empty"
            )


def _prepare_runtime_probe_pycache_prefix(
    path: Path,
    *,
    source_root: Path,
    dependency_paths: Sequence[str],
) -> Path:
    path.mkdir(mode=0o700)
    _validate_empty_runtime_pycache_prefix(path)
    resolved = path.resolve(strict=True)
    boundaries = [source_root.resolve(strict=True)]
    try:
        boundaries.extend(Path(item).resolve(strict=True) for item in dependency_paths)
    except OSError as exc:
        raise ProductionCodeInventoryError(
            "runtime probe dependency path could not be resolved"
        ) from exc
    if any(resolved == boundary or resolved.is_relative_to(boundary) for boundary in boundaries):
        raise ProductionCodeInventoryError(
            "runtime probe pycache prefix overlaps source or dependency paths"
        )
    return resolved


_RUNTIME_PROBE_TIMEOUT_SECONDS = 120.0


@dataclass
class _RuntimeProbeCaptureState:
    buffers: dict[str, bytearray] = field(
        default_factory=lambda: {"stdout": bytearray(), "stderr": bytearray()}
    )
    completed_streams: set[str] = field(default_factory=set)
    overflowed_streams: set[str] = field(default_factory=set)
    reader_errors: list[Exception] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    wake: threading.Event = field(default_factory=threading.Event)


def _drain_runtime_probe_stream(
    name: str,
    pipe,
    maximum_bytes: int,
    state: _RuntimeProbeCaptureState,
) -> None:
    try:
        while True:
            remaining = maximum_bytes - len(state.buffers[name])
            chunk = pipe.read(min(64 * 1024, remaining + 1))
            if not chunk:
                break
            retained = chunk[:remaining]
            if retained:
                state.buffers[name].extend(retained)
            if len(chunk) > remaining:
                with state.lock:
                    state.overflowed_streams.add(name)
                state.wake.set()
                break
    except Exception as exc:  # pragma: no cover - platform pipe failure
        with state.lock:
            state.reader_errors.append(exc)
        state.wake.set()
    finally:
        with state.lock:
            state.completed_streams.add(name)
        state.wake.set()
        try:
            pipe.close()
        except OSError:
            pass


def _wait_for_runtime_probe(
    process: subprocess.Popen[bytes],
    state: _RuntimeProbeCaptureState,
) -> str | None:
    deadline = time.monotonic() + _RUNTIME_PROBE_TIMEOUT_SECONDS
    exit_observed_at: float | None = None
    while True:
        with state.lock:
            streams_complete = len(state.completed_streams) == 2
            overflows = sorted(state.overflowed_streams)
            errors = bool(state.reader_errors)
        if overflows or errors:
            reason = "output_limit:" + ",".join(overflows) if overflows else "output_reader_failure"
            return reason
        now = time.monotonic()
        if now >= deadline:
            return "timeout"
        if process.poll() is not None:
            if streams_complete:
                return None
            if exit_observed_at is None:
                exit_observed_at = now
            elif now - exit_observed_at >= 0.25:
                return "descendant_pipe_drain_timeout"
        state.wake.wait(timeout=min(0.02, max(0.0, deadline - now)))
        state.wake.clear()


def _finish_runtime_probe(
    process: subprocess.Popen[bytes],
    readers: Sequence[threading.Thread],
    state: _RuntimeProbeCaptureState,
) -> int:
    try:
        returncode = process.wait(timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ProductionCodeInventoryError(
            "source inventory probe process did not terminate"
        ) from exc
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
        raise ProductionCodeInventoryError(
            "source inventory probe output readers did not terminate"
        )
    with state.lock:
        errors = list(state.reader_errors)
        overflows = sorted(state.overflowed_streams)
    if errors and not overflows:
        raise ProductionCodeInventoryError(
            "source inventory probe output capture failed"
        ) from errors[0]
    return returncode


def _run_bounded_runtime_probe(
    argv: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    controller_factory: Callable[[], Any] | None = None,
) -> tuple[int, bytes, bytes]:
    if controller_factory is None:
        from maverick import proof_provenance as imported_provenance

        controller_factory = imported_provenance.process_tree_controller
    with controller_factory() as tree:
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                **tree.popen_kwargs,
            )
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "source inventory probe could not start"
            ) from exc
        tree.attach(process)
        if process.stdout is None or process.stderr is None:  # pragma: no cover - invariant
            raise ProductionCodeInventoryError(
                "source inventory probe pipes were not created"
            )
        state = _RuntimeProbeCaptureState()
        readers = [
            threading.Thread(
                target=_drain_runtime_probe_stream,
                args=(name, pipe, maximum, state),
                name=f"runtime-probe-{name}-reader",
                daemon=True,
            )
            for name, pipe, maximum in (
                ("stdout", process.stdout, MAX_RUNTIME_PROBE_STDOUT_BYTES),
                ("stderr", process.stderr, MAX_RUNTIME_PROBE_STDERR_BYTES),
            )
        ]
        for reader in readers:
            reader.start()
        termination_reason = _wait_for_runtime_probe(process, state)
        if termination_reason is not None:
            seal = tree.seal(process, terminate=True)
            if seal.control_failure is not None:
                termination_reason = (
                    f"process_tree_control_failure:{seal.control_failure}"
                )
        returncode = _finish_runtime_probe(process, readers, state)
        if termination_reason is None:
            seal = tree.seal(process)
            if seal.control_failure is not None:
                termination_reason = (
                    f"process_tree_control_failure:{seal.control_failure}"
                )
            elif seal.descendant_survived:
                termination_reason = "descendant_process_survived"
    if termination_reason is not None:
        if termination_reason.startswith("output_limit:"):
            raise ProductionCodeInventoryError(
                "source inventory probe exceeded output bounds"
            )
        if termination_reason == "timeout":
            raise ProductionCodeInventoryError("source inventory probe timed out")
        if termination_reason == "output_reader_failure":
            raise ProductionCodeInventoryError(
                "source inventory probe output capture failed"
            )
        raise ProductionCodeInventoryError(
            "source inventory probe did not terminate its complete process tree: "
            f"{termination_reason}"
        )
    return returncode, bytes(state.buffers["stdout"]), bytes(state.buffers["stderr"])


_RUNTIME_PROBE = r"""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
pycache_prefix = Path(sys.argv[2]).resolve()
if len(sys.argv) != 4:
    raise RuntimeError("runtime probe dependency snapshot argv is not exact")
dependency_root = Path(sys.argv[3]).resolve()
if Path(sys.pycache_prefix or "").resolve() != pycache_prefix:
    raise RuntimeError("runtime probe pycache prefix is not active")
sys.path[:0] = [
    str(root / "packages/maverick-core"),
    str(root / "packages/maverick-dashboard"),
    str(root / "packages/maverick-mcp"),
    str(root / "packages/maverick-channels"),
    str(dependency_root),
]

import annotated_doc
import annotated_types
import anyio
import fastapi
import jinja2
import markupsafe
import pydantic
import pydantic_core
import python_multipart
import sniffio
import starlette
import typing_extensions
import typing_inspection
import maverick.tools.enterprise_connectors as enterprise_connectors
import maverick_dashboard.app as dashboard_app
import maverick_dashboard.endpoint_policy as dashboard_endpoint_policy
import maverick_mcp.server as mcp_server

def bound_origin(module):
    return Path(module.__file__).resolve(strict=True).relative_to(root).as_posix()

import_origins = {
    "dashboard_app": bound_origin(dashboard_app),
    "dashboard_endpoint_policy": bound_origin(dashboard_endpoint_policy),
    "enterprise_connectors": bound_origin(enterprise_connectors),
    "mcp_server": bound_origin(mcp_server),
}
dependency_origins = sorted({
    str(origin)
    for module in tuple(sys.modules.values())
    for raw_origin in (getattr(module, "__file__", None),)
    if isinstance(raw_origin, str)
    for origin in (Path(raw_origin).resolve(strict=True),)
    if origin.is_relative_to(dependency_root)
})
app = dashboard_app.app
endpoint_policy_report = dashboard_endpoint_policy.endpoint_policy_report
ENTERPRISE_CONNECTOR_NAMES = enterprise_connectors.ENTERPRISE_CONNECTOR_NAMES
PRODUCTION_CONNECTOR_STATUS = enterprise_connectors.PRODUCTION_CONNECTOR_STATUS
PUBLIC_DATA_CONNECTOR_NAMES = enterprise_connectors.PUBLIC_DATA_CONNECTOR_NAMES
READ_CONNECTOR_NAMES = enterprise_connectors.READ_CONNECTOR_NAMES
_BESPOKE_CATALOG = enterprise_connectors._BESPOKE_CATALOG
TOOLS = mcp_server.TOOLS

report = endpoint_policy_report(app)
routes = []
for row in report["routes"]:
    policy = row["policy"]
    routes.append({
        "kind": row["kind"],
        "method": row["method"],
        "path": row["path"],
        "name": row["name"],
        "policy_class": policy["policy_class"],
        "production_allowed": policy["production_allowed"],
    })
connector_groups = {
    "write_capable": sorted(ENTERPRISE_CONNECTOR_NAMES),
    "read_only": sorted(READ_CONNECTOR_NAMES),
    "bespoke": sorted(item["name"] for item in _BESPOKE_CATALOG),
    "public_data": sorted(PUBLIC_DATA_CONNECTOR_NAMES),
}
all_connectors = sorted(
    set(connector_groups["write_capable"])
    | set(connector_groups["read_only"])
    | set(connector_groups["bespoke"])
)
value = {
    "import_origins": import_origins,
    "dependency_origins": dependency_origins,
    "routes": {
        "covered": report["covered"],
        "route_count": report["actual_route_count"],
        "route_inventory_sha256": report["actual_inventory_sha256"],
        "expected_route_count": report["expected_route_count"],
        "expected_route_inventory_sha256": report["expected_inventory_sha256"],
        "production_admitted_count": report["production_admitted_count"],
        "production_policy_sha256": report["production_policy_sha256"],
        "expected_production_admitted_count": report["expected_production_admitted_count"],
        "expected_production_policy_sha256": report["expected_production_policy_sha256"],
        "inventory": routes,
    },
    "mcp": {
        "tools": sorted(item["name"] for item in TOOLS),
        "catalog_sha256": __import__("hashlib").sha256(
            json.dumps(
                TOOLS,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    },
    "connectors": {
        "production_status": PRODUCTION_CONNECTOR_STATUS,
        "groups": connector_groups,
        "all_source_connectors": all_connectors,
    },
}
print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
"""


@dataclass(frozen=True)
class InventorySnapshot:
    """One immutable authority set used by every inventory section."""

    live_root: Path
    source_root: Path
    source_content: Mapping[str, Any]
    source_tree_identity: Mapping[str, Any]
    live_source_provenance: Mapping[str, Any]
    proof_artifact_root: Path
    proof_artifact_identity: Mapping[str, object]
    proof_tree_identity: Mapping[str, Any]
    dependency_module: Any
    dependency_binding: Any
    dependency_root: Path
    dependency_tree_identity: Mapping[str, Any]
    provenance: Any


def _runtime_inventory(snapshot: InventorySnapshot) -> dict[str, Any]:  # noqa: C901
    runtime_root = snapshot.source_root
    provenance = snapshot.provenance
    dependency_module = snapshot.dependency_module
    dependency_binding = snapshot.dependency_binding
    dependency_root = snapshot.dependency_root
    bound_source_content = dict(snapshot.source_content)
    semantic_execution_identity = provenance.semantic_execution_tree_identity(
        bound_source_content,
        symlink_materialization_policy=provenance.SYMLINK_MATERIALIZATION_POLICY,
    )
    with tempfile.TemporaryDirectory(prefix="lightwork-code-inventory-runtime-") as temporary:
        temporary_root = Path(temporary)
        raw_home = temporary_root / "runtime-home"
        raw_home.mkdir(mode=0o700)
        pycache_prefix = _prepare_runtime_probe_pycache_prefix(
            temporary_root / "pycache",
            source_root=runtime_root,
            dependency_paths=[str(dependency_root)],
        )
        try:
            returncode, stdout, stderr = _run_bounded_runtime_probe(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-X",
                    f"pycache_prefix={pycache_prefix}",
                    "-X",
                    "utf8",
                    "-W",
                    "default",
                    "-c",
                    _RUNTIME_PROBE,
                    str(runtime_root),
                    str(pycache_prefix),
                    str(dependency_root),
                ],
                cwd=runtime_root,
                env=_runtime_probe_environment(str(raw_home)),
                controller_factory=provenance.process_tree_controller,
            )
        finally:
            _validate_empty_runtime_pycache_prefix(pycache_prefix)
            try:
                dependency_module.assert_runtime_dependencies_unchanged(
                    dependency_binding,
                    runtime_root,
                )
                dependency_tree_at_completion = (
                    provenance.materialized_tree_identity(dependency_root)
                )
            except (
                dependency_module.DependencyBindingError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                raise ProductionCodeInventoryError(
                    "runtime dependencies could not be verified after execution"
                ) from exc
            if dependency_tree_at_completion != snapshot.dependency_tree_identity:
                raise ProductionCodeInventoryError(
                    "runtime dependency snapshot changed while inventory code was executing"
                )
            try:
                materialized_tree_at_completion = provenance.materialized_tree_identity(
                    runtime_root
                )
            except (OSError, RuntimeError, ValueError) as exc:
                raise ProductionCodeInventoryError(
                    "runtime source identity could not be verified after execution"
                ) from exc
            if materialized_tree_at_completion != snapshot.source_tree_identity:
                raise ProductionCodeInventoryError(
                    "runtime source tree changed while inventory code was executing"
                )
        dependency_origin_ownership: list[dict[str, str]] | None = None
        if returncode == 0 and not stderr:
            try:
                provisional = json.loads(
                    stdout.decode("utf-8", errors="strict"),
                    object_pairs_hook=_strict_object,
                    parse_constant=_reject_constant,
                )
                raw_dependency_origins = provisional.get("dependency_origins")
                if not isinstance(raw_dependency_origins, list) or not all(
                    isinstance(item, str) and item
                    for item in raw_dependency_origins
                ):
                    raise ProductionCodeInventoryError(
                        "runtime dependency origin inventory is malformed"
                    )
                dependency_origin_ownership = (
                    dependency_module.validate_runtime_dependency_origins(
                        dependency_binding,
                        raw_dependency_origins,
                        materialized_root=dependency_root,
                    )
                )
            except (
                dependency_module.DependencyBindingError,
                AttributeError,
                TypeError,
                UnicodeDecodeError,
                ValueError,
                RecursionError,
            ) as exc:
                raise ProductionCodeInventoryError(
                    "runtime dependency origins are not bound to the private snapshot"
                ) from exc
    if returncode != 0:
        detail = " ".join(stderr.decode("utf-8", errors="replace").split())[-1000:]
        raise ProductionCodeInventoryError(
            f"source inventory probe failed with exit {returncode}: {detail}"
        )
    if stderr:
        detail = " ".join(stderr.decode("utf-8", errors="replace").split())[-1000:]
        raise ProductionCodeInventoryError(
            f"source inventory probe emitted unexpected stderr: {detail}"
        )
    try:
        value = json.loads(
            stdout.decode("utf-8", errors="strict"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ProductionCodeInventoryError("source inventory probe emitted invalid JSON") from exc
    _check_depth(value)
    if not isinstance(value, dict) or set(value) != {
        "routes",
        "mcp",
        "connectors",
        "import_origins",
        "dependency_origins",
    }:
        raise ProductionCodeInventoryError("source inventory probe schema drifted")
    if value["import_origins"] != _RUNTIME_PROBE_SOURCE_ORIGINS:
        raise ProductionCodeInventoryError(
            "source inventory probe imported outside its bound source paths"
        )
    if dependency_origin_ownership is None:
        raise ProductionCodeInventoryError(
            "runtime dependency origin ownership was not verified"
        )

    routes = value["routes"]
    if (
        not isinstance(routes, dict)
        or routes.get("covered") is not True
        or routes.get("route_count") != routes.get("expected_route_count")
        or routes.get("route_inventory_sha256")
        != routes.get("expected_route_inventory_sha256")
        or routes.get("production_admitted_count")
        != routes.get("expected_production_admitted_count")
        or routes.get("production_policy_sha256")
        != routes.get("expected_production_policy_sha256")
    ):
        raise ProductionCodeInventoryError("constructed dashboard route policy drifted")
    route_rows = routes.get("inventory")
    if not isinstance(route_rows, list) or len(route_rows) != routes.get("route_count"):
        raise ProductionCodeInventoryError("constructed dashboard route inventory is incomplete")
    routes["inventory"] = sorted(
        route_rows,
        key=lambda row: (
            str(row.get("kind")),
            str(row.get("method")),
            str(row.get("path")),
            str(row.get("name")),
        ),
    )

    mcp = value["mcp"]
    if not isinstance(mcp, dict) or not isinstance(mcp.get("tools"), list):
        raise ProductionCodeInventoryError("MCP tool inventory is malformed")
    tools = mcp["tools"]
    if not tools or any(not isinstance(item, str) or not item for item in tools):
        raise ProductionCodeInventoryError("MCP tool names are malformed")
    if len(tools) != len(set(tools)) or not _HEX64.fullmatch(str(mcp.get("catalog_sha256"))):
        raise ProductionCodeInventoryError("MCP tool inventory is non-canonical")
    mcp["tool_count"] = len(tools)
    mcp["production_included"] = False

    connectors = value["connectors"]
    if not isinstance(connectors, dict) or connectors.get("production_status") != "unsupported":
        raise ProductionCodeInventoryError("connector support boundary drifted")
    groups = connectors.get("groups")
    all_connectors = connectors.get("all_source_connectors")
    if not isinstance(groups, dict) or not isinstance(all_connectors, list):
        raise ProductionCodeInventoryError("connector inventory is malformed")
    for name in ("write_capable", "read_only", "bespoke", "public_data"):
        items = groups.get(name)
        if (
            not isinstance(items, list)
            or any(not isinstance(item, str) or not item for item in items)
            or len(items) != len(set(items))
        ):
            raise ProductionCodeInventoryError(f"connector group is malformed: {name}")
    authority_union = set(groups["write_capable"]) | set(groups["read_only"]) | set(
        groups["bespoke"]
    )
    if all_connectors != sorted(authority_union):
        raise ProductionCodeInventoryError("connector union differs from source groups")
    if not set(groups["public_data"]).issubset(groups["read_only"]):
        raise ProductionCodeInventoryError("public-data connectors are not read-only")
    connectors["group_counts"] = {
        name: len(groups[name])
        for name in ("write_capable", "read_only", "bespoke", "public_data")
    }
    connectors["source_connector_count"] = len(all_connectors)
    connectors["source_sha256"] = _digest_rows(all_connectors)
    connectors["production_included"] = []
    value.pop("import_origins")
    value.pop("dependency_origins")
    value["runtime_probe"] = {
        **_RUNTIME_PROBE_POLICY,
        "bound_source_content": bound_source_content,
        "semantic_execution_tree_identity": semantic_execution_identity,
        "dependency_binding": dict(dependency_binding.report),
        "dependency_origin_ownership": dependency_origin_ownership,
        "dependency_snapshot": {
            "cache_free_materialization": True,
            "local_materialized_tree_identity_enforced": True,
            "local_materialized_tree_identity_serialized": False,
            "read_only_transition_identity_preserved": True,
            "completion_identity_preserved": True,
        },
        "symlink_materialization_policy": provenance.SYMLINK_MATERIALIZATION_POLICY,
        "local_materialized_tree_identity_schema": (
            provenance.MATERIALIZED_TREE_IDENTITY_SCHEMA
        ),
        "local_materialized_tree_identity_enforced": True,
        "local_materialized_tree_identity_serialized": False,
        "read_only_transition_identity_preserved": True,
        "source_import_origins": dict(_RUNTIME_PROBE_SOURCE_ORIGINS),
    }
    return value


def _assignment_dict_keys(tree: ast.Module, name: str) -> list[str]:
    value: ast.AST | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == name:
                value = node.value
    if not isinstance(value, ast.Dict):
        raise ProductionCodeInventoryError(f"Python mapping assignment is missing: {name}")
    keys: list[str] = []
    for key in value.keys:
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ProductionCodeInventoryError(f"{name} keys must be literal strings")
        keys.append(key.value)
    if len(keys) != len(set(keys)):
        raise ProductionCodeInventoryError(f"{name} has duplicate keys")
    return keys


def _channels_inventory(root: Path) -> dict[str, Any]:
    server_path = _repo_file(root, "packages/maverick-core/maverick/server.py")
    tree = _parse_python(server_path)
    names = sorted(_assignment_dict_keys(tree, "_WIRES"))
    ports: dict[str, int] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("_wire_"):
            continue
        channel = node.name.removeprefix("_wire_")
        for child in ast.walk(node):
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "cfg"
                and len(child.args) >= 2
                and isinstance(child.args[0], ast.Constant)
                and child.args[0].value == "port"
                and isinstance(child.args[1], ast.Constant)
                and isinstance(child.args[1].value, int)
            ):
                if channel in ports and ports[channel] != child.args[1].value:
                    raise ProductionCodeInventoryError(
                        f"channel {channel} declares conflicting listener ports"
                    )
                ports[channel] = child.args[1].value
    if not set(ports).issubset(names):
        raise ProductionCodeInventoryError("channel listener port has no registered wire")
    package_init = _read_utf8_source(
        _repo_file(root, "packages/maverick-channels/maverick_channels/__init__.py"),
        label="channel package source",
    )
    advertised = re.search(r"Server-wired adapters \((\d+);", package_init)
    if advertised is None or int(advertised.group(1)) != len(names):
        raise ProductionCodeInventoryError("channel package documentation count drifted")
    return {
        "registered_channel_count": len(names),
        "registered_channels": names,
        "default_listener_ports": dict(sorted(ports.items())),
        "source_sha256": _sha256_source_text(server_path),
        "source_digest_mode": SOURCE_TEXT_DIGEST_MODE,
        "production_included": [],
    }


def _click_port_default(tree: ast.Module, function_name: str) -> int:
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != function_name:
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not any(
                isinstance(arg, ast.Constant) and arg.value == "--port"
                for arg in decorator.args
            ):
                continue
            for keyword in decorator.keywords:
                if (
                    keyword.arg == "default"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, int)
                ):
                    return keyword.value.value
    raise ProductionCodeInventoryError(f"CLI port default is missing: {function_name}")


def _argparse_port_default(tree: ast.Module, function_name: str) -> int:
    functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(functions) != 1:
        raise ProductionCodeInventoryError(f"entrypoint function is missing: {function_name}")
    for node in ast.walk(functions[0]):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument" or not any(
            isinstance(arg, ast.Constant) and arg.value == "--port" for arg in node.args
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "default"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, int)
            ):
                return keyword.value.value
    raise ProductionCodeInventoryError(f"argparse port default is missing: {function_name}")


def _helm_service_ports(path: Path) -> tuple[int, int]:
    text = _read_utf8_source(path, label="Helm service values")
    match = re.search(r"(?ms)^service:\s*\n(?P<body>(?:^[ ]{2}[^\n]*\n?)+)", text)
    if match is None:
        raise ProductionCodeInventoryError("Helm service values block is missing")
    body = match.group("body")

    def field(name: str) -> int:
        found = re.search(rf"(?m)^  {re.escape(name)}:\s*(\d+)\s*$", body)
        if found is None:
            raise ProductionCodeInventoryError(f"Helm service.{name} is missing")
        return int(found.group(1))

    return field("port"), field("targetPort")


def _docker_dashboard_port(path: Path) -> int:
    text = _read_utf8_source(path, label="production Dockerfile")
    matches = re.findall(r"(?m)^CMD\s+(\[[^\n]+\])\s*$", text)
    if len(matches) != 1:
        raise ProductionCodeInventoryError("production Dockerfile CMD is not exact")
    try:
        command = json.loads(matches[0])
        index = command.index("--port")
        port = int(command[index + 1])
    except (ValueError, TypeError, IndexError, json.JSONDecodeError) as exc:
        raise ProductionCodeInventoryError("production Dockerfile has no literal port") from exc
    return port


def _ports_inventory(root: Path, channels: Mapping[str, Any]) -> dict[str, Any]:
    cli_path = _repo_file(root, "packages/maverick-core/maverick/cli/__init__.py")
    app_path = _repo_file(
        root, "packages/maverick-dashboard/maverick_dashboard/app.py"
    )
    values_path = _repo_file(root, "deploy/helm/maverick/values.yaml")
    docker_path = _repo_file(root, "deploy/docker/Dockerfile.production")
    cli_tree = _parse_python(cli_path)
    dashboard_default = _click_port_default(cli_tree, "dashboard")
    mcp_default = _click_port_default(cli_tree, "mcp")
    app_default = _argparse_port_default(_parse_python(app_path), "main")
    service_port, target_port = _helm_service_ports(values_path)
    docker_port = _docker_dashboard_port(docker_path)
    if len({dashboard_default, app_default, target_port, docker_port}) != 1:
        raise ProductionCodeInventoryError("production dashboard ports drifted")
    return {
        "production_dashboard": {
            "service_port": service_port,
            "container_port": target_port,
            "cli_default_port": dashboard_default,
            "module_default_port": app_default,
            "image_command_port": docker_port,
        },
        "excluded_source_listeners": {
            "mcp_http_default_port": mcp_default,
            "channel_default_ports": dict(channels["default_listener_ports"]),
        },
        "source_sha256": _digest_rows(
            [
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha256_source_text(path),
                    "digest_mode": SOURCE_TEXT_DIGEST_MODE,
                }
                for path in (cli_path, app_path, values_path, docker_path)
            ]
        ),
        "source_digest_mode": CANONICAL_JSON_DIGEST_MODE,
    }


def _installer_inventory(
    root: Path,
    surface: Mapping[str, Any],
) -> dict[str, Any]:
    bridge_path = _repo_file(root, "apps/installer-cli/maverick_installer/bridge.py")
    tree = _parse_python(bridge_path)
    run_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "run"
    ]
    if len(run_functions) != 1:
        raise ProductionCodeInventoryError("desktop installer bridge run function is missing")
    step_calls = [
        node
        for node in ast.walk(run_functions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_step"
        and len(node.args) >= 3
    ]
    step_calls.sort(key=lambda node: (node.lineno, node.col_offset))
    steps: list[dict[str, object]] = []
    for node in step_calls:
        try:
            step_id = ast.literal_eval(node.args[0])
        except (ValueError, TypeError) as exc:
            raise ProductionCodeInventoryError("installer step ID must be literal") from exc
        if not isinstance(step_id, str):
            raise ProductionCodeInventoryError("installer step ID must be text")
        if step_id == "__done__":
            continue
        try:
            question = ast.literal_eval(node.args[1])
        except (ValueError, TypeError) as exc:
            raise ProductionCodeInventoryError("installer question must be literal") from exc
        if not isinstance(question, str):
            raise ProductionCodeInventoryError("installer question must be text")
        kind = "choice"
        for keyword in node.keywords:
            if keyword.arg == "kind":
                try:
                    kind = ast.literal_eval(keyword.value)
                except (ValueError, TypeError) as exc:
                    raise ProductionCodeInventoryError("installer step kind must be literal") from exc
        choice_authority = "literal"
        choice_count: int | None
        try:
            choices = ast.literal_eval(node.args[2])
        except (ValueError, TypeError):
            choice_count = None
            names = [child.id for child in ast.walk(node.args[2]) if isinstance(child, ast.Name)]
            choice_authority = ",".join(sorted(set(names))) or "dynamic"
        else:
            if not isinstance(choices, list):
                raise ProductionCodeInventoryError("installer literal choices must be an array")
            choice_count = len(choices)
        steps.append(
            {
                "id": step_id,
                "question": question,
                "kind": kind,
                "choice_count": choice_count,
                "choice_authority": choice_authority,
            }
        )
    expected_ids = ["name", "governance", "api_key", "workdir", "budget"]
    observed_ids = [str(step["id"]) for step in steps]
    if observed_ids != expected_ids:
        raise ProductionCodeInventoryError(
            f"consumer installer question order drifted: {observed_ids}"
        )
    installer_rows = [
        row
        for row in surface.get("surfaces", [])
        if isinstance(row, dict) and row.get("id") == "package:maverick-installer"
    ]
    if len(installer_rows) != 1:
        raise ProductionCodeInventoryError("installer production surface is not exact")
    installer_status = installer_rows[0].get("status")
    installer_included = installer_rows[0].get("included")
    if not isinstance(installer_status, str) or not isinstance(installer_included, bool):
        raise ProductionCodeInventoryError("installer production status is malformed")
    return {
        "consumer_question_count": len(steps),
        "consumer_questions": steps,
        "questions_sha256": _digest_rows(steps),
        "production_status": installer_status,
        "production_included": installer_included,
        "source": bridge_path.relative_to(root).as_posix(),
        "source_sha256": _sha256_source_text(bridge_path),
        "source_digest_mode": SOURCE_TEXT_DIGEST_MODE,
    }


def _feature_inventory(
    surface: Mapping[str, Any],
    surface_sha256: str,
) -> dict[str, Any]:
    rows = surface.get("surfaces")
    if not isinstance(rows, list) or not rows:
        raise ProductionCodeInventoryError("production surface inventory is empty")
    normalized: list[dict[str, object]] = []
    identities: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "category", "status", "included"}:
            raise ProductionCodeInventoryError("production surface row schema drifted")
        identity = row["id"]
        if not isinstance(identity, str) or not identity or identity in identities:
            raise ProductionCodeInventoryError("production surface identities are malformed")
        identities.add(identity)
        normalized.append(
            {
                "id": identity,
                "category": row["category"],
                "status": row["status"],
                "included": row["included"],
            }
        )
    normalized.sort(key=lambda row: str(row["id"]))
    features = [row for row in normalized if row["category"] == "feature"]
    return {
        "surface_manifest_sha256": surface_sha256,
        "surface_manifest_digest_mode": SOURCE_TEXT_DIGEST_MODE,
        "surface_count": len(normalized),
        "feature_count": len(features),
        "included_surface_count": sum(bool(row["included"]) for row in normalized),
        "status_counts": dict(sorted(Counter(str(row["status"]) for row in normalized).items())),
        "features": features,
        "all_surface_statuses": normalized,
    }


def _load_proof_provenance(root: Path):
    path = _repo_file(root, "packages/maverick-core/maverick/proof_provenance.py")
    module_name = "inventory_proof_provenance"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ProductionCodeInventoryError("cannot load proof provenance verifier")
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_module = sys.modules.get(module_name, sentinel)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    try:
        # Dataclasses and other import-time helpers resolve the defining module
        # through sys.modules.  Register it only for the duration of execution,
        # then restore any caller-owned module without leaving import state or a
        # source-changing __pycache__ behind.
        sys.modules[module_name] = module
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProductionCodeInventoryError("cannot load proof provenance verifier") from exc
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if previous_module is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


def _load_production_dependency_binding(root: Path):
    path = _repo_file(root, "scripts/production_dependency_binding.py")
    module_name = "inventory_production_dependency_binding"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ProductionCodeInventoryError(
            "cannot load production dependency binding"
        )
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_module = sys.modules.get(module_name, sentinel)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    try:
        sys.modules[module_name] = module
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProductionCodeInventoryError(
            "cannot load production dependency binding"
        ) from exc
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        if previous_module is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    return module


def _load_results_generator(root: Path):
    path = _repo_file(root, "proof/generate_results.py")
    module_name = "inventory_results_generator"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ProductionCodeInventoryError("cannot load proof results renderer")
    module = importlib.util.module_from_spec(spec)
    sentinel = object()
    previous_module = sys.modules.get(module_name, sentinel)
    previous_maverick = {
        name: value
        for name, value in sys.modules.items()
        if name == "maverick" or name.startswith("maverick.")
    }
    original_sys_path = list(sys.path)
    previous_dont_write_bytecode = sys.dont_write_bytecode
    try:
        for name in previous_maverick:
            sys.modules.pop(name, None)
        sys.modules[module_name] = module
        sys.path.insert(0, str(root / "packages" / "maverick-core"))
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ProductionCodeInventoryError("cannot load proof results renderer") from exc
    finally:
        sys.path[:] = original_sys_path
        sys.dont_write_bytecode = previous_dont_write_bytecode
        for name in list(sys.modules):
            if name == "maverick" or name.startswith("maverick."):
                sys.modules.pop(name, None)
        sys.modules.update(previous_maverick)
        if previous_module is sentinel:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
    renderer = getattr(module, "render_results_markdown", None)
    if not callable(renderer):
        raise ProductionCodeInventoryError("proof results renderer API is unavailable")
    return module


@contextmanager
def _production_inventory_snapshot(root: Path):  # noqa: C901
    """Capture one source/proof/dependency authority set and verify it at exit."""

    live_root = _absolute_lexical(root)
    _safe_directory_chain(live_root, label="inventory repository root")
    live_provenance = _load_proof_provenance(live_root)
    exclusions = live_provenance.proof_source_exclusions(
        live_root,
        generated_paths=(live_root / "proof/RESULTS.md", live_root / "proof/results"),
    )
    initial_live_source = live_provenance.repository_provenance(
        live_root,
        exclude_paths=exclusions,
    )
    if not isinstance(initial_live_source, dict) or initial_live_source.get("available") is not True:
        raise ProductionCodeInventoryError(
            "live source provenance is unavailable before inventory capture"
        )
    try:
        initial_source_content = live_provenance.source_content_identity(
            initial_live_source
        )
    except ValueError as exc:
        raise ProductionCodeInventoryError(
            "live source-content identity is malformed before inventory capture"
        ) from exc

    with tempfile.TemporaryDirectory(prefix="lightwork-code-inventory-snapshot-") as temporary:
        temporary_root = Path(temporary)
        source_root = temporary_root / "source"
        try:
            materialized_source_content = live_provenance.materialize_repository_source(
                live_root,
                source_root,
                exclude_paths=exclusions,
                symlink_materialization_policy=(
                    live_provenance.SYMLINK_MATERIALIZATION_POLICY
                ),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProductionCodeInventoryError(
                "cannot materialize the composite inventory source snapshot"
            ) from exc
        if materialized_source_content != initial_source_content:
            raise ProductionCodeInventoryError(
                "live source changed while the composite inventory snapshot was captured"
            )
        try:
            source_tree_identity = live_provenance.materialized_tree_identity(source_root)
            live_provenance.make_materialized_tree_read_only(source_root)
            read_only_source_tree = live_provenance.materialized_tree_identity(source_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProductionCodeInventoryError(
                "cannot lock the composite inventory source snapshot"
            ) from exc
        if read_only_source_tree != source_tree_identity:
            raise ProductionCodeInventoryError(
                "source snapshot identity changed during its read-only transition"
            )

        proof_artifact_root = temporary_root / "proof-artifacts"
        proof_artifact_identity = _capture_proof_artifact_snapshot(
            live_root,
            proof_artifact_root,
            (identity for identity, _script in _PROOF_HARNESS_SCRIPTS),
        )
        if _proof_artifact_identity(
            live_root,
            (identity for identity, _script in _PROOF_HARNESS_SCRIPTS),
        ) != proof_artifact_identity:
            raise ProductionCodeInventoryError(
                "live proof artifacts changed while the composite snapshot was captured"
            )
        try:
            proof_tree_identity = live_provenance.materialized_tree_identity(
                proof_artifact_root
            )
            live_provenance.make_materialized_tree_read_only(proof_artifact_root)
            read_only_proof_tree = live_provenance.materialized_tree_identity(
                proof_artifact_root
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ProductionCodeInventoryError(
                "cannot lock the proof artifact snapshot"
            ) from exc
        if read_only_proof_tree != proof_tree_identity:
            raise ProductionCodeInventoryError(
                "proof artifact snapshot changed during its read-only transition"
            )

        provenance = _load_proof_provenance(source_root)
        snapshot_exclusions = provenance.proof_source_exclusions(
            source_root,
            generated_paths=(
                source_root / "proof/RESULTS.md",
                source_root / "proof/results",
            ),
        )
        if snapshot_exclusions != exclusions:
            raise ProductionCodeInventoryError(
                "snapshot and live source exclusion semantics differ"
            )
        dependency_module = _load_production_dependency_binding(source_root)
        dependency_paths = _runtime_probe_import_paths(live_root)
        try:
            dependency_binding = dependency_module.bind_runtime_dependencies(
                source_root,
                dependency_paths,
            )
            dependency_root = dependency_module.materialize_runtime_dependencies(
                dependency_binding,
                temporary_root / "dependencies",
            )
            dependency_tree_identity = provenance.materialized_tree_identity(
                dependency_root
            )
            provenance.make_materialized_tree_read_only(dependency_root)
            read_only_dependency_tree = provenance.materialized_tree_identity(
                dependency_root
            )
        except (
            dependency_module.DependencyBindingError,
            OSError,
            RuntimeError,
            ValueError,
        ) as exc:
            raise ProductionCodeInventoryError(
                "cannot capture the composite runtime dependency snapshot"
            ) from exc
        if read_only_dependency_tree != dependency_tree_identity:
            raise ProductionCodeInventoryError(
                "dependency snapshot changed during its read-only transition"
            )

        snapshot = InventorySnapshot(
            live_root=live_root,
            source_root=source_root,
            source_content=MappingProxyType(dict(initial_source_content)),
            source_tree_identity=MappingProxyType(dict(source_tree_identity)),
            live_source_provenance=MappingProxyType(dict(initial_live_source)),
            proof_artifact_root=proof_artifact_root,
            proof_artifact_identity=MappingProxyType(dict(proof_artifact_identity)),
            proof_tree_identity=MappingProxyType(dict(proof_tree_identity)),
            dependency_module=dependency_module,
            dependency_binding=dependency_binding,
            dependency_root=dependency_root,
            dependency_tree_identity=MappingProxyType(dict(dependency_tree_identity)),
            provenance=provenance,
        )
        try:
            yield snapshot
        finally:
            try:
                source_tree_at_completion = provenance.materialized_tree_identity(
                    source_root
                )
                proof_tree_at_completion = provenance.materialized_tree_identity(
                    proof_artifact_root
                )
                dependency_tree_at_completion = provenance.materialized_tree_identity(
                    dependency_root
                )
                dependency_module.assert_runtime_dependencies_unchanged(
                    dependency_binding,
                    source_root,
                )
            except (
                dependency_module.DependencyBindingError,
                OSError,
                RuntimeError,
                ValueError,
            ) as exc:
                raise ProductionCodeInventoryError(
                    "composite inventory snapshot stability could not be verified"
                ) from exc
            if source_tree_at_completion != source_tree_identity:
                raise ProductionCodeInventoryError(
                    "source snapshot changed while inventory sections were derived"
                )
            if proof_tree_at_completion != proof_tree_identity:
                raise ProductionCodeInventoryError(
                    "proof artifact snapshot changed while inventory sections were derived"
                )
            if dependency_tree_at_completion != dependency_tree_identity:
                raise ProductionCodeInventoryError(
                    "dependency snapshot changed while inventory sections were derived"
                )
            final_live_source = live_provenance.repository_provenance(
                live_root,
                exclude_paths=exclusions,
            )
            if final_live_source != initial_live_source:
                raise ProductionCodeInventoryError(
                    "live source changed while inventory sections were derived"
                )
            final_live_proof = _proof_artifact_identity(
                live_root,
                (identity for identity, _script in _PROOF_HARNESS_SCRIPTS),
            )
            if final_live_proof != proof_artifact_identity:
                raise ProductionCodeInventoryError(
                    "live proof artifacts changed while inventory sections were derived"
                )


def _proof_result_file(
    root: Path,
    record: Mapping[str, Any],
    *,
    label: str,
    expected_relative: str,
) -> tuple[str, str, int, bytes]:
    if not isinstance(record, Mapping) or set(record) != {"path", "bytes", "sha256"}:
        raise ProductionCodeInventoryError(f"{label} raw-output schema drifted")
    relative = record.get("path")
    byte_count = record.get("bytes")
    digest = record.get("sha256")
    if (
        not isinstance(relative, str)
        or not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or byte_count < 0
        or not isinstance(digest, str)
        or not _HEX64.fullmatch(digest)
    ):
        raise ProductionCodeInventoryError(f"{label} raw-output metadata is malformed")
    if relative != expected_relative:
        raise ProductionCodeInventoryError(f"{label} raw-output path drifted")
    raw = _read_exact_proof_file(
        root,
        expected_relative,
        maximum_bytes=MAX_PROOF_RAW_FILE_BYTES,
    )
    if len(raw) != byte_count or _sha256_bytes(raw) != digest:
        raise ProductionCodeInventoryError(f"{label} retained raw output drifted")
    return relative, digest, byte_count, raw


def _validate_generation_record(
    root: Path,
    *,
    manifest_raw: bytes,
    results_raw: bytes,
    raw_outputs: Mapping[str, Mapping[str, bytes]],
) -> tuple[str, str]:
    generation_path = _repo_file(root, "proof/results/generation.json")
    generation_raw = _read_exact_proof_file(
        root,
        "proof/results/generation.json",
        maximum_bytes=MAX_PROOF_GENERATION_BYTES,
    )
    generation = _decode_json(
        generation_raw,
        label="proof generation commit marker",
    )
    if not isinstance(generation, dict) or set(generation) != {
        "schema",
        "manifest",
        "results",
        "raw_outputs",
        "limits",
        "observed",
        "publication",
    }:
        raise ProductionCodeInventoryError("proof generation marker schema drifted")
    if generation["schema"] != "lightwork-proof-generation.v2":
        raise ProductionCodeInventoryError("proof generation marker identity drifted")
    canonical = (
        json.dumps(
            generation,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    if canonical != generation_raw:
        raise ProductionCodeInventoryError(
            "proof generation marker is not canonical UTF-8 JSON with LF termination"
        )

    def validate_file_record(
        value: object,
        *,
        expected_path: str,
        payload: bytes,
        label: str,
    ) -> None:
        if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
            raise ProductionCodeInventoryError(f"{label} generation record drifted")
        if (
            value["path"] != expected_path
            or not isinstance(value["bytes"], int)
            or isinstance(value["bytes"], bool)
            or value["bytes"] != len(payload)
            or value["sha256"] != _sha256_bytes(payload)
        ):
            raise ProductionCodeInventoryError(f"{label} generation binding drifted")

    validate_file_record(
        generation["manifest"],
        expected_path="proof/results/manifest.json",
        payload=manifest_raw,
        label="proof manifest",
    )
    validate_file_record(
        generation["results"],
        expected_path="proof/RESULTS.md",
        payload=results_raw,
        label="proof RESULTS",
    )
    expected_raw: list[tuple[str, bytes]] = []
    raw_total = 0
    for harness in _PROOF_HARNESS_CONTRACT:
        identity = str(harness["id"])
        streams = raw_outputs[identity]
        for stream in ("stdout", "stderr"):
            raw_total += len(streams[stream])
            expected_raw.append(
                (f"proof/results/raw/{identity}.{stream}.txt", streams[stream])
            )
    raw_records = generation["raw_outputs"]
    if not isinstance(raw_records, list) or len(raw_records) != len(expected_raw):
        raise ProductionCodeInventoryError("proof generation raw-output inventory drifted")
    for record, (expected_path, payload) in zip(raw_records, expected_raw, strict=True):
        validate_file_record(
            record,
            expected_path=expected_path,
            payload=payload,
            label=expected_path,
        )
    expected_limits = {
        "harness_timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
        "raw_stream_max_bytes": MAX_PROOF_RAW_FILE_BYTES,
        "raw_output_total_max_bytes": MAX_PROOF_RAW_TOTAL_BYTES,
        "results_max_bytes": MAX_PROOF_RESULTS_BYTES,
        "manifest_max_bytes": MAX_PROOF_MANIFEST_BYTES,
        "generation_max_bytes": MAX_PROOF_GENERATION_BYTES,
        "artifact_tree_max_bytes": MAX_PROOF_TREE_BYTES,
    }
    if generation["limits"] != expected_limits:
        raise ProductionCodeInventoryError("proof generation limit contract drifted")
    if generation["observed"] != {
        "raw_output_total_bytes": raw_total,
        "manifest_bytes": len(manifest_raw),
        "results_bytes": len(results_raw),
    }:
        raise ProductionCodeInventoryError("proof generation observed byte counts drifted")
    if generation["publication"] != (
        "staged complete artifact set; raw streams, manifest, and RESULTS publish first; "
        "this generation record publishes last and is the fail-closed commit marker"
    ):
        raise ProductionCodeInventoryError("proof generation publication boundary drifted")
    return generation_path.relative_to(root).as_posix(), _sha256_bytes(generation_raw)


def _finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProductionCodeInventoryError(f"{label} is not numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ProductionCodeInventoryError(f"{label} is not finite")
    return normalized


def _validated_command_argv(
    value: object,
    *,
    label: str,
    expected: list[str] | None = None,
) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"argv", "display"}:
        raise ProductionCodeInventoryError(f"{label} schema drifted")
    argv = value["argv"]
    display = value["display"]
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(token, str) and token for token in argv)
        or not isinstance(display, str)
        or not display
    ):
        raise ProductionCodeInventoryError(f"{label} is malformed")
    if expected is not None and argv != expected:
        raise ProductionCodeInventoryError(f"{label} argv drifted")
    if display not in {shlex.join(argv), subprocess.list2cmdline(argv)}:
        raise ProductionCodeInventoryError(f"{label} display is not canonical")
    return argv


def _recorded_path_is_absolute(value: object, *, platform_name: str) -> bool:
    """Validate an evidence path using the platform that produced the record."""
    if not isinstance(value, str) or not value or "\0" in value:
        return False
    if platform_name.casefold().startswith("windows"):
        return PureWindowsPath(value).is_absolute()
    return PurePosixPath(value).is_absolute()


def _validate_runtime_provenance(value: object) -> str:
    keys = {
        "python_executable",
        "python_version",
        "python_implementation",
        "python_build",
        "platform",
        "machine",
        "dependencies",
        "dependency_count",
        "dependencies_sha256",
        "dependency_digest_scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ProductionCodeInventoryError("proof runtime provenance schema drifted")
    dependencies = value["dependencies"]
    if (
        not isinstance(value["python_executable"], str)
        or not value["python_executable"]
        or not all(
            isinstance(value[name], str) and value[name]
            for name in ("python_version", "python_implementation", "platform", "machine")
        )
        or not isinstance(value["python_build"], list)
        or not all(isinstance(item, str) for item in value["python_build"])
        or not isinstance(dependencies, list)
        or not all(
            isinstance(item, dict)
            and set(item) == {"name", "version"}
            and isinstance(item["name"], str)
            and isinstance(item["version"], str)
            for item in dependencies
        )
        or dependencies
        != sorted(dependencies, key=lambda item: (item["name"].casefold(), item["version"]))
        or not isinstance(value["dependency_count"], int)
        or isinstance(value["dependency_count"], bool)
        or value["dependency_count"] != len(dependencies)
        or value["dependencies_sha256"] != _sha256_bytes(_canonical_bytes(dependencies))
        or value["dependency_digest_scope"]
        != "all distributions visible to importlib.metadata"
    ):
        raise ProductionCodeInventoryError("proof runtime provenance is malformed")
    return str(value["dependencies_sha256"])


def _proof_inventory(snapshot: InventorySnapshot) -> dict[str, Any]:  # noqa: C901
    root = snapshot.source_root
    artifact_root = snapshot.proof_artifact_root
    proof_root = _absolute_lexical(root / "proof")
    if not proof_root.is_dir():
        raise ProductionCodeInventoryError("proof directory is missing")
    paths = sorted(
        (path for path in proof_root.glob("*_proof.py") if path.is_file()),
        key=lambda path: path.name,
    )
    run_path = _repo_file(root, "proof/run_proof.py")
    if run_path not in paths:
        paths.append(run_path)
        paths.sort(key=lambda path: path.name)
    rows = [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _sha256_source_text(path),
            "digest_mode": SOURCE_TEXT_DIGEST_MODE,
        }
        for path in paths
    ]
    results_path = _repo_file(artifact_root, "proof/RESULTS.md")
    expected_scripts = dict(_PROOF_HARNESS_SCRIPTS)
    _validate_exact_proof_tree(artifact_root, expected_scripts)
    results_raw = _read_exact_proof_file(
        artifact_root,
        "proof/RESULTS.md",
        maximum_bytes=MAX_PROOF_RESULTS_BYTES,
    )
    try:
        results_raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProductionCodeInventoryError("proof RESULTS is not strict UTF-8") from exc
    manifest_path = _repo_file(artifact_root, "proof/results/manifest.json")
    manifest_raw = _read_exact_proof_file(
        artifact_root,
        "proof/results/manifest.json",
        maximum_bytes=MAX_PROOF_MANIFEST_BYTES,
    )
    manifest = _decode_json(
        manifest_raw,
        label="source-content-bound proof results manifest",
    )
    try:
        canonical_manifest = (
            json.dumps(
                manifest,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProductionCodeInventoryError(
            "proof results manifest is not canonical JSON"
        ) from exc
    if manifest_raw != canonical_manifest:
        raise ProductionCodeInventoryError(
            "proof results manifest is not canonical UTF-8 JSON with LF termination"
        )
    if not isinstance(manifest, dict) or set(manifest) != {
        "kind",
        "version",
        "generated_at",
        "generated_at_utc",
        "canonical_generator_command",
        "generator_command",
        "execution_mode",
        "evidence_valid",
        "evidence_valid_scope",
        "production_release_evidence",
        "execution_limits",
        "environment_overrides",
        "execution_environment",
        "interpreter_boundary",
        "source_collection_boundary",
        "source_execution_tree",
        "source",
        "source_at_completion",
        "source_stable",
        "runtime",
        "runs",
        "all_commands_passed",
        "evidence_boundary",
    }:
        raise ProductionCodeInventoryError("source-content-bound proof manifest schema drifted")
    if (
        manifest["kind"] != "lightwork-source-content-bound-fixture-results"
        or manifest["version"] != 2
    ):
        raise ProductionCodeInventoryError("source-content-bound proof manifest identity drifted")
    source = manifest["source"]
    source_at_completion = manifest["source_at_completion"]
    runtime = manifest["runtime"]
    runs = manifest["runs"]
    if not isinstance(source, dict) or source.get("available") is not True:
        raise ProductionCodeInventoryError("proof results are not bound to a Git source state")
    if manifest["source_stable"] is not True or source_at_completion != source:
        raise ProductionCodeInventoryError(
            "source worktree changed while source-bound proof results were executing"
        )
    if (
        not isinstance(source.get("revision"), str)
        or not (
            _HEX40.fullmatch(source["revision"])
            or _HEX64.fullmatch(source["revision"])
        )
        or not isinstance(source.get("dirty"), bool)
        or not isinstance(source.get("worktree_state_sha256"), str)
        or not _HEX64.fullmatch(source["worktree_state_sha256"])
        or not isinstance(source.get("excluded_paths"), list)
    ):
        raise ProductionCodeInventoryError("proof source provenance is malformed")
    provenance = snapshot.provenance
    expected_exclusions = provenance.proof_source_exclusions(
        root,
        generated_paths=(root / "proof/RESULTS.md", root / "proof/results"),
    )
    if source["excluded_paths"] != expected_exclusions:
        raise ProductionCodeInventoryError(
            "proof source exclusions are not the exact generated-artifact allow-list"
        )
    try:
        recorded_source_content = provenance.source_content_identity(source)
    except ValueError as exc:
        raise ProductionCodeInventoryError(str(exc)) from exc
    current_source_content = dict(snapshot.source_content)
    if current_source_content != recorded_source_content:
        raise ProductionCodeInventoryError(
            "source-content-bound proof results do not match current source content"
        )
    if set(source) != set(snapshot.live_source_provenance):
        raise ProductionCodeInventoryError("proof source provenance schema drifted")
    environment_valid, environment_reason = (
        provenance.validate_fixture_environment_provenance(
            manifest["execution_environment"],
            manifest["environment_overrides"],
        )
    )
    if not environment_valid:
        raise ProductionCodeInventoryError(environment_reason)
    execution_environment = manifest["execution_environment"]
    runtime_dependencies_sha256 = _validate_runtime_provenance(runtime)
    if (
        manifest["evidence_valid"] is not True
        or manifest["evidence_valid_scope"]
        != "internally consistent source-content-bound local fixture execution only"
        or manifest["production_release_evidence"] is not False
        or manifest["execution_mode"]
        != {
            "id": "canonical-isolated-cli-v2",
            "canonical_cli_verified": True,
            "runner_injected": False,
            "timestamp_injected": False,
        }
    ):
        raise ProductionCodeInventoryError("proof evidence-valid execution boundary drifted")

    generator = _load_results_generator(root)
    generator_limits = {
        "harness_timeout_seconds": generator.PROOF_HARNESS_TIMEOUT_SECONDS,
        "raw_stream_max_bytes": generator.PROOF_RAW_STREAM_MAX_BYTES,
        "raw_output_total_max_bytes": generator.PROOF_RAW_OUTPUT_TOTAL_MAX_BYTES,
        "results_max_bytes": generator.PROOF_RESULTS_MAX_BYTES,
        "manifest_max_bytes": generator.PROOF_MANIFEST_MAX_BYTES,
        "generation_max_bytes": generator.PROOF_GENERATION_MAX_BYTES,
        "artifact_tree_max_bytes": generator.PROOF_ARTIFACT_TREE_MAX_BYTES,
        "source_proof_file_max_bytes": generator.PROOF_SOURCE_FILE_MAX_BYTES,
        "source_proof_tree_max_bytes": generator.PROOF_SOURCE_TREE_MAX_BYTES,
    }
    if generator_limits != {
        "harness_timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
        "raw_stream_max_bytes": MAX_PROOF_RAW_FILE_BYTES,
        "raw_output_total_max_bytes": MAX_PROOF_RAW_TOTAL_BYTES,
        "results_max_bytes": MAX_PROOF_RESULTS_BYTES,
        "manifest_max_bytes": MAX_PROOF_MANIFEST_BYTES,
        "generation_max_bytes": MAX_PROOF_GENERATION_BYTES,
        "artifact_tree_max_bytes": MAX_PROOF_TREE_BYTES,
        "source_proof_file_max_bytes": MAX_PROOF_SOURCE_FILE_BYTES,
        "source_proof_tree_max_bytes": MAX_PROOF_SOURCE_TREE_BYTES,
    }:
        raise ProductionCodeInventoryError("proof generator/verifier limits drifted")
    execution_limits = manifest["execution_limits"]
    if not isinstance(execution_limits, dict) or set(execution_limits) != {
        *generator_limits,
        "process_tree_termination",
        "raw_output_total_bytes",
    }:
        raise ProductionCodeInventoryError("proof manifest execution-limit schema drifted")
    if any(execution_limits[name] != value for name, value in generator_limits.items()):
        raise ProductionCodeInventoryError("proof manifest execution limits drifted")
    if execution_limits["process_tree_termination"] != (
        "Linux subreaper PID/start-time lifecycle seal; Windows suspended-spawn "
        "kill-on-close Job Object lifecycle seal"
    ):
        raise ProductionCodeInventoryError("proof process-tree boundary drifted")
    raw_output_total_bytes = execution_limits["raw_output_total_bytes"]
    if (
        not isinstance(raw_output_total_bytes, int)
        or isinstance(raw_output_total_bytes, bool)
        or not 0 <= raw_output_total_bytes <= MAX_PROOF_RAW_TOTAL_BYTES
    ):
        raise ProductionCodeInventoryError("proof observed raw-output byte count is malformed")

    canonical_base = [
        "python",
        "-I",
        "-S",
        "-B",
        "-X",
        "utf8",
        "-W",
        "default",
        "proof/generate_results.py",
    ]
    canonical_argv = _validated_command_argv(
        manifest["canonical_generator_command"],
        label="canonical proof generator command",
    )
    if canonical_argv not in (canonical_base, [*canonical_base, "--require-pass"]):
        raise ProductionCodeInventoryError("canonical proof generator argv drifted")
    generator_argv = _validated_command_argv(
        manifest["generator_command"],
        label="observed proof generator command",
    )
    if generator_argv[1:] != canonical_argv[1:]:
        raise ProductionCodeInventoryError("observed proof generator argv drifted")

    interpreter = manifest["interpreter_boundary"]
    if not isinstance(interpreter, dict) or set(interpreter) != {
        "entrypoint_argv_prefix",
        "runtime_executable",
        "entrypoint_site_initialization",
        "runtime_imports",
        "runtime_paths_added_without_pth_processing",
        "isolated_entrypoint_ignored_environment_names",
        "descendant_subprocess_isolation_complete",
        "descendant_subprocess_boundary",
        "runtime_binary_hash_to_execution_atomic",
        "runtime_binary_boundary",
    }:
        raise ProductionCodeInventoryError("proof interpreter-boundary schema drifted")
    entrypoint_prefix = ["python", "-I", "-S", "-B", "-X", "utf8", "-W", "default"]
    runtime_executable = interpreter["runtime_executable"]
    if not isinstance(runtime_executable, dict) or set(runtime_executable) != {
        "path",
        "sha256",
        "version",
        "implementation",
        "outside_repository",
        "digest_scope",
    }:
        raise ProductionCodeInventoryError("proof runtime executable schema drifted")
    runtime_path = runtime_executable.get("path")
    recorded_platform = str(runtime["platform"])
    if (
        interpreter["entrypoint_argv_prefix"] != entrypoint_prefix
        or interpreter["entrypoint_site_initialization"] != "disabled with -S"
        or interpreter["runtime_paths_added_without_pth_processing"] is not True
        or interpreter["isolated_entrypoint_ignored_environment_names"]
        != list(generator._ISOLATED_IGNORED_PYTHON_ENV)
        or interpreter["descendant_subprocess_isolation_complete"] is not False
        or interpreter["descendant_subprocess_boundary"]
        != (
            "trusted-host seam; lifecycle sealing covers descendants, but same-UID "
            "hostile code can attack the verifier, and harness-created Python/pytest/"
            "solver subprocesses are not all routed through the isolated bootstrap"
        )
        or interpreter["runtime_binary_hash_to_execution_atomic"] is not False
        or interpreter["runtime_binary_boundary"]
        != (
            "trusted-host executable; exact bytes are hashed before execution, but the "
            "operating system path lookup/open is not handle-pinned across every launch"
        )
        or not _recorded_path_is_absolute(
            runtime_path,
            platform_name=recorded_platform,
        )
        or runtime_path != runtime["python_executable"]
        or generator_argv[0] != runtime_path
        or not isinstance(runtime_executable.get("sha256"), str)
        or _HEX64.fullmatch(runtime_executable["sha256"]) is None
        or not isinstance(runtime_executable.get("version"), str)
        or not runtime_executable["version"]
        or not isinstance(runtime_executable.get("implementation"), str)
        or not runtime_executable["implementation"]
        or runtime_executable["outside_repository"] is not True
        or runtime_executable["digest_scope"]
        != "exact Python executable bytes and runtime version"
    ):
        raise ProductionCodeInventoryError("proof interpreter boundary drifted")

    runtime_imports = interpreter["runtime_imports"]
    if not isinstance(runtime_imports, dict) or set(runtime_imports) != {
        "schema",
        "paths",
        "admissions",
        "sha256",
        "digest_scope",
    }:
        raise ProductionCodeInventoryError("proof runtime-import control schema drifted")
    import_paths = runtime_imports["paths"]
    admissions = runtime_imports["admissions"]
    if (
        runtime_imports["schema"] != generator._RUNTIME_IMPORT_PATHS_SCHEMA
        or not isinstance(import_paths, list)
        or len(import_paths) != len(set(import_paths))
        or not all(
            _recorded_path_is_absolute(path, platform_name=recorded_platform)
            for path in import_paths
        )
        or not isinstance(admissions, list)
        or not all(
            isinstance(item, dict)
            and set(item) == {"path", "reason"}
            and isinstance(item["path"], str)
            and isinstance(item["reason"], str)
            and item["reason"]
            for item in admissions
        )
        or [item["path"] for item in admissions] != import_paths
        or runtime_imports["sha256"]
        != _sha256_bytes(
            _canonical_bytes(
                {"schema": runtime_imports["schema"], "paths": import_paths}
            )
        )
        or runtime_imports["digest_scope"]
        != "exact canonical JSON runtime import-path control bytes"
    ):
        raise ProductionCodeInventoryError("proof runtime-import control drifted")

    source_collection = manifest["source_collection_boundary"]
    if source_collection != {
        "git_binary_trust": "trusted-host",
        "git_binary": source.get("source_content_git_diagnostics", {}).get("runtime"),
        "git_binary_hash_to_execution_atomic": False,
        "worktree_control": "local regular .git directory; indirection rejected",
        "global_and_system_git_configuration": "suppressed",
    }:
        raise ProductionCodeInventoryError("proof source-collection boundary drifted")

    source_execution_tree = manifest["source_execution_tree"]
    if not isinstance(source_execution_tree, dict) or set(source_execution_tree) != {
        "local_identity_schema",
        "local_materialized_tree_identity_enforced",
        "local_materialized_tree_identity_serialized",
        "read_only_transition_identity_preserved",
        "symlink_materialization_policy",
        "semantic_identity",
        "required_regular_paths",
        "required_regular_paths_sha256",
        "exact_proof_paths",
        "exact_proof_paths_sha256",
        "mutation_checks",
    }:
        raise ProductionCodeInventoryError("proof source-execution-tree schema drifted")
    required_regular_paths = list(generator._REQUIRED_BOUND_SOURCE_PATHS)
    exact_proof_paths = sorted(
        f"proof/{name}" for name in generator._MATERIALIZED_PROOF_FILES
    )
    if (
        source_execution_tree["local_identity_schema"]
        != provenance.MATERIALIZED_TREE_IDENTITY_SCHEMA
        or source_execution_tree["local_materialized_tree_identity_enforced"] is not True
        or source_execution_tree["local_materialized_tree_identity_serialized"] is not False
        or source_execution_tree["read_only_transition_identity_preserved"] is not True
        or source_execution_tree["symlink_materialization_policy"]
        != provenance.SYMLINK_MATERIALIZATION_POLICY
        or source_execution_tree["semantic_identity"]
        != provenance.semantic_execution_tree_identity(
            recorded_source_content,
            symlink_materialization_policy=(
                provenance.SYMLINK_MATERIALIZATION_POLICY
            ),
        )
        or source_execution_tree["required_regular_paths"] != required_regular_paths
        or source_execution_tree["required_regular_paths_sha256"]
        != _sha256_bytes(_canonical_bytes(required_regular_paths))
        or source_execution_tree["exact_proof_paths"] != exact_proof_paths
        or source_execution_tree["exact_proof_paths_sha256"]
        != _sha256_bytes(_canonical_bytes(exact_proof_paths))
        or source_execution_tree["mutation_checks"]
        != "before and after every harness"
    ):
        raise ProductionCodeInventoryError("proof source-execution-tree binding drifted")

    generator_contract = tuple(
        {
            "id": harness.id,
            "title": harness.title,
            "script": harness.script,
            "prerequisites": tuple(harness.prerequisites),
            "limitations": tuple(harness.limitations),
        }
        for harness in generator.HARNESSES
    )
    if generator_contract != _PROOF_HARNESS_CONTRACT:
        raise ProductionCodeInventoryError("proof generator/verifier harness contract drifted")
    if not isinstance(runs, list) or len(runs) != len(expected_scripts):
        raise ProductionCodeInventoryError("source-content-bound proof run inventory drifted")
    expected_run_ids = [str(item["id"]) for item in _PROOF_HARNESS_CONTRACT]
    if [run.get("id") if isinstance(run, dict) else None for run in runs] != expected_run_ids:
        raise ProductionCodeInventoryError("proof run order or identity drifted")
    retained_outputs: list[dict[str, Any]] = []
    raw_outputs: dict[str, dict[str, bytes]] = {}
    run_keys = {
        "id",
        "title",
        "command",
        "execution_command",
        "started_at",
        "started_at_utc",
        "completed_at",
        "completed_at_utc",
        "duration_seconds",
        "exit_code",
        "status",
        "skip_marker_observed",
        "timed_out",
        "output_limit_exceeded",
        "termination_reason",
        "execution_limits",
        "stdout",
        "stderr",
        "raw_output_sha256",
        "raw_output_digest_scope",
        "prerequisites",
        "limitations",
    }
    observed_raw_total = 0
    for run, contract in zip(runs, _PROOF_HARNESS_CONTRACT, strict=True):
        if not isinstance(run, dict) or set(run) != run_keys:
            raise ProductionCodeInventoryError("source-content-bound proof run is malformed")
        identity = str(contract["id"])
        script = str(contract["script"])
        if (
            run["id"] != identity
            or run["title"] != contract["title"]
            or run["prerequisites"] != list(contract["prerequisites"])
            or run["limitations"] != list(contract["limitations"])
        ):
            raise ProductionCodeInventoryError(f"proof run contract drifted: {identity}")
        logical_argv = [
            *entrypoint_prefix,
            "proof/isolated_harness_bootstrap.py",
            script,
        ]
        execution_argv = [runtime_path, *logical_argv[1:]]
        _validated_command_argv(
            run["command"],
            label=f"proof logical command {identity}",
            expected=logical_argv,
        )
        _validated_command_argv(
            run["execution_command"],
            label=f"proof execution command {identity}",
            expected=execution_argv,
        )
        started_at = _finite_number(run["started_at"], label=f"{identity} started_at")
        completed_at = _finite_number(
            run["completed_at"], label=f"{identity} completed_at"
        )
        duration = _finite_number(
            run["duration_seconds"], label=f"{identity} duration_seconds"
        )
        exit_code = run["exit_code"]
        termination_reason = run["termination_reason"]
        if (
            completed_at < started_at
            or duration != max(0.0, completed_at - started_at)
            or run["started_at_utc"] != provenance.utc_timestamp(started_at)
            or run["completed_at_utc"] != provenance.utc_timestamp(completed_at)
            or not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or not -(2**31) <= exit_code < 2**31
            or run["status"] not in {"passed", "failed", "skipped"}
            or not isinstance(run["skip_marker_observed"], bool)
            or not isinstance(run["timed_out"], bool)
            or not isinstance(run["output_limit_exceeded"], bool)
            or not (
                termination_reason is None
                or isinstance(termination_reason, str)
                and termination_reason
                in {
                    "timeout",
                    "output_limit:stdout",
                    "output_limit:stderr",
                    "output_limit:stderr,stdout",
                    "output_reader_failure",
                    "descendant_pipe_drain_timeout",
                    "process_tree_control_unavailable",
                    "process_tree_verification_failed",
                    "descendant_process_survived",
                }
            )
            or run["timed_out"] != (termination_reason == "timeout")
            or run["output_limit_exceeded"]
            != bool(
                isinstance(termination_reason, str)
                and termination_reason.startswith("output_limit:")
            )
            or run["execution_limits"]
            != {
                "timeout_seconds": PROOF_HARNESS_TIMEOUT_SECONDS,
                "stdout_max_bytes": MAX_PROOF_RAW_FILE_BYTES,
                "stderr_max_bytes": MAX_PROOF_RAW_FILE_BYTES,
            }
        ):
            raise ProductionCodeInventoryError(f"proof execution provenance drifted: {identity}")
        stdout_path, stdout_sha256, stdout_bytes, stdout_raw = _proof_result_file(
            artifact_root,
            run.get("stdout"),
            label=f"{identity} stdout",
            expected_relative=f"proof/results/raw/{identity}.stdout.txt",
        )
        stderr_path, stderr_sha256, stderr_bytes, stderr_raw = _proof_result_file(
            artifact_root,
            run.get("stderr"),
            label=f"{identity} stderr",
            expected_relative=f"proof/results/raw/{identity}.stderr.txt",
        )
        observed_raw_total += stdout_bytes + stderr_bytes
        skip_marker_observed = any(
            line.lstrip().startswith(b"[SKIP]")
            for payload in (stdout_raw, stderr_raw)
            for line in payload.splitlines()
        )
        machine_failure = (
            run["timed_out"]
            or run["output_limit_exceeded"]
            or termination_reason is not None
        )
        if not machine_failure and exit_code == 0 and not skip_marker_observed:
            expected_status = "passed"
        elif not machine_failure and exit_code == 2 and skip_marker_observed:
            expected_status = "skipped"
        else:
            expected_status = "failed"
        if (
            run["skip_marker_observed"] is not skip_marker_observed
            or run["status"] != expected_status
        ):
            raise ProductionCodeInventoryError(f"proof status semantics drifted: {identity}")
        combined = {
            "exit_code": exit_code,
            "status": run["status"],
            "timed_out": run["timed_out"],
            "output_limit_exceeded": run["output_limit_exceeded"],
            "termination_reason": termination_reason,
            "stdout_sha256": stdout_sha256,
            "stderr_sha256": stderr_sha256,
        }
        if (
            not isinstance(run.get("raw_output_sha256"), str)
            or run.get("raw_output_sha256") != _sha256_bytes(_canonical_bytes(combined))
            or run.get("raw_output_digest_scope")
            != (
                "status, machine termination fields, exit code, and exact retained bounded "
                "stdout/stderr byte digests"
            )
        ):
            raise ProductionCodeInventoryError(f"proof raw-output digest drifted: {identity}")
        retained_outputs.append(
            {
                "id": identity,
                "exit_code": exit_code,
                "status": run["status"],
                "skip_marker_observed": run["skip_marker_observed"],
                "timed_out": run["timed_out"],
                "output_limit_exceeded": run["output_limit_exceeded"],
                "termination_reason": termination_reason,
                "stdout_path": stdout_path,
                "stdout_sha256": stdout_sha256,
                "stdout_bytes": stdout_bytes,
                "stderr_path": stderr_path,
                "stderr_sha256": stderr_sha256,
                "stderr_bytes": stderr_bytes,
                "raw_output_sha256": run["raw_output_sha256"],
            }
        )
        raw_outputs[identity] = {"stdout": stdout_raw, "stderr": stderr_raw}
    if observed_raw_total != raw_output_total_bytes:
        raise ProductionCodeInventoryError("proof aggregate raw-output byte count drifted")
    if manifest["all_commands_passed"] is not all(
        run["status"] == "passed" for run in runs
    ):
        raise ProductionCodeInventoryError("proof aggregate result drifted")
    generated_at = _finite_number(manifest.get("generated_at"), label="proof generated_at")
    expected_evidence_boundary = [
        "source-content-bound local regression and prerequisite evidence only",
        "fixture/stub/oracle/trusted-host limitations remain authoritative",
        "not competitive capability, customer ROI, production, or release evidence",
        (
            "fixture children inherit only the recorded system allow-list; ambient app, "
            "provider, Python-path, and pytest injection variables are discarded"
        ),
        (
            "isolated no-site startup covers each top-level harness only; nested host "
            "subprocesses remain an explicit trusted-host limitation"
        ),
    ]
    if (
        manifest.get("generated_at_utc") != provenance.utc_timestamp(generated_at)
        or manifest.get("evidence_boundary") != expected_evidence_boundary
    ):
        raise ProductionCodeInventoryError("proof execution boundary is malformed")
    manifest_sha256 = _sha256_bytes(manifest_raw)
    generation_path, generation_sha256 = _validate_generation_record(
        artifact_root,
        manifest_raw=manifest_raw,
        results_raw=results_raw,
        raw_outputs=raw_outputs,
    )
    try:
        rendered = generator.render_results_markdown(
            manifest,
            raw_outputs,
            manifest_path="proof/results/manifest.json",
            manifest_sha256=manifest_sha256,
        )
        rendered_raw = rendered.encode("utf-8", errors="strict")
    except (AttributeError, TypeError, UnicodeError, ValueError) as exc:
        raise ProductionCodeInventoryError("proof RESULTS could not be re-rendered") from exc
    if rendered_raw != results_raw:
        raise ProductionCodeInventoryError(
            "proof RESULTS differs from the deterministic manifest/raw rendering"
        )
    return {
        "harness_count": len(rows),
        "harnesses": rows,
        "harness_source_sha256": _digest_rows(rows),
        "harness_source_digest_mode": CANONICAL_JSON_DIGEST_MODE,
        "results_path": results_path.relative_to(artifact_root).as_posix(),
        "results_sha256": _sha256_exact_file(results_path),
        "results_digest_mode": EXACT_BYTE_DIGEST_MODE,
        "manifest_path": manifest_path.relative_to(artifact_root).as_posix(),
        "manifest_sha256": manifest_sha256,
        "manifest_digest_mode": EXACT_BYTE_DIGEST_MODE,
        "generation_path": generation_path,
        "generation_sha256": generation_sha256,
        "generation_digest_mode": EXACT_BYTE_DIGEST_MODE,
        "freshness_status": "source_content_bound_local",
        "assessed_source_revision": source["revision"],
        "execution_timestamp": manifest["generated_at_utc"],
        "worktree_dirty": source["dirty"],
        "worktree_state_sha256": source["worktree_state_sha256"],
        "source_content": recorded_source_content,
        "semantic_execution_tree_identity": source_execution_tree[
            "semantic_identity"
        ],
        "runtime_dependencies_sha256": runtime_dependencies_sha256,
        "execution_environment_policy_sha256": execution_environment[
            "policy_sha256"
        ],
        "execution_environment_sha256": execution_environment[
            "environment_sha256"
        ],
        "all_commands_passed": manifest["all_commands_passed"],
        "retained_outputs": retained_outputs,
        "current_source_content_bound": True,
        "production_release_evidence": False,
    }


def _validate_documentation(root: Path, inventory: Mapping[str, Any]) -> None:
    question_count = inventory["installer_questions"]["consumer_question_count"]
    if question_count != 5:
        raise ProductionCodeInventoryError("consumer installer question count is not five")
    required_question_claims = {
        "README.md": "five questions, safe defaults",
        "docs/handbook.md": "wizard: five questions, safe defaults",
        "apps/installer-cli/README.md": "five questions (name, governance level, API key",
    }
    for relative, marker in required_question_claims.items():
        text = _read_utf8_source(
            _repo_file(root, relative),
            label=f"installer documentation {relative}",
        ).lower()
        if marker.lower() not in text:
            raise ProductionCodeInventoryError(
                f"installer question documentation drifted: {relative}"
            )

    tool_names = set(inventory["mcp_tools"]["tools"])
    claims: list[tuple[str, int, int]] = []
    for base in (root / "docs" / "clients", root / "examples" / "clients"):
        if not base.is_dir():
            raise ProductionCodeInventoryError(f"MCP client documentation is missing: {base}")
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {
                ".cs",
                ".go",
                ".java",
                ".js",
                ".json",
                ".md",
                ".mjs",
                ".py",
                ".rs",
                ".toml",
                ".ts",
                ".xml",
                ".yaml",
                ".yml",
            }:
                continue
            if {"bin", "node_modules", "obj", "target"}.intersection(
                part.lower() for part in path.parts
            ):
                continue
            text = _read_utf8_source(
                path,
                label=f"MCP client documentation {path.relative_to(root).as_posix()}",
            )
            for match in _TOOL_COUNT_CLAIM.finditer(text):
                claims.append(
                    (
                        path.relative_to(root).as_posix(),
                        text.count("\n", 0, match.start()) + 1,
                        int(match.group("count")),
                    )
                )
    if not claims:
        raise ProductionCodeInventoryError("numeric MCP tool-count claim is missing")
    drift = [claim for claim in claims if claim[2] != len(tool_names)]
    if drift:
        raise ProductionCodeInventoryError(f"numeric MCP tool-count claim drifted: {drift[0]}")
    typescript = _read_utf8_source(
        _repo_file(root, "docs/clients/typescript-quickstart.md"),
        label="TypeScript MCP quickstart",
    )
    missing = sorted(name for name in tool_names if name not in typescript)
    if missing:
        raise ProductionCodeInventoryError(
            f"TypeScript MCP quickstart omits source tools: {missing}"
        )


def _collect_production_code_inventory_from_snapshot(
    snapshot: InventorySnapshot,
) -> dict[str, Any]:
    root = snapshot.source_root
    workspace = _read_toml(_repo_file(root, "pyproject.toml"), label="workspace")
    surface, surface_sha256 = _surface_document(root)
    source_version = surface.get("source_version")
    profile_id = surface.get("profile_id")
    if not isinstance(source_version, str) or not isinstance(profile_id, str):
        raise ProductionCodeInventoryError("production surface identity is incomplete")
    runtime = _runtime_inventory(snapshot)
    channels = _channels_inventory(root)
    constraints = surface.get("constraints")
    surfaces = surface.get("surfaces")
    if not isinstance(constraints, dict) or not isinstance(surfaces, list):
        raise ProductionCodeInventoryError("production surface constraints are malformed")
    admitted_channels = constraints.get("channels")
    admitted_connectors = constraints.get("connectors")
    if not isinstance(admitted_channels, list) or any(
        not isinstance(item, str) for item in admitted_channels
    ):
        raise ProductionCodeInventoryError("production channel constraints are malformed")
    if not isinstance(admitted_connectors, list) or any(
        not isinstance(item, str) for item in admitted_connectors
    ):
        raise ProductionCodeInventoryError("production connector constraints are malformed")
    if not set(admitted_channels).issubset(channels["registered_channels"]):
        raise ProductionCodeInventoryError("production profile names an unknown channel")
    if not set(admitted_connectors).issubset(runtime["connectors"]["all_source_connectors"]):
        raise ProductionCodeInventoryError("production profile names an unknown connector")
    if admitted_connectors and runtime["connectors"]["production_status"] == "unsupported":
        raise ProductionCodeInventoryError("unsupported connectors cannot enter production")
    channels["production_included"] = sorted(admitted_channels)
    runtime["connectors"]["production_included"] = sorted(admitted_connectors)

    mcp_rows = [
        row
        for row in surfaces
        if isinstance(row, dict)
        and row.get("id") in {"interface:mcp-stdio", "interface:mcp-http"}
    ]
    if len(mcp_rows) != 2 or any(not isinstance(row.get("included"), bool) for row in mcp_rows):
        raise ProductionCodeInventoryError("MCP production surfaces are not exact")
    mcp_included = any(bool(row["included"]) for row in mcp_rows)
    runtime["mcp"]["production_included"] = mcp_included
    runtime["mcp"]["production_tools"] = (
        list(runtime["mcp"]["tools"]) if mcp_included else []
    )
    proof = _proof_inventory(snapshot)
    if runtime["runtime_probe"]["bound_source_content"] != proof["source_content"]:
        raise ProductionCodeInventoryError(
            "runtime inventory and retained proof bind different source content"
        )
    if (
        runtime["runtime_probe"]["semantic_execution_tree_identity"]
        != proof["semantic_execution_tree_identity"]
    ):
        raise ProductionCodeInventoryError(
            "runtime inventory and retained proof bind different execution semantics"
        )
    inventory: dict[str, Any] = {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "source_version": source_version,
        "profile_id": profile_id,
        "digest_contract": dict(DIGEST_CONTRACT),
        "tests": _tests_inventory(root, workspace),
        "packages": _workspace_packages(root, workspace, surface),
        "dashboard_routes": runtime["routes"],
        "channels": channels,
        "mcp_tools": runtime["mcp"],
        "connectors": runtime["connectors"],
        "runtime_probe": runtime["runtime_probe"],
        "ports": _ports_inventory(root, channels),
        "installer_questions": _installer_inventory(root, surface),
        "feature_statuses": _feature_inventory(surface, surface_sha256),
        "proof_freshness": proof,
        "evidence_boundary": (
            "source-derived inventory only; it does not prove hosted CI, deployed "
            "interoperability, external service behavior, customer outcomes, or release eligibility"
        ),
    }
    _validate_documentation(root, inventory)
    return inventory


def collect_production_code_inventory(root: Path) -> dict[str, Any]:
    """Derive every section from one immutable source/proof/dependency snapshot."""

    with _production_inventory_snapshot(root) as snapshot:
        return _collect_production_code_inventory_from_snapshot(snapshot)


def _first_difference(expected: object, actual: object, path: str = "$") -> str | None:
    if type(expected) is not type(actual):
        return f"{path} type differs"
    if isinstance(expected, dict):
        expected_keys = set(expected)
        actual_keys = set(actual)
        if expected_keys != actual_keys:
            return (
                f"{path} keys differ: missing={sorted(expected_keys - actual_keys)}, "
                f"unknown={sorted(actual_keys - expected_keys)}"
            )
        for key in expected:
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference is not None:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} length differs: expected {len(expected)}, got {len(actual)}"
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual, strict=True)):
            difference = _first_difference(expected_item, actual_item, f"{path}[{index}]")
            if difference is not None:
                return difference
        return None
    if expected != actual:
        return f"{path} differs: expected {expected!r}, got {actual!r}"
    return None


def validate_inventory_document(expected: Mapping[str, Any], actual: object) -> None:
    """Reject every omission, addition, reordering, or stale derived value."""
    if not isinstance(actual, dict):
        raise ProductionCodeInventoryError("production code inventory root must be an object")
    difference = _first_difference(expected, actual)
    if difference is not None:
        raise ProductionCodeInventoryError(f"production code inventory drifted: {difference}")


def verify_production_code_inventory(
    root: Path,
    *,
    inventory_path: Path | None = None,
) -> dict[str, object]:
    root = _absolute_lexical(root)
    expected = collect_production_code_inventory(root)
    path = inventory_path or root / DEFAULT_PATH
    if not path.is_absolute():
        path = root / path
    actual = _load_json(path, label="production code inventory")
    validate_inventory_document(expected, actual)
    return {
        "repository": REPOSITORY,
        "source_version": expected["source_version"],
        "test_file_count": expected["tests"]["file_count"],
        "declared_test_function_count": expected["tests"]["declared_test_function_count"],
        "workspace_package_count": expected["packages"]["workspace_member_count"],
        "production_package_count": expected["packages"]["production_included_count"],
        "dashboard_route_count": expected["dashboard_routes"]["route_count"],
        "production_route_count": expected["dashboard_routes"]["production_admitted_count"],
        "registered_channel_count": expected["channels"]["registered_channel_count"],
        "mcp_tool_count": expected["mcp_tools"]["tool_count"],
        "source_connector_count": expected["connectors"]["source_connector_count"],
        "production_connector_count": len(expected["connectors"]["production_included"]),
        "consumer_installer_question_count": expected["installer_questions"][
            "consumer_question_count"
        ],
        "proof_freshness_status": expected["proof_freshness"]["freshness_status"],
        "production_release_evidence": expected["proof_freshness"][
            "production_release_evidence"
        ],
    }


def _inventory_output_target(root: Path, path: Path | None) -> tuple[Path, Path]:
    root = _absolute_lexical(root)
    target = path or root / DEFAULT_PATH
    if not target.is_absolute():
        target = root / target
    target = _absolute_lexical(target)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise ProductionCodeInventoryError(
            "inventory output must stay inside repository"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ProductionCodeInventoryError(
            "inventory output must be a canonical file inside repository"
        )
    return target, relative


def _ensure_safe_output_parent(
    root: Path,
    relative_parent: Path,
) -> tuple[Path, list[tuple[Path, tuple[int, int, int, int]]]]:
    root = _absolute_lexical(root)
    identities = _safe_directory_chain(root, label="inventory output")
    current = root
    for part in relative_parent.parts:
        if part in {"", ".", ".."}:
            raise ProductionCodeInventoryError(
                "inventory output parent path is non-canonical"
            )
        candidate = current / part
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            _recheck_directory_identities(identities, label="inventory output")
            try:
                candidate.mkdir(mode=0o700)
                metadata = candidate.lstat()
            except OSError as exc:
                raise ProductionCodeInventoryError(
                    "inventory output parent cannot be created safely"
                ) from exc
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "inventory output parent cannot be inspected"
            ) from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(metadata)
        ):
            raise ProductionCodeInventoryError(
                "inventory output path contains a linked or non-directory ancestor"
            )
        current = candidate
        identities.append((current, _directory_identity(metadata)))
    return current, identities


def _existing_output_identity(path: Path) -> tuple[int, int, int, int, int, int, int] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ProductionCodeInventoryError(
            "inventory output target cannot be inspected"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse_point(metadata)
        or getattr(metadata, "st_nlink", 1) != 1
    ):
        raise ProductionCodeInventoryError(
            "inventory output target is not a single-link regular file"
        )
    return _file_identity(metadata)


def _atomic_write_inventory(  # noqa: C901
    root: Path,
    target: Path,
    payload: bytes,
) -> None:
    if len(payload) > MAX_INVENTORY_BYTES:
        raise ProductionCodeInventoryError("generated inventory exceeds its bounded size")
    _target, relative = _inventory_output_target(root, target)
    parent, parent_identities = _ensure_safe_output_parent(root, relative.parent)
    target = parent / relative.name
    target_before = _existing_output_identity(target)
    stage: Path | None = None
    descriptor: int | None = None
    stage_identity: tuple[int, int, int, int, int, int, int] | None = None
    try:
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        for _attempt in range(32):
            candidate = parent / f".{relative.name}.inventory-{secrets.token_hex(16)}.tmp"
            try:
                descriptor = os.open(candidate, flags, 0o600)
            except FileExistsError:
                continue
            except OSError as exc:
                raise ProductionCodeInventoryError(
                    "inventory staging file cannot be created safely"
                ) from exc
            stage = candidate
            break
        if descriptor is None or stage is None:
            raise ProductionCodeInventoryError(
                "inventory staging namespace is exhausted"
            )
        view = memoryview(payload)
        while view:
            try:
                written = os.write(descriptor, view)
            except OSError as exc:
                raise ProductionCodeInventoryError(
                    "inventory staging write failed"
                ) from exc
            if written <= 0:  # pragma: no cover - operating-system invariant
                raise ProductionCodeInventoryError(
                    "inventory staging write did not progress"
                )
            view = view[written:]
        try:
            os.fsync(descriptor)
            staged = os.fstat(descriptor)
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "inventory staging file could not be synchronized"
            ) from exc
        if (
            not stat.S_ISREG(staged.st_mode)
            or getattr(staged, "st_nlink", 1) != 1
            or staged.st_size != len(payload)
            or _is_reparse_point(staged)
        ):
            raise ProductionCodeInventoryError(
                "inventory staging file identity is unsafe"
            )
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            verified_chunks: list[bytes] = []
            verified_bytes = 0
            while verified_bytes <= len(payload):
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, len(payload) + 1 - verified_bytes),
                )
                if not chunk:
                    break
                verified_chunks.append(chunk)
                verified_bytes += len(chunk)
            staged_after_read = os.fstat(descriptor)
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "inventory staging file could not be verified"
            ) from exc
        if (
            b"".join(verified_chunks) != payload
            or verified_bytes != len(payload)
            or _file_identity(staged_after_read) != _file_identity(staged)
        ):
            raise ProductionCodeInventoryError(
                "inventory staging file bytes changed before publication"
            )
        stage_identity = _file_identity(staged)
        os.close(descriptor)
        descriptor = None

        _recheck_directory_identities(parent_identities, label="inventory output")
        target_at_publish = _existing_output_identity(target)
        if target_at_publish != target_before:
            raise ProductionCodeInventoryError(
                "inventory output target changed before atomic publication"
            )
        try:
            os.replace(stage, target)
            published = target.lstat()
        except OSError as exc:
            raise ProductionCodeInventoryError(
                "inventory output could not be published atomically"
            ) from exc
        if _file_identity(published) != stage_identity:
            raise ProductionCodeInventoryError(
                "inventory output target changed during atomic publication"
            )
        _recheck_directory_identities(parent_identities, label="inventory output")
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            directory_descriptor = os.open(parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            if os.name != "nt":
                raise ProductionCodeInventoryError(
                    "inventory output directory could not be synchronized"
                ) from exc
        _recheck_directory_identities(parent_identities, label="inventory output")
        if _existing_output_identity(target) != stage_identity:
            raise ProductionCodeInventoryError(
                "inventory output target changed after atomic publication"
            )
        stage = None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if stage is not None:
            try:
                if (
                    _existing_output_identity(stage) is not None
                    and _directory_identity(parent.lstat())
                    == parent_identities[-1][1]
                ):
                    stage.unlink()
            except (OSError, ProductionCodeInventoryError):
                pass


def write_production_code_inventory(root: Path, path: Path | None = None) -> Path:
    root = _absolute_lexical(root)
    target, _relative = _inventory_output_target(root, path)
    document = collect_production_code_inventory(root)
    payload = (
        json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode("utf-8")
    _atomic_write_inventory(
        root,
        target,
        payload,
    )
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--inventory", type=Path)
    parser.add_argument(
        "--write",
        action="store_true",
        help="regenerate the deterministic committed inventory before verifying it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.write:
            written = write_production_code_inventory(args.root, args.inventory)
            print(f"wrote {written}")
        summary = verify_production_code_inventory(
            args.root,
            inventory_path=args.inventory,
        )
    except ProductionCodeInventoryError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
