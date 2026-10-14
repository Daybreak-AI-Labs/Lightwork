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


def _load_script(name: str):
    path = REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _valid_namespace_document() -> dict:
    document = json.loads(
        (
            REPO_ROOT
            / "release"
            / "namespace-ownership-attestation.template.json"
        ).read_text(encoding="utf-8")
    )
    document["attested_at"] = "2026-08-03T20:00:00Z"
    document["expires_at"] = "2026-08-31T20:00:00Z"
    for index, entry in enumerate(document["namespaces"], start=1):
        entry["ownership"] = {
            "verified": True,
            "registry_account": "daybreak-release-owner",
            "evidence_reference": f"registry-review:2026-08-03:{index}",
        }
        entry["trusted_publisher"]["verified"] = entry["publish_authorized"]
    return document


def test_release_state_reconciles_the_current_checkout():
    verifier = _load_script("verify_release_state")

    summary = verifier.verify_release_state(REPO_ROOT)

    assert summary == {
        "repository": "Daybreak-AI-Labs/Lightwork",
        "source_version": "0.1.7",
        "source_state": "unreleased",
        "latest_canonical_tag": "v0.1.6",
        "latest_canonical_commit": "86760df9882bdb48692a9776058e8013eed05300",
        "github_release_count": 0,
        "python_distribution_count": 8,
        "independent_distribution_count": 3,
        "production_release_eligible": False,
        "production_readiness_status": "draft",
        "pytest_exception_count": 444,
        "production_critical_pytest_exception_count": 227,
        "production_model_policy_status": "draft",
        "production_support_matrix_rows": 18,
        "dependency_failure_contract_rows": 13,
        "claim_evidence_ledger_status": "draft",
        "claim_evidence_ledger_claims": 47,
        "production_code_inventory_tests": 18888,
        "production_migration_contract_status": (
            "repository-contract-not-live-qualification-evidence"
        ),
        "secret_rotation_contract_rows": 8,
        "tenant_quota_contract_resources": 6,
        "production_api_contract_operations": 69,
        "production_onboarding_domains": 11,
        "production_identity_provider_count": 2,
        "production_data_boundary_flows": 9,
        "production_operations_alerts": 26,
        "production_qa_gate_statuses": 6,
        "production_runtime_contract_status": (
            "code-derived-contract-live-cluster-proof-required"
        ),
        "service_objectives_status": "draft",
        "production_unresolved_required_gates": 82,
    }

    state = json.loads(
        (REPO_ROOT / "release" / "release-state.json").read_text(encoding="utf-8")
    )
    assert state["manifests"]["pytest_exceptions"] == "qa/pytest-exceptions.json"
    assert state["manifests"]["dependency_failure_contract"] == (
        "release/dependency-failure-contract.json"
    )
    assert state["manifests"]["production_support_matrix"] == (
        "release/production-support-matrix.json"
    )
    assert state["manifests"]["claim_evidence_ledger"] == (
        "release/claim-evidence-ledger.json"
    )
    assert state["manifests"]["production_code_inventory"] == (
        "release/production-code-inventory.json"
    )
    assert state["manifests"]["production_api_contract"] == (
        "release/production-api-contract.json"
    )
    assert state["manifests"]["production_openapi_schema"] == (
        "release/production-openapi-schema.json"
    )
    assert state["manifests"]["production_onboarding_contract"] == (
        "release/production-onboarding-contract.json"
    )
    assert state["manifests"]["production_data_boundary"] == (
        "release/production-data-boundary.json"
    )
    assert state["manifests"]["production_identity_posture_contract"] == (
        "release/production-identity-posture-contract.json"
    )
    assert state["manifests"]["production_operations_contract"] == (
        "release/production-operations-contract.json"
    )
    assert state["manifests"]["production_qa_assurance"] == (
        "release/production-qa-assurance.json"
    )
    assert state["manifests"]["production_runtime_contract"] == (
        "release/production-runtime-contract.json"
    )
    assert state["manifests"]["production_migration_contract"] == (
        "release/production-migration-contract.json"
    )
    assert state["manifests"]["secret_rotation_contract"] == (
        "release/secret-rotation-contract.json"
    )
    assert state["manifests"]["tenant_quota_contract"] == (
        "release/tenant-quota-contract.json"
    )
    surface = json.loads(
        (REPO_ROOT / "release" / "production-surface-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    pytest_governance = next(
        item
        for item in surface["surfaces"]
        if item["id"] == "feature:pytest-exception-governance"
    )
    assert pytest_governance == {
        "id": "feature:pytest-exception-governance",
        "category": "feature",
        "status": "supported_beta",
        "included": True,
    }


def test_release_state_remote_input_is_explicit_and_exact():
    verifier = _load_script("verify_release_state")

    verifier.verify_release_state(REPO_ROOT, github_releases=[])
    with pytest.raises(
        verifier.ReleaseStateError,
        match="fresh GitHub release inventory differs",
    ):
        verifier.verify_release_state(
            REPO_ROOT,
            github_releases=[
                {
                    "tagName": "v0.1.6",
                    "isDraft": False,
                    "isPrerelease": False,
                }
            ],
        )


def test_release_state_fresh_inventory_allows_only_the_exact_candidate():
    verifier = _load_script("verify_release_state")
    state = json.loads(
        (REPO_ROOT / "release" / "release-state.json").read_text(
            encoding="utf-8"
        )
    )
    candidate = {
        "tagName": "v0.1.7",
        "isDraft": True,
        "isPrerelease": False,
    }

    verifier._validate_github_release_observation(
        state,
        github_releases=[candidate],
        release_tag="v0.1.7",
        require_candidate_release=True,
    )
    with pytest.raises(
        verifier.ReleaseStateError,
        match="omits the exact candidate release",
    ):
        verifier._validate_github_release_observation(
            state,
            github_releases=[],
            release_tag="v0.1.7",
            require_candidate_release=True,
        )
    with pytest.raises(
        verifier.ReleaseStateError,
        match="differs from release-state snapshot",
    ):
        verifier._validate_github_release_observation(
            state,
            github_releases=[
                candidate,
                {
                    "tagName": "v0.1.6",
                    "isDraft": False,
                    "isPrerelease": False,
                },
            ],
            release_tag="v0.1.7",
            require_candidate_release=True,
        )
    with pytest.raises(verifier.ReleaseStateError, match="duplicate tags"):
        verifier._validate_github_release_observation(
            state,
            github_releases=[candidate, candidate],
            release_tag="v0.1.7",
            require_candidate_release=True,
        )


@pytest.mark.parametrize(
    "payload",
    (
        '{"schema":"first","schema":"substituted"}',
        '{"schema":"lightwork.release-state.v1","value":NaN}',
    ),
)
def test_release_state_reader_rejects_ambiguous_json(tmp_path, payload):
    verifier = _load_script("verify_release_state")
    path = tmp_path / "release-state.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(verifier.ReleaseStateError, match="not valid UTF-8 JSON"):
        verifier._read_json(path, label="release state")


def test_release_state_requires_the_exact_identity_posture_contract(tmp_path):
    verifier = _load_script("verify_release_state")
    root = tmp_path / "repo"
    path = root / "release" / "production-identity-posture-contract.json"
    path.parent.mkdir(parents=True)
    shutil.copy2(
        REPO_ROOT / "release" / "production-identity-posture-contract.json",
        path,
    )
    state = {
        "manifests": {
            "production_identity_posture_contract": (
                "release/production-identity-posture-contract.json"
            )
        }
    }

    assert verifier._validate_production_identity_posture_contract(root, state) == {
        "provider_count": 2,
        "selection_cardinality": "exactly_one",
    }
    document = json.loads(path.read_text(encoding="utf-8"))
    document["supported_providers"].append("saml")
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        verifier.ReleaseStateError,
        match="differs from the reviewed boundary",
    ):
        verifier._validate_production_identity_posture_contract(root, state)

    state["manifests"]["production_identity_posture_contract"] = (
        "release/unreviewed-identity.json"
    )
    with pytest.raises(
        verifier.ReleaseStateError,
        match="does not bind the production identity posture contract",
    ):
        verifier._validate_production_identity_posture_contract(root, state)


