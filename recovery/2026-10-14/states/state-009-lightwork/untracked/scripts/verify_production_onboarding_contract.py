#!/usr/bin/env python3
"""Verify the fail-closed production onboarding report contract."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.production-onboarding-contract.v2"
REPOSITORY = "Daybreak-AI-Labs/Lightwork"
EVIDENCE_AUTHORITY_POLICY = {
    "manifest_schema": "lightwork.production-onboarding-evidence-manifest.v2",
    "signature_algorithm": "Ed25519",
    "authority_custody": "out-of-band-pinned-public-key",
    "contract_binding": "exact-byte-sha256",
    "release_state_binding": "exact-byte-sha256-release-candidate",
    "source_revision_binding": "trusted-runtime-revision",
    "deployment_identity_binding": (
        "out-of-band-pinned-deployment-id-and-config-sha256"
    ),
    "identity_posture_binding": (
        "signed-check-row-to-out-of-band-provider-and-posture-sha256"
    ),
    "requirement_coverage": "exactly-once-per-contract-requirement",
    "duplicate_replay_policy": (
        "reject-within-manifest-evidence-id-and-artifact-reuse"
    ),
}
CONTRACT_PATH = Path("release/production-onboarding-contract.json")
SURFACE_PATH = Path("release/production-surface-manifest.json")
SOURCE_BINDING_PATHS = (
    "apps/installer-cli/README.md",
    "apps/installer-cli/maverick_installer/bridge.py",
    "apps/installer-cli/maverick_installer/main.py",
    "apps/installer-cli/maverick_installer/wizard.py",
    "apps/installer-cli/tests/test_bridge_consumer_flow.py",
    "apps/installer-cli/tests/test_entrypoint_and_abort.py",
    "apps/installer-desktop/src/App.svelte",
    "deploy/docker/Dockerfile.production",
    "deploy/desktop/install.ps1",
    "deploy/desktop/install.sh",
    "deploy/helm/README.md",
    "deploy/helm/maverick/templates/_helpers.tpl",
    "deploy/helm/maverick/templates/deployment.yaml",
    "deploy/helm/maverick/templates/identity-configmap.yaml",
    "deploy/helm/maverick/values-production.yaml",
    "deploy/helm/maverick/values.yaml",
    "deploy/vps/install.sh",
    "docs/operator-first-run.md",
    "packages/maverick-core/maverick/cli/__init__.py",
    "packages/maverick-core/maverick/cli/_ops_groups.py",
    "packages/maverick-core/maverick/dashboard_rbac_policy.py",
    "packages/maverick-core/maverick/deployment.py",
    "packages/maverick-core/maverick/oidc.py",
    "packages/maverick-core/maverick/production_exclusions.py",
    "packages/maverick-core/maverick/production_identity.py",
    "packages/maverick-core/maverick/production_onboarding.py",
    "packages/maverick-core/maverick/proxy_auth.py",
    "packages/maverick-core/tests/test_helm_production_profile.py",
    "packages/maverick-core/tests/test_production_identity.py",
    "packages/maverick-core/tests/test_production_onboarding.py",
    "packages/maverick-core/tests/test_proxy_auth.py",
    "packages/maverick-dashboard/maverick_dashboard/endpoint_policy.py",
    "packages/maverick-dashboard/maverick_dashboard/rbac.py",
    "packages/maverick-dashboard/tests/test_endpoint_policy.py",
)
REPOSITORY_CONTRACTS = (
    (
        "production_surface",
        "release/production-surface-manifest.json",
        "lightwork.production-surface.v1",
    ),
    (
        "production_runtime",
        "release/production-runtime-contract.json",
        "lightwork.production-runtime-contract.v1",
    ),
    (
        "production_identity_posture",
        "release/production-identity-posture-contract.json",
        "lightwork.production-identity-posture-contract.v1",
    ),
    (
        "production_model_policy",
        "release/production-model-policy.json",
        "lightwork.production-model-policy.v1",
    ),
    (
        "production_support_matrix",
        "release/production-support-matrix.json",
        "lightwork.production-support-matrix.v1",
    ),
    (
        "production_data_boundary",
        "release/production-data-boundary.json",
        "lightwork.production-data-boundary.v1",
    ),
    (
        "production_operations",
        "release/production-operations-contract.json",
        "lightwork.production-operations-contract.v1",
    ),
    (
        "dependency_failure",
        "release/dependency-failure-contract.json",
        "lightwork.dependency-failure-contract.v1",
    ),
    (
        "production_migration",
        "release/production-migration-contract.json",
        "schema_version:1",
    ),
    (
        "secret_rotation",
        "release/secret-rotation-contract.json",
        "lightwork.secret-rotation-contract.v1",
    ),
    (
        "tenant_quota",
        "release/tenant-quota-contract.json",
        "lightwork.tenant-quota-contract.v1",
    ),
    (
        "service_objectives",
        "release/service-objectives.json",
        "lightwork.service-objectives.v1",
    ),
    (
        "production_readiness",
        "release/production-readiness-evidence.json",
        "lightwork.production-readiness-evidence.v3",
    ),
    (
        "change_control",
        "release/production-change-control-policy.json",
        "lightwork.production-change-control-policy.v2",
    ),
    (
        "claim_evidence",
        "release/claim-evidence-ledger.json",
        "lightwork.claim-evidence-ledger.v1",
    ),
)

CHECKS = (
    {
        "id": "provider_model_policy",
        "label": "Provider and model policy",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_model_policy",
            "production_support_matrix",
        ],
        "evidence_requirements": [
            "exact served-model artifact and runtime identity",
            "approved endpoint region retention cap and failover policy",
            "live request and failure-path qualification",
        ],
    },
    {
        "id": "identity_auth",
        "label": "Identity and authentication",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_identity_posture",
            "production_runtime",
            "production_support_matrix",
            "dependency_failure",
        ],
        "evidence_requirements": [
            "exact issuer or trusted-proxy identity mapping",
            "revocation logout and break-glass adverse exercise",
            "cross-tenant and unauthenticated denial result",
        ],
    },
    {
        "id": "sandbox_isolation",
        "label": "Sandbox and isolation",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_runtime",
            "production_support_matrix",
        ],
        "evidence_requirements": [
            "exact runtime class and container image identity",
            "deployed containment and no-host-execution adverse result",
            "network and filesystem isolation observation",
        ],
    },
    {
        "id": "shield_safety_boundary",
        "label": "Shield and safety boundary",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_runtime",
            "production_operations",
        ],
        "evidence_requirements": [
            "required Shield policy and model-output boundary",
            "adversarial input and required-redaction failure result",
            "consequential-action refusal observation",
        ],
    },
    {
        "id": "audit_signer_key_custody",
        "label": "Audit signer and key custody",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_runtime",
            "production_data_boundary",
            "secret_rotation",
        ],
        "evidence_requirements": [
            "off-host signer custody and key identity",
            "signed chain verification and off-host anchor",
            "historical verification after rotation and recovery",
        ],
    },
    {
        "id": "postgres_storage_migrations",
        "label": "Postgres storage and migrations",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_data_boundary",
            "production_migration",
            "dependency_failure",
        ],
        "evidence_requirements": [
            "exact TLS role RLS and schema posture",
            "adjacent migration backup restore and interruption exercise",
            "cross-tenant denial and recovery observation",
        ],
    },
    {
        "id": "connectivity_egress_tls",
        "label": "Connectivity, egress, and TLS",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_runtime",
            "dependency_failure",
            "production_support_matrix",
        ],
        "evidence_requirements": [
            "exact ingress certificate DNS and proxy identity",
            "egress allowlist and denied-destination observation",
            "dependency timeout and recovery result",
        ],
    },
    {
        "id": "secrets_rotation",
        "label": "Secrets and rotation",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_runtime",
            "secret_rotation",
        ],
        "evidence_requirements": [
            "exact mounted secret inventory and external custody",
            "overlap rollback validation and old-authority revocation",
            "pod recreation and readiness observation",
        ],
    },
    {
        "id": "tenant_quotas",
        "label": "Tenant quotas and backpressure",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": ["tenant_quota"],
        "evidence_requirements": [
            "exact policy generation and workload-derived limits",
            "live saturation fairness settlement and expiry result",
            "failover reconciliation and capacity observation",
        ],
    },
    {
        "id": "observability",
        "label": "Observability and alerting",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_operations",
            "service_objectives",
            "dependency_failure",
        ],
        "evidence_requirements": [
            "deployed scrape dashboards logs and bounded-cardinality metrics",
            "alert delivery acknowledgement and runbook exercise",
            "objective measurement and error-budget observation",
        ],
    },
    {
        "id": "operational_evidence",
        "label": "Release and operational evidence",
        "required": True,
        "external_observation_required": True,
        "minimum_evidence_items": 3,
        "repository_contract_refs": [
            "production_readiness",
            "change_control",
            "claim_evidence",
            "service_objectives",
        ],
        "evidence_requirements": [
            "signed exact-revision release governance packet",
            "independent approvals and external qualification records",
            "current owner escalation incident and recovery evidence",
        ],
    },
)

CHECKS = tuple(
    {
        **row,
        "runtime_binding": (
            "identity-provider-and-posture-sha256"
            if row["id"] == "identity_auth"
            else None
        ),
    }
    for row in CHECKS
)

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")


class ProductionOnboardingContractError(ValueError):
    """The onboarding contract is stale, incomplete, or not source-derived."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ProductionOnboardingContractError(
                f"{label} is missing or not a non-empty regular file"
            )
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except ProductionOnboardingContractError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionOnboardingContractError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ProductionOnboardingContractError(f"{label} must be an object")
    return value


