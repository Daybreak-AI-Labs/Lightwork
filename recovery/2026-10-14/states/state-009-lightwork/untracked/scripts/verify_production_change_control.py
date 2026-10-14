#!/usr/bin/env python3
"""Verify repository-enforced production change-control invariants.

This verifier deliberately does not claim to inspect GitHub repository,
ruleset, or Environment settings.  It proves only that the reviewed policy,
CODEOWNERS routing, and the workflow source in this checkout still agree.  The
policy inventories the separate administrator/auditor evidence required for
the controls that live outside Git.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.production-change-control-policy.v2"
REPOSITORY = "Daybreak-AI-Labs/Lightwork"
EXTERNAL_BOUNDARY = "external-required-not-verified-by-repository"

_POLICY_KEYS = {
    "schema",
    "repository",
    "source_version",
    "normal_change_policy",
    "emergency_change_policy",
    "code_owner_routing",
    "workflow_default_permissions",
    "workflow_controls",
    "external_evidence_requirements",
}
_NORMAL_POLICY = {
    "pull_request_required": True,
    "minimum_independent_approvals": 1,
    "author_self_approval_allowed": False,
    "required_checks_must_pass": True,
    "direct_production_mutation_allowed": False,
}
_EMERGENCY_POLICY = {
    "release_workflow_bypass_allowed": False,
    "procedure": (
        "docs/compliance/procedures/change-management-procedure.md"
        "#7-emergency-change-procedure"
    ),
    "retroactive_review_business_days": 1,
    "evidence_boundary": "external-process-record",
}
_CODEOWNER_PATTERNS = (
    "*",
    "/packages/maverick-core/",
    "/packages/maverick-shield/",
    "/packages/maverick-channels/",
    "/packages/maverick-dashboard/",
    "/packages/maverick-mcp/",
    "/.github/workflows/",
    "/.github/workflows/ci.yml",
    "/.github/workflows/release.yml",
    "/.github/workflows/publish.yml",
    "/.github/workflows/native.yml",
    "/.github/workflows/compliance-posture.yml",
    "/README.md",
    "/SECURITY.md",
    "/apps/installer-cli/",
    "/apps/installer-desktop/",
    "/deploy/",
    "/deploy/docker/Dockerfile.production",
    "/deploy/observability/grafana-production-overview.json",
    "/deploy/observability/prometheus-rules.yaml",
    "/deploy/observability/runbook.md",
    "/deploy/postgres/README.md",
    "/docs/encryption.md",
    "/docs/api.md",
    "/docs/operator-first-run.md",
    "/docs/compliance/",
    "/docs/enterprise/legal/",
    "/docs/programs/",
    "/docs/strategy/",
    "/docs/enterprise/dependency-failure-contract.md",
    "/docs/enterprise/claim-evidence-ledger.md",
    "/docs/enterprise/deployment-playbook.md",
    "/docs/enterprise/operator-cli-contract.md",
    "/docs/enterprise/production-data-boundary.md",
    "/docs/enterprise/production-migration-safety.md",
    "/docs/enterprise/production-operations-runbook.md",
    "/docs/enterprise/production-runtime-contract.md",
    "/docs/enterprise/production-support-matrix.md",
    "/docs/enterprise/secret-rotation-contract.md",
    "/docs/enterprise/tenant-quota-contract.md",
    "/docs/test-exception-policy.md",
    "/docs/specs/external-effect-semantics.md",
    "/docs/specs/flow-node-transition-ledger.md",
    "/deploy/helm/maverick/",
    "/packages/maverick-core/maverick/audit/evidence.py",
    "/packages/maverick-core/maverick/audit_evidence_verify.py",
    "/packages/maverick-core/maverick/cli/__init__.py",
    "/packages/maverick-core/maverick/cli/_ops_groups.py",
    "/packages/maverick-core/maverick/deployment.py",
    "/packages/maverick-core/maverick/operator_contract.py",
    "/packages/maverick-core/maverick/postgres_encryption_migrate.py",
    "/packages/maverick-core/maverick/production_migration.py",
    "/packages/maverick-core/maverick/production_observability.py",
    "/packages/maverick-core/maverick/production_identity.py",
    "/packages/maverick-core/maverick/production_onboarding.py",
    "/packages/maverick-core/maverick/production_runtime_contract.py",
    "/packages/maverick-core/maverick/flow/execution.py",
    "/packages/maverick-core/maverick/flow/runner.py",
    "/packages/maverick-core/maverick/flow/store.py",
    "/packages/maverick-core/maverick/logging_config.py",
    "/packages/maverick-core/maverick/orchestrator.py",
    "/packages/maverick-core/maverick/support_bundle.py",
    "/packages/maverick-core/maverick/tenant/registry.py",
    "/packages/maverick-core/maverick/tenant_quota_authority.py",
    "/packages/maverick-core/maverick/world_model_backends/pg_rls.py",
    "/packages/maverick-core/maverick/world_model_backends/postgres.py",
    "/packages/maverick-dashboard/maverick_dashboard/health_routes.py",
    "/packages/maverick-dashboard/maverick_dashboard/api_boundary.py",
    "/packages/maverick-dashboard/maverick_dashboard/contracts/",
    "/packages/maverick-dashboard/maverick_dashboard/endpoint_policy.py",
    "/packages/maverick-dashboard/maverick_dashboard/openapi_contract.py",
    "/packages/maverick-dashboard/maverick_dashboard/production_visibility.py",
    "/packages/maverick-dashboard/maverick_dashboard/service_credentials.py",
    "/packages/maverick-dashboard/maverick_dashboard/stream_cursor.py",
    "/packages/maverick-dashboard/maverick_dashboard/task_dispatch.py",
    "/packages/maverick-dashboard/maverick_dashboard/tenant_quota_health.py",
    "/packages/maverick-core/tests/test_audit_evidence.py",
    "/packages/maverick-core/tests/test_claim_evidence_ledger.py",
    "/packages/maverick-core/tests/test_dependency_failure_contract.py",
    "/packages/maverick-core/tests/test_encryption_ops_cli.py",
    "/packages/maverick-core/tests/test_pg_rls.py",
    "/packages/maverick-core/tests/test_operator_contract.py",
    "/packages/maverick-core/tests/test_postgres_encryption_migrate.py",
    "/packages/maverick-core/tests/test_postgres_production_posture.py",
    "/packages/maverick-core/tests/test_postgres_rls_unit.py",
    "/packages/maverick-core/tests/test_postgres_tenant_migrations.py",
    "/packages/maverick-core/tests/test_production_deployment_gate.py",
    "/packages/maverick-core/tests/test_production_code_inventory.py",
    "/packages/maverick-core/tests/test_production_data_boundary.py",
    "/packages/maverick-core/tests/test_production_migration.py",
    "/packages/maverick-core/tests/test_production_migration_contract.py",
    "/packages/maverick-core/tests/test_production_observability.py",
    "/packages/maverick-core/tests/test_production_identity.py",
    "/packages/maverick-core/tests/test_production_onboarding.py",
    "/packages/maverick-core/tests/test_production_operations_contract.py",
    "/packages/maverick-core/tests/test_production_qa_assurance.py",
    "/packages/maverick-core/tests/test_production_readiness_evidence.py",
    "/packages/maverick-core/tests/test_production_runtime_contract.py",
    "/packages/maverick-core/tests/test_production_runtime_release_verifier.py",
    "/packages/maverick-core/tests/test_tenant_registry.py",
    "/packages/maverick-core/tests/test_tenant_rls_cli.py",
    "/packages/maverick-core/tests/test_pytest_exception_policy.py",
    "/packages/maverick-core/tests/test_production_support_matrix.py",
    "/packages/maverick-core/tests/test_secret_rotation_contract.py",
    "/packages/maverick-core/tests/test_support_bundle_security.py",
    "/packages/maverick-core/tests/test_tenant_quota_authority.py",
    "/packages/maverick-core/tests/test_flow_action_tenant_quota.py",
    "/packages/maverick-core/tests/test_flow_execution_hardening.py",
    "/packages/maverick-core/tests/test_flow_postgres_authority.py",
    "/packages/maverick-dashboard/tests/test_dashboard_production_assurance.py",
    "/packages/maverick-dashboard/tests/test_production_api_contract.py",
    "/packages/maverick-dashboard/tests/test_tenant_quota_health.py",
    "/qa/pytest-exceptions.json",
    "/release/dependency-failure-contract.json",
    "/release/claim-evidence-ledger.json",
    "/release/production-api-contract.json",
    "/release/production-change-control-policy.json",
    "/release/production-code-inventory.json",
    "/release/production-data-boundary.json",
    "/release/production-identity-posture-contract.json",
    "/release/production-migration-contract.json",
    "/release/release-state.json",
    "/release/production-readiness-evidence.json",
    "/release/production-model-policy.json",
    "/release/production-onboarding-contract.json",
    "/release/production-openapi-schema.json",
    "/release/production-operations-contract.json",
    "/release/production-qa-assurance.json",
    "/release/production-runtime-contract.json",
    "/release/production-surface-manifest.json",
    "/release/production-support-matrix.json",
    "/release/service-objectives.json",
    "/release/secret-rotation-contract.json",
    "/release/tenant-quota-contract.json",
    "/scripts/verify_claim_evidence_ledger.py",
    "/scripts/verify_dependency_failure_contract.py",
    "/scripts/verify_github_release_assets.py",
    "/scripts/verify_release_state.py",
    "/scripts/verify_production_api_contract.py",
    "/scripts/verify_production_change_control.py",
    "/scripts/verify_production_code_inventory.py",
    "/scripts/verify_production_data_boundary.py",
    "/scripts/verify_production_migration_contract.py",
    "/scripts/verify_production_model_policy.py",
    "/scripts/verify_production_onboarding_contract.py",
    "/scripts/verify_production_operations_contract.py",
    "/scripts/verify_production_qa_assurance.py",
    "/scripts/verify_production_readiness.py",
    "/scripts/verify_production_runtime_contract.py",
    "/scripts/verify_production_support_matrix.py",
    "/scripts/verify_pytest_exceptions.py",
    "/scripts/verify_service_objectives.py",
    "/scripts/verify_secret_rotation_contract.py",
    "/scripts/verify_tenant_quota_contract.py",
    "/packages/maverick-core/tests/test_release_integrity.py",
    "/packages/maverick-core/tests/test_release_policy.py",
)
_CODEOWNER_OWNERS = ("@cdayAI",)
_WORKFLOW_DEFAULT_PERMISSIONS = {
    ".github/workflows/ci.yml": {"contents": "read"},
    ".github/workflows/release.yml": {"contents": "read"},
    ".github/workflows/publish.yml": {"contents": "read"},
    ".github/workflows/native.yml": {"contents": "read"},
}
_BINDING_KEYS = {
    "id",
    "workflow",
    "authority_job",
    "mutating_job",
    "environment",
    "authority_job_sha256",
    "required_needs",
    "allowed_write_permissions",
    "required_authority_commands",
    "required_authority_fragments",
    "required_mutating_if_terms",
}
_EXPECTED_BINDINGS = {
    "ghcr-staging-image": {
        "workflow": ".github/workflows/release.yml",
        "authority_job": "validate-release-tag",
        "mutating_job": "docker-image",
        "environment": "production-release",
        "authority_job_sha256": (
            "414e44b7c4ce21af36c4955b9acc4340dd187766c3ec5c511779b1ace006ef1d"
        ),
        "required_needs": ("validate-release-tag",),
        "allowed_write_permissions": ("packages",),
        "required_authority_commands": (
            "python -I scripts/verify_release_state.py --root . "
            '--release-tag "$tag" --require-release-ready',
            "python -I scripts/verify_pytest_exceptions.py --root .",
            "python -I scripts/verify_production_change_control.py --root .",
            "python -I scripts/verify_claim_evidence_ledger.py --root . --release-candidate",
            "python -I scripts/verify_production_code_inventory.py --root .",
            "python -I scripts/verify_production_migration_contract.py --root .",
            "python -I scripts/verify_secret_rotation_contract.py --root .",
            "python -I scripts/verify_tenant_quota_contract.py --root .",
            "python -I scripts/verify_production_api_contract.py --root .",
            "python -I scripts/verify_production_onboarding_contract.py --root .",
            "python -I scripts/verify_production_data_boundary.py --root .",
            "python -I scripts/verify_production_operations_contract.py --root .",
            "python -I scripts/verify_production_qa_assurance.py --root .",
            "python -I scripts/verify_production_runtime_contract.py --root .",
        ),
        "required_authority_fragments": (),
        "required_mutating_if_terms": (),
    },
    "github-release-draft": {
        "workflow": ".github/workflows/release.yml",
        "authority_job": "validate-release-tag",
        "mutating_job": "release",
        "environment": "production-release",
        "authority_job_sha256": (
            "414e44b7c4ce21af36c4955b9acc4340dd187766c3ec5c511779b1ace006ef1d"
        ),
        "required_needs": (
            "validate-release-tag",
            "docker-image",
            "binaries",
            "standalone-sku-artifacts",
            "release-runtime-sbom",
        ),
        "allowed_write_permissions": ("contents", "id-token", "packages"),
        "required_authority_commands": (
            "python -I scripts/verify_release_state.py --root . "
            '--release-tag "$tag" --require-release-ready',
            "python -I scripts/verify_pytest_exceptions.py --root .",
            "python -I scripts/verify_production_change_control.py --root .",
            "python -I scripts/verify_claim_evidence_ledger.py --root . --release-candidate",
            "python -I scripts/verify_production_code_inventory.py --root .",
            "python -I scripts/verify_production_migration_contract.py --root .",
            "python -I scripts/verify_secret_rotation_contract.py --root .",
            "python -I scripts/verify_tenant_quota_contract.py --root .",
            "python -I scripts/verify_production_api_contract.py --root .",
            "python -I scripts/verify_production_onboarding_contract.py --root .",
            "python -I scripts/verify_production_data_boundary.py --root .",
            "python -I scripts/verify_production_operations_contract.py --root .",
            "python -I scripts/verify_production_qa_assurance.py --root .",
            "python -I scripts/verify_production_runtime_contract.py --root .",
        ),
        "required_authority_fragments": (),
        "required_mutating_if_terms": (),
    },
    "python-release-evidence-signing": {
        "workflow": ".github/workflows/publish.yml",
        "authority_job": "change-control-authority",
        "mutating_job": "sign",
        "environment": "production-release",
        "authority_job_sha256": (
            "1b23f0b6998c007bafe66347f86499cceb5ae374f93bdc976fbdd9fa485f0b57"
        ),
        "required_needs": ("build", "change-control-authority"),
        "allowed_write_permissions": ("contents", "id-token"),
        "required_authority_commands": (
            "python -I scripts/verify_pytest_exceptions.py --root .",
            "python -I scripts/verify_production_change_control.py --root .",
        ),
        "required_authority_fragments": ("ref: ${{ github.sha }}",),
        "required_mutating_if_terms": (),
    },
    "pypi-publication": {
        "workflow": ".github/workflows/publish.yml",
        "authority_job": "change-control-authority",
        "mutating_job": "publish",
        "environment": "pypi",
        "authority_job_sha256": (
            "1b23f0b6998c007bafe66347f86499cceb5ae374f93bdc976fbdd9fa485f0b57"
        ),
        "required_needs": (
            "build",
            "sign",
            "finalize-release",
            "release-authority",
            "namespace-authority",
            "change-control-authority",
        ),
        "allowed_write_permissions": ("id-token",),
        "required_authority_commands": (
            "python -I scripts/verify_pytest_exceptions.py --root .",
            "python -I scripts/verify_production_change_control.py --root .",
        ),
        "required_authority_fragments": ("ref: ${{ github.sha }}",),
        "required_mutating_if_terms": (
            "needs.finalize-release.result == 'success'",
        ),
    },
    "github-release-finalization": {
        "workflow": ".github/workflows/publish.yml",
        "authority_job": "change-control-authority",
        "mutating_job": "finalize-release",
        "environment": "production-release",
        "authority_job_sha256": (
            "1b23f0b6998c007bafe66347f86499cceb5ae374f93bdc976fbdd9fa485f0b57"
        ),
        "required_needs": (
            "build",
            "sign",
            "release-authority",
            "change-control-authority",
        ),
        "allowed_write_permissions": ("contents", "packages"),
        "required_authority_commands": (
            "python -I scripts/verify_pytest_exceptions.py --root .",
            "python -I scripts/verify_production_change_control.py --root .",
        ),
        "required_authority_fragments": ("ref: ${{ github.sha }}",),
        "required_mutating_if_terms": (
            "needs.release-authority.result == 'success'",
            "needs.change-control-authority.result == 'success'",
        ),
    },
    "native-pypi-publication": {
        "workflow": ".github/workflows/native.yml",
        "authority_job": "validate-native-tag",
        "mutating_job": "publish-pypi",
        "environment": "pypi",
        "authority_job_sha256": (
            "245b038b95293e762f4769da2b18ccefc25551e037b7fa9428351b1b514e2bc9"
        ),
        "required_needs": (
            "core",
            "verify-audit",
            "python",
            "wasm",
            "validate-native-tag",
        ),
        "allowed_write_permissions": ("id-token",),
        "required_authority_commands": (
            "python -I scripts/verify_production_change_control.py --root .",
        ),
        "required_authority_fragments": (),
        "required_mutating_if_terms": ("false",),
    },
    "native-npm-publication-disabled": {
        "workflow": ".github/workflows/native.yml",
        "authority_job": "validate-native-tag",
        "mutating_job": "publish-npm",
        "environment": "npm",
        "authority_job_sha256": (
            "245b038b95293e762f4769da2b18ccefc25551e037b7fa9428351b1b514e2bc9"
        ),
        "required_needs": (
            "core",
            "verify-audit",
            "python",
            "wasm",
            "validate-native-tag",
        ),
        "allowed_write_permissions": (),
        "required_authority_commands": (
            "python -I scripts/verify_production_change_control.py --root .",
        ),
        "required_authority_fragments": (),
        "required_mutating_if_terms": ("false",),
    },
}
_EXTERNAL_CONTROLS = {
    "github-main-protection": (
        "github",
        "refs/heads/main",
        (
            "pull-request-required",
            "minimum-one-independent-approval",
            "required-checks-blocking",
            "author-self-approval-prevented",
        ),
        "github-ruleset-export-and-merged-pr-record",
    ),
    "github-release-tag-protection": (
        "github",
        "refs/tags/v*",
        (
            "creation-restricted-to-authorized-release-actors",
            "update-and-deletion-restricted",
        ),
        "github-ruleset-export",
    ),
    "github-native-tag-protection": (
        "github",
        "refs/tags/native-v*",
        (
            "creation-restricted-to-authorized-release-actors",
            "update-and-deletion-restricted",
        ),
        "github-ruleset-export",
    ),
    "github-production-release-environment": (
        "github",
        "environment:production-release",
        (
            "required-independent-reviewer",
            "self-review-prevented",
            "deployment-refs-limited-to-protected-release-tags",
        ),
        "github-environment-settings-export-and-deployment-record",
    ),
    "github-pypi-environment": (
        "github",
        "environment:pypi",
        (
            "required-independent-reviewer",
            "self-review-prevented",
            "deployment-refs-limited-to-protected-release-tags",
        ),
        "github-environment-settings-export-and-deployment-record",
    ),
    "github-npm-environment": (
        "github",
        "environment:npm",
        (
            "required-independent-reviewer",
            "self-review-prevented",
            "deployment-refs-limited-to-protected-release-tags",
        ),
        "github-environment-settings-export-and-deployment-record",
    ),
    "postgres-tenant-rls-deployment": (
        "deployment",
        "enterprise-single-control-plane-v1:postgres-tenant-boundary",
        (
            "exact-release-migration-owner-rls-apply-report",
            "runtime-role-nosuperuser-nobypassrls-and-nonowner",
            "all-root-and-child-policies-enabled-and-forced",
            "deployed-readiness-postgres-rls-verified",
            "live-cross-tenant-read-write-adverse-report",
        ),
        "signed-migration-deployment-and-adverse-test-record",
    ),
    "postgres-encryption-migration": (
        "deployment",
        "enterprise-single-control-plane-v1:postgres-encryption",
        (
            "exact-release-whole-database-pre-and-post-inventory",
            "zero-plaintext-remaining-and-strict-mode-enabled",
            "sealed-column-manifest-and-schema-version-bound",
            "managed-snapshot-or-pitr-marker",
            "successful-restore-and-key-recovery-drill",
        ),
        "signed-migration-snapshot-and-recovery-record",
    ),
    "production-migration-qualification": (
        "deployment",
        "enterprise-single-control-plane-v1:postgres-migration",
        (
            "exact-release-and-image-bound-operation-authorization",
            "writer-drain-and-zero-active-admission-proof",
            "managed-backup-or-pitr-completion-and-retention-proof",
            "representative-adjacent-predecessor-rehearsal",
            "precommit-interruption-rollback-proof",
            "postcommit-same-operation-reconciliation-proof",
            "independent-full-restore-and-integrity-proof",
            "post-migration-tls-rls-encryption-and-readiness-proof",
            "signed-immutable-migration-report-retention",
        ),
        "signed-production-migration-qualification-record",
    ),
    "secret-rotation-qualification": (
        "deployment",
        "enterprise-single-control-plane-v1:secret-rotation",
        (
            "exact-release-secret-inventory-and-custody-review",
            "provider-current-and-previous-authority-prepared",
            "pod-recreation-and-readiness-proof",
            "historical-decrypt-and-audit-verification",
            "rollback-before-old-authority-revocation",
            "old-authority-revoked-only-after-validation",
            "signed-rotation-and-recovery-record-retained",
        ),
        "signed-secret-rotation-drill-record",
    ),
    "tenant-quota-capacity-qualification": (
        "deployment",
        "enterprise-single-control-plane-v1:tenant-quota",
        (
            "exact-policy-generation-and-workload-derived-limits",
            "live-postgres-cross-process-admission-and-settlement",
            "saturation-fairness-and-backpressure-proof",
            "crash-expiry-orphan-and-reconciliation-proof",
            "managed-postgres-failover-and-recovery-proof",
            "authority-row-retention-and-capacity-plan",
            "signed-representative-load-result-retained",
        ),
        "signed-tenant-quota-capacity-qualification-record",
    ),
    "production-runtime-cluster-qualification": (
        "deployment",
        "enterprise-single-control-plane-v1:runtime-boundary",
        (
            "exact-release-image-and-runtime-contract-digest",
            "runtimeclass-cgroup-pid-file-memory-and-cpu-enforcement",
            "read-only-root-exact-writable-mounts-and-secret-projection",
            "dns-and-approved-dependency-egress-with-no-public-egress",
            "live-admission-and-boundary-negative-tests",
            "signed-runtime-qualification-record-retained",
        ),
        "signed-runtime-cluster-qualification-record",
    ),
    "production-data-processing-qualification": (
        "deployment-and-legal",
        "enterprise-single-control-plane-v1:data-boundary",
        (
            "actual-processors-vendors-regions-and-residency-approved",
            "retention-deletion-and-backup-replica-schedules-approved",
            "live-tls-at-rest-access-and-credential-revocation-evidence",
            "tenant-erasure-and-downstream-deletion-evidence",
            "exact-release-data-flow-inventory-reconciled",
            "signed-data-processing-qualification-record-retained",
        ),
        "signed-data-processing-qualification-record",
    ),
    "production-operations-qualification": (
        "operations",
        "enterprise-single-control-plane-v1:operations",
        (
            "named-continuously-staffed-on-call-and-backup-rotation",
            "live-page-dashboard-scrape-tls-and-black-box-monitoring-tests",
            "witnessed-incident-halt-credential-and-restore-drills",
            "measured-capacity-cost-baselines-and-approved-service-objectives",
            "customer-support-status-maintenance-and-notification-routes",
            "signed-operations-qualification-record-retained",
        ),
        "signed-production-operations-qualification-record",
    ),
    "production-api-qualification": (
        "release-and-deployment",
        "enterprise-single-control-plane-v1:versioned-api",
        (
            "exact-release-openapi-and-contract-digests",
            "compatibility-deprecation-and-migration-policy-approved",
            "live-browser-client-and-reconnect-conformance",
            "supported-client-and-mcp-conformance-where-claimed",
            "customer-facing-api-and-admin-documentation-reviewed",
            "signed-api-qualification-record-retained",
        ),
        "signed-production-api-qualification-record",
    ),
    "production-qa-security-qualification": (
        "quality-and-security",
        "enterprise-single-control-plane-v1:qa-assurance",
        (
            "hosted-required-checks-pass-on-exact-revision",
            "production-like-e2e-load-crash-restore-and-cross-platform-results",
            "independent-penetration-test-and-remediation-closure",
            "incident-vulnerability-and-disclosure-staffing-and-drills",
            "claimed-compliance-regimes-independently-assessed",
            "signed-qa-security-qualification-record-retained",
        ),
        "signed-production-qa-security-qualification-record",
    ),
    "production-onboarding-qualification": (
        "release-governance",
        "enterprise-single-control-plane-v1:onboarding-readiness",
        (
            "signed-evidence-manifest-bound-to-exact-revision-and-contract-digest",
            "distinct-authorized-approvers-and-unexpired-observation-window",
            "non-replayed-per-requirement-evidence-coverage",
            "all-live-and-external-domain-gates-satisfied",
            "immutable-onboarding-report-and-evidence-retained",
        ),
        "signed-production-onboarding-qualification-record",
    ),
    "claim-evidence-approval": (
        "release-governance",
        "enterprise-single-control-plane-v1:buyer-claims",
        (
            "exact-revision-document-and-evidence-digests-reviewed",
            "two-distinct-release-and-security-approvers",
            "unresolved-claims-remain-not-buyer-claimable",
            "dated-external-change-control-reference",
            "approval-valid-through-ledger-refresh-date",
        ),
        "signed-claim-evidence-approval-record",
    ),
}


class ChangeControlError(ValueError):
    """The reviewed policy and production mutation paths disagree."""


def _mapping(value: object, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ChangeControlError(f"{label} must be an object")
    return value


def _exact_keys(
    value: object,
    expected: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    document = _mapping(value, label=label)
    if set(document) != expected:
        raise ChangeControlError(
            f"{label} fields differ: "
            f"missing={sorted(expected - set(document))}, "
            f"unknown={sorted(set(document) - expected)}"
        )
    return document


def _read_json(path: Path, *, label: str) -> object:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ChangeControlError(f"{label} is missing or not a regular file")
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ChangeControlError(f"cannot read {label}: {path}") from exc


def _repo_file(root: Path, value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ChangeControlError(f"{label} must be a repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ChangeControlError(f"{label} escapes the repository")
    path = root / relative
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
            raise ChangeControlError(f"{label} is missing or not a regular file")
    except OSError as exc:
        raise ChangeControlError(f"cannot inspect {label}: {path}") from exc
    return path


def _jobs(workflow: str, *, label: str) -> dict[str, str]:
    jobs_match = re.search(r"(?m)^jobs:\s*(?:#.*)?$", workflow)
    if jobs_match is None:
        raise ChangeControlError(f"{label} has no jobs mapping")
    jobs_text = workflow[jobs_match.end() :]
    matches = list(re.finditer(r"(?m)^  ([a-zA-Z0-9_-]+):\s*(?:#.*)?$", jobs_text))
    if not matches:
        raise ChangeControlError(f"{label} has no job definitions")
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        if name in result:
            raise ChangeControlError(f"{label} has duplicate job {name}")
        result[name] = jobs_text[match.start() : end]
    return result


def _field_block(job: str, field: str) -> tuple[str, list[str]]:
    match = re.search(
        rf"(?m)^    {re.escape(field)}:[ \t]*(.*?)[ \t]*$",
        job,
    )
    if match is None:
        raise ChangeControlError(f"job has no {field} field")
    inline = match.group(1).split("#", 1)[0].strip()
    if inline and inline not in {"|", ">", "|-", ">-"}:
        return inline, []
    lines = job[match.end() :].splitlines()
    children: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent <= 4:
            break
        children.append(line)
    return "", children


def _sequence_field(job: str, field: str) -> tuple[str, ...]:
    inline, children = _field_block(job, field)
    text = inline or "\n".join(children)
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    text = text.replace("[", "").replace("]", "").replace("- ", "")
    values = [part.strip() for part in re.split(r"[,\n]", text) if part.strip()]
    if not values or any(re.fullmatch(r"[a-zA-Z0-9_-]+", item) is None for item in values):
        raise ChangeControlError(f"job {field} must be a literal sequence")
    return tuple(values)


def _environment(job: str) -> str:
    inline, children = _field_block(job, "environment")
    if inline:
        return inline.strip('"\'')
    names = []
    for line in children:
        match = re.fullmatch(r"\s+name:\s*([^#]+?)(?:\s+#.*)?", line)
        if match is not None:
            names.append(match.group(1).strip().strip('"\''))
    if len(names) != 1:
        raise ChangeControlError("job environment must have exactly one literal name")
    return names[0]


def _permissions(job: str) -> dict[str, str]:
    inline, children = _field_block(job, "permissions")
    if inline:
        raise ChangeControlError("job permissions must be an explicit mapping")
    permissions: dict[str, str] = {}
    for line in children:
        match = re.fullmatch(r"\s+([a-z-]+):\s*([a-z]+)(?:\s+#.*)?", line)
        if match is None:
            raise ChangeControlError("job permissions contain unsupported syntax")
        name, access = match.groups()
        if name in permissions:
            raise ChangeControlError(f"job permissions repeat {name}")
        permissions[name] = access
    return permissions


def _top_level_permissions(workflow: str, *, label: str) -> dict[str, str]:
    jobs_match = re.search(r"(?m)^jobs:\s*(?:#.*)?$", workflow)
    if jobs_match is None:
        raise ChangeControlError(f"{label} has no jobs mapping")
    header = workflow[: jobs_match.start()]
    match = re.search(r"(?m)^permissions:[ \t]*$", header)
    if match is None:
        raise ChangeControlError(f"{label} has no default permissions mapping")
    permissions: dict[str, str] = {}
    for line in header[match.end() :].splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        field = re.fullmatch(r"  ([a-z-]+):\s*([a-z]+)(?:\s+#.*)?", line)
        if field is None:
            raise ChangeControlError(
                f"{label} default permissions contain unsupported syntax"
            )
        name, access = field.groups()
        if name in permissions:
            raise ChangeControlError(f"{label} default permissions repeat {name}")
        permissions[name] = access
    return permissions


def _run_scripts(job: str) -> list[str]:
    matches = list(re.finditer(r"(?m)^        run:\s*(.*?)\s*$", job))
    scripts: list[str] = []
    for match in matches:
        inline = match.group(1).split("#", 1)[0].strip()
        if inline and inline not in {"|", ">", "|-", ">-"}:
            scripts.append(inline)
            continue
        lines: list[str] = []
        for line in job[match.end() :].splitlines():
            if not line.strip():
                lines.append("")
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= 8:
                break
            lines.append(line[10:] if indent >= 10 else line.lstrip())
        scripts.append("\n".join(lines))
    return scripts


def _normalized_shell(value: str) -> str:
    value = re.sub(r"\\\s*\n\s*", " ", value)
    active = [
        line for line in value.splitlines() if not line.lstrip().startswith("#")
    ]
    return " ".join("\n".join(active).split())


def _logical_shell_lines(script: str) -> list[str]:
    """Return executable shell lines while omitting comments and heredoc bodies."""
    lines: list[str] = []
    pending = ""
    heredoc: str | None = None
    for raw in script.splitlines():
        stripped = raw.strip()
        if heredoc is not None:
            if stripped == heredoc:
                heredoc = None
            continue
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        logical = f"{pending} {stripped}".strip() if pending else stripped
        if logical.endswith("\\"):
            pending = logical[:-1].rstrip()
            continue
        pending = ""
        lines.append(" ".join(logical.split()))
        match = re.search(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1", logical)
        if match is not None:
            heredoc = match.group(2)
    if pending:
        raise ChangeControlError("authority run block ends with a shell continuation")
    if heredoc is not None:
        raise ChangeControlError("authority run block has an unterminated heredoc")
    return lines


def _top_level_shell_commands(script: str) -> list[str]:
    """Extract commands that execute unconditionally at shell block depth zero."""
    commands: list[str] = []
    depth = 0
    for line in _logical_shell_lines(script):
        if re.match(r"^(?:fi|done|esac)(?:\s*;)?$", line) or re.match(
            r"^[)}](?:\s*\|\|\s*true)?$",
            line,
        ):
            if depth == 0:
                raise ChangeControlError("authority run block has unmatched shell closure")
            depth -= 1
            continue
        opens = bool(
            re.match(r"^(?:if|for|while|until|select)\b.*(?:then|do)\s*$", line)
            or re.match(r"^case\b.*\bin\s*$", line)
            or re.search(r"(?:^|\s)[({]\s*$", line)
        )
        if opens:
            depth += 1
            continue
        if depth == 0:
            commands.append(line)
    if depth != 0:
        raise ChangeControlError("authority run block has unclosed shell control flow")
    return commands


def _active_yaml(value: str) -> str:
    active = []
    for line in value.splitlines():
        if line.lstrip().startswith("#"):
            continue
        # ``#`` is data inside quoted YAML or shell strings.  Removing inline
        # suffixes would let an attacker hide appended shell from the digest.
        active.append(line.rstrip())
    return "\n".join(active)


def _authority_job_sha256(authority: str) -> str:
    # Bind the complete extracted job text.  In particular, blank lines and
    # trailing whitespace are significant: spaces after a shell continuation
    # backslash can change execution while looking cosmetically equivalent.
    # Newline spelling is the sole normalization so the policy is portable
    # between LF and CRLF checkouts without weakening shell/YAML semantics.
    canonical = authority.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _literal_strings(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ChangeControlError(f"{label} must be a non-empty-string list")
    if len(value) != len(set(value)):
        raise ChangeControlError(f"{label} contains duplicates")
    return tuple(value)


def _validate_binding(root: Path, binding: Mapping[str, Any]) -> None:
    binding_id = binding["id"]
    if not isinstance(binding_id, str) or binding_id not in _EXPECTED_BINDINGS:
        raise ChangeControlError(f"unknown workflow control: {binding_id}")
    expected = _EXPECTED_BINDINGS[binding_id]
    normalized = {
        "workflow": binding["workflow"],
        "authority_job": binding["authority_job"],
        "mutating_job": binding["mutating_job"],
        "environment": binding["environment"],
        "authority_job_sha256": binding["authority_job_sha256"],
        "required_needs": _literal_strings(
            binding["required_needs"], label=f"{binding_id} required_needs"
        ),
        "allowed_write_permissions": _literal_strings(
            binding["allowed_write_permissions"],
            label=f"{binding_id} allowed_write_permissions",
        ),
        "required_authority_commands": _literal_strings(
            binding["required_authority_commands"],
            label=f"{binding_id} required_authority_commands",
        ),
        "required_authority_fragments": _literal_strings(
            binding["required_authority_fragments"],
            label=f"{binding_id} required_authority_fragments",
        ),
        "required_mutating_if_terms": _literal_strings(
            binding["required_mutating_if_terms"],
            label=f"{binding_id} required_mutating_if_terms",
        ),
    }
    if normalized != expected:
        raise ChangeControlError(f"{binding_id} differs from the enforced baseline")
    authority_digest = binding["authority_job_sha256"]
    if (
        not isinstance(authority_digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", authority_digest) is None
    ):
        raise ChangeControlError(f"{binding_id} authority job digest is malformed")

    workflow_path = _repo_file(root, binding["workflow"], label=binding_id)
    try:
        workflow = workflow_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChangeControlError(f"cannot read {binding_id} workflow") from exc
    jobs = _jobs(workflow, label=str(binding["workflow"]))
    authority_name = str(binding["authority_job"])
    mutating_name = str(binding["mutating_job"])
    if authority_name not in jobs or mutating_name not in jobs:
        raise ChangeControlError(f"{binding_id} references a missing workflow job")
    authority = jobs[authority_name]
    mutating = jobs[mutating_name]
    if _environment(mutating) != binding["environment"]:
        raise ChangeControlError(f"{binding_id} environment differs from policy")
    if set(_sequence_field(mutating, "needs")) != set(binding["required_needs"]):
        raise ChangeControlError(f"{binding_id} needs differ from policy")
    writes = {
        name for name, access in _permissions(mutating).items() if access == "write"
    }
    if writes != set(binding["allowed_write_permissions"]):
        raise ChangeControlError(f"{binding_id} write permissions differ from policy")

    if _authority_job_sha256(authority) != authority_digest:
        raise ChangeControlError(f"{binding_id} authority job digest differs from policy")
    raw_scripts = _run_scripts(authority)
    scripts = [
        command
        for script in raw_scripts
        for command in _top_level_shell_commands(script)
    ]
    for command in binding["required_authority_commands"]:
        normalized_command = _normalized_shell(str(command))
        if scripts.count(normalized_command) != 1:
            raise ChangeControlError(
                f"{binding_id} authority job must run exactly once at top level: {command}"
            )
    active_authority = _active_yaml(authority)
    for fragment in binding["required_authority_fragments"]:
        if fragment not in active_authority:
            raise ChangeControlError(
                f"{binding_id} authority job is missing: {fragment}"
            )
    condition = " ".join(_field_block(mutating, "if")[0:1])
    if not condition:
        _inline, lines = _field_block(mutating, "if")
        condition = " ".join(line.strip() for line in lines)
    for term in binding["required_mutating_if_terms"]:
        if term not in condition:
            raise ChangeControlError(f"{binding_id} mutation condition is missing: {term}")


def _validate_codeowners(root: Path, routing: object) -> None:
    routing = _exact_keys(
        routing,
        {"path", "required_patterns", "evidence_boundary"},
        label="code-owner routing",
    )
    if routing["path"] != ".github/CODEOWNERS":
        raise ChangeControlError("CODEOWNERS path differs from policy")
    patterns = _literal_strings(
        routing["required_patterns"], label="required CODEOWNERS patterns"
    )
    if patterns != _CODEOWNER_PATTERNS:
        raise ChangeControlError("required CODEOWNERS patterns differ from baseline")
    if routing["evidence_boundary"] != (
        "routing-only-not-proof-of-independent-review"
    ):
        raise ChangeControlError("CODEOWNERS evidence boundary is misleading")
    path = _repo_file(root, routing["path"], label="CODEOWNERS")
    entries: dict[str, tuple[str, ...]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ChangeControlError("cannot read CODEOWNERS") from exc
    for line in lines:
        content = line.split("#", 1)[0].strip()
        if not content:
            continue
        fields = content.split()
        if len(fields) < 2:
            raise ChangeControlError("CODEOWNERS has a pattern without an owner")
        pattern = fields[0]
        if pattern in entries:
            raise ChangeControlError(f"CODEOWNERS repeats protected pattern: {pattern}")
        entries[pattern] = tuple(fields[1:])
    missing = sorted(set(patterns) - set(entries))
    if missing:
        raise ChangeControlError(f"CODEOWNERS lacks explicit protected paths: {missing}")
    unknown = sorted(set(entries) - set(patterns))
    if unknown:
        raise ChangeControlError(f"CODEOWNERS has unreviewed routing patterns: {unknown}")
    wrong_owners = sorted(
        pattern for pattern, owners in entries.items() if owners != _CODEOWNER_OWNERS
    )
    if wrong_owners:
        raise ChangeControlError(
            f"CODEOWNERS owners differ from the reviewed tuple: {wrong_owners}"
        )


def _validate_external_controls(value: object) -> None:
    if not isinstance(value, list):
        raise ChangeControlError("external evidence requirements must be a list")
    actual: dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(value):
        entry = _exact_keys(
            item,
            {
                "id",
                "provider",
                "scope",
                "requirements",
                "evidence_type",
                "evidence_boundary",
            },
            label=f"external evidence requirement {index}",
        )
        control_id = entry["id"]
        if not isinstance(control_id, str) or control_id in actual:
            raise ChangeControlError("external evidence requirement IDs must be unique")
        actual[control_id] = entry
    if set(actual) != set(_EXTERNAL_CONTROLS):
        raise ChangeControlError("external evidence inventory differs from baseline")
    for control_id, expected in _EXTERNAL_CONTROLS.items():
        entry = actual[control_id]
        observed = (
            entry["provider"],
            entry["scope"],
            _literal_strings(
                entry["requirements"], label=f"{control_id} requirements"
            ),
            entry["evidence_type"],
        )
        if observed != expected or entry["evidence_boundary"] != EXTERNAL_BOUNDARY:
            raise ChangeControlError(f"{control_id} differs from the enforced baseline")


def _validate_write_inventory(
    root: Path,
    defaults: object,
    bindings: Mapping[str, Mapping[str, Any]],
) -> int:
    defaults = _mapping(defaults, label="workflow default permissions")
    normalized_defaults: dict[str, dict[str, str]] = {}
    for workflow, value in defaults.items():
        if not isinstance(workflow, str):
            raise ChangeControlError("workflow permission paths must be strings")
        permission_map = _mapping(value, label=f"{workflow} default permissions")
        if not all(
            isinstance(name, str) and isinstance(access, str)
            for name, access in permission_map.items()
        ):
            raise ChangeControlError("workflow default permissions must be strings")
        normalized_defaults[workflow] = dict(permission_map)
    if normalized_defaults != _WORKFLOW_DEFAULT_PERMISSIONS:
        raise ChangeControlError("workflow default permissions differ from baseline")

    declared = {
        (str(binding["workflow"]), str(binding["mutating_job"]))
        for binding in bindings.values()
        if binding["allowed_write_permissions"]
    }
    discovered: set[tuple[str, str]] = set()
    for workflow, expected_permissions in _WORKFLOW_DEFAULT_PERMISSIONS.items():
        path = _repo_file(root, workflow, label=workflow)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ChangeControlError(f"cannot read {workflow}") from exc
        if _top_level_permissions(source, label=workflow) != expected_permissions:
            raise ChangeControlError(f"{workflow} default permissions differ from policy")
        for job_name, job in _jobs(source, label=workflow).items():
            if re.search(r"(?m)^    permissions:", job) is None:
                continue
            writes = {
                name
                for name, access in _permissions(job).items()
                if access == "write"
            }
            if not writes:
                continue
            identity = (workflow, job_name)
            discovered.add(identity)
            if identity not in declared:
                raise ChangeControlError(
                    f"unclassified write-capable workflow job: {workflow}:{job_name}"
                )
    if discovered != declared:
        raise ChangeControlError(
            "declared production mutations differ from write-capable jobs: "
            f"missing={sorted(declared - discovered)}, "
            f"unknown={sorted(discovered - declared)}"
        )
    return len(discovered)


def validate_policy_document(
    root: Path,
    document: object,
) -> dict[str, object]:
    """Validate policy semantics and every repository-enforced binding."""
    root = root.resolve()
    policy = _exact_keys(document, _POLICY_KEYS, label="change-control policy")
    if policy["schema"] != SCHEMA or policy["repository"] != REPOSITORY:
        raise ChangeControlError("change-control policy identity is unsupported")

    release_state = _mapping(
        _read_json(root / "release" / "release-state.json", label="release state"),
        label="release state",
    )
    manifests = _mapping(
        release_state.get("manifests"), label="release-state manifests"
    )
    if manifests.get("production_change_control") != (
        "release/production-change-control-policy.json"
    ):
        raise ChangeControlError("release state does not bind the change-control policy")
    if policy["source_version"] != release_state.get("source_version"):
        raise ChangeControlError("change-control policy version differs from release state")
    if _mapping(policy["normal_change_policy"], label="normal change policy") != (
        _NORMAL_POLICY
    ):
        raise ChangeControlError("normal change policy differs from enforced baseline")
    if _mapping(
        policy["emergency_change_policy"], label="emergency change policy"
    ) != _EMERGENCY_POLICY:
        raise ChangeControlError("emergency change policy differs from enforced baseline")

    _validate_codeowners(root, policy["code_owner_routing"])
    controls = policy["workflow_controls"]
    if not isinstance(controls, list):
        raise ChangeControlError("workflow controls must be a list")
    bindings: dict[str, Mapping[str, Any]] = {}
    for index, value in enumerate(controls):
        binding = _exact_keys(value, _BINDING_KEYS, label=f"workflow control {index}")
        binding_id = binding["id"]
        if not isinstance(binding_id, str) or binding_id in bindings:
            raise ChangeControlError("workflow control IDs must be unique strings")
        bindings[binding_id] = binding
    if set(bindings) != set(_EXPECTED_BINDINGS):
        raise ChangeControlError("workflow control inventory differs from baseline")
    for binding_id in _EXPECTED_BINDINGS:
        _validate_binding(root, bindings[binding_id])

    write_capable_jobs = _validate_write_inventory(
        root,
        policy["workflow_default_permissions"],
        bindings,
    )
    _validate_external_controls(policy["external_evidence_requirements"])
    return {
        "repository": REPOSITORY,
        "source_version": policy["source_version"],
        "workflow_control_count": len(bindings),
        "write_capable_job_count": write_capable_jobs,
        "codeowner_pattern_count": len(_CODEOWNER_PATTERNS),
        "external_evidence_requirement_count": len(_EXTERNAL_CONTROLS),
        "external_evidence_status": "required-not-verified-by-this-tool",
    }


def verify_production_change_control(
    root: Path,
    *,
    policy_path: Path | None = None,
) -> dict[str, object]:
    """Read and verify the reviewed production change-control policy."""
    root = root.resolve()
    path = policy_path or root / "release" / "production-change-control-policy.json"
    return validate_policy_document(
        root,
        _read_json(path, label="production change-control policy"),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--policy", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = verify_production_change_control(
            args.root,
            policy_path=args.policy,
        )
    except ChangeControlError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
