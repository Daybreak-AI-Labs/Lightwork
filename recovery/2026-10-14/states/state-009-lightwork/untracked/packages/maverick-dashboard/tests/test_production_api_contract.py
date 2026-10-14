"""Adverse tests for the signed, full production API contract."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_api_contract.py"
SPEC = importlib.util.spec_from_file_location("verify_production_api_contract", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _minimal_root(tmp_path: Path) -> Path:
    contract = _json(ROOT / MODULE.CONTRACT_PATH)
    relative_paths = {
        MODULE.CONTRACT_PATH.as_posix(),
        MODULE.OPENAPI_PATH.as_posix(),
        MODULE.PACKAGED_PATH.as_posix(),
        MODULE.SURFACE_PATH.as_posix(),
        *(row["path"] for row in contract["source_bindings"]),
    }
    for relative in relative_paths:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return tmp_path


def _validate(root: Path) -> list[str]:
    contract = MODULE._load_json(
        root / MODULE.CONTRACT_PATH,
        label="production API contract",
    )
    schema = MODULE._load_json(
        root / MODULE.OPENAPI_PATH,
        label="production OpenAPI schema",
    )
    current = _json(ROOT / MODULE.OPENAPI_PATH)
    return MODULE.validate_contract(root, contract, schema, current)


def test_complete_production_api_contract_matches_checkout() -> None:
    messages = MODULE.verify(ROOT)
    assert "verified 69 exact production OpenAPI operations" in messages
    assert "lifecycle_status=unpublished-release-candidate" in messages
    assert "release_eligible=false" in messages


def test_contract_refuses_full_schema_drift_even_when_summary_is_unchanged(
    tmp_path: Path,
) -> None:
    root = _minimal_root(tmp_path)
    schema_path = root / MODULE.OPENAPI_PATH
    schema = _json(schema_path)
    schema["info"]["description"] = "unreviewed release description"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    contract_path = root / MODULE.CONTRACT_PATH
    contract = _json(contract_path)
    contract["schema_artifact_sha256"] = MODULE._sha256(schema_path)
    contract["canonical_schema_sha256"] = MODULE.hashlib.sha256(
        MODULE._canonical(schema)
    ).hexdigest()
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        MODULE.ProductionApiContractError,
        match="checkout-generated schema",
    ):
        _validate(root)


def test_contract_refuses_packaged_startup_artifact_drift(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    packaged_path = root / MODULE.PACKAGED_PATH
    packaged = _json(packaged_path)
    packaged["operation_count"] -= 1
    packaged_path.write_text(json.dumps(packaged), encoding="utf-8")
    contract_path = root / MODULE.CONTRACT_PATH
    contract = _json(contract_path)
    contract["packaged_runtime_contract_sha256"] = MODULE._sha256(packaged_path)
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(
        MODULE.ProductionApiContractError,
        match="packaged runtime OpenAPI field drifted",
    ):
        _validate(root)


def test_contract_refuses_source_binding_drift(tmp_path: Path) -> None:
    root = _minimal_root(tmp_path)
    source = root / "packages/maverick-dashboard/maverick_dashboard/openapi_contract.py"
    source.write_text(source.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")

    with pytest.raises(MODULE.ProductionApiContractError, match="source binding drifted"):
        _validate(root)


def test_contract_cannot_activate_an_unpublished_compatibility_promise(
    tmp_path: Path,
) -> None:
    root = _minimal_root(tmp_path)
    contract_path = root / MODULE.CONTRACT_PATH
    contract = _json(contract_path)
    contract["release_boundary"]["compatibility_commitment_active"] = True
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(MODULE.ProductionApiContractError, match="overclaims support"):
        _validate(root)
