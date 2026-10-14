from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_runtime_contract.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_production_runtime_contract_release", SCRIPT
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _copy_contract_tree(target: Path) -> None:
    for relative in (MODULE.RUNTIME_SOURCE, MODULE.CONTRACT, MODULE.SURFACE):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)


def test_repository_runtime_contract_is_code_derived_and_live_unqualified():
    result = MODULE.verify(ROOT)

    assert result["profile_id"] == "enterprise-single-control-plane-v1"
    assert result["live_cluster_proof_required"] is True


def test_runtime_contract_verifier_honors_explicit_root(tmp_path):
    _copy_contract_tree(tmp_path)
    artifact = tmp_path / MODULE.CONTRACT
    artifact.write_bytes(artifact.read_bytes().replace(b'"pids": 256', b'"pids": 257'))

    with pytest.raises(
        MODULE.ProductionRuntimeContractVerificationError,
        match="reviewed artifact",
    ):
        MODULE.verify(tmp_path)


def test_runtime_contract_verifier_refuses_source_binding_drift(tmp_path):
    _copy_contract_tree(tmp_path)
    source = tmp_path / MODULE.RUNTIME_SOURCE
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# unreviewed drift\n",
        encoding="utf-8",
    )

    with pytest.raises(
        MODULE.ProductionRuntimeContractVerificationError,
        match="source binding drifted",
    ):
        MODULE.verify(tmp_path)


def test_runtime_contract_refuses_surface_substitution(tmp_path):
    _copy_contract_tree(tmp_path)
    surface = tmp_path / MODULE.SURFACE
    surface.write_text(
        surface.read_text(encoding="utf-8").replace(
            '"profile_id": "enterprise-single-control-plane-v1"',
            '"profile_id": "substituted-profile"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        MODULE.ProductionRuntimeContractVerificationError,
        match="production surface",
    ):
        MODULE.verify(tmp_path)
