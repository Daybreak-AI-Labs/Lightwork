from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_production_operations_contract.py"
SPEC = importlib.util.spec_from_file_location("verify_production_operations_contract", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_repository_operations_contract_is_source_bound_and_live_unqualified():
    result = MODULE.verify()
    assert result["alerts"] == 26
    assert result["dashboard_panels"] == 12
    assert result["incident_scenarios"] == 9
    assert result["source_bindings"] == 18
    assert result["live_qualification_required"] is True


def test_operations_contract_refuses_ready_status(tmp_path, monkeypatch):
    value = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    value["status"] = "production_ready"
    path = tmp_path / "operations.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)
    with pytest.raises(MODULE.OperationsContractError, match="live-unqualified"):
        MODULE.verify()


def test_operations_contract_refuses_alert_without_runbook(tmp_path, monkeypatch):
    rules = (ROOT / MODULE.RULES).read_text(encoding="utf-8")
    rules = rules.replace("          runbook_url:", "          missing_runbook:", 1)
    path = tmp_path / "rules.yaml"
    path.write_text(rules, encoding="utf-8")
    monkeypatch.setattr(MODULE, "RULES", path)
    with pytest.raises(MODULE.OperationsContractError, match="metadata"):
        MODULE.verify()


def test_operations_contract_refuses_unsafe_halt_instruction(tmp_path, monkeypatch):
    runbook = (ROOT / MODULE.LEGACY_RUNBOOK).read_text(encoding="utf-8")
    path = tmp_path / "incident.md"
    path.write_text(runbook + '\nrm -f "${MAVERICK_HALT_FILE}"\n', encoding="utf-8")
    monkeypatch.setattr(MODULE, "LEGACY_RUNBOOK", path)
    with pytest.raises(MODULE.OperationsContractError, match="unsafe halt"):
        MODULE.verify()


def test_operations_contract_refuses_dashboard_signal_gap(tmp_path, monkeypatch):
    dashboard = json.loads((ROOT / MODULE.DASHBOARD).read_text(encoding="utf-8"))
    dashboard["panels"] = dashboard["panels"][:-1]
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps(dashboard), encoding="utf-8")
    monkeypatch.setattr(MODULE, "DASHBOARD", path)
    with pytest.raises(MODULE.OperationsContractError, match="dashboard"):
        MODULE.verify()


def test_operations_contract_refuses_all_alert_expressions_removed(
    tmp_path,
    monkeypatch,
):
    assert MODULE.yaml is not None
    rules = MODULE.yaml.safe_load((ROOT / MODULE.RULES).read_text(encoding="utf-8"))
    for group in rules["groups"]:
        for rule in group["rules"]:
            rule.pop("expr", None)
    path = tmp_path / "rules.yaml"
    path.write_text(MODULE.yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(MODULE, "RULES", path)

    with pytest.raises(MODULE.OperationsContractError, match="schema/group drift"):
        MODULE.verify()


def test_operations_contract_refuses_inert_alert_expression(tmp_path, monkeypatch):
    assert MODULE.yaml is not None
    rules = MODULE.yaml.safe_load((ROOT / MODULE.RULES).read_text(encoding="utf-8"))
    rules["groups"][0]["rules"][0]["expr"] = "vector(0)"
    path = tmp_path / "rules.yaml"
    path.write_text(MODULE.yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(MODULE, "RULES", path)

    with pytest.raises(MODULE.OperationsContractError, match="inert or unsafe"):
        MODULE.verify()


def test_operations_contract_refuses_alert_duration_drift(tmp_path, monkeypatch):
    assert MODULE.yaml is not None
    rules = MODULE.yaml.safe_load((ROOT / MODULE.RULES).read_text(encoding="utf-8"))
    rules["groups"][0]["rules"][0]["for"] = "1s"
    path = tmp_path / "rules.yaml"
    path.write_text(MODULE.yaml.safe_dump(rules, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(MODULE, "RULES", path)

    with pytest.raises(MODULE.OperationsContractError, match="for-duration drift"):
        MODULE.verify()


def test_operations_contract_refuses_unreviewed_dashboard_query(
    tmp_path,
    monkeypatch,
):
    dashboard = json.loads((ROOT / MODULE.DASHBOARD).read_text(encoding="utf-8"))
    dashboard["panels"][0]["targets"][0]["expr"] = "maverick_unreviewed_total"
    path = tmp_path / "dashboard.json"
    path.write_text(json.dumps(dashboard), encoding="utf-8")
    monkeypatch.setattr(MODULE, "DASHBOARD", path)

    with pytest.raises(MODULE.OperationsContractError, match="dashboard query drift"):
        MODULE.verify()


def test_operations_contract_refuses_duplicate_yaml_keys(tmp_path, monkeypatch):
    rules = (ROOT / MODULE.RULES).read_text(encoding="utf-8")
    marker = "      - alert: MaverickDown"
    rules = rules.replace(marker, marker + "\n        alert: MaverickDown", 1)
    path = tmp_path / "rules.yaml"
    path.write_text(rules, encoding="utf-8")
    monkeypatch.setattr(MODULE, "RULES", path)

    with pytest.raises(MODULE.OperationsContractError, match="duplicate YAML key"):
        MODULE.verify()


def test_operations_contract_refuses_source_digest_drift(tmp_path, monkeypatch):
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["source_bindings"][0]["sha256"] = "0" * 64
    path = tmp_path / "operations.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.OperationsContractError, match="source binding drifted"):
        MODULE.verify()


def test_operations_contract_refuses_duplicate_json_keys(tmp_path, monkeypatch):
    raw = (ROOT / MODULE.CONTRACT).read_text(encoding="utf-8")
    marker = '  "status": "source_controls_complete_live_operations_unqualified",'
    raw = raw.replace(marker, marker + '\n  "status": "production_ready",', 1)
    path = tmp_path / "operations.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.OperationsContractError, match="cannot read"):
        MODULE.verify()


def test_operations_contract_refuses_non_finite_json(tmp_path, monkeypatch):
    raw = (ROOT / MODULE.CONTRACT).read_text(encoding="utf-8")
    raw = raw.replace('"protected_scopes": [', '"protected_scopes": [NaN,', 1)
    path = tmp_path / "operations.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setattr(MODULE, "CONTRACT", path)

    with pytest.raises(MODULE.OperationsContractError, match="cannot read"):
        MODULE.verify()


def test_operations_verifier_honors_explicit_root(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    contract = json.loads((ROOT / MODULE.CONTRACT).read_text(encoding="utf-8"))
    contract["status"] = "production_ready"
    (release / MODULE.CONTRACT.name).write_text(json.dumps(contract), encoding="utf-8")
    (release / MODULE.SURFACE.name).write_bytes((ROOT / MODULE.SURFACE).read_bytes())

    with pytest.raises(MODULE.OperationsContractError, match="live-unqualified"):
        MODULE.verify(tmp_path)
