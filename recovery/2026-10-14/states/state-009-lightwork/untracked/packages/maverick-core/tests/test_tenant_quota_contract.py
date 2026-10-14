from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "verify_tenant_quota_contract.py"
SPEC = importlib.util.spec_from_file_location("tenant_quota_verifier", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)

VALID_DIGEST = "sha256:" + ("a" * 64)
VALID_AUTHORIZATION_SHA = "b" * 64
VALID_SOURCE_REVISION = "c" * 40
VALID_ONBOARDING_CONTRACT_SHA = "d" * 64
VALID_RELEASE_STATE_SHA = "e" * 64
VALID_AUTHORITY_PUBLIC_KEY = "f" * 64
VALID_IDENTITY_POSTURE_SHA = "1" * 64

OIDC_IDENTITY_ARGS = (
    "--set",
    "identity.mode=oidc",
    "--set-string",
    "identity.config.oidc.issuer=https://idp.example.test/",
    "--set-string",
    "identity.config.oidc.audience=lightwork",
    "--set-string",
    "identity.config.oidc.jwksUri=https://idp.example.test/jwks.json",
    "--set",
    "identity.config.oidc.algorithms[0]=RS256",
    "--set",
    "identity.config.oidc.algorithms[1]=ES256",
    "--set-string",
    "identity.config.oidc.clientId=lightwork-dashboard",
    "--set-string",
    "identity.config.oidc.redirectUri=https://lightwork.example.test/auth/callback",
    "--set",
    "identity.config.dashboard.defaultRole=viewer",
    "--set",
    "identity.config.dashboard.admins[0]=user:break-glass",
    "--set",
    "identity.config.dashboard.defaultSuites[0]=finance",
    "--set",
    "identity.config.dashboard.groupRoles.PlatformAdmins=admin",
    "--set-json",
    'identity.config.dashboard.groupSuites.Finance=["finance"]',
    "--set-json",
    (
        'identity.config.kernel.roles.restricted={"allowTools":'
        '["knowledge_search"],"denyTools":["exec"],"maxRisk":"low",'
        '"allowPaths":["/workspace/**"],"allowHosts":[]}'
    ),
    "--set",
    "identity.config.kernel.roleAssignments.default=restricted",
)


