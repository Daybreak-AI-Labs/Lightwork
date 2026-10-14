from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_secret_rotation_contract.py"
SPEC = importlib.util.spec_from_file_location("secret_rotation_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def _copy_contract(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    fixed = (
        "release/secret-rotation-contract.json",
        "release/production-runtime-contract.json",
        "release/production-surface-manifest.json",
        "deploy/helm/maverick/templates/deployment.yaml",
        "deploy/helm/maverick/templates/_helpers.tpl",
        "deploy/helm/maverick/values-production.yaml",
        "deploy/helm/README.md",
        "docs/enterprise/deployment-playbook.md",
    )
    for relative in fixed:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    contract = json.loads((root / verifier.CONTRACT_PATH).read_text(encoding="utf-8"))
    for row in contract["secrets"]:
        for relative in row["code_evidence"]:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                destination.write_text("evidence\n", encoding="utf-8")
    return root


def test_committed_secret_rotation_contract_is_exact_and_conservative() -> None:
    assert verifier.verify_secret_rotation_contract(ROOT) == {
        "schema": verifier.SCHEMA,
        "secret_rows": 8,
        "staged_files": 7,
    }


def test_rotation_contract_refuses_a_missing_secret_owner(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["secrets"] = contract["secrets"][:-1]
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.SecretRotationContractError, match="incomplete"):
        verifier.verify_secret_rotation_contract(root)


def test_rotation_contract_refuses_zero_downtime_overclaim(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["deployment_contract"]["zero_downtime_claimed"] = True
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.SecretRotationContractError, match="weakens"):
        verifier.verify_secret_rotation_contract(root)


def test_rotation_contract_refuses_runtime_projection_drift(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.RUNTIME_PATH
    runtime = json.loads(path.read_text(encoding="utf-8"))
    runtime["filesystem"]["projected_secret_files"].append("OPENAI_API_KEY")
    path.write_text(json.dumps(runtime), encoding="utf-8")

    with pytest.raises(verifier.SecretRotationContractError, match="runtime custody"):
        verifier.verify_secret_rotation_contract(root)


def test_rotation_contract_refuses_missing_rollout_trigger(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.DEPLOYMENT_PATH
    text = path.read_text(encoding="utf-8").replace(
        "lightwork.daybreak.ai/secret-rotation-generation",
        "lightwork.daybreak.ai/removed-generation",
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(verifier.SecretRotationContractError, match="rotation control"):
        verifier.verify_secret_rotation_contract(root)
