from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_verifier():
    path = REPO_ROOT / "scripts" / "verify_service_objectives.py"
    spec = importlib.util.spec_from_file_location("verify_service_objectives", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _contract() -> dict:
    return json.loads(
        (REPO_ROOT / "release" / "service-objectives.json").read_text(
            encoding="utf-8"
        )
    )


def _root_with_contract(tmp_path: Path, contract: dict) -> Path:
    release = tmp_path / "release"
    release.mkdir(parents=True)
    for name in ("production-surface-manifest.json", "release-state.json"):
        shutil.copy2(REPO_ROOT / "release" / name, release / name)
    (release / "service-objectives.json").write_text(
        json.dumps(contract, indent=2) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _approved_contract() -> dict:
    contract = _contract()
    contract["status"] = "approved"
    targets = {
        "availability": 99.5,
        "api_latency_p95_ms": 1500,
        "recovery_time_seconds": 14400,
        "recovery_point_seconds": 900,
        "data_durability_percent": 99.99,
        "support_response_sev1_seconds": 1800,
        "audit_anchor_lag_seconds": 93600,
    }
    for objective in contract["objectives"]:
        objective["target"] = targets[objective["id"]]
        objective["measurement_source"] = (
            f"external-evidence:{objective['id']}#sha256=" + "a" * 64
        )
    contract["error_budget_policy"]["exception_authority"] = (
        "change-control:service-objective-exception-board"
    )
    contract["approval"] = {
        "approved_at": "2026-08-03T12:00:00Z",
        "expires_at": "2027-08-03T12:00:00Z",
        "approvers": [
            {"principal": "person:service-owner", "role": "service-owner"},
            {"principal": "person:sre-owner", "role": "site-reliability"},
        ],
    }
    return contract


def test_repository_contract_is_valid_but_explicitly_unapproved():
    verifier = _load_verifier()

    summary = verifier.validate_service_objectives(REPO_ROOT)

    assert summary == {
        "profile_id": "enterprise-single-control-plane-v1",
        "source_version": "0.1.7",
        "status": "draft",
        "objective_count": 7,
        "unset_targets": 7,
        "release_candidate": False,
    }


def test_release_candidate_refuses_unapproved_objectives():
    verifier = _load_verifier()

    with pytest.raises(
        verifier.ServiceObjectiveError,
        match="not approved",
    ):
        verifier.validate_service_objectives(REPO_ROOT, release_candidate=True)


def test_distinct_time_bounded_approvals_and_complete_targets_can_pass(tmp_path):
    verifier = _load_verifier()
    root = _root_with_contract(tmp_path, _approved_contract())

    summary = verifier.validate_service_objectives(
        root,
        release_candidate=True,
        now=datetime(2026, 8, 3, 13, tzinfo=timezone.utc),
    )

    assert summary["status"] == "approved"
    assert summary["unset_targets"] == 0
    assert summary["release_candidate"] is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["objectives"][0].update(target=None),
            "unset targets",
        ),
        (
            lambda value: value["objectives"][0].update(target=101),
            "cannot exceed 100",
        ),
        (
            lambda value: value["objectives"][0].update(
                measurement_source="latest-dashboard"
            ),
            "not immutable evidence",
        ),
        (
            lambda value: value["objectives"].pop(),
            "inventory is incomplete",
        ),
        (
            lambda value: value["approval"]["approvers"][1].update(
                principal="person:service-owner"
            ),
            "approvers must be distinct",
        ),
        (
            lambda value: value["error_budget_policy"].update(
                freeze_on_exhaustion=False
            ),
            "must freeze risky releases",
        ),
    ],
)
def test_release_objective_contract_fails_closed(tmp_path, mutation, message):
    verifier = _load_verifier()
    contract = _approved_contract()
    mutation(contract)
    root = _root_with_contract(tmp_path, contract)

    with pytest.raises(verifier.ServiceObjectiveError, match=message):
        verifier.validate_service_objectives(
            root,
            release_candidate=True,
            now=datetime(2026, 8, 3, 13, tzinfo=timezone.utc),
        )


def test_expired_approval_and_profile_drift_are_refused(tmp_path):
    verifier = _load_verifier()
    expired = _approved_contract()
    expired["approval"]["expires_at"] = "2026-08-03T12:30:00Z"
    root = _root_with_contract(tmp_path / "expired", expired)
    with pytest.raises(verifier.ServiceObjectiveError, match="expired"):
        verifier.validate_service_objectives(
            root,
            release_candidate=True,
            now=datetime(2026, 8, 3, 13, tzinfo=timezone.utc),
        )

    drifted = _approved_contract()
    drifted["profile_id"] = "some-other-profile"
    root = _root_with_contract(tmp_path / "drifted", drifted)
    with pytest.raises(verifier.ServiceObjectiveError, match="different profile"):
        verifier.validate_service_objectives(root)


def test_cli_release_candidate_is_nonzero_for_current_draft():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO_ROOT / "scripts" / "verify_service_objectives.py"),
            "--root",
            str(REPO_ROOT),
            "--release-candidate",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "not approved" in result.stderr


def test_fixture_helpers_do_not_mutate_repository_contract():
    before = _contract()
    approved = _approved_contract()

    assert before["status"] == "draft"
    assert approved["status"] == "approved"
    assert _contract() == copy.deepcopy(before)