def _copy_contract(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    fixed = (
        verifier.CONTRACT_PATH,
        verifier.SURFACE_PATH,
        verifier.HELM_README_PATH,
        verifier.RUNBOOK_PATH,
    )
    for relative in fixed:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    contract = json.loads((root / verifier.CONTRACT_PATH).read_text(encoding="utf-8"))
    copied_sources = set(contract["code_evidence"]) | set(contract["source_bindings"])
    for relative_text in copied_sources:
        relative = Path(relative_text)
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes((ROOT / relative).read_bytes())
    return root


def _refresh_source_binding(root: Path, relative: Path) -> None:
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["source_bindings"][relative.as_posix()] = verifier._normalized_sha256(
        root / relative,
        f"test source binding {relative.as_posix()}",
    )
    path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")


def _json_quota_helm_command(overlay: Path) -> list[str]:
    return [
        shutil.which("helm") or "helm",
        "template",
        "maverick",
        str(ROOT / "deploy/helm/maverick"),
        "-f",
        str(ROOT / "deploy/helm/maverick/values-production.yaml"),
        "-f",
        str(overlay),
        "--set",
        f"image.digest={VALID_DIGEST}",
        "--set",
        "secret.name=maverick-secrets",
        "--set",
        "secret.rotationGeneration=g000001",
        "--set",
        "productionOnboarding.deploymentId=urn:lightwork:deployment:quota-contract",
        "--set",
        f"productionOnboarding.sourceRevision={VALID_SOURCE_REVISION}",
        "--set",
        f"productionOnboarding.contractSha256={VALID_ONBOARDING_CONTRACT_SHA}",
        "--set",
        f"productionOnboarding.releaseStateSha256={VALID_RELEASE_STATE_SHA}",
        "--set",
        "productionOnboarding.authorityKeyId=urn:lightwork:key:quota-contract",
        "--set",
        f"productionOnboarding.authorityPublicKey={VALID_AUTHORITY_PUBLIC_KEY}",
        "--set",
        f"productionOnboarding.identityPostureSha256={VALID_IDENTITY_POSTURE_SHA}",
        *OIDC_IDENTITY_ARGS,
        "--set",
        "migration.secretName=maverick-migration",
        "--set",
        f"migration.authorizationSha256={VALID_AUTHORIZATION_SHA}",
        "--set",
        "migration.allowedEgressCIDRs[0]=10.44.0.10/32",
        "--set",
        "model.name=approved-model",
        "--set",
        f"model.artifactDigest={VALID_DIGEST}",
        "--set",
        "model.baseURL=https://vllm.example.test/v1",
        "--set",
        "model.region=us-east-1",
        "--set",
        "model.maxSpendUSD=100",
        "--set",
        "kms.address=https://vault.example.test",
        "--set",
        "indeterminateReconciliation.escalationTarget=incident-response",
        "--set",
        "indeterminateReconciliation.adjudicators[0]=user:incident-commander",
        "--set",
        "tenantQuota.policyGeneration=1",
        "--set",
        "networkPolicy.allowedEgressCIDRs[0]=10.44.0.0/24",
        "--set",
        "networkPolicy.dnsCIDRs[0]=10.96.0.10/32",
        "--set",
        "networkPolicy.allowedIngressNamespaces[0]=ingress-nginx",
        "--set",
        "ingress.className=nginx",
        "--set",
        "ingress.hosts[0].host=lightwork.example.test",
        "--set",
        "ingress.tls[0].secretName=lightwork-tls",
        "--set",
        "ingress.tls[0].hosts[0]=lightwork.example.test",
    ]


def _write_json_quota_overlay(tmp_path: Path, policy: object) -> Path:
    path = tmp_path / "quota-json.yaml"
    encoded = json.dumps(policy, allow_nan=False, separators=(",", ":"))
    path.write_text(
        "tenantQuota:\n  limitsJSON: >-\n    " + encoded + "\n",
        encoding="utf-8",
    )
    return path


def test_committed_tenant_quota_contract_is_exact_and_conservative() -> None:
    assert verifier.verify_tenant_quota_contract(ROOT) == {
        "schema": verifier.SCHEMA,
        "resources": 6,
        "scalar_environment_variables": 6,
    }


def test_contract_refuses_duplicate_json_keys(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    text = path.read_text(encoding="utf-8").replace(
        '  "schema": "lightwork.tenant-quota-contract.v1",',
        '  "schema": "lightwork.tenant-quota-contract.v1",\n'
        '  "schema": "lightwork.tenant-quota-contract.v1",',
        1,
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(verifier.TenantQuotaContractError, match="duplicate keys"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_a_missing_resource(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["resources"] = contract["resources"][:-1]
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.TenantQuotaContractError, match="incomplete"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_storage_coverage_overclaim(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    storage = next(row for row in contract["resources"] if row["id"] == "storage_bytes")
    storage["coverage"] = "all_tenant_storage"
    storage["limitation"] = "none"
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.TenantQuotaContractError, match="reviewed enforcement"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_release_ready_overclaim(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["qualification"]["release_ready_claimed"] = True
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.TenantQuotaContractError, match="overclaims"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_generation_fence_drift(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.CONTRACT_PATH
    contract = json.loads(path.read_text(encoding="utf-8"))
    contract["generation"]["minimum"] = 0
    path.write_text(json.dumps(contract), encoding="utf-8")

    with pytest.raises(verifier.TenantQuotaContractError, match="generation"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_helm_source_exclusivity_removal(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.HELPERS_PATH
    text = path.read_text(encoding="utf-8").replace(
        "exactly one tenant quota policy source",
        "multiple tenant quota sources allowed",
    )
    path.write_text(text, encoding="utf-8")
    _refresh_source_binding(root, verifier.HELPERS_PATH)

    with pytest.raises(verifier.TenantQuotaContractError, match="Helm validation"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_missing_scalar_deployment_wiring(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.DEPLOYMENT_PATH
    text = path.read_text(encoding="utf-8").replace(
        "name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES",
        "name: REMOVED_TENANT_QUOTA_MAX_STORAGE_BYTES",
    )
    path.write_text(text, encoding="utf-8")
    _refresh_source_binding(root, verifier.DEPLOYMENT_PATH)

    with pytest.raises(verifier.TenantQuotaContractError, match="deployment lacks"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_first_tenant_only_readiness(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.HEALTH_PATH
    text = path.read_text(encoding="utf-8").replace(
        "    validate_deployment_policy()\n",
        "",
    )
    path.write_text(text, encoding="utf-8")
    _refresh_source_binding(root, verifier.HEALTH_PATH)

    with pytest.raises(verifier.TenantQuotaContractError, match="before the roster"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_runtime_environment_map_drift(tmp_path: Path) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.AUTHORITY_PATH
    text = path.read_text(encoding="utf-8").replace(
        '"storage_bytes": "MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES",',
        '"storage_bytes": "MAVERICK_UNREVIEWED_STORAGE_LIMIT",',
        1,
    )
    path.write_text(text, encoding="utf-8")
    _refresh_source_binding(root, verifier.AUTHORITY_PATH)

    with pytest.raises(verifier.TenantQuotaContractError, match="environment map"):
        verifier.verify_tenant_quota_contract(root)


def test_contract_refuses_commented_out_quota_wiring_with_marker_left_behind(
    tmp_path: Path,
) -> None:
    root = _copy_contract(tmp_path)
    path = root / verifier.DEPLOYMENT_PATH
    active = (
        "            - name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES\n"
        "              value: {{ .Values.tenantQuota.limits.storageBytes | quote }}"
    )
    commented = (
        "            # - name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES\n"
        "            #   value: {{ .Values.tenantQuota.limits.storageBytes | quote }}"
    )
    text = path.read_text(encoding="utf-8")
    assert active in text
    path.write_text(text.replace(active, commented, 1), encoding="utf-8")
    assert "name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES" in path.read_text(
        encoding="utf-8"
    )

    with pytest.raises(
        verifier.TenantQuotaContractError,
        match=r"source binding changed: .*deployment[.]yaml",
    ):
        verifier.verify_tenant_quota_contract(root)


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_helm_renders_the_strict_json_policy_alternative(tmp_path: Path) -> None:
    limits = {
        "concurrent_runs": 8,
        "queued_jobs": 250,
        "daily_tokens": 10_000_000,
        "daily_spend_dollars": 250.0,
        "tool_calls": 50_000,
        "storage_bytes": 10_737_418_240,
    }
    overlay = _write_json_quota_overlay(
        tmp_path,
        {"default": limits, "tenants": {"tenant-priority": {"concurrent_runs": 12}}},
    )

    proc = subprocess.run(
        _json_quota_helm_command(overlay),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert "name: MAVERICK_TENANT_QUOTA_LIMITS_JSON" in proc.stdout
    assert "name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES" not in proc.stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
@pytest.mark.parametrize(
    ("replacement", "expected"),
    [
        ({"concurrent_runs": "8"}, "must be a JSON number"),
        ({"storage_bytes": 9_007_199_254_740_992}, "exact JSON integer range"),
    ],
)
def test_helm_refuses_inexact_json_quota_values(
    tmp_path: Path,
    replacement: dict[str, object],
    expected: str,
) -> None:
    limits: dict[str, object] = {
        "concurrent_runs": 8,
        "queued_jobs": 250,
        "daily_tokens": 10_000_000,
        "daily_spend_dollars": 250.0,
        "tool_calls": 50_000,
        "storage_bytes": 10_737_418_240,
    }
    limits.update(replacement)
    overlay = _write_json_quota_overlay(tmp_path, {"default": limits})

    proc = subprocess.run(
        _json_quota_helm_command(overlay),
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode != 0
    assert expected in proc.stderr
