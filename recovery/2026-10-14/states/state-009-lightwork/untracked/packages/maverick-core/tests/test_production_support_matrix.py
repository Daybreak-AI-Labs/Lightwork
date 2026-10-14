"""The published first-profile support matrix must be exact and conservative."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_support_matrix.py"
SPEC = importlib.util.spec_from_file_location("verify_production_support_matrix", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _matrix() -> dict:
    return json.loads(
        (ROOT / "release" / "production-support-matrix.json").read_text(
            encoding="utf-8"
        )
    )


def test_support_matrix_agrees_with_authoritative_repository_contracts() -> None:
    messages = MODULE.verify(ROOT)
    assert "verified 18 support-matrix rows" in messages
    assert "release_eligible=false" in messages


def test_support_matrix_does_not_overstate_external_qualification() -> None:
    matrix = _matrix()
    rows = {row["dimension"]: row for row in matrix["matrix"]}
    for dimension in (
        "deployment",
        "world_model",
        "container_isolation",
        "model_provider",
        "key_management",
        "identity",
        "browser_client",
    ):
        assert rows[dimension]["status"] == "qualification_required"
    assert matrix["release_eligible"] is False


def test_support_matrix_excludes_source_tree_feature_inflation() -> None:
    rows = _matrix()["matrix"]
    extension_row = next(
        row for row in rows if row["dimension"] == "state_learning_and_extensions"
    )
    assert extension_row["status"] == "excluded"
    for label in ("self-learning", "DGM", "plugins", "MCP", "connectors"):
        assert label in extension_row["selection"]


def test_verifier_refuses_manifest_identity_drift(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    matrix = _matrix()
    matrix["profile_id"] = "unreviewed-profile"
    (release / "production-support-matrix.json").write_text(
        json.dumps(matrix), encoding="utf-8"
    )
    manifest = json.loads(
        (ROOT / "release" / "production-surface-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    (release / "production-surface-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    with pytest.raises(MODULE.SupportMatrixError, match="profile_id"):
        MODULE.verify(tmp_path)