def test_current_source_cannot_be_treated_as_release_ready():
    verifier = _load_script("verify_release_state")

    with pytest.raises(
        verifier.ReleaseStateError,
        match="release-ready verification requires --release-tag",
    ):
        verifier.verify_release_state(
            REPO_ROOT,
            require_release_ready=True,
        )


def test_namespace_inventory_covers_every_cohort_and_sdk_name():
    verifier = _load_script("verify_namespace_ownership")

    inventory = verifier.expected_namespaces(REPO_ROOT)

    assert set(inventory) == {
        ("pypi", "maverick-agent"),
        ("pypi", "maverick-shield"),
        ("pypi", "maverick-channels"),
        ("pypi", "maverick-evolve"),
        ("pypi", "maverick-dashboard"),
        ("pypi", "maverick-mcp-server"),
        ("pypi", "maverick-knowledge"),
        ("pypi", "maverick-installer"),
        ("pypi", "maverick-native"),
        ("npm", "@maverick/plugin-sdk"),
        ("npm", "mvk-scan-wasm"),
    }
    assert inventory[("npm", "@maverick/plugin-sdk")]["publish_authorized"] is False
    assert inventory[("pypi", "maverick-native")]["publish_authorized"] is False
    assert inventory[("npm", "mvk-scan-wasm")]["publish_authorized"] is False


