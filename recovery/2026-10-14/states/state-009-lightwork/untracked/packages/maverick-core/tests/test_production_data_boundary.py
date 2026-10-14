from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_data_boundary.py"
SPEC = importlib.util.spec_from_file_location("verify_production_data_boundary", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_repository_production_data_boundary_is_source_bound():
    result = MODULE.verify()
    assert result["data_flows"] == 9
    assert result["source_bindings"] == 25
    assert result["live_qualification_required"] is True


def test_data_boundary_refuses_manifest_profile_drift(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["profile_id"] = "unreviewed-profile"
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)
    with pytest.raises(MODULE.DataBoundaryError, match="profile_id"):
        MODULE.verify()


def test_data_boundary_refuses_missing_flow(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["data_flows"].pop()
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)
    with pytest.raises(MODULE.DataBoundaryError, match="inventory"):
        MODULE.verify()


def test_data_boundary_refuses_missing_evidence(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["data_flows"][0]["code_evidence"] = ["missing.py"]
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)
    with pytest.raises(MODULE.DataBoundaryError, match="missing/unsafe"):
        MODULE.verify()


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("encryption_in_transit", "none"),
        ("encryption_at_rest", "none"),
        ("retention", "retain forever"),
        ("live_qualification", ["already production qualified"]),
    ],
)
def test_data_boundary_refuses_reviewed_semantic_substitution(
    tmp_path,
    monkeypatch,
    field,
    replacement,
):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["data_flows"][0][field] = replacement
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.DataBoundaryError, match="reviewed semantics"):
        MODULE.verify()


def test_data_boundary_refuses_documentation_as_code_evidence(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["data_flows"][0]["code_evidence"] = ["README.md"]
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.DataBoundaryError, match="fully source bound"):
        MODULE.verify()


def test_data_boundary_refuses_source_digest_drift(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["source_bindings"][0]["sha256"] = "0" * 64
    path = tmp_path / "boundary.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.DataBoundaryError, match="source binding drifted"):
        MODULE.verify()


def test_data_boundary_refuses_duplicate_json_keys(tmp_path, monkeypatch):
    raw = (ROOT / MODULE.CONTRACT).read_text(encoding="utf-8")
    marker = '  "status": "source_contract_complete_live_qualification_required",'
    raw = raw.replace(marker, marker + '\n  "status": "production_ready",', 1)
    path = tmp_path / "boundary.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.DataBoundaryError, match="cannot read"):
        MODULE.verify()


def test_data_boundary_refuses_non_finite_json(tmp_path, monkeypatch):
    raw = (ROOT / MODULE.CONTRACT).read_text(encoding="utf-8")
    raw = raw.replace('  "principles": [', '  "principles": [NaN,', 1)
    path = tmp_path / "boundary.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.DataBoundaryError, match="cannot read"):
        MODULE.verify()


def test_data_boundary_verifier_honors_explicit_root(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["profile_id"] = "substituted-profile"
    (release / MODULE.CONTRACT.name).write_text(json.dumps(contract), encoding="utf-8")
    (release / MODULE.SURFACE.name).write_bytes((ROOT / MODULE.SURFACE).read_bytes())

    with pytest.raises(MODULE.DataBoundaryError, match="profile_id"):
        MODULE.verify(tmp_path)
