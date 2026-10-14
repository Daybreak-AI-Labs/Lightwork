from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_dependency_failure_contract.py"
SPEC = importlib.util.spec_from_file_location("dependency_contract_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _copy_contract(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "release").mkdir(parents=True)
    for path in (
        "release/dependency-failure-contract.json",
        "release/production-surface-manifest.json",
    ):
        destination = root / path
        destination.write_bytes((ROOT / path).read_bytes())
    contract = json.loads((root / "release/dependency-failure-contract.json").read_text())
    for row in contract["dependencies"]:
        for relative in row["code_evidence"]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text("evidence\n", encoding="utf-8")
    return root


def test_committed_dependency_contract_is_complete_and_conservative() -> None:
    summary = verifier.verify_dependency_failure_contract(ROOT)

    assert summary == {
        "dependencies": 13,
        "excluded": 3,
        "schema": verifier.SCHEMA,
    }


def test_contract_refuses_a_missing_dependency(tmp_path) -> None:
    root = _copy_contract(tmp_path)
    path = root / "release/dependency-failure-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["dependencies"] = contract["dependencies"][:-1]
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.DependencyFailureContractError, match="incomplete"):
        verifier.verify_dependency_failure_contract(root)


def test_contract_refuses_readiness_or_qualification_overclaim(tmp_path) -> None:
    root = _copy_contract(tmp_path)
    path = root / "release/dependency-failure-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    vllm = next(row for row in contract["dependencies"] if row["id"] == "vllm")
    vllm["qualification"] = "repository_tested"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.DependencyFailureContractError, match="overclaims"):
        verifier.verify_dependency_failure_contract(root)


def test_contract_refuses_excluded_runtime_claim(tmp_path) -> None:
    root = _copy_contract(tmp_path)
    path = root / "release/dependency-failure-contract.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    vector = next(row for row in contract["dependencies"] if row["id"] == "vector-store")
    vector["readiness"] = "fail_closed"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.DependencyFailureContractError, match="claims runtime"):
        verifier.verify_dependency_failure_contract(root)