def test_namespace_attestation_requires_exact_current_authority():
    verifier = _load_script("verify_namespace_ownership")
    document = _valid_namespace_document()
    now = datetime(2026, 8, 4, tzinfo=timezone.utc)

    summary = verifier.validate_attestation_document(
        REPO_ROOT,
        document,
        now=now,
    )

    assert summary["namespace_count"] == 11
    assert summary["publish_authorized_count"] == 8
    assert summary["release_version"] == "0.1.7"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document["namespaces"][0]["ownership"].update(
                {"verified": False}
            ),
            "registry ownership is unverified",
        ),
        (
            lambda document: document["namespaces"][0]["trusted_publisher"].update(
                {"workflow": "other.yml"}
            ),
            "Trusted Publisher identity differs",
        ),
        (
            lambda document: document["namespaces"].pop(),
            "namespace inventory differs",
        ),
        (
            lambda document: document.update(
                {"expires_at": "2026-08-03T20:00:01Z"}
            ),
            "has expired",
        ),
    ],
)
def test_namespace_attestation_rejects_unproven_or_stale_claims(mutation, message):
    verifier = _load_script("verify_namespace_ownership")
    document = copy.deepcopy(_valid_namespace_document())
    mutation(document)

    with pytest.raises(verifier.NamespaceOwnershipError, match=message):
        verifier.validate_attestation_document(
            REPO_ROOT,
            document,
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )


def test_namespace_cli_fails_closed_without_live_signed_evidence():
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            str(REPO_ROOT / "scripts" / "verify_namespace_ownership.py"),
            "--root",
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "namespace ownership attestation is missing" in result.stderr