def _source_sha256(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ProductionOnboardingContractError(
            f"source binding is not readable UTF-8: {path}"
        ) from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _exact_mapping(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProductionOnboardingContractError(f"{label} must be an object")
    if set(value) != expected:
        raise ProductionOnboardingContractError(
            f"{label} fields differ: missing={sorted(expected - set(value))}, "
            f"unknown={sorted(set(value) - expected)}"
        )
    return value


def _repository_contract_rows(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(REPOSITORY_CONTRACTS):
        raise ProductionOnboardingContractError(
            "repository contract inventory is incomplete"
        )
    profile_id = _read_json(root / SURFACE_PATH, label="production surface").get(
        "profile_id"
    )
    actual: list[tuple[str, str, str]] = []
    for index, raw in enumerate(value):
        row = _exact_mapping(
            raw,
            {"id", "path", "schema"},
            label=f"repository_contracts[{index}]",
        )
        identity = (row.get("id"), row.get("path"), row.get("schema"))
        if not all(isinstance(item, str) and item for item in identity):
            raise ProductionOnboardingContractError(
                f"repository_contracts[{index}] has invalid fields"
            )
        actual.append(identity)  # type: ignore[arg-type]
        document = _read_json(root / str(row["path"]), label=str(row["id"]))
        expected_schema = str(row["schema"])
        if expected_schema == "schema_version:1":
            if document.get("schema_version") != 1:
                raise ProductionOnboardingContractError(
                    f"{row['id']} schema_version is unsupported"
                )
        elif document.get("schema") != expected_schema:
            raise ProductionOnboardingContractError(
                f"{row['id']} schema is unsupported"
            )
        document_profile = document.get("profile_id")
        if document_profile is not None and document_profile != profile_id:
            raise ProductionOnboardingContractError(
                f"{row['id']} targets a different production profile"
            )
    if tuple(actual) != REPOSITORY_CONTRACTS:
        raise ProductionOnboardingContractError(
            "repository contract inventory differs from the reviewed order"
        )


def _source_bindings(root: Path, value: object) -> None:
    if not isinstance(value, list) or len(value) != len(SOURCE_BINDING_PATHS):
        raise ProductionOnboardingContractError("source binding inventory is incomplete")
    actual: list[str] = []
    for index, raw in enumerate(value):
        row = _exact_mapping(
            raw,
            {"path", "sha256", "digest_mode"},
            label=f"source_bindings[{index}]",
        )
        path = row.get("path")
        digest = row.get("sha256")
        if (
            not isinstance(path, str)
            or path not in SOURCE_BINDING_PATHS
            or row.get("digest_mode") != "utf8-lf-sha256"
            or not isinstance(digest, str)
            or _DIGEST_RE.fullmatch(digest) is None
        ):
            raise ProductionOnboardingContractError(
                f"source_bindings[{index}] is malformed"
            )
        actual.append(path)
        if _source_sha256(root / path) != digest:
            raise ProductionOnboardingContractError(f"source binding drifted: {path}")
    if tuple(actual) != SOURCE_BINDING_PATHS:
        raise ProductionOnboardingContractError(
            "source bindings differ from the reviewed order"
        )


def _cli_wiring(root: Path) -> None:
    path = root / "packages/maverick-core/maverick/cli/_ops_groups.py"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionOnboardingContractError("production onboarding CLI is invalid") from exc
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "production_onboarding_readiness_cmd"
        ),
        None,
    )
    if function is None:
        raise ProductionOnboardingContractError("production onboarding CLI is missing")
    command_names = []
    for decorator in function.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "command"
            and decorator.args
            and isinstance(decorator.args[0], ast.Constant)
        ):
            command_names.append(decorator.args[0].value)
    if command_names != ["production-onboarding-readiness"]:
        raise ProductionOnboardingContractError(
            "production onboarding command registration drifted"
        )
    calls = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    if not {"evaluate", "render_json"} <= calls:
        raise ProductionOnboardingContractError(
            "production onboarding command bypasses the canonical evaluator"
        )


