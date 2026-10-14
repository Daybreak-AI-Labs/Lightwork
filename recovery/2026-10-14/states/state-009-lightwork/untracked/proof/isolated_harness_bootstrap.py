#!/usr/bin/env python3
"""Launch one allow-listed proof harness from an isolated source materialization."""
from __future__ import annotations

import json
import runpy
import stat
import sys
from pathlib import Path, PurePosixPath

_ALLOWED_HARNESSES = {
    "proof/adapter_rung_proof.py",
    "proof/dgm_code_rung_proof.py",
    "proof/dgm_uplift_proof.py",
    "proof/run_proof.py",
    "proof/self_harness_proof.py",
    "proof/swebench_governed_proof.py",
}
_RUNTIME_IMPORT_PATHS_SCHEMA = "lightwork-proof-runtime-import-paths.v1"
_RUNTIME_IMPORT_PATHS_FILE = "runtime-import-paths.json"
_EXACT_PROOF_FILES = {
    "adapter_rung_proof.py",
    "dgm_code_rung_proof.py",
    "dgm_uplift_proof.py",
    "generate_results.py",
    "isolated_harness_bootstrap.py",
    "run_proof.py",
    "self_harness_proof.py",
    "swebench_governed_proof.py",
}


def _is_reparse_point(path: Path) -> bool:
    try:
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        return False
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(marker and attributes & marker)


def _require_unlinked_absolute_directory(path: Path, *, label: str) -> None:
    if not path.is_absolute():
        raise SystemExit(f"{label} is not absolute")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor /= part
        try:
            metadata = cursor.lstat()
        except FileNotFoundError as exc:
            raise SystemExit(f"{label} is missing") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse_point(cursor)
        ):
            raise SystemExit(f"{label} contains a link or non-directory ancestor")


def _runtime_import_paths(root: Path) -> list[str]:
    control = root.parent / _RUNTIME_IMPORT_PATHS_FILE
    try:
        metadata = control.lstat()
    except FileNotFoundError as exc:
        raise SystemExit("runtime import-path control is missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or control.is_symlink()
        or metadata.st_nlink != 1
        or _is_reparse_point(control)
    ):
        raise SystemExit("runtime import-path control is not a regular file")
    try:
        record = json.loads(control.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit("runtime import-path control is malformed") from exc
    if (
        not isinstance(record, dict)
        or set(record) != {"schema", "paths"}
        or record.get("schema") != _RUNTIME_IMPORT_PATHS_SCHEMA
        or not isinstance(record.get("paths"), list)
        or len(record["paths"]) != len(set(record["paths"]))
    ):
        raise SystemExit("runtime import-path control schema drifted")
    admitted: list[str] = []
    for raw_path in record["paths"]:
        if not isinstance(raw_path, str) or not raw_path:
            raise SystemExit("runtime import path is malformed")
        candidate = Path(raw_path)
        if not candidate.is_absolute() or not candidate.is_dir():
            raise SystemExit("runtime import path is not an absolute directory")
        _require_unlinked_absolute_directory(candidate, label="runtime import path")
        resolved = str(candidate.resolve())
        if resolved != raw_path:
            raise SystemExit("runtime import path is not canonical")
        admitted.append(resolved)
    return admitted


def _require_regular_directory(path: Path, *, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise SystemExit(f"{label} is missing") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or path.is_symlink()
        or _is_reparse_point(path)
    ):
        raise SystemExit(f"{label} is not a regular directory")


def _validate_exact_proof_tree(root: Path) -> None:
    proof = root / "proof"
    _require_regular_directory(proof, label="materialized proof directory")
    observed: set[str] = set()
    for path in proof.iterdir():
        observed.add(path.name)
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise SystemExit("materialized proof tree changed during validation") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_nlink != 1
            or _is_reparse_point(path)
        ):
            raise SystemExit("materialized proof tree contains a non-regular entry")
    if observed != _EXACT_PROOF_FILES:
        raise SystemExit("materialized proof tree is not the exact allow-listed file set")


def main() -> int:
    if (
        not sys.flags.isolated
        or not sys.flags.no_site
        or not sys.flags.safe_path
        or not sys.flags.utf8_mode
        or not sys.dont_write_bytecode
    ):
        raise SystemExit("proof harness requires isolated no-site Python startup")
    if len(sys.argv) != 2 or sys.argv[1] not in _ALLOWED_HARNESSES:
        raise SystemExit("expected exactly one allow-listed repository-relative harness")
    relative = PurePosixPath(sys.argv[1])
    if relative.is_absolute() or ".." in relative.parts:
        raise SystemExit("harness path must remain repository-relative")
    root = Path(__file__).resolve().parent.parent
    _validate_exact_proof_tree(root)
    script = root.joinpath(*relative.parts)
    if not script.is_file() or script.is_symlink():
        raise SystemExit("harness is not a regular materialized source file")
    runtime_paths = _runtime_import_paths(root)
    _require_regular_directory(
        root / "packages" / "maverick-core",
        label="materialized core import directory",
    )
    _require_regular_directory(
        root / "benchmarks",
        label="materialized benchmark import directory",
    )
    unsafe_entries = {"", str(root), str(root / "proof")}
    sys.path[:] = [entry for entry in sys.path if entry not in unsafe_entries]
    for entry in reversed(runtime_paths):
        sys.path.insert(0, entry)
    sys.path.insert(0, str(root / "benchmarks"))
    sys.path.insert(0, str(root / "packages" / "maverick-core"))
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="strict")
    sys.argv = [relative.as_posix()]
    try:
        runpy.run_path(str(script), run_name="__main__")
    finally:
        _validate_exact_proof_tree(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