def test_namespace_gate_verifies_the_exact_ssh_signed_bytes(tmp_path):
    ssh_keygen = shutil.which("ssh-keygen")
    if ssh_keygen is None:
        pytest.skip("ssh-keygen is not installed")
    verifier = _load_script("verify_namespace_ownership")
    attestation = tmp_path / "namespace-ownership-attestation.json"
    attestation.write_text(
        json.dumps(_valid_namespace_document(), indent=2) + "\n",
        encoding="utf-8",
    )
    key = tmp_path / "attestor"
    generated = subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert generated.returncode == 0, generated.stderr
    public_fields = (tmp_path / "attestor.pub").read_text(encoding="ascii").split()
    allowed_signers = tmp_path / "allowed-signers"
    allowed_signers.write_text(
        f"{verifier.SIGNER_IDENTITY} {public_fields[0]} {public_fields[1]}\n",
        encoding="ascii",
    )
    signed = subprocess.run(
        [
            ssh_keygen,
            "-Y",
            "sign",
            "-f",
            str(key),
            "-n",
            verifier.SIGNATURE_NAMESPACE,
            str(attestation),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert signed.returncode == 0, signed.stderr

    summary = verifier.verify_namespace_ownership(
        REPO_ROOT,
        attestation_path=attestation,
        signature_path=Path(f"{attestation}.sig"),
        allowed_signers_path=allowed_signers,
        now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        ssh_keygen=ssh_keygen,
    )

    assert summary["namespace_count"] == 11


def test_publish_workflow_requires_all_release_authorities_before_oidc():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")

    assert "python -I scripts/verify_release_state.py" in workflow
    assert "--require-release-ready" in workflow
    assert "--github-releases-json \"$inventory\"" in workflow
    assert "--require-candidate-github-release" in workflow
    assert "gh release list" in workflow
    assert "python -I scripts/verify_namespace_ownership.py --root ." in workflow
    assert "python -I scripts/verify_pytest_exceptions.py --root ." in workflow
    assert "python -I scripts/verify_production_change_control.py --root ." in workflow
    publish_job = workflow.split("\n  publish:", 1)[1].split(
        "\n  verify-published:", 1
    )[0]
    for authority in (
        "finalize-release",
        "release-authority",
        "namespace-authority",
        "change-control-authority",
    ):
        assert authority in publish_job
    assert workflow.index("release-authority:") < workflow.index("  publish:")
    assert workflow.index("namespace-authority:") < workflow.index("  publish:")
    assert workflow.index("change-control-authority:") < workflow.index("  publish:")


def test_release_workflow_requires_authority_and_signs_the_production_contract():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "release.yml"
    ).read_text(encoding="utf-8")
    validation_job = workflow.split("\n  validate-release-tag:", 1)[1].split(
        "\n  docker-image:", 1
    )[0]
    release_job = workflow.split("\n  release:", 1)[1]

    assert "python -I scripts/verify_release_state.py" in validation_job
    assert "--release-tag \"$tag\"" in validation_job
    assert "--require-release-ready" in validation_job
    assert "python -I scripts/verify_production_change_control.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_pytest_exceptions.py --root ." in validation_job
    assert (
        "python -I scripts/verify_claim_evidence_ledger.py --root . "
        "--release-candidate"
    ) in validation_job
    assert "python -I scripts/verify_production_code_inventory.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_api_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_data_boundary.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_onboarding_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_operations_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_qa_assurance.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_runtime_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_production_migration_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_secret_rotation_contract.py --root ." in (
        validation_job
    )
    assert "python -I scripts/verify_tenant_quota_contract.py --root ." in (
        validation_job
    )
    assert "environment: production-release" in release_job
    stage = release_job.index("Stage the exact production governance packet")
    checksum = release_job.index("Generate a complete release checksum manifest")
    signing = release_job.index("Sign release artifacts (keyless, Sigstore)")
    upload = release_job.index("Create the signed release as a draft")
    assert stage < checksum < signing < upload
    assert "release/production-surface-manifest.json" in release_job
    assert "release/claim-evidence-ledger.json" in release_job
    assert "release/production-code-inventory.json" in release_job
    assert "release/production-api-contract.json" in release_job
    assert "release/production-openapi-schema.json" in release_job
    assert "release/production-onboarding-contract.json" in release_job
    assert "release/production-data-boundary.json" in release_job
    assert "release/production-identity-posture-contract.json" in release_job
    assert "release/production-operations-contract.json" in release_job
    assert "release/production-qa-assurance.json" in release_job
    assert "release/production-runtime-contract.json" in release_job
    assert "release/production-migration-contract.json" in release_job
    assert "release/secret-rotation-contract.json" in release_job
    assert "release/tenant-quota-contract.json" in release_job
    assert "release/production-support-matrix.json" in release_job
    assert "release/production-readiness-evidence.json" in release_job
    assert "release/production-model-policy.json" in release_job
    assert "release/service-objectives.json" in release_job
    assert "release/production-change-control-policy.json" in release_job
    assert "release/dependency-failure-contract.json" in release_job
    assert "qa/pytest-exceptions.json" in release_job
    assert "release/release-state.json" in release_job
    assert 'f"lightwork-production-surface-{version}.json"' in release_job
    assert 'f"lightwork-claim-evidence-ledger-{version}.json"' in release_job
    assert 'f"lightwork-production-code-inventory-{version}.json"' in release_job
    assert 'f"lightwork-production-api-contract-{version}.json"' in release_job
    assert 'f"lightwork-production-openapi-schema-{version}.json"' in release_job
    assert 'f"lightwork-production-onboarding-contract-{version}.json"' in release_job
    assert 'f"lightwork-production-data-boundary-{version}.json"' in release_job
    assert (
        'f"lightwork-production-identity-posture-contract-{version}.json"'
        in release_job
    )
    assert 'f"lightwork-production-operations-contract-{version}.json"' in release_job
    assert 'f"lightwork-production-qa-assurance-{version}.json"' in release_job
    assert 'f"lightwork-production-runtime-contract-{version}.json"' in release_job
    assert 'f"lightwork-production-migration-contract-{version}.json"' in release_job
    assert 'f"lightwork-secret-rotation-contract-{version}.json"' in release_job
    assert 'f"lightwork-tenant-quota-contract-{version}.json"' in release_job
    assert "not isinstance(claim_approvers, list)" in release_job
    assert "not isinstance(secret_rotation_deployment, dict)" in release_job
    assert "not isinstance(tenant_quota_deployment, dict)" in release_job
    assert "not isinstance(tenant_quota_qualification, list)" in release_job
    assert 'f"lightwork-dependency-failure-contract-{version}.json"' in release_job
    assert 'f"lightwork-production-support-matrix-{version}.json"' in release_job
    assert 'f"lightwork-production-readiness-{version}.json"' in release_job
    assert 'f"lightwork-production-model-policy-{version}.json"' in release_job
    assert 'f"lightwork-service-objectives-{version}.json"' in release_job
    assert 'f"lightwork-production-change-control-{version}.json"' in release_job
    assert 'f"lightwork-pytest-exceptions-{version}.json"' in release_job
    assert 'f"lightwork-release-state-{version}.json"' in release_job
    assert 'release_state.get("eligible") is not True' in release_job
    assert 'readiness.get("status") != "ready"' in release_job
    assert 'readiness.get("assessed_source_revision") != revision' not in release_job
    assert '"lightwork.production-readiness-evidence.v3"' in release_job
    assert "readiness_source_content_identity(Path(\".\"))" in release_job
    assert (
        'readiness.get("assessed_source_content") != expected_readiness_source'
        in release_job
    )
    assert 'model_policy.get("status") != "approved"' in release_job
    assert 'service_objectives.get("status") != "approved"' in release_job
    assert 'support_matrix.get("release_eligible") is not True' in release_job
    assert (
        'identity_posture_contract != expected_identity_posture_contract'
        in release_job
    )
    assert 'pytest_exceptions.get("schema") != "lightwork.pytest-exceptions.v1"' in (
        release_job
    )
    assert 'manifests.get("pytest_exceptions") != "qa/pytest-exceptions.json"' in (
        release_job
    )
    assert 'manifests.get("production_support_matrix")' in release_job
    assert 'manifests.get("dependency_failure_contract")' in release_job
    assert 'manifests.get("claim_evidence_ledger")' in release_job
    assert 'manifests.get("production_code_inventory")' in release_job
    assert 'manifests.get("production_api_contract")' in release_job
    assert 'manifests.get("production_openapi_schema")' in release_job
    assert 'manifests.get("production_onboarding_contract")' in release_job
    assert 'manifests.get("production_data_boundary")' in release_job
    assert 'manifests.get("production_identity_posture_contract")' in release_job
    assert 'manifests.get("production_operations_contract")' in release_job
    assert 'manifests.get("production_qa_assurance")' in release_job
    assert 'manifests.get("production_runtime_contract")' in release_job
    assert 'manifests.get("production_migration_contract")' in release_job
    assert 'manifests.get("secret_rotation_contract")' in release_job
    assert 'manifests.get("tenant_quota_contract")' in release_job
    assert 'release_state_document.get("source_state") != "release_candidate"' in (
        release_job
    )
    assert "artifacts=(dist/maverick-* dist/lightwork-*)" in release_job


def _change_control_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relative in (
        ".github/CODEOWNERS",
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        ".github/workflows/publish.yml",
        ".github/workflows/native.yml",
        "release/release-state.json",
        "release/production-change-control-policy.json",
    ):
        source = REPO_ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return root


def test_production_change_control_policy_matches_repository_bindings():
    verifier = _load_script("verify_production_change_control")

    summary = verifier.verify_production_change_control(REPO_ROOT)

    assert summary == {
        "repository": "Daybreak-AI-Labs/Lightwork",
        "source_version": "0.1.7",
        "workflow_control_count": 7,
        "write_capable_job_count": 6,
        "codeowner_pattern_count": 156,
        "external_evidence_requirement_count": 18,
        "external_evidence_status": "required-not-verified-by-this-tool",
    }

    policy = json.loads(
        (
            REPO_ROOT / "release" / "production-change-control-policy.json"
        ).read_text(encoding="utf-8")
    )
    external = {item["id"]: item for item in policy["external_evidence_requirements"]}
    assert "postgres-tenant-rls-deployment" in external
    assert "postgres-encryption-migration" in external
    assert "production-migration-qualification" in external
    assert "secret-rotation-qualification" in external
    assert "tenant-quota-capacity-qualification" in external
    assert "claim-evidence-approval" in external
    assert "production-runtime-cluster-qualification" in external
    assert "production-data-processing-qualification" in external
    assert "production-operations-qualification" in external
    assert "production-api-qualification" in external
    assert "production-qa-security-qualification" in external
    assert "production-onboarding-qualification" in external
    assert all(
        external[control]["evidence_boundary"]
        == "external-required-not-verified-by-repository"
        for control in (
            "postgres-tenant-rls-deployment",
            "postgres-encryption-migration",
            "production-migration-qualification",
            "secret-rotation-qualification",
            "tenant-quota-capacity-qualification",
            "claim-evidence-approval",
            "production-runtime-cluster-qualification",
            "production-data-processing-qualification",
            "production-operations-qualification",
            "production-api-qualification",
            "production-qa-security-qualification",
            "production-onboarding-qualification",
        )
    )
    required_patterns = policy["code_owner_routing"]["required_patterns"]
    assert "*" in required_patterns
    assert "/packages/maverick-core/" in required_patterns
    assert "/packages/maverick-dashboard/" in required_patterns
    assert "/.github/workflows/" in required_patterns


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda source: source.replace(
                "/packages/maverick-core/maverick/production_identity.py @cdayAI",
                "/packages/maverick-core/maverick/production_identity.py @attacker",
                1,
            ),
            "owners differ from the reviewed tuple",
        ),
        (
            lambda source: source
            + "\n/packages/maverick-core/maverick/production_identity.py @attacker\n",
            "repeats protected pattern",
        ),
        (
            lambda source: source + "\n**/production_identity.py @attacker\n",
            "unreviewed routing patterns",
        ),
    ),
)
def test_production_change_control_rejects_codeowner_override_paths(
    tmp_path,
    mutation,
    message,
):
    verifier = _load_script("verify_production_change_control")
    root = _change_control_root(tmp_path)
    codeowners = root / ".github" / "CODEOWNERS"
    source = codeowners.read_text(encoding="utf-8")
    mutated = mutation(source)
    assert mutated != source
    codeowners.write_text(mutated, encoding="utf-8")

    with pytest.raises(verifier.ChangeControlError, match=message):
        verifier.verify_production_change_control(root)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda source: source.replace(
            "          python -I scripts/verify_production_change_control.py --root .",
            "          echo 'python -I scripts/verify_production_change_control.py --root .'",
            1,
        ),
        lambda source: source.replace(
            "          python -I scripts/verify_production_change_control.py --root .",
            "          if false; then\n"
            "            python -I scripts/verify_production_change_control.py --root .\n"
            "          fi",
            1,
        ),
        lambda source: source.replace(
            "          python -I scripts/verify_production_change_control.py --root .",
            "          python -I scripts/verify_production_change_control.py --root . || true",
            1,
        ),
        lambda source: source.replace(
            "      - name: Verify workflow environments, dependencies, and write boundaries\n"
            "        run: |",
            "      - name: Verify workflow environments, dependencies, and write boundaries\n"
            "        if: false\n"
            "        run: |",
            1,
        ),
        lambda source: source.replace(
            "          python -I scripts/verify_production_change_control.py --root .",
            "          python -I scripts/verify_production_change_control.py --root .\n"
            "          echo \"#\"; malicious-command",
            1,
        ),
    ),
)
def test_production_change_control_binds_complete_authority_job_semantics(
    tmp_path,
    mutation,
):
    verifier = _load_script("verify_production_change_control")
    root = _change_control_root(tmp_path)
    workflow_path = root / ".github" / "workflows" / "publish.yml"
    source = workflow_path.read_text(encoding="utf-8")
    mutated = mutation(source)
    assert mutated != source
    workflow_path.write_text(mutated, encoding="utf-8")

    with pytest.raises(
        verifier.ChangeControlError,
        match="authority job digest differs from policy",
    ):
        verifier.verify_production_change_control(root)