def _installer_wiring(root: Path) -> None:
    """Prove every supported installer frontend preserves the report-only boundary."""

    standalone_path = root / "apps/installer-cli/maverick_installer/main.py"
    wizard_path = root / "apps/installer-cli/maverick_installer/wizard.py"
    bridge_path = root / "apps/installer-cli/maverick_installer/bridge.py"
    core_path = root / "packages/maverick-core/maverick/cli/__init__.py"
    try:
        standalone_text = standalone_path.read_text(encoding="utf-8")
        wizard_text = wizard_path.read_text(encoding="utf-8")
        bridge_text = bridge_path.read_text(encoding="utf-8")
        core_text = core_path.read_text(encoding="utf-8")
        bridge_tree = ast.parse(bridge_text, filename=str(bridge_path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ProductionOnboardingContractError(
            "installer production boundary is unreadable or invalid"
        ) from exc

    for marker in (
        "--production",
        "--production-contract",
        "--production-observations",
        "--production-release-state",
        "--production is report-only",
        "evaluate_installer_completion",
    ):
        if marker not in standalone_text:
            raise ProductionOnboardingContractError(
                f"standalone installer is missing report-only marker {marker!r}"
            )
    for marker in (
        "--production",
        "--production is report-only",
        "_finish_production_init",
        "evaluate_installer_completion",
        "Evaluation configuration installed; this is not a production-readiness ",
        "attestation.",
    ):
        if marker not in core_text:
            raise ProductionOnboardingContractError(
                f"core init is missing report-only marker {marker!r}"
            )
    if (
        "_finish_production_install" in wizard_text
        or "Lightwork is ready" in wizard_text
        or "[bold green]Setup complete" in wizard_text
        or "[bold green]Fast setup finished" in wizard_text
        or "[bold green]Everything's on" in wizard_text
        or "Evaluation setup" not in wizard_text
        or "production-readiness attestation" not in wizard_text
    ):
        raise ProductionOnboardingContractError(
            "ordinary wizard can claim or invoke production completion"
        )
    if "Lightwork is ready" in bridge_text:
        raise ProductionOnboardingContractError(
            "desktop sidecar still claims evaluation setup is ready"
        )
    bridge_run = next(
        (
            node
            for node in bridge_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "run"
        ),
        None,
    )
    if bridge_run is None:
        raise ProductionOnboardingContractError("desktop sidecar run path is missing")
    call_lines: dict[str, list[int]] = {}
    for node in ast.walk(bridge_run):
        if not isinstance(node, ast.Call):
            continue
        name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        call_lines.setdefault(name, []).append(node.lineno)
    evaluation_lines = call_lines.get("evaluate_installer_completion", [])
    config_lines = call_lines.get("write_consumer_config", [])
    if (
        len(evaluation_lines) != 1
        or len(config_lines) != 1
        or evaluation_lines[0] >= config_lines[0]
        or "report-only" not in bridge_text
        or "does not establish production readiness" not in bridge_text
    ):
        raise ProductionOnboardingContractError(
            "desktop sidecar does not evaluate production before all config writes"
        )

    exact_markers = {
        "deploy/vps/install.sh": "this is not production readiness",
        "deploy/desktop/install.sh": (
            "Installation alone does not establish production readiness"
        ),
        "deploy/desktop/install.ps1": (
            "Installation alone does not establish production readiness"
        ),
        "apps/installer-desktop/src/App.svelte": (
            "Installation and setup checks do not establish production readiness"
        ),
        "apps/installer-cli/README.md": (
            "is report-only:"
        ),
    }
    for relative, marker in exact_markers.items():
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProductionOnboardingContractError(
                f"installer boundary document is unreadable: {relative}"
            ) from exc
        if marker not in text:
            raise ProductionOnboardingContractError(
                f"installer boundary marker is missing: {relative}:{marker}"
            )

    helm_markers = {
        "deploy/helm/maverick/values-production.yaml": (
            "productionOnboarding:",
            "enabled: true",
            "deploymentId:",
            "sourceRevision:",
            "contractSha256:",
            "releaseStateSha256:",
            "authorityKeyId:",
            "authorityPublicKey:",
            "identityPostureSha256:",
            "observationsKey: MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
        ),
        "deploy/helm/maverick/templates/_helpers.tpl": (
            'define "maverick.productionDeploymentConfigSha256"',
            'unset $values "productionOnboarding"',
            'unset $secret "rotationGeneration"',
            "sha256sum (toJson $values)",
            "productionOnboarding.authorityPublicKey",
            "productionOnboarding.identityPostureSha256",
            "productionOnboarding.observationsKey",
        ),
        "deploy/helm/maverick/templates/deployment.yaml": (
            "MAVERICK_PRODUCTION_DEPLOYMENT_ID",
            "MAVERICK_PRODUCTION_DEPLOYMENT_CONFIG_SHA256",
            "MAVERICK_SOURCE_REVISION",
            "MAVERICK_PRODUCTION_ONBOARDING_CONTRACT_SHA256",
            "MAVERICK_PRODUCTION_RELEASE_STATE_SHA256",
            "MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_KEY_ID",
            "MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_PUBLIC_KEY",
            "MAVERICK_PRODUCTION_IDENTITY_PROVIDER",
            "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
            "MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
            'include "maverick.productionDeploymentConfigSha256" .',
            "/run/secrets/lightwork/MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
        ),
        "deploy/helm/README.md": (
            "operator trust pins, not values copied from the observations",
            "production-deployment-config-sha256",
            "external cross-evaluation",
        ),
        "packages/maverick-core/tests/test_helm_production_profile.py": (
            "VALID_DEPLOYMENT_ID",
            "MAVERICK_PRODUCTION_DEPLOYMENT_CONFIG_SHA256",
            "productionOnboarding.observationsKey=OTHER",
        ),
    }
    for relative, markers in helm_markers.items():
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ProductionOnboardingContractError(
                f"production Helm onboarding path is unreadable: {relative}"
            ) from exc
        missing = [marker for marker in markers if marker not in text]
        if missing:
            raise ProductionOnboardingContractError(
                f"production Helm onboarding path is incomplete: {relative}:{missing}"
            )


def build(root: Path) -> dict[str, Any]:
    root = root.resolve()
    surface = _read_json(root / SURFACE_PATH, label="production surface")
    release_state = surface.get("release_state")
    eligible = (
        release_state.get("eligible") if isinstance(release_state, Mapping) else None
    )
    return {
        "schema": SCHEMA,
        "repository": REPOSITORY,
        "profile_id": surface.get("profile_id"),
        "source_version": surface.get("source_version"),
        "release_eligible": eligible,
        "status": "repository-contract-requires-live-deployment-evidence",
        "evidence_authority": dict(EVIDENCE_AUTHORITY_POLICY),
        "max_observation_age_days": 31,
        "checks": list(CHECKS),
        "repository_contracts": [
            {"id": row[0], "path": row[1], "schema": row[2]}
            for row in REPOSITORY_CONTRACTS
        ],
        "source_bindings": [
            {
                "path": path,
                "sha256": _source_sha256(root / path),
                "digest_mode": "utf8-lf-sha256",
            }
            for path in SOURCE_BINDING_PATHS
        ],
        "boundary": {
            "ordinary_wizard_is_production_authority": False,
            "ordinary_installer_completion": "evaluation-only-not-production-ready",
            "installer_production_mode": "report-only-no-config-mutation",
            "production_completion_authority": (
                "signed-deployment-bound-readiness-report"
            ),
            "missing_or_expired_required_observation": "not-ready",
            "cross_evaluation_replay": "requires-external-consumption-ledger",
            "network_calls": "none",
            "state_mutation": "none",
            "legal_certification": False,
        },
    }


def verify(root: Path) -> list[str]:
    root = root.resolve()
    contract = _read_json(root / CONTRACT_PATH, label="production onboarding contract")
    _exact_mapping(
        contract,
        {
            "schema",
            "repository",
            "profile_id",
            "source_version",
            "release_eligible",
            "status",
            "evidence_authority",
            "max_observation_age_days",
            "checks",
            "repository_contracts",
            "source_bindings",
            "boundary",
        },
        label="production onboarding contract",
    )
    surface = _read_json(root / SURFACE_PATH, label="production surface")
    release_state = surface.get("release_state")
    eligible = (
        release_state.get("eligible") if isinstance(release_state, Mapping) else None
    )
    if (
        contract.get("schema") != SCHEMA
        or contract.get("repository") != REPOSITORY
        or contract.get("profile_id") != surface.get("profile_id")
        or contract.get("source_version") != surface.get("source_version")
        or not isinstance(eligible, bool)
        or contract.get("release_eligible") is not eligible
        or contract.get("status")
        != "repository-contract-requires-live-deployment-evidence"
        or contract.get("evidence_authority") != EVIDENCE_AUTHORITY_POLICY
        or contract.get("max_observation_age_days") != 31
    ):
        raise ProductionOnboardingContractError(
            "production onboarding identity or release boundary drifted"
        )
    if contract.get("checks") != list(CHECKS):
        raise ProductionOnboardingContractError(
            "production onboarding check inventory drifted"
        )
    boundary = _exact_mapping(
        contract.get("boundary"),
        {
            "ordinary_wizard_is_production_authority",
            "ordinary_installer_completion",
            "installer_production_mode",
            "production_completion_authority",
            "missing_or_expired_required_observation",
            "cross_evaluation_replay",
            "network_calls",
            "state_mutation",
            "legal_certification",
        },
        label="boundary",
    )
    if dict(boundary) != {
        "ordinary_wizard_is_production_authority": False,
        "ordinary_installer_completion": "evaluation-only-not-production-ready",
        "installer_production_mode": "report-only-no-config-mutation",
        "production_completion_authority": (
            "signed-deployment-bound-readiness-report"
        ),
        "missing_or_expired_required_observation": "not-ready",
        "cross_evaluation_replay": "requires-external-consumption-ledger",
        "network_calls": "none",
        "state_mutation": "none",
        "legal_certification": False,
    }:
        raise ProductionOnboardingContractError(
            "production onboarding boundary is not fail closed"
        )
    _repository_contract_rows(root, contract.get("repository_contracts"))
    _source_bindings(root, contract.get("source_bindings"))
    _cli_wiring(root)
    _installer_wiring(root)
    docs = (root / "docs/operator-first-run.md").read_text(encoding="utf-8")
    docs_normalized = re.sub(r"\s+", " ", docs)
    for marker in (
        "Production onboarding is a separate fail-closed report",
        "They are not a production-readiness attestation",
        "Omitting `--observations`",
        "exits nonzero",
        "stable deployment ID",
        "report-only alias",
        "external deployment-authority ledger",
    ):
        if marker not in docs_normalized:
            raise ProductionOnboardingContractError(
                f"operator documentation is missing {marker!r}"
            )
    return [
        f"verified {len(CHECKS)} production onboarding domains",
        f"verified {len(REPOSITORY_CONTRACTS)} referenced repository contracts",
        f"profile={contract['profile_id']}",
        f"release_eligible={str(eligible).lower()}",
        "live_evidence_status=required-not-verified-by-this-tool",
    ]


def write(root: Path) -> list[str]:
    root = root.resolve()
    (root / CONTRACT_PATH).write_text(
        json.dumps(build(root), indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return verify(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        messages = write(args.root) if args.write else verify(args.root)
    except ProductionOnboardingContractError as exc:
        print(f"production onboarding contract: FAIL: {exc}", file=sys.stderr)
        return 1
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
