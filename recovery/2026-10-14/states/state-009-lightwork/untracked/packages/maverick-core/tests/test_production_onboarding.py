"""Fail-closed tests for production onboarding evidence and its source contract."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from maverick.production_identity import (
    POSTURE_DIGEST_ENV,
    PROVIDER_ENV,
    runtime_identity_posture,
)
from maverick.production_onboarding import (
    AUTHORITY_KEY_ID_ENV,
    AUTHORITY_PUBLIC_KEY_ENV,
    CONTRACT_DIGEST_ENV,
    DEPLOYMENT_CONFIG_DIGEST_ENV,
    DEPLOYMENT_ID_ENV,
    EVIDENCE_MANIFEST_SCHEMA,
    OBSERVATIONS_SCHEMA,
    RELEASE_STATE_DIGEST_ENV,
    RELEASE_STATE_ENV,
    SOURCE_REVISION_ENV,
    ProductionOnboardingError,
    evaluate,
)

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / "release" / "production-onboarding-contract.json"
SCRIPT = ROOT / "scripts" / "verify_production_onboarding_contract.py"
SPEC = importlib.util.spec_from_file_location(
    "verify_production_onboarding_contract",
    SCRIPT,
)
assert SPEC is not None and SPEC.loader is not None
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
REVISION = "a" * 40
DEPLOYMENT_ID = "urn:lightwork:deployment:production-test"
DEPLOYMENT_CONFIG_SHA256 = "b" * 64


def _identity_config() -> dict:
    return {
        "auth": {
            "oidc": {
                "enabled": True,
                "issuer": "https://idp.example.test/",
                "audience": "lightwork",
                "jwks_uri": "https://idp.example.test/jwks.json",
                "algorithms": ["RS256"],
                "client_id": "lightwork-dashboard",
            },
            "proxy": {"enabled": False},
        },
        "dashboard": {
            "default_role": "viewer",
            "admins": ["user:break-glass"],
            "group_roles": {"Operators": "operator"},
        },
    }


@pytest.fixture(autouse=True)
def _clear_contract_custody(monkeypatch):
    for name in (
        "MAVERICK_PRODUCTION_ONBOARDING_CONTRACT",
        CONTRACT_DIGEST_ENV,
        RELEASE_STATE_ENV,
        RELEASE_STATE_DIGEST_ENV,
        SOURCE_REVISION_ENV,
        DEPLOYMENT_ID_ENV,
        DEPLOYMENT_CONFIG_DIGEST_ENV,
        AUTHORITY_KEY_ID_ENV,
        AUTHORITY_PUBLIC_KEY_ENV,
        PROVIDER_ENV,
        POSTURE_DIGEST_ENV,
        "MAVERICK_DASHBOARD_ADMINS",
        "MAVERICK_DASHBOARD_INVITES",
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_OIDC_ALGORITHMS",
        "MAVERICK_OIDC_AUDIENCE",
        "MAVERICK_OIDC_CLIENT_ID",
        "MAVERICK_OIDC_ENABLED",
        "MAVERICK_OIDC_ISSUER",
        "MAVERICK_OIDC_JWKS_URI",
        "MAVERICK_PROXY_AUTH",
        "MAVERICK_SAML_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", raising=False)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _eligible_contract(tmp_path: Path) -> tuple[Path, dict]:
    contract = _json(CONTRACT_PATH)
    contract["release_eligible"] = True
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(contract), encoding="utf-8")
    return path, contract


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def _release_state(tmp_path: Path, contract: dict) -> Path:
    path = tmp_path / "release-state.json"
    path.write_text(
        json.dumps(
            {
                "schema": "lightwork.release-state.v1",
                "repository": contract["repository"],
                "source_version": contract["source_version"],
                "source_state": "release_candidate",
            }
        ),
        encoding="utf-8",
    )
    return path


def _authority(monkeypatch) -> Ed25519PrivateKey:
    from maverick import config as config_module

    identity_config = _identity_config()
    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda *args, **kwargs: identity_config,
    )
    monkeypatch.setattr(config_module, "config_source_errors", dict)
    identity_posture = runtime_identity_posture(config=identity_config)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv(
        AUTHORITY_KEY_ID_ENV,
        "urn:lightwork:onboarding-authority:test",
    )
    monkeypatch.setenv(AUTHORITY_PUBLIC_KEY_ENV, public.hex())
    monkeypatch.setenv(DEPLOYMENT_ID_ENV, DEPLOYMENT_ID)
    monkeypatch.setenv(
        DEPLOYMENT_CONFIG_DIGEST_ENV,
        DEPLOYMENT_CONFIG_SHA256,
    )
    monkeypatch.setenv(PROVIDER_ENV, identity_posture.provider)
    monkeypatch.setenv(POSTURE_DIGEST_ENV, identity_posture.sha256)
    return private


def _set_custody(
    monkeypatch,
    *,
    contract_path: Path,
    release_state_path: Path,
) -> None:
    monkeypatch.setenv(CONTRACT_DIGEST_ENV, _sha256(contract_path))
    monkeypatch.setenv(RELEASE_STATE_ENV, str(release_state_path))
    monkeypatch.setenv(RELEASE_STATE_DIGEST_ENV, _sha256(release_state_path))
    monkeypatch.setenv(SOURCE_REVISION_ENV, REVISION)


def _observations(
    contract: dict,
    *,
    contract_path: Path,
    release_state_path: Path,
    private: Ed25519PrivateKey,
) -> dict:
    rows = []
    counter = 0
    for check in contract["checks"]:
        evidence = []
        for requirement in check["evidence_requirements"]:
            counter += 1
            reference = f"urn:lightwork:test-evidence:{counter}"
            digest = f"{counter:064x}"
            evidence_id = hashlib.sha256(
                "\0".join(
                    (check["id"], requirement, reference, digest)
                ).encode()
            ).hexdigest()
            evidence.append(
                {
                    "evidence_id": evidence_id,
                    "requirement": requirement,
                    "reference": reference,
                    "sha256": digest,
                }
            )
        rows.append(
            {
                "id": check["id"],
                "status": "pass",
                "checked_at": "2026-08-04T11:00:00Z",
                "expires_at": "2026-08-05T11:00:00Z",
                "evidence": evidence,
                "runtime_binding": (
                    {
                        "provider": "oidc",
                        "posture_sha256": os.environ[POSTURE_DIGEST_ENV],
                    }
                    if check["id"] == "identity_auth"
                    else None
                ),
            }
        )
    manifest = {
        "schema": EVIDENCE_MANIFEST_SCHEMA,
        "profile_id": contract["profile_id"],
        "source_version": contract["source_version"],
        "source_revision": REVISION,
        "deployment_id": DEPLOYMENT_ID,
        "deployment_config_sha256": DEPLOYMENT_CONFIG_SHA256,
        "contract_sha256": _sha256(contract_path),
        "release_state_sha256": _sha256(release_state_path),
        "release_state_source_state": "release_candidate",
        "nonce": "1" * 64,
        "issued_at": "2026-08-04T11:00:00Z",
        "expires_at": "2026-08-05T11:00:00Z",
        "checks": rows,
    }
    manifest_bytes = _canonical(manifest)
    return {
        "schema": OBSERVATIONS_SCHEMA,
        "evidence_manifest": manifest,
        "evidence_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "authority": {
            "algorithm": "Ed25519",
            "key_id": "urn:lightwork:onboarding-authority:test",
            "signature": private.sign(manifest_bytes).hex(),
        },
    }


def _resign(observations: dict, private: Ed25519PrivateKey) -> None:
    manifest_bytes = _canonical(observations["evidence_manifest"])
    observations["evidence_manifest_sha256"] = hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    observations["authority"]["signature"] = private.sign(manifest_bytes).hex()


def _signed_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path, dict, dict, Ed25519PrivateKey]:
    contract_path, contract = _eligible_contract(tmp_path)
    state_path = _release_state(tmp_path, contract)
    private = _authority(monkeypatch)
    _set_custody(
        monkeypatch,
        contract_path=contract_path,
        release_state_path=state_path,
    )
    observations = _observations(
        contract,
        contract_path=contract_path,
        release_state_path=state_path,
        private=private,
    )
    return contract_path, state_path, contract, observations, private


def test_missing_live_observations_are_explicit_and_fail_closed() -> None:
    report = evaluate(contract_file=CONTRACT_PATH, now=NOW)
    assert report.ready is False
    assert report.pass_count == 0
    assert report.fail_count == 0
    assert report.not_checked_count == 11
    assert report.blockers[0] == "release-contract-not-eligible"
    assert {row.status for row in report.checks} == {"not_checked"}


def test_exact_current_evidence_can_pass_an_eligible_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    report = evaluate(
        contract_file=contract_path,
        observations_file=observations_path,
        release_state_file=state_path,
        now=NOW,
    )
    assert report.ready is True
    assert report.pass_count == 11
    assert report.fail_count == report.not_checked_count == 0
    assert report.source_revision == REVISION
    assert report.deployment_id == DEPLOYMENT_ID
    assert report.deployment_config_sha256 == DEPLOYMENT_CONFIG_SHA256
    assert report.authority_key_id == "urn:lightwork:onboarding-authority:test"
    assert report.evidence_manifest_sha256 == observations["evidence_manifest_sha256"]
    assert report.blockers == ()


def test_missing_digest_evidence_turns_claimed_pass_into_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    observations["evidence_manifest"]["checks"][0]["evidence"] = []
    _resign(observations, private)
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    report = evaluate(
        contract_file=contract_path,
        observations_file=observations_path,
        release_state_file=state_path,
        now=NOW,
    )
    assert report.ready is False
    assert report.checks[0].status == "fail"
    assert report.checks[0].id in report.blockers


def test_expired_evidence_fails_and_unknown_checks_are_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, contract, observations, private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    observations["evidence_manifest"]["checks"][0]["expires_at"] = (
        "2026-08-04T11:30:00Z"
    )
    _resign(observations, private)
    observations_path = tmp_path / "expired.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    report = evaluate(
        contract_file=contract_path,
        observations_file=observations_path,
        release_state_file=state_path,
        now=NOW,
    )
    assert report.checks[0].status == "fail"
    assert "expired" in report.checks[0].detail.lower()

    observations = _observations(
        contract,
        contract_path=contract_path,
        release_state_path=state_path,
        private=private,
    )
    observations["evidence_manifest"]["checks"].append(
        {
            "id": "unreviewed_check",
            "status": "not_checked",
            "checked_at": None,
            "expires_at": None,
            "evidence": [],
            "runtime_binding": None,
        }
    )
    _resign(observations, private)
    observations_path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="unreviewed check IDs"):
        evaluate(
            contract_file=contract_path,
            observations_file=observations_path,
            release_state_file=state_path,
            now=NOW,
        )


def test_fake_zero_revision_and_made_up_refs_cannot_self_attest_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    manifest = observations["evidence_manifest"]
    manifest["source_revision"] = "0" * 40
    for index, evidence in enumerate(manifest["checks"][0]["evidence"], start=1):
        evidence["reference"] = f"urn:lightwork:made-up:{index}"
        evidence["sha256"] = f"{index + 900:064x}"
        evidence["evidence_id"] = hashlib.sha256(
            "\0".join(
                (
                    manifest["checks"][0]["id"],
                    evidence["requirement"],
                    evidence["reference"],
                    evidence["sha256"],
                )
            ).encode()
        ).hexdigest()
    _resign(observations, private)
    observations_path = tmp_path / "fake-self-attestation.json"
    observations_path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(
        ProductionOnboardingError,
        match="exact contract, release state, revision, and deployment target",
    ):
        evaluate(
            contract_file=contract_path,
            observations_file=observations_path,
            release_state_file=state_path,
            now=NOW,
        )


def test_signed_evidence_for_another_deployment_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    path = tmp_path / "other-deployment.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    monkeypatch.setenv(DEPLOYMENT_ID_ENV, "urn:lightwork:deployment:different")
    with pytest.raises(ProductionOnboardingError, match="deployment target"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_signed_evidence_for_same_id_but_other_deployment_config_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    path = tmp_path / "other-deployment-config.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    monkeypatch.setenv(DEPLOYMENT_CONFIG_DIGEST_ENV, "c" * 64)
    with pytest.raises(ProductionOnboardingError, match="deployment target"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def _identity_row(observations: dict) -> dict:
    return next(
        row
        for row in observations["evidence_manifest"]["checks"]
        if row["id"] == "identity_auth"
    )


def test_identity_binding_is_required_and_exact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    _identity_row(observations).pop("runtime_binding")
    _resign(observations, private)
    path = tmp_path / "missing-identity-binding.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="fields differ"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )

    observations = _observations(
        _contract,
        contract_path=contract_path,
        release_state_path=state_path,
        private=private,
    )
    _identity_row(observations)["runtime_binding"]["alternate_provider"] = (
        "trusted_proxy_sso"
    )
    _resign(observations, private)
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="identity_auth runtime binding"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_unsigned_identity_binding_forgery_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    _identity_row(observations)["runtime_binding"]["provider"] = (
        "trusted_proxy_sso"
    )
    path = tmp_path / "forged-identity-binding.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="manifest digest"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_signed_identity_binding_drift_from_runtime_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    monkeypatch.setenv(PROVIDER_ENV, "trusted_proxy_sso")
    path = tmp_path / "drifted-identity-binding.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="current runtime identity/RBAC"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_signed_identity_binding_rejects_current_static_rbac_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick import config as config_module

    contract_path, state_path, _contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    changed = _identity_config()
    changed["dashboard"]["group_roles"]["Operators"] = "viewer"
    monkeypatch.setattr(
        config_module,
        "load_config",
        lambda *args, **kwargs: changed,
    )
    path = tmp_path / "current-rbac-drift.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    with pytest.raises(ProductionOnboardingError, match="current runtime identity/RBAC"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_stateless_re_evaluation_is_truthfully_not_a_global_replay_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, contract, observations, _private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    path = tmp_path / "reusable-manifest.json"
    path.write_text(json.dumps(observations), encoding="utf-8")
    first = evaluate(
        contract_file=contract_path,
        observations_file=path,
        release_state_file=state_path,
        now=NOW,
    )
    second = evaluate(
        contract_file=contract_path,
        observations_file=path,
        release_state_file=state_path,
        now=NOW,
    )
    assert first.ready is second.ready is True
    assert first.evidence_manifest_sha256 == second.evidence_manifest_sha256
    assert "stateless" in second.boundary
    assert contract["evidence_authority"]["duplicate_replay_policy"] == (
        "reject-within-manifest-evidence-id-and-artifact-reuse"
    )


def test_signed_evidence_replay_across_requirements_is_rejected(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, state_path, _contract, observations, private = _signed_fixture(
        tmp_path,
        monkeypatch,
    )
    first = observations["evidence_manifest"]["checks"][0]["evidence"][0]
    replay = observations["evidence_manifest"]["checks"][1]["evidence"][0]
    replay["reference"] = first["reference"]
    replay["sha256"] = first["sha256"]
    replay["evidence_id"] = hashlib.sha256(
        "\0".join(
            (
                observations["evidence_manifest"]["checks"][1]["id"],
                replay["requirement"],
                replay["reference"],
                replay["sha256"],
            )
        ).encode()
    ).hexdigest()
    _resign(observations, private)
    path = tmp_path / "replayed.json"
    path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(ProductionOnboardingError, match="uniquely cover"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_arbitrary_eligible_contract_without_custody_cannot_become_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    contract_path, contract = _eligible_contract(tmp_path)
    state_path = _release_state(tmp_path, contract)
    private = _authority(monkeypatch)
    monkeypatch.setenv(SOURCE_REVISION_ENV, REVISION)
    observations = _observations(
        contract,
        contract_path=contract_path,
        release_state_path=state_path,
        private=private,
    )
    path = tmp_path / "observations.json"
    path.write_text(json.dumps(observations), encoding="utf-8")

    with pytest.raises(ProductionOnboardingError, match="custody is missing"):
        evaluate(
            contract_file=contract_path,
            observations_file=path,
            release_state_file=state_path,
            now=NOW,
        )


def test_production_contract_digest_custody_is_enforced(monkeypatch) -> None:
    monkeypatch.setenv("MAVERICK_REQUIRE_PRODUCTION_SURFACE", "1")
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_ONBOARDING_CONTRACT_SHA256",
        "0" * 64,
    )
    with pytest.raises(ProductionOnboardingError, match="digest does not match"):
        evaluate(contract_file=CONTRACT_PATH, now=NOW)


def test_cli_is_production_classified_and_missing_observations_exit_nonzero() -> None:
    from maverick.cli import main
    from maverick.production_exclusions import cli_command_classification

    assert cli_command_classification("production-onboarding-readiness") == "core"
    result = CliRunner().invoke(
        main,
        ["production-onboarding-readiness", "--contract", str(CONTRACT_PATH)],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ready"] is False
    assert payload["not_checked_count"] == 11


def test_init_production_is_report_only_and_writes_no_installer_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick.cli import main

    target = tmp_path / "must-not-exist.toml"
    monkeypatch.setenv("MAVERICK_CONFIG", str(target))
    result = CliRunner().invoke(
        main,
        ["init", "--production", "--production-contract", str(CONTRACT_PATH)],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["ready"] is False
    assert payload["not_checked_count"] == 11
    assert not target.exists()


def test_init_refuses_config_mutation_options_in_production_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick.cli import main

    source = tmp_path / "candidate.toml"
    source.write_text('[sandbox]\nbackend = "local"\n', encoding="utf-8")
    target = tmp_path / "must-not-exist.toml"
    monkeypatch.setenv("MAVERICK_CONFIG", str(target))
    result = CliRunner().invoke(
        main,
        ["init", "--production", "--from-file", str(source)],
    )
    assert result.exit_code == 2
    assert "report-only" in result.output
    assert not target.exists()


def test_headless_evaluation_config_never_claims_production_ready(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from maverick.cli import main

    source = tmp_path / "candidate.toml"
    source.write_text('[sandbox]\nbackend = "local"\n', encoding="utf-8")
    target = tmp_path / "evaluation.toml"
    monkeypatch.setenv("MAVERICK_CONFIG", str(target))
    result = CliRunner().invoke(main, ["init", "--from-file", str(source)])
    assert result.exit_code == 0, result.output
    assert target.is_file()
    assert "not a production-readiness attestation" in result.output


def _minimal_verifier_root(tmp_path: Path) -> Path:
    contract = _json(CONTRACT_PATH)
    paths = {
        VERIFIER.CONTRACT_PATH.as_posix(),
        VERIFIER.SURFACE_PATH.as_posix(),
        *(row["path"] for row in contract["repository_contracts"]),
        *(row["path"] for row in contract["source_bindings"]),
    }
    for relative in paths:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    return tmp_path


def test_onboarding_source_contract_is_exact_and_refuses_drift(tmp_path: Path) -> None:
    messages = VERIFIER.verify(ROOT)
    assert "verified 11 production onboarding domains" in messages
    root = _minimal_verifier_root(tmp_path)
    contract_path = root / VERIFIER.CONTRACT_PATH
    contract = _json(contract_path)
    contract["checks"][0]["required"] = False
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(
        VERIFIER.ProductionOnboardingContractError,
        match="check inventory drifted",
    ):
        VERIFIER.verify(root)
