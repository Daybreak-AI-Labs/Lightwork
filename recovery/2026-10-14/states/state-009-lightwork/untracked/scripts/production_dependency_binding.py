#!/usr/bin/env python3
"""Portable, fail-closed binding for production-inventory Python dependencies.

The production code inventory deliberately starts Python with ``-I -S`` and
adds only reviewed external dependency roots.  This module binds the Python
source admitted from those roots to the repository's exact CI constraints.
Native extension bytes remain platform-specific and are therefore an explicit
version-and-host trust boundary rather than being represented as portable
source evidence.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType

SCHEMA = "lightwork.runtime-dependency-binding.v1"
SOURCE_DIGEST_MODE = "utf8-lf-sha256"
CONSTRAINTS_PATH = "requirements/ci.txt"
MAX_CONSTRAINTS_BYTES = 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_DEPENDENCY_FILE_BYTES = 4 * 1024 * 1024
MAX_DEPENDENCY_NATIVE_FILE_BYTES = 64 * 1024 * 1024
MAX_DEPENDENCY_SOURCE_BYTES = 32 * 1024 * 1024
MAX_DEPENDENCY_MATERIALIZED_BYTES = 128 * 1024 * 1024
MAX_DEPENDENCY_SOURCE_FILES = 4096
MAX_DEPENDENCY_ROOT_ENTRIES = 4096
MAX_RECORD_ROWS = 16384
SUPPORTED_PYTHON_MIN = (3, 10)
SUPPORTED_PYTHON_MAX = (3, 12)

# This is the complete third-party closure imported by the inventory probe.
# Keep it explicit: expansion requires constraints review and regenerated proof.
REVIEWED_DISTRIBUTIONS = (
    "annotated-doc",
    "annotated-types",
    "anyio",
    "fastapi",
    "jinja2",
    "markupsafe",
    "pydantic",
    "pydantic-core",
    "python-multipart",
    "sniffio",
    "starlette",
    "typing-extensions",
    "typing-inspection",
)
NATIVE_EXTENSION_DISTRIBUTIONS = ("markupsafe", "pydantic-core")

_CANONICAL_NAME = re.compile(r"[-_.]+")
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)=="
    r"(?P<version>[^;\s]+)(?:\s*;\s*(?P<marker>.+))?$"
)
_EXACT_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]*$")
_WINDOWS_REPARSE_POINT = 0x400
_NATIVE_SUFFIXES = (".pyd", ".so", ".dylib", ".dll")


class DependencyBindingError(RuntimeError):
    """The inventory's external dependency authority is unsafe or drifted."""


@dataclass(frozen=True)
class DependencyBinding:
    """Portable report plus local-only path authority used by the probe parent."""

    report: Mapping[str, object]
    roots: tuple[Path, ...]
    declared_origins: Mapping[str, tuple[str, str]]
    declared_relative_origins: Mapping[str, tuple[str, str]]
    captured_files: Mapping[str, bytes]


def _canonical_name(value: str) -> str:
    return _CANONICAL_NAME.sub("-", value).lower()


def _is_reparse_point(value: os.stat_result) -> bool:
    return bool(getattr(value, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_size),
        int(value.st_mtime_ns),
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(getattr(value, "st_file_attributes", 0)),
    )


def _safe_root(path: Path, *, label: str) -> Path:
    try:
        absolute = path.absolute()
        supplied_metadata = absolute.lstat()
        if absolute.is_symlink() or _is_reparse_point(supplied_metadata):
            raise DependencyBindingError(f"{label} is a link or reparse point")
        resolved = absolute.resolve(strict=True)
        metadata = resolved.lstat()
    except OSError as exc:
        raise DependencyBindingError(f"{label} cannot be resolved") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or resolved.is_symlink()
        or _is_reparse_point(metadata)
    ):
        raise DependencyBindingError(f"{label} is not a regular directory")
    return resolved


def _relative_parts(relative: str, *, label: str) -> tuple[str, ...]:
    if not relative or "\\" in relative or "\x00" in relative:
        raise DependencyBindingError(f"{label} path is non-canonical")
    value = PurePosixPath(relative)
    if value.is_absolute() or any(part in {"", ".", ".."} for part in value.parts):
        raise DependencyBindingError(f"{label} path escapes its root")
    if value.as_posix() != relative:
        raise DependencyBindingError(f"{label} path is non-canonical")
    return value.parts