def test_production_change_control_authority_digest_preserves_shell_whitespace():
    verifier = _load_script("verify_production_change_control")
    baseline = (
        "  authority:\n"
        "    run: |\n"
        "      command \\\n"
        "        --flag\n"
    )

    assert verifier._authority_job_sha256(baseline) != (
        verifier._authority_job_sha256(baseline.replace("command \\", "command \\ "))
    )
    assert verifier._authority_job_sha256(baseline) != (
        verifier._authority_job_sha256(baseline.replace("    run: |\n", "    run: |\n\n"))
    )
    assert verifier._authority_job_sha256(baseline) == (
        verifier._authority_job_sha256(baseline.replace("\n", "\r\n"))
    )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document["normal_change_policy"].update(
                {"minimum_independent_approvals": 0}
            ),
            "normal change policy differs",
        ),
        (
            lambda document: next(
                control
                for control in document["workflow_controls"]
                if control["id"] == "github-release-finalization"
            ).update({"environment": "unprotected"}),
            "github-release-finalization differs",
        ),
        (
            lambda document: document["external_evidence_requirements"][0].update(
                {"evidence_boundary": "repository-verified"}
            ),
            "github-main-protection differs",
        ),
    ],
)
def test_production_change_control_rejects_weakened_or_false_policy_claims(
    mutation,
    message,
):
    verifier = _load_script("verify_production_change_control")
    document = json.loads(
        (
            REPO_ROOT / "release" / "production-change-control-policy.json"
        ).read_text(encoding="utf-8")
    )
    mutation(document)

    with pytest.raises(verifier.ChangeControlError, match=message):
        verifier.validate_policy_document(REPO_ROOT, document)


