#!/usr/bin/env python3
"""Fail closed when the published production support matrix drifts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class SupportMatrixError(ValueError):
    """The support matrix is incomplete, ambiguous, or inconsistent."""


_STATUSES = {
    "admitted",
    "qualification_required",
    "evaluation_only",
    "excluded",
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SupportMatrixError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SupportMatrixError(f"{path} must contain a JSON object")
    return value


def _single(rows: list[dict[str, Any]], dimension: str) -> dict[str, Any]:
    matches = [row for row in rows if row.get("dimension") == dimension]
    if len(matches) != 1:
        raise SupportMatrixError(
            f"matrix needs exactly one {dimension!r} row, found {len(matches)}"
        )
    return matches[0]


def verify(root: Path) -> list[str]:  # noqa: C901 - one fail-closed contract walk
    root = root.resolve()
    matrix_path = root / "release" / "production-support-matrix.json"
    manifest_path = root / "release" / "production-surface-manifest.json"
    matrix = _load_json(matrix_path)
    manifest = _load_json(manifest_path)

    if matrix.get("schema") != "lightwork.production-support-matrix.v1":
        raise SupportMatrixError("unsupported production support-matrix schema")
    for field in ("profile_id", "source_version"):
        if matrix.get(field) != manifest.get(field):
            raise SupportMatrixError(f"support matrix {field} differs from manifest")
    release_state = manifest.get("release_state")
    eligible = bool(release_state.get("eligible")) if isinstance(release_state, dict) else False
    if matrix.get("release_eligible") is not eligible:
        raise SupportMatrixError("support matrix release eligibility differs from manifest")

    raw_rows = matrix.get("matrix")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SupportMatrixError("matrix must be a non-empty array")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(raw_rows):
        if not isinstance(value, dict):
            raise SupportMatrixError(f"matrix row {index} must be an object")
        dimension = value.get("dimension")
        selection = value.get("selection")
        status = value.get("status")
        constraint = value.get("constraint")
        if not all(isinstance(item, str) and item.strip() for item in (
            dimension, selection, status, constraint
        )):
            raise SupportMatrixError(f"matrix row {index} has blank or non-string fields")
        if status not in _STATUSES:
            raise SupportMatrixError(f"matrix row {index} has unknown status {status!r}")
        key = (dimension, selection)
        if key in seen:
            raise SupportMatrixError(f"duplicate matrix row {key!r}")
        seen.add(key)
        rows.append(value)

    constraints = manifest.get("constraints")
    if not isinstance(constraints, dict):
        raise SupportMatrixError("production manifest constraints are unavailable")
    checks = {
        "operating_system": str(constraints.get("operating_system")),
        "architecture": ",".join(constraints.get("architectures") or []),
        "control_plane_topology": f"{constraints.get('control_plane_replicas')} replica",
        "world_model": str(constraints.get("world_model_backend")),
        "container_isolation": "gVisor",
        "model_provider": ",".join(constraints.get("model_providers") or []),
        "key_management": ",".join(constraints.get("kms_providers") or []),
        "arbitrary_execution": ",".join(constraints.get("sandbox_backends") or []),
    }
    for dimension, expected in checks.items():
        row = _single(rows, dimension)
        if expected.lower() not in str(row["selection"]).lower():
            raise SupportMatrixError(
                f"{dimension} row does not reflect manifest constraint {expected!r}"
            )

    chart = (root / "deploy" / "helm" / "maverick" / "Chart.yaml").read_text(
        encoding="utf-8"
    )
    chart_version = re.search(r"(?m)^version:\s*([^\s#]+)", chart)
    if chart_version is None:
        raise SupportMatrixError("Helm chart version is unavailable")
    deployment = _single(rows, "deployment")
    if f"chart {chart_version.group(1)}" not in deployment["selection"]:
        raise SupportMatrixError("deployment row does not match the Helm chart version")

    pyproject = (root / "packages" / "maverick-core" / "pyproject.toml").read_text(
        encoding="utf-8"
    )
    if 'requires-python = ">=3.10"' not in pyproject:
        raise SupportMatrixError("unexpected core Python compatibility floor")
    runtime = _single(rows, "python_runtime")
    if "3.12" not in runtime["selection"]:
        raise SupportMatrixError("production runtime row must name CPython 3.12")
    release_workflow = (root / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    if "PYTHON_IMAGE=python:3.12-slim@sha256:" not in release_workflow:
        raise SupportMatrixError("production release image is not pinned to Python 3.12")
    ci_workflow = (root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    if "pgvector/pgvector:pg16@sha256:" not in ci_workflow:
        raise SupportMatrixError("repository Postgres 16 live-test target is not pinned")

    required = matrix.get("release_qualification_required")
    if not isinstance(required, list) or len(required) < 8:
        raise SupportMatrixError("release qualification list is incomplete")
    if any(not isinstance(item, str) or not item.strip() for item in required):
        raise SupportMatrixError("release qualification entries must be non-empty strings")

    docs = (root / "docs" / "enterprise" / "production-support-matrix.md").read_text(
        encoding="utf-8"
    )
    for required_text in (
        "qualification_required",
        "revision-bound evidence packet",
        "does not turn",
    ):
        if required_text not in docs:
            raise SupportMatrixError(
                f"support-matrix documentation is missing {required_text!r}"
            )

    return [
        f"verified {len(rows)} support-matrix rows",
        f"profile={matrix['profile_id']}",
        f"source_version={matrix['source_version']}",
        f"release_eligible={str(eligible).lower()}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        messages = verify(args.root)
    except SupportMatrixError as exc:
        print(f"production support matrix: FAIL: {exc}", file=sys.stderr)
        return 1
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