def _safe_read(root: Path, relative: str, *, maximum: int, label: str) -> bytes:
    parts = _relative_parts(relative, label=label)
    parent_identities: list[tuple[Path, tuple[int, int, int, int, int]]] = []
    current = root
    try:
        for part in parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or current.is_symlink()
                or _is_reparse_point(metadata)
            ):
                raise DependencyBindingError(f"{label} has an unsafe ancestor")
            parent_identities.append((current, _identity(metadata)))
        path = current / parts[-1]
        before = path.lstat()
    except OSError as exc:
        raise DependencyBindingError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or path.is_symlink()
        or _is_reparse_point(before)
        or before.st_nlink != 1
    ):
        raise DependencyBindingError(f"{label} is not a single-link regular file")
    if before.st_size > maximum:
        raise DependencyBindingError(f"{label} exceeds its byte limit")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise DependencyBindingError(f"{label} cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or _identity(opened) != _identity(before)
        ):
            raise DependencyBindingError(f"{label} changed before it was opened")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > maximum:
                raise DependencyBindingError(f"{label} exceeds its byte limit")
        after_open = os.fstat(descriptor)
        if _identity(after_open) != _identity(opened):
            raise DependencyBindingError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)

    try:
        after = path.lstat()
        if _identity(after) != _identity(before):
            raise DependencyBindingError(f"{label} changed while it was read")
        for parent, expected in parent_identities:
            if _identity(parent.lstat()) != expected:
                raise DependencyBindingError(f"{label} ancestor changed while it was read")
    except OSError as exc:
        raise DependencyBindingError(f"{label} changed while it was read") from exc
    return b"".join(chunks)


def _safe_declared_leaf(root: Path, relative: str, *, label: str) -> Path:
    parts = _relative_parts(relative, label=label)
    current = root
    try:
        for part in parts[:-1]:
            current = current / part
            metadata = current.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or current.is_symlink()
                or _is_reparse_point(metadata)
            ):
                raise DependencyBindingError(f"{label} has an unsafe ancestor")
        path = current / parts[-1]
        metadata = path.lstat()
    except OSError as exc:
        raise DependencyBindingError(f"{label} is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or path.is_symlink()
        or _is_reparse_point(metadata)
        or metadata.st_nlink != 1
    ):
        raise DependencyBindingError(f"{label} is not a single-link regular file")
    return path