def test_production_change_control_rejects_an_unprotected_finalizer(tmp_path):
    verifier = _load_script("verify_production_change_control")
    root = _change_control_root(tmp_path)
    workflow_path = root / ".github" / "workflows" / "publish.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    protected = (
        "    environment: production-release\n"
        "    timeout-minutes: 30\n"
    )
    assert protected in workflow
    workflow_path.write_text(
        workflow.replace(protected, "    timeout-minutes: 30\n", 1),
        encoding="utf-8",
    )

    with pytest.raises(verifier.ChangeControlError, match="no environment field"):
        verifier.verify_production_change_control(root)


def test_production_change_control_rejects_an_unclassified_write_job(tmp_path):
    verifier = _load_script("verify_production_change_control")
    root = _change_control_root(tmp_path)
    workflow_path = root / ".github" / "workflows" / "release.yml"
    with workflow_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "\n  unreviewed-mutation:\n"
            "    runs-on: ubuntu-24.04\n"
            "    permissions:\n"
            "      contents: write\n"
            "    steps:\n"
            "      - run: gh release edit v0.0.0 --draft=false\n"
        )

    with pytest.raises(verifier.ChangeControlError, match="unclassified write-capable"):
        verifier.verify_production_change_control(root)


def test_production_change_control_rejects_enabling_native_publication(tmp_path):
    verifier = _load_script("verify_production_change_control")
    root = _change_control_root(tmp_path)
    workflow_path = root / ".github" / "workflows" / "native.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    disabled = "if: startsWith(github.ref, 'refs/tags/native-v') && false"
    assert disabled in workflow
    workflow_path.write_text(
        workflow.replace(
            disabled,
            "if: startsWith(github.ref, 'refs/tags/native-v')",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        verifier.ChangeControlError,
        match="native-pypi-publication mutation condition is missing: false",
    ):
        verifier.verify_production_change_control(root)


def test_finalizer_requires_default_branch_change_control_authority():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    authority = workflow.split("\n  change-control-authority:", 1)[1].split(
        "\n  namespace-authority:", 1
    )[0]
    finalizer = workflow.split("\n  finalize-release:", 1)[1]

    assert "ref: ${{ github.sha }}" in authority
    assert "MAVERICK_PUBLIC_PYPI_ENABLED" not in authority
    assert "environment: production-release" in finalizer
    assert "change-control-authority" in "\n".join(finalizer.split("\n", 8)[0:8])
    assert "release-authority" in "\n".join(finalizer.split("\n", 8)[0:8])
    assert "needs.release-authority.result == 'success'" in finalizer
    assert "needs.change-control-authority.result == 'success'" in finalizer


def test_public_release_is_reverified_before_container_and_pypi_mutation():
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")
    publish_job = workflow.split("\n  publish:", 1)[1].split(
        "\n  verify-published:", 1
    )[0]
    finalizer = workflow.split("\n  finalize-release:", 1)[1]

    assert "finalize-release" in "\n".join(publish_job.split("\n", 10)[:10])
    assert "needs.finalize-release.result == 'success'" in publish_job
    draft_verify = finalizer.index("Verify every signed byte in the exact release state")
    public_verify = finalizer.index(
        "Publish and reverify the exact GitHub cohort before registry promotion"
    )
    container_promote = finalizer.index(
        "Promote and verify the signed exact-version container tag"
    )
    assert draft_verify < public_verify < container_promote
    assert "PyPI is deliberately downstream of this job" in finalizer
    assert "Exact container promotion verified; PyPI publication may now begin" in (
        finalizer
    )
    assert (
        'plan="$RUNNER_TEMP/lightwork-published-release-assets/'
        'maverick-container-${RELEASE_TAG}.json"'
    ) in finalizer
    assert "lightwork-final-release-assets/maverick-container-" not in finalizer


def test_native_registry_mutations_remain_explicitly_unauthorized():
    state = json.loads(
        (REPO_ROOT / "release" / "release-state.json").read_text(encoding="utf-8")
    )
    native = {
        (record["registry"], record["name"]): record
        for record in state["independent_distributions"]
        if record["workflow"] == "native.yml"
    }
    assert set(native) == {
        ("pypi", "maverick-native"),
        ("npm", "mvk-scan-wasm"),
    }
    assert all(record["publish_authorized"] is False for record in native.values())

    workflow = (
        REPO_ROOT / ".github" / "workflows" / "native.yml"
    ).read_text(encoding="utf-8")
    publish_pypi = workflow.split("\n  publish-pypi:", 1)[1].split(
        "\n  publish-npm:", 1
    )[0]
    publish_npm = workflow.split("\n  publish-npm:", 1)[1]
    assert "&& false" in publish_pypi
    assert "\n      false\n" in publish_npm
    assert "environment: pypi" in publish_pypi
    assert "environment: npm" in publish_npm
    assert "NPM_TOKEN" not in publish_npm
    assert "npm publish" not in publish_npm