def _source_bytes(raw: bytes, *, label: str) -> bytes:
    try:
        text = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DependencyBindingError(f"{label} is not UTF-8 Python source") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _canonical_digest(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _constraints(repository_root: Path) -> tuple[dict[str, tuple[str, str | None]], str]:
    raw = _safe_read(
        repository_root,
        CONSTRAINTS_PATH,
        maximum=MAX_CONSTRAINTS_BYTES,
        label="production dependency constraints",
    )
    normalized = _source_bytes(raw, label="production dependency constraints")
    pins: dict[str, tuple[str, str | None]] = {}
    for number, raw_line in enumerate(normalized.decode("utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.fullmatch(line)
        if match is None:
            raise DependencyBindingError(
                f"dependency constraint line {number} is not an exact pin"
            )
        name = _canonical_name(match.group("name"))
        version = match.group("version")
        if _EXACT_VERSION.fullmatch(version) is None:
            raise DependencyBindingError(
                f"dependency constraint line {number} has a non-canonical version"
            )
        if name in pins:
            raise DependencyBindingError(f"duplicate dependency constraint: {name}")
        pins[name] = (version, match.group("marker"))
    for name in REVIEWED_DISTRIBUTIONS:
        if name not in pins:
            raise DependencyBindingError(f"reviewed dependency is not constrained: {name}")
        if pins[name][1] is not None:
            raise DependencyBindingError(
                f"reviewed dependency pin must be unconditional: {name}"
            )
    return pins, hashlib.sha256(normalized).hexdigest()


def _metadata_identity(raw: bytes, *, label: str) -> tuple[str, str]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DependencyBindingError(f"{label} metadata is not UTF-8") from exc
    header_text = text.replace("\r\n", "\n").split("\n\n", 1)[0]
    names = [
        line[6:].strip()
        for line in header_text.splitlines()
        if line.startswith("Name: ")
    ]
    versions = [
        line[9:].strip()
        for line in header_text.splitlines()
        if line.startswith("Version: ")
    ]
    if len(names) != 1 or len(versions) != 1 or not names[0] or not versions[0]:
        raise DependencyBindingError(f"{label} metadata identity is ambiguous")
    return _canonical_name(names[0]), versions[0]


def _root_entries(root: Path) -> list[os.DirEntry[str]]:
    entries: list[os.DirEntry[str]] = []
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                if len(entries) >= MAX_DEPENDENCY_ROOT_ENTRIES:
                    raise DependencyBindingError("dependency root exceeds its entry limit")
                entries.append(entry)
    except OSError as exc:
        raise DependencyBindingError("dependency root cannot be enumerated") from exc
    return entries


def _record_paths(raw: bytes, *, label: str) -> tuple[list[str], list[str]]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DependencyBindingError(f"{label} RECORD is not UTF-8") from exc
    python_paths: list[str] = []
    native_paths: list[str] = []
    seen: set[str] = set()
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        for number, row in enumerate(reader, start=1):
            if number > MAX_RECORD_ROWS:
                raise DependencyBindingError(f"{label} RECORD exceeds its row limit")
            if len(row) != 3 or not row[0]:
                raise DependencyBindingError(f"{label} RECORD row is malformed")
            relative = row[0]
            if relative in seen:
                raise DependencyBindingError(f"{label} RECORD contains duplicate paths")
            seen.add(relative)
            if relative.endswith(".py"):
                _relative_parts(relative, label=f"{label} source")
                python_paths.append(relative)
            elif relative.lower().endswith(_NATIVE_SUFFIXES):
                _relative_parts(relative, label=f"{label} native extension")
                native_paths.append(relative)
    except csv.Error as exc:
        raise DependencyBindingError(f"{label} RECORD is malformed") from exc
    if not python_paths:
        raise DependencyBindingError(f"{label} declares no Python source")
    return sorted(python_paths), sorted(native_paths)


def bind_runtime_dependencies(  # noqa: C901
    repository_root: Path,
    dependency_roots: Sequence[Path | str],
) -> DependencyBinding:
    """Bind the reviewed dependency closure and return portable evidence."""

    if not (
        SUPPORTED_PYTHON_MIN
        <= tuple(sys.version_info[:2])
        <= SUPPORTED_PYTHON_MAX
    ):
        raise DependencyBindingError("runtime interpreter is outside Python 3.10-3.12")
    repository = _safe_root(Path(repository_root), label="repository root")
    pins, constraints_sha256 = _constraints(repository)
    roots = tuple(
        sorted(
            {_safe_root(Path(item), label="dependency root") for item in dependency_roots},
            key=lambda item: os.path.normcase(str(item)),
        )
    )
    if not roots:
        raise DependencyBindingError("no dependency roots were admitted")

    discovered: dict[str, tuple[Path, str, list[str], list[str]]] = {}
    for root in roots:
        for entry in _root_entries(root):
            if not entry.name.endswith(".dist-info"):
                continue
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                entry_stat = (root / entry.name).lstat()
            except OSError as exc:
                raise DependencyBindingError("dependency metadata cannot be inspected") from exc
            if _is_reparse_point(entry_stat):
                continue
            relative = entry.name
            metadata_directory_identity = _directory_identity(entry_stat)
            metadata = _safe_read(
                root,
                f"{relative}/METADATA",
                maximum=MAX_METADATA_BYTES,
                label=f"dependency metadata {relative}",
            )
            name, version = _metadata_identity(metadata, label=relative)
            if name not in REVIEWED_DISTRIBUTIONS:
                continue
            expected_version = pins[name][0]
            if version != expected_version:
                raise DependencyBindingError(
                    f"reviewed dependency version drifted: {name} {version} != {expected_version}"
                )
            if name in discovered:
                raise DependencyBindingError(f"reviewed dependency is ambiguous: {name}")
            record = _safe_read(
                root,
                f"{relative}/RECORD",
                maximum=MAX_RECORD_BYTES,
                label=f"dependency RECORD {relative}",
            )
            python_paths, native_paths = _record_paths(record, label=name)
            try:
                metadata_directory_at_completion = (root / relative).lstat()
            except OSError as exc:
                raise DependencyBindingError(
                    f"dependency metadata changed while it was read: {relative}"
                ) from exc
            if (
                _directory_identity(metadata_directory_at_completion)
                != metadata_directory_identity
            ):
                raise DependencyBindingError(
                    f"dependency metadata changed while it was read: {relative}"
                )
            discovered[name] = (root, version, python_paths, native_paths)

    missing = sorted(set(REVIEWED_DISTRIBUTIONS) - set(discovered))
    if missing:
        raise DependencyBindingError(
            "reviewed dependency distributions are missing: " + ", ".join(missing)
        )

    rows: list[dict[str, object]] = []
    declared: dict[str, tuple[str, str]] = {}
    declared_relative: dict[str, tuple[str, str]] = {}
    captured_files: dict[str, bytes] = {}
    total_bytes = 0
    total_materialized_bytes = 0
    for distribution in REVIEWED_DISTRIBUTIONS:
        root, version, python_paths, native_paths = discovered[distribution]
        for relative in python_paths:
            if len(rows) >= MAX_DEPENDENCY_SOURCE_FILES:
                raise DependencyBindingError("dependency source exceeds its file limit")
            captured = _safe_read(
                root,
                relative,
                maximum=MAX_DEPENDENCY_FILE_BYTES,
                label=f"dependency source {distribution}/{relative}",
            )
            semantic = _source_bytes(
                captured,
                label=f"dependency source {distribution}/{relative}",
            )
            total_bytes += len(semantic)
            if total_bytes > MAX_DEPENDENCY_SOURCE_BYTES:
                raise DependencyBindingError("dependency source exceeds its aggregate byte limit")
            row = {
                "distribution": distribution,
                "version": version,
                "relative_path": relative,
                "bytes": len(semantic),
                "sha256": hashlib.sha256(semantic).hexdigest(),
            }
            rows.append(row)
            key = os.path.normcase(
                str(
                    _safe_declared_leaf(
                        root,
                        relative,
                        label=f"dependency source {distribution}/{relative}",
                    ).resolve(strict=True)
                )
            )
            if key in declared:
                raise DependencyBindingError("dependency source ownership is ambiguous")
            declared[key] = (distribution, "python-source")
            if relative in declared_relative:
                raise DependencyBindingError("dependency snapshot ownership is ambiguous")
            declared_relative[relative] = (distribution, "python-source")
            captured_files[relative] = captured
            total_materialized_bytes += len(captured)
        for relative in native_paths:
            captured = _safe_read(
                root,
                relative,
                maximum=MAX_DEPENDENCY_NATIVE_FILE_BYTES,
                label=f"dependency native extension {distribution}/{relative}",
            )
            key = os.path.normcase(
                str(
                    _safe_declared_leaf(
                        root,
                        relative,
                        label=f"dependency native extension {distribution}/{relative}",
                    ).resolve(strict=True)
                )
            )
            if key in declared:
                raise DependencyBindingError("dependency native ownership is ambiguous")
            declared[key] = (distribution, "native-extension")
            if relative in declared_relative:
                raise DependencyBindingError("dependency snapshot ownership is ambiguous")
            declared_relative[relative] = (distribution, "native-extension")
            captured_files[relative] = captured
            total_materialized_bytes += len(captured)
        if total_materialized_bytes > MAX_DEPENDENCY_MATERIALIZED_BYTES:
            raise DependencyBindingError(
                "dependency snapshot exceeds its aggregate byte limit"
            )
    rows.sort(key=lambda row: (str(row["distribution"]), str(row["relative_path"])))
    distributions = [
        {"distribution": name, "version": discovered[name][1]}
        for name in REVIEWED_DISTRIBUTIONS
    ]
    report: dict[str, object] = {
        "schema": SCHEMA,
        "constraints_path": CONSTRAINTS_PATH,
        "constraints_sha256": constraints_sha256,
        "constraints_digest_mode": SOURCE_DIGEST_MODE,
        "python_policy": ">=3.10,<3.13",
        "distributions": distributions,
        "native_extension_distributions": list(NATIVE_EXTENSION_DISTRIBUTIONS),
        "native_extension_boundary": (
            "platform-specific extension bytes are captured once into the private execution "
            "snapshot and checked by local tree identity, but their artifact authenticity "
            "remains a version-pinned trusted-host boundary; portable evidence binds all "
            "declared UTF-8 Python source"
        ),
        "source_files": rows,
        "source_file_count": len(rows),
        "source_bytes": total_bytes,
        "source_digest_mode": "canonical-json-over-utf8-lf-source-files-sha256",
        "source_sha256": _canonical_digest(rows),
        "absolute_host_paths_serialized": False,
    }
    return DependencyBinding(
        report=MappingProxyType(report),
        roots=roots,
        declared_origins=MappingProxyType(declared),
        declared_relative_origins=MappingProxyType(declared_relative),
        captured_files=MappingProxyType(captured_files),
    )


def assert_runtime_dependencies_unchanged(
    binding: DependencyBinding,
    repository_root: Path,
) -> None:
    """Recompute and compare the complete portable binding after execution."""

    current = bind_runtime_dependencies(repository_root, binding.roots)
    if dict(current.report) != dict(binding.report):
        raise DependencyBindingError("runtime dependencies changed during inventory execution")


def materialize_runtime_dependencies(
    binding: DependencyBinding,
    destination: Path,
) -> Path:
    """Write the one-time captured code into a private cache-free dependency tree."""

    destination = destination.absolute()
    parent = _safe_root(destination.parent, label="dependency snapshot parent")
    if destination.parent != parent:
        raise DependencyBindingError("dependency snapshot parent is non-canonical")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise DependencyBindingError("dependency snapshot destination is unsafe") from exc
    else:
        raise DependencyBindingError("dependency snapshot destination already exists")
    try:
        destination.mkdir(mode=0o700)
        for relative, raw in sorted(binding.captured_files.items()):
            parts = _relative_parts(relative, label="dependency snapshot file")
            if "__pycache__" in parts or relative.endswith((".pyc", ".pyo")):
                raise DependencyBindingError("dependency snapshot contains bytecode cache")
            current = destination
            for part in parts[:-1]:
                current = current / part
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    metadata = current.lstat()
                    if (
                        not stat.S_ISDIR(metadata.st_mode)
                        or current.is_symlink()
                        or _is_reparse_point(metadata)
                    ):
                        raise DependencyBindingError(
                            "dependency snapshot has an unsafe directory"
                        ) from None
            path = current / parts[-1]
            flags = (
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_BINARY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
            )
            descriptor = os.open(path, flags, 0o600)
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:  # pragma: no cover - OS write invariant
                        raise DependencyBindingError(
                            "dependency snapshot write did not progress"
                        )
                    view = view[written:]
                os.fsync(descriptor)
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_size != len(raw)
                ):
                    raise DependencyBindingError(
                        "dependency snapshot file identity is unsafe"
                    )
            finally:
                os.close(descriptor)
    except (DependencyBindingError, OSError):
        # The caller owns cleanup of its enclosing TemporaryDirectory.  Never
        # reuse a partially materialized authority tree.
        raise
    return destination


def validate_runtime_dependency_origins(
    binding: DependencyBinding,
    origins: Sequence[str],
    *,
    materialized_root: Path | None = None,
) -> list[dict[str, str]]:
    """Require each loaded external module to be owned by the reviewed closure."""

    observed: set[tuple[str, str]] = set()
    observed_distributions: set[str] = set()
    seen: set[str] = set()
    materialized = (
        _safe_root(materialized_root, label="materialized dependency root")
        if materialized_root is not None
        else None
    )
    for raw_origin in origins:
        if not isinstance(raw_origin, str) or not raw_origin:
            raise DependencyBindingError("runtime dependency origin is malformed")
        try:
            resolved = Path(raw_origin).resolve(strict=True)
        except OSError as exc:
            raise DependencyBindingError("runtime dependency origin is unavailable") from exc
        if materialized is None:
            key = os.path.normcase(str(resolved))
            owner = binding.declared_origins.get(key)
        else:
            try:
                relative = resolved.relative_to(materialized).as_posix()
            except ValueError as exc:
                raise DependencyBindingError(
                    "runtime dependency origin escaped its materialized root"
                ) from exc
            owner = binding.declared_relative_origins.get(relative)
            key = "materialized:" + os.path.normcase(relative)
        if key in seen:
            continue
        seen.add(key)
        if owner is None:
            raise DependencyBindingError(
                "runtime imported an unbound dependency file: " + resolved.name
            )
        distribution, kind = owner
        if kind == "native-extension" and distribution not in NATIVE_EXTENSION_DISTRIBUTIONS:
            raise DependencyBindingError(
                f"runtime imported an unapproved native extension: {distribution}"
            )
        observed_distributions.add(distribution)
        observed.add((distribution, kind))
    missing = sorted(set(REVIEWED_DISTRIBUTIONS) - observed_distributions)
    if missing:
        raise DependencyBindingError(
            "runtime did not exercise the complete reviewed dependency closure: "
            + ", ".join(missing)
        )
    return [
        {"distribution": distribution, "kind": kind}
        for distribution, kind in sorted(observed)
    ]
