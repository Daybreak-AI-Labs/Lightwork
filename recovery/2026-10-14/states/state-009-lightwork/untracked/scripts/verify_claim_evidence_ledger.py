#!/usr/bin/env python3
"""Generate and verify the buyer-facing claim/evidence ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = "lightwork.claim-evidence-ledger.v1"
LEDGER_PATH = Path("release/claim-evidence-ledger.json")
LEDGER_DOC_PATH = Path("docs/enterprise/claim-evidence-ledger.md")
PROFILE_ID = "enterprise-single-control-plane-v1"
SOURCE_VERSION = "0.1.7"
REVIEWED_AT = "2026-08-03"
REFRESH_BY = "2026-09-02"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 50_000
SOURCE_TEXT_DIGEST_MODE = "utf8-lf-sha256"
EXACT_BYTE_DIGEST_MODE = "exact-byte-sha256"

# Proof summaries are committed with ``-text`` because their retained output
# chain intentionally binds platform bytes.  Every other ledger input is
# reviewed UTF-8 text and is hashed in LF form so Git's checkout policy cannot
# invalidate a record after it is staged, committed, or verified on Linux.
EXACT_BYTE_BINDINGS = frozenset({"proof/RESULTS.md"})

ALLOWED_STATUSES = {
    "repository_verified",
    "source_inventory_only",
    "deployment_evidence_required",
    "external_evidence_required",
    "historical_only",
    "not_claimed",
    "draft",
}
BUYER_ALLOWED_STATUSES = {"repository_verified", "source_inventory_only"}

CORE_BUYER_DOCS = {
    "README.md",
    "SECURITY.md",
    "docs/DISTRIBUTION.md",
    "docs/FEATURES.md",
    "docs/api.md",
    "docs/comparison.md",
    "docs/connectors.md",
    "docs/handbook.md",
    "docs/index.md",
    "docs/press-kit.md",
    "docs/product-portfolio.md",
    "proof/RESULTS.md",
    "release/README.md",
}

STRATEGY_BUYER_DOCS = {
    "docs/strategy/README.md",
    "docs/strategy/benchmark-plan.md",
    "docs/strategy/competitive-landscape.md",
    "docs/strategy/counterfactual-promotion.md",
    "docs/strategy/design-partner-scorecard.md",
    "docs/strategy/flagship-demo-script.md",
    "docs/strategy/investor-narrative.md",
    "docs/strategy/moat-and-acquisition.md",
    "docs/strategy/moonshot-bets.md",
    "docs/strategy/pitch-deck-outline.md",
    "docs/strategy/vision-2031.md",
}
STRATEGY_STATUS_MARKER = "Strategy status - source-only / historical / proposal:"

# Aggregate repository totals in long-lived pitch prose become false as soon as
# the generated surface changes.  Exact-revision inventories belong in release
# artifacts, not strategy documents.  Planned benchmark scenario counts and
# thresholds are deliberately outside this narrow rule.
FORBIDDEN_STRATEGY_DYNAMIC_TOTALS = {
    "aggregate test count": re.compile(
        r"(?:~\s*)?\b\d[\d,]*(?:\+)?\s+"
        r"(?:(?:new|deterministic|focused|self-improvement|unit|integration|"
        r"network-free|local)\s+){0,3}tests?\b",
        re.IGNORECASE,
    ),
    "generated inventory count": re.compile(
        r"\b\d[\d,]*(?:\+)?\s+"
        r"(?:(?:specialist|generated|public-data|read-only)\s+){0,3}"
        r"(?:agents?|connectors?|packs?|routes?|skills?|suites?)\b",
        re.IGNORECASE,
    ),
    "fuzz iteration count": re.compile(
        r"\b(?:up\s+to\s+)?\d[\d,]*(?:\+)?\s+(?:fuzz\s+)?iterations?\b",
        re.IGNORECASE,
    ),
}

DISCLAIMER_MARKERS = {
    "README.md": ("Current production-support boundary", "unreleased"),
    "SECURITY.md": ("no supported production release", "unreleased"),
    "docs/DISTRIBUTION.md": ("Current boundary", "unreleased"),
    "docs/FEATURES.md": ("unreleased", "first production profile"),
    "docs/api.md": ("Current production-support boundary", "unreleased"),
    "docs/comparison.md": ("Current production-support boundary", "unreleased"),
    "docs/connectors.md": (
        "not evidence that an upstream API is currently compatible",
        "first production profile admits **no connectors**",
    ),
    "docs/index.md": ("unreleased", "Production authority"),
    "docs/handbook.md": ("Current production-support boundary", "unreleased"),
    "docs/press-kit.md": ("Current production-support boundary", "unreleased"),
    "docs/product-portfolio.md": (
        "Not a current offer or production-support statement",
        "unreleased",
    ),
    "docs/enterprise/architecture.md": (
        "Roadmap, not current product assurance",
        "unreleased",
    ),
    "docs/enterprise/client-access-rollout.md": (
        "Source/evaluation workflow only",
        "unreleased",
    ),
    "docs/enterprise/deployment-playbook.md": (
        "Qualification boundary",
        "unreleased",
    ),
    "docs/enterprise/diligence.md": ("Production-support boundary", "unreleased"),
    "docs/enterprise/dependency-failure-contract.md": (
        "intentionally not an availability claim",
        "requires a live outage and recovery drill",
    ),
    "docs/enterprise/editions.md": ("Release boundary", "unreleased"),
    "docs/enterprise/operator-cli-contract.md": (
        "default-deny",
        "not a machine-compatibility promise",
    ),
    "docs/enterprise/production-migration-safety.md": (
        "repository enforcement",
        "not evidence that a customer database",
    ),
    "docs/enterprise/production-runtime-contract.md": (
        "first production surface",
        "outside the admitted manifest",
    ),
    "docs/enterprise/production-support-matrix.md": (
        "Source-tree presence",
        "production-supported.",
    ),
    "docs/enterprise/product-operations.md": (
        "Source/evaluation design, not a current operating promise",
        "unreleased",
    ),
    "docs/enterprise/production-data-boundary.md": (
        "This is a source contract, not a DPA",
        "Passing means the checked-in inventory matches",
    ),
    "docs/enterprise/production-operations-runbook.md": (
        "It does **not** claim that a pager route",
        "deployment records remain release qualifications",
    ),
    "docs/enterprise/security-overview.md": (
        "Current production-support boundary",
        "unreleased",
    ),
    "docs/enterprise/secret-rotation-contract.md": (
        "repository-enforced rollout",
        "not evidence that a customer's Kubernetes",
    ),
    "docs/enterprise/service-objectives.md": (
        "current contract is deliberately `draft`",
        "must remain blocked",
    ),
    "docs/enterprise/single-client-deployment.md": (
        "Superseded production boundary",
        "unreleased",
    ),
    "docs/enterprise/tenant-quota-contract.md": (
        "code and configuration evidence, not proof",
        "load qualification remains",
        "required before release.",
    ),
    "proof/RESULTS.md": ("Not production-release evidence", "self-authored local run"),
    "release/README.md": (
        "Public publishing is intentionally fail closed",
        "no Releases",
    ),
}
for _strategy_doc in STRATEGY_BUYER_DOCS:
    DISCLAIMER_MARKERS[_strategy_doc] = (STRATEGY_STATUS_MARKER,)

PROGRAM_BUYER_DOCS = {
    "docs/programs/README.md",
    "docs/programs/ambassadors.md",
    "docs/programs/badge-program.md",
    "docs/programs/certification.md",
    "docs/programs/community-grants.md",
    "docs/programs/community-survey.md",
    "docs/programs/conference-booth.md",
    "docs/programs/conference-v3.md",
    "docs/programs/curriculum-kit.md",
    "docs/programs/foundation-exploration.md",
    "docs/programs/github-stars-campaign.md",
    "docs/programs/hackathons.md",
    "docs/programs/integration-partnerships.md",
    "docs/programs/localized-communities.md",
    "docs/programs/meetups.md",
    "docs/programs/office-hours.md",
    "docs/programs/press-and-case-studies.md",
    "docs/programs/roadmap-voting.md",
    "docs/programs/skill-of-the-year.md",
    "docs/programs/sponsorship-tiers.md",
    "docs/programs/summit-v1-virtual.md",
    "docs/programs/summit-v2-hybrid.md",
    "docs/programs/swag.md",
    "docs/programs/tutorial-videos.md",
    "docs/programs/university-outreach.md",
}
for _program_doc in PROGRAM_BUYER_DOCS:
    DISCLAIMER_MARKERS[_program_doc] = ("Roadmap/proposal only:",)

LEGAL_BUYER_DOCS = {
    "docs/enterprise/legal/README.md",
    "docs/enterprise/legal/accessibility-statement-template.md",
    "docs/enterprise/legal/commercial-agreement-checklist.md",
    "docs/enterprise/legal/dpa-template.md",
    "docs/enterprise/legal/privacy-notice-template.md",
    "docs/enterprise/legal/procurement-scope.md",
    "docs/enterprise/legal/sla-template.md",
    "docs/enterprise/legal/subprocessors.md",
}
DISCLAIMER_MARKERS.update(
    {
        "docs/enterprise/legal/README.md": ("Template boundary", "unreleased"),
        "docs/enterprise/legal/accessibility-statement-template.md": (
            "Draft template only:",
            "no accessibility conformance",
        ),
        "docs/enterprise/legal/commercial-agreement-checklist.md": (
            "Draft checklist only",
            "not a license grant",
        ),
        "docs/enterprise/legal/dpa-template.md": ("Not legal advice", "template"),
        "docs/enterprise/legal/privacy-notice-template.md": (
            "Draft template only",
            "not legal advice",
        ),
        "docs/enterprise/legal/procurement-scope.md": (
            "Draft template only",
            "no supported",
        ),
        "docs/enterprise/legal/sla-template.md": ("Not legal advice", "template"),
        "docs/enterprise/legal/subprocessors.md": ("Template.", "deployment"),
    }
)

COMPLIANCE_MAPPING_DOCS = {
    "docs/compliance/control-crosswalk.md",
    "docs/compliance/iso-27001/statement-of-applicability.md",
    "docs/compliance/iso-42001/statement-of-applicability.md",
    "docs/compliance/soc2-controls.md",
}
COMPLIANCE_DRAFT_DOCS = {
    "docs/compliance/README.md",
    "docs/compliance/iso-27001/README.md",
    "docs/compliance/iso-42001/README.md",
    "docs/compliance/risk-management-methodology.md",
    "docs/compliance/soc2/README.md",
    "docs/compliance/policies/access-control-policy.md",
    "docs/compliance/policies/ai-management-policy.md",
    "docs/compliance/policies/business-continuity-policy.md",
    "docs/compliance/policies/change-management-policy.md",
    "docs/compliance/policies/cryptography-policy.md",
    "docs/compliance/policies/data-protection-and-retention-policy.md",
    "docs/compliance/policies/human-resources-security-policy.md",
    "docs/compliance/policies/incident-response-policy.md",
    "docs/compliance/policies/information-security-policy.md",
    "docs/compliance/policies/risk-management-policy.md",
    "docs/compliance/policies/secure-development-policy.md",
    "docs/compliance/policies/supplier-security-policy.md",
    "docs/compliance/procedures/README.md",
    "docs/compliance/procedures/change-management-procedure.md",
    "docs/compliance/procedures/hr-security-procedures.md",
    "docs/compliance/procedures/incident-response-runbook.md",
    "docs/compliance/procedures/internal-audit-plan.md",
    "docs/compliance/procedures/risk-assessment-and-review-procedure.md",
    "docs/compliance/procedures/vendor-management-procedure.md",
    "docs/compliance/procedures/vulnerability-management-procedure.md",
}
COMPLIANCE_TEMPLATE_DOCS = {
    "docs/compliance/templates/acceptable-use-policy.md",
    "docs/compliance/templates/incident-report-template.md",
    "docs/compliance/templates/management-review-minutes-template.md",
    "docs/compliance/templates/vendor-security-questionnaire.md",
}
COMPLIANCE_REGISTER_DOCS = {
    "docs/compliance/registers/asset-inventory.md",
    "docs/compliance/registers/corrective-action-log.md",
    "docs/compliance/registers/remediation-tracker.md",
    "docs/compliance/registers/subprocessor-register.md",
    "docs/compliance/registers/vendor-register.md",
    "docs/compliance/risk-register.md",
}
COMPLIANCE_HISTORICAL_DOCS = {
    "docs/compliance/evidence/2026-06-24-management-review.md",
    "docs/compliance/evidence/2026-Q2-internal-audit-report.md",
    "docs/compliance/evidence/README.md",
}
COMPLIANCE_DEPLOYMENT_DOCS = {
    "docs/compliance/deployment/hardening-checklist.md",
}
COMPLIANCE_BOUNDARY_DOCS = {
    "docs/compliance/status-boundary.md",
}
COMPLIANCE_BUYER_DOCS = (
    COMPLIANCE_MAPPING_DOCS
    | COMPLIANCE_DRAFT_DOCS
    | COMPLIANCE_TEMPLATE_DOCS
    | COMPLIANCE_REGISTER_DOCS
    | COMPLIANCE_HISTORICAL_DOCS
    | COMPLIANCE_DEPLOYMENT_DOCS
    | COMPLIANCE_BOUNDARY_DOCS
)
COMPLIANCE_INCIDENT_DOCS = {
    "docs/compliance/policies/incident-response-policy.md",
    "docs/compliance/procedures/incident-response-runbook.md",
    "docs/compliance/procedures/vulnerability-management-procedure.md",
    "docs/compliance/templates/incident-report-template.md",
}
for _compliance_doc in COMPLIANCE_MAPPING_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — repository mapping — source only:",
    )
for _compliance_doc in COMPLIANCE_DRAFT_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — source-controlled draft:",
    )
for _compliance_doc in COMPLIANCE_TEMPLATE_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — template — unexecuted:",
    )
for _compliance_doc in COMPLIANCE_REGISTER_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — sample register — not authoritative:",
    )
for _compliance_doc in COMPLIANCE_HISTORICAL_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — historical local snapshot — non-independent:",
    )
for _compliance_doc in COMPLIANCE_DEPLOYMENT_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — deployment reference — unqualified:",
    )
for _compliance_doc in COMPLIANCE_BOUNDARY_DOCS:
    DISCLAIMER_MARKERS[_compliance_doc] = (
        "Compliance status — source-controlled boundary:",
    )


def _claim(
    claim_id: str,
    category: str,
    statement: str,
    status: str,
    allowed: bool,
    evidence: Sequence[tuple[str, str]],
    method: str,
    environment: str,
    limitations: str,
    owner: str,
) -> dict[str, Any]:
    return {
        "id": claim_id,
        "category": category,
        "statement": statement,
        "status": status,
        "buyer_claim_allowed": allowed,
        "evidence": list(evidence),
        "validation_method": method,
        "validation_environment": environment,
        "limitations": limitations,
        "owner": owner,
    }


CLAIM_DEFINITIONS = (
    _claim(
        "release-unavailable",
        "release",
        "Source version 0.1.7 is unreleased and no supported production release exists.",
        "repository_verified",
        True,
        (("release/production-surface-manifest.json", "manifest"),),
        "strict manifest verification",
        "repository checkout",
        "This states repository release status only and is not a future release commitment.",
        "release-governance",
    ),
    _claim(
        "first-profile-boundary",
        "support-scope",
        "The reviewed first production profile is a narrow, single-control-plane Linux/amd64 Kubernetes profile.",
        "repository_verified",
        True,
        (
            ("release/production-surface-manifest.json", "manifest"),
            ("release/production-support-matrix.json", "contract"),
        ),
        "cross-file exact-contract verification",
        "repository checkout",
        "Admission in source still requires exact release and live qualification evidence.",
        "release-governance",
    ),
    _claim(
        "production-package-inventory",
        "source-inventory",
        "Three workspace packages are admitted by the first-profile source manifest.",
        "source_inventory_only",
        True,
        (
            ("release/production-code-inventory.json", "generated-inventory"),
            ("release/production-surface-manifest.json", "manifest"),
        ),
        "deterministic source inventory",
        "repository checkout",
        "Package admission is not release availability or deployed service qualification.",
        "release-governance",
    ),
    _claim(
        "dashboard-route-inventory",
        "source-inventory",
        "The dashboard route and policy-class inventory is generated from the constructed application.",
        "source_inventory_only",
        True,
        (
            ("release/production-code-inventory.json", "generated-inventory"),
            ("packages/maverick-dashboard/maverick_dashboard/endpoint_policy.py", "source"),
        ),
        "isolated application construction and route enumeration",
        "repository checkout",
        "Inventory does not prove deployed authorization, availability, or useful behavior.",
        "application-security",
    ),
    _claim(
        "operator-tool-set",
        "operator-interface",
        "The first profile admits exactly nine read-only operator tools and denies unclassified commands.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/operator_contract.py", "source"),
            ("packages/maverick-core/tests/test_operator_contract.py", "regression"),
            ("release/production-surface-manifest.json", "manifest"),
        ),
        "exact contract and adverse command classification tests",
        "local repository test environment",
        "This does not qualify every API response or a deployed operator journey.",
        "platform-security",
    ),
    _claim(
        "single-control-plane-no-ha",
        "availability",
        "The first profile permits one control-plane replica and makes no high-availability claim.",
        "repository_verified",
        True,
        (
            ("release/production-support-matrix.json", "contract"),
            ("deploy/helm/maverick/values-production.yaml", "deployment-source"),
        ),
        "manifest and Helm profile consistency verification",
        "repository checkout",
        "Dependency availability and customer recovery objectives still require live evidence.",
        "platform-operations",
    ),
    _claim(
        "postgres-tenant-controls-source",
        "tenant-isolation",
        "The source implements exact PostgreSQL runtime principals, tenant context, and forced-RLS checks for the first profile.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/world_model_backends/postgres.py", "source"),
            ("packages/maverick-core/tests/test_postgres_production_posture.py", "regression"),
            ("packages/maverick-core/tests/test_pg_rls.py", "regression"),
        ),
        "unit and opt-in PostgreSQL contract tests",
        "local repository test environment",
        "Code evidence does not prove isolation in a customer's PostgreSQL service.",
        "data-security",
    ),
    _claim(
        "postgres-tenant-isolation-live",
        "tenant-isolation",
        "Cross-tenant isolation is qualified on the exact production PostgreSQL deployment.",
        "deployment_evidence_required",
        False,
        (
            ("release/production-support-matrix.json", "qualification-contract"),
            ("docs/enterprise/deployment-playbook.md", "runbook"),
        ),
        "live forced-RLS and wrong-principal exercise",
        "exact candidate deployment",
        "No revision-bound live PostgreSQL qualification packet exists yet.",
        "data-security",
    ),
    _claim(
        "encryption-controls-source",
        "encryption",
        "The source contains authenticated encryption and Vault-backed key-management controls for admitted state paths.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/crypto_at_rest.py", "source"),
            ("packages/maverick-core/maverick/kms_backends.py", "source"),
            ("packages/maverick-core/tests/test_crypto_at_rest.py", "regression"),
        ),
        "cryptographic round-trip and failure-path tests",
        "local repository test environment",
        "This is not proof of live Vault custody, rotation, backup, or plaintext eradication.",
        "data-security",
    ),
    _claim(
        "audit-signing-source",
        "audit",
        "The source implements signed hash-chained audit records and chain verification.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/audit/signing.py", "source"),
            ("packages/maverick-core/tests/test_audit_signing_key_injection.py", "regression"),
        ),
        "signing, verification, and key-injection tests",
        "local repository test environment",
        "A host-local chain alone cannot prove resistance to host compromise or deletion.",
        "security-operations",
    ),
    _claim(
        "audit-tamper-evidence-live",
        "audit",
        "The deployed audit trail is tamper-evident under off-host custody and retained checkpoints.",
        "deployment_evidence_required",
        False,
        (
            ("release/secret-rotation-contract.json", "custody-contract"),
            ("docs/enterprise/deployment-playbook.md", "runbook"),
        ),
        "off-host custody, deletion, delivery, and historical verification drill",
        "exact candidate deployment",
        "No revision-bound off-host audit-custody and deletion-resistance evidence exists yet.",
        "security-operations",
    ),
    _claim(
        "application-egress-controls-source",
        "network-security",
        "The source applies fail-closed checks to governed model dispatch and supported HTTP client paths.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/enterprise.py", "source"),
            ("packages/maverick-core/maverick/deployment.py", "source"),
            ("packages/maverick-core/tests/test_deployment.py", "regression"),
        ),
        "application-layer denial and redirect-path tests",
        "local repository test environment",
        "These controls are not a packet firewall and cannot contain arbitrary in-process code.",
        "application-security",
    ),
    _claim(
        "network-no-egress-live",
        "network-security",
        "The exact deployment prevents all unauthorized network egress.",
        "deployment_evidence_required",
        False,
        (
            ("deploy/helm/maverick/templates/networkpolicy.yaml", "deployment-source"),
            ("docs/enterprise/deployment-playbook.md", "runbook"),
        ),
        "live namespace, raw-socket, DNS, redirect, and subprocess escape exercise",
        "exact candidate cluster",
        "No revision-bound cluster-level containment result exists yet.",
        "platform-security",
    ),
    _claim(
        "arbitrary-execution-disabled",
        "sandbox",
        "The first profile disables arbitrary command-execution backends.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/sandbox/disabled.py", "source"),
            ("packages/maverick-core/tests/test_production_sandbox_disabled.py", "regression"),
            ("release/production-surface-manifest.json", "manifest"),
        ),
        "backend selection and direct-invocation adverse tests",
        "local repository test environment",
        "This does not qualify optional sandbox backends, which remain excluded.",
        "platform-security",
    ),
    _claim(
        "integrations-excluded",
        "support-scope",
        "Channels, connectors, MCP interfaces, plugins, and attachments are excluded from the first profile.",
        "repository_verified",
        True,
        (
            ("release/production-surface-manifest.json", "manifest"),
            ("release/production-code-inventory.json", "generated-inventory"),
            ("packages/maverick-core/tests/test_production_feature_exclusions.py", "regression"),
        ),
        "surface-manifest and direct-invocation exclusion tests",
        "local repository test environment",
        "Excluded source may exist but is not a supported production capability.",
        "release-governance",
    ),
    _claim(
        "adaptive-systems-research-only",
        "adaptive-systems",
        "Self-learning, code DGM, weights DGM, and automatic flow promotion are not admitted production authority.",
        "repository_verified",
        True,
        (
            ("release/production-surface-manifest.json", "manifest"),
            ("packages/maverick-core/tests/test_production_feature_exclusions.py", "regression"),
        ),
        "surface exclusion and direct-invocation tests",
        "local repository test environment",
        "Research source and fixtures do not establish safe or useful improvement.",
        "ml-governance",
    ),
    _claim(
        "adaptive-improvement",
        "adaptive-systems",
        "Adaptive systems produce useful, safe, customer-relevant improvement.",
        "not_claimed",
        False,
        (("proof/RESULTS.md", "historical-proof-notice"),),
        "held-out customer evaluation with governed promotion and rollback",
        "representative customer workload",
        "No customer-bound comparative evidence establishes this claim.",
        "ml-governance",
    ),
    _claim(
        "proof-harness-results",
        "proof",
        "Checked-in proof-harness results qualify the current production release.",
        "historical_only",
        False,
        (
            ("proof/RESULTS.md", "historical-result"),
            ("release/production-code-inventory.json", "generated-inventory"),
        ),
        "revision-bound rerun in the release evidence packet",
        "exact release candidate",
        "The checked-in results lack a current source revision and production qualification context.",
        "release-governance",
    ),
    _claim(
        "compliance-certification",
        "compliance",
        "Lightwork is certified or independently attested for a regulated framework.",
        "external_evidence_required",
        False,
        (("SECURITY.md", "security-disclosure"),),
        "independent audit or certification with exact scope and period",
        "external assessor",
        "Repository self-checks and framework mappings are not certification.",
        "compliance",
    ),
    _claim(
        "performance-slo",
        "performance",
        "The production service meets a measured availability, latency, throughput, or recovery objective.",
        "draft",
        False,
        (("release/service-objectives.json", "draft-objectives"),),
        "revision-bound load, soak, outage, backup, and restore runs",
        "exact candidate deployment",
        "Objectives are draft; there is no representative live result packet.",
        "site-reliability",
    ),
    _claim(
        "customer-roi",
        "business-outcome",
        "Lightwork produces a quantified customer ROI or labor-saving outcome.",
        "not_claimed",
        False,
        (("docs/enterprise/diligence.md", "buyer-disclosure"),),
        "customer-owned baseline and controlled outcome measurement",
        "representative customer deployment",
        "No customer-bound ROI study exists in the repository.",
        "product",
    ),
    _claim(
        "model-policy-approved",
        "model-provider",
        "A production model artifact and vLLM endpoint are approved for release.",
        "draft",
        False,
        (("release/production-model-policy.json", "draft-policy"),),
        "model, endpoint, retention, region, credential, cap, and failover approval",
        "exact candidate deployment",
        "The checked-in model policy is draft and contains no approved model.",
        "ml-governance",
    ),
    _claim(
        "identity-qualified-live",
        "identity",
        "OIDC or trusted-proxy identity is qualified against the exact production provider.",
        "deployment_evidence_required",
        False,
        (
            ("release/production-support-matrix.json", "qualification-contract"),
            ("packages/maverick-dashboard/tests/test_exact_principal_identity.py", "regression"),
        ),
        "live login, claim mapping, logout, revocation, replay, and break-glass exercise",
        "exact candidate identity provider",
        "Local token fixtures do not qualify a live identity provider.",
        "identity-security",
    ),
    _claim(
        "dependency-failure-drills-live",
        "resilience",
        "PostgreSQL, Vault, vLLM, identity, and audit dependency failures meet the production failure contract.",
        "deployment_evidence_required",
        False,
        (
            ("release/dependency-failure-contract.json", "contract"),
            ("docs/enterprise/dependency-failure-contract.md", "runbook"),
        ),
        "live dependency outage and recovery drills",
        "exact candidate deployment",
        "Repository fault tests do not establish managed-service outage behavior.",
        "site-reliability",
    ),
    _claim(
        "secret-rotation-source",
        "secret-management",
        "The repository defines fail-closed rotation phases and per-secret ownership for the first profile.",
        "repository_verified",
        True,
        (
            ("release/secret-rotation-contract.json", "contract"),
            ("scripts/verify_secret_rotation_contract.py", "verifier"),
            ("packages/maverick-core/tests/test_secret_rotation_contract.py", "regression"),
        ),
        "strict cross-file contract verification",
        "local repository test environment",
        "The contract does not prove a successful rotation in customer infrastructure.",
        "platform-security",
    ),
    _claim(
        "secret-rotation-live",
        "secret-management",
        "Every required production secret has completed a rollback-safe rotation drill.",
        "deployment_evidence_required",
        False,
        (("release/secret-rotation-contract.json", "drill-contract"),),
        "provider-specific rotation, historical-read, rollback, and revocation drill",
        "exact candidate deployment",
        "No revision-bound live rotation packet exists yet.",
        "platform-security",
    ),
    _claim(
        "support-bundle-source",
        "supportability",
        "The source implements redacted, encrypted support bundles with explicit preview and consent.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/support_bundle.py", "source"),
            ("packages/maverick-core/tests/test_support_bundle_security.py", "regression"),
        ),
        "parser, redaction, encryption, consent, and adverse output tests",
        "local repository test environment",
        "Operational key custody and recipient workflows still require live qualification.",
        "support-security",
    ),
    _claim(
        "support-bundle-custody-live",
        "supportability",
        "Production support recipients and decryption keys are provisioned under approved custody.",
        "deployment_evidence_required",
        False,
        (("docs/enterprise/product-operations.md", "operations-runbook"),),
        "support-recipient provisioning and access-review exercise",
        "production support organization",
        "No production recipient-key or access-review evidence exists yet.",
        "support-security",
    ),
    _claim(
        "registry-and-release-published",
        "release",
        "A signed immutable production image and complete release evidence packet are published.",
        "external_evidence_required",
        False,
        (
            ("release/release-state.json", "draft-release-state"),
            ("release/registry-observation-2026-08-03.json", "registry-observation"),
        ),
        "registry digest, signature, provenance, release-asset, and revision verification",
        "GitHub release and production OCI registry",
        "No current revision-bound production release is published.",
        "release-governance",
    ),
    _claim(
        "durability-controls-source",
        "durability",
        "The source implements idempotency, durable authority, reconciliation, and resume controls for admitted execution paths.",
        "repository_verified",
        True,
        (
            ("packages/maverick-core/maverick/api_idempotency.py", "source"),
            ("packages/maverick-core/maverick/flow/postgres_authority.py", "source"),
            ("packages/maverick-dashboard/tests/test_idempotency_delivery_recovery.py", "regression"),
        ),
        "duplicate, crash-window, replay, and reconciliation regressions",
        "local repository test environment",
        "Repository tests do not establish service recovery objectives under real outages.",
        "platform-reliability",
    ),
    _claim(
        "durability-recovery-live",
        "durability",
        "The exact deployment meets declared resume, recovery, and data-loss objectives.",
        "deployment_evidence_required",
        False,
        (("release/service-objectives.json", "draft-objectives"),),
        "live outage, process-loss, duplicate-delivery, backup, and restore exercise",
        "exact candidate deployment",
        "No revision-bound representative recovery result exists yet.",
        "site-reliability",
    ),
    _claim(
        "tenant-quota-source",
        "capacity-governance",
        "The source implements generation-fenced tenant quota admission, reservations, and settlement for admitted resource paths.",
        "repository_verified",
        True,
        (
            ("release/tenant-quota-contract.json", "contract"),
            ("packages/maverick-core/maverick/tenant_quota_authority.py", "source"),
            ("packages/maverick-core/tests/test_tenant_quota_authority.py", "regression"),
            ("packages/maverick-core/tests/test_flow_action_tenant_quota.py", "regression"),
        ),
        "admission, stale-policy, reservation, expiry, and settlement tests",
        "local repository test environment",
        "Attachment, vector, and connector storage are excluded; live capacity is unqualified.",
        "platform-reliability",
    ),
    _claim(
        "tenant-capacity-live",
        "capacity-governance",
        "Tenant ceilings and backpressure protect the exact production deployment at representative load.",
        "deployment_evidence_required",
        False,
        (("release/tenant-quota-contract.json", "qualification-contract"),),
        "representative saturation, fairness, expiry, crash, and recovery load test",
        "exact candidate deployment",
        "No workload-derived limits or live saturation evidence exists yet.",
        "site-reliability",
    ),
    _claim(
        "migration-safety-source",
        "database-migration",
        "The repository defines authorized, adjacent, checksum-locked production migration semantics.",
        "repository_verified",
        True,
        (
            ("release/production-migration-contract.json", "contract"),
            ("packages/maverick-core/maverick/production_migration.py", "source"),
            ("packages/maverick-core/tests/test_production_migration.py", "regression"),
        ),
        "exact contract, authorization, checksum, and failure-path tests",
        "local repository test environment",
        "This does not prove a safe migration against a customer's database and data volume.",
        "data-operations",
    ),
    _claim(
        "migration-drill-live",
        "database-migration",
        "The exact release migration completed backup, restore, rollback, and representative-data qualification.",
        "deployment_evidence_required",
        False,
        (
            ("release/production-migration-contract.json", "drill-contract"),
            ("docs/enterprise/production-migration-safety.md", "runbook"),
        ),
        "preflight, backup, adjacent migration, verification, and restore drill",
        "exact candidate PostgreSQL deployment",
        "No revision-bound live migration packet exists yet.",
        "data-operations",
    ),
    _claim(
        "versioned-api-contract-source",
        "api-contract",
        "The first-profile source has an exact versioned OpenAPI digest, lifecycle policy, and startup drift refusal.",
        "repository_verified",
        True,
        (
            (
                "packages/maverick-dashboard/maverick_dashboard/openapi_contract.py",
                "source",
            ),
            (
                "packages/maverick-dashboard/maverick_dashboard/contracts/openapi-production-v1.json",
                "contract",
            ),
            (
                "packages/maverick-dashboard/tests/test_production_api_boundary.py",
                "regression",
            ),
        ),
        "exact generated-schema digest comparison and adverse drift regression",
        "local repository test environment",
        "This is an unpublished repository contract, not a released client-support or availability commitment.",
        "application-security",
    ),
    _claim(
        "strategy-documents-source-only",
        "public-claims",
        "Every document under docs/strategy is recursively inventoried and explicitly classifies repository observations as source-only.",
        "source_inventory_only",
        True,
        (
            ("docs/strategy/README.md", "strategy-status-policy"),
            (
                "release/production-surface-manifest.json",
                "production-authority",
            ),
        ),
        "exact recursive inventory, status marker, dynamic-total refusal, and whole-document hash binding",
        "repository checkout",
        "Source presence does not establish release support, deployment qualification, compatibility, performance, control effectiveness, or customer value.",
        "product-governance",
    ),
    _claim(
        "strategy-documents-historical-only",
        "public-claims",
        "Measurements, market comparisons, build logs, reviews, and past outcomes in strategy documents are historical rather than current-release evidence.",
        "historical_only",
        False,
        (
            ("docs/strategy/README.md", "strategy-status-policy"),
            ("proof/RESULTS.md", "historical-local-results-boundary"),
        ),
        "status boundary plus exact whole-document binding",
        "historical local observation",
        "Historical statements may be stale, locally scoped, self-authored, or unbound to the candidate revision and require fresh evidence before external use.",
        "product-governance",
    ),
    _claim(
        "strategy-documents-proposal-only",
        "public-claims",
        "Strategy targets, timelines, valuations, positioning, demos, customer outcomes, and future capabilities are unapproved proposals.",
        "draft",
        False,
        (
            ("docs/strategy/README.md", "strategy-status-policy"),
            ("docs/ROADMAP.md", "roadmap"),
        ),
        "explicit proposal boundary and whole-document hash binding",
        "repository checkout",
        "No commitment, customer fact, return on investment, valuation, vendor intent, legal conclusion, service level, or production status is established.",
        "product-governance",
    ),
    _claim(
        "program-documents-proposal-only",
        "public-claims",
        "Every document under docs/programs is explicitly a roadmap proposal rather than an operating program or product-status claim.",
        "repository_verified",
        True,
        (
            ("docs/programs/README.md", "policy"),
            ("docs/ROADMAP.md", "roadmap"),
            ("release/production-surface-manifest.json", "production-authority"),
        ),
        "exact program-document inventory, disclaimer, and whole-document hash binding",
        "repository checkout",
        "Future operation, approval, compatibility, staffing, funding, legal terms, and external use require separate evidence.",
        "product-governance",
    ),
    _claim(
        "legal-materials-template-only",
        "legal-procurement",
        "The enterprise legal pack is expressly a set of deployment-specific templates requiring counsel and named-owner approval.",
        "repository_verified",
        True,
        (
            ("docs/enterprise/legal/README.md", "scope-policy"),
            ("docs/enterprise/legal/procurement-scope.md", "status-register"),
            ("LICENSE", "source-license"),
            ("THIRD-PARTY-NOTICES.md", "declared-dependency-notice-aid"),
        ),
        "exact legal-template inventory, disclaimer, and whole-document hash binding",
        "repository checkout",
        "No customer agreement, legal conclusion, privacy compliance, accessibility conformance, SLA, or certification is established.",
        "legal-and-procurement",
    ),
    _claim(
        "compliance-documents-source-only",
        "compliance-governance",
        "The repository contains 44 status-classified compliance documents as draft, source mapping, deployment reference, template, sample register, historical snapshot, or boundary material.",
        "source_inventory_only",
        True,
        (
            ("docs/compliance/status-boundary.md", "status-policy"),
            ("docs/compliance/README.md", "scope-index"),
            ("docs/compliance/control-crosswalk.md", "source-mapping"),
        ),
        "exact recursive document inventory, status marker, and whole-document hash binding",
        "repository checkout",
        "Source control and repository regressions do not establish organizational approval, live operation, legal compliance, control effectiveness, or certification.",
        "compliance-governance",
    ),
    _claim(
        "compliance-operation-and-assessment-live",
        "compliance-governance",
        "The compliance pack is approved and operated by a staffed organization and has current independent assessment evidence.",
        "external_evidence_required",
        False,
        (
            ("docs/compliance/status-boundary.md", "status-policy"),
            ("docs/compliance/README.md", "program-boundary"),
        ),
        "named-owner approvals, operating-window records, exact-deployment evidence, and independent assessment",
        "operating organization and exact release deployment",
        "No staffed operation, observation window, independent assessment, attestation, certification, or legal-compliance conclusion is established.",
        "compliance-governance",
    ),
    _claim(
        "compliance-snapshots-historical-only",
        "compliance-governance",
        "The two dated 2026 compliance records are self-authored historical local snapshots and cannot qualify the current source or a release.",
        "historical_only",
        False,
        (
            (
                "docs/compliance/evidence/2026-Q2-internal-audit-report.md",
                "historical-local-review",
            ),
            (
                "docs/compliance/evidence/2026-06-24-management-review.md",
                "historical-self-record",
            ),
        ),
        "status boundary plus exact whole-document binding",
        "historical local observation",
        "Immutable revision, raw-output provenance, independence, current environment, and external assessment are not established.",
        "compliance-governance",
    ),
    _claim(
        "incident-response-materials-source-only",
        "security-response",
        "The repository contains draft incident-response and vulnerability-management materials plus candidate containment and audit source.",
        "source_inventory_only",
        True,
        (
            ("SECURITY.md", "draft-public-policy"),
            (
                "docs/compliance/procedures/incident-response-runbook.md",
                "draft-runbook",
            ),
            (
                "docs/compliance/procedures/vulnerability-management-procedure.md",
                "draft-procedure",
            ),
        ),
        "exact source and document review with whole-document binding",
        "repository checkout",
        "This is not a monitored intake, staffed response, supported version, live containment exercise, or forensic-evidence operation.",
        "security-response",
    ),
    _claim(
        "incident-response-operation-live",
        "security-response",
        "A staffed security organization monitors intake, meets response and notification targets, exercises containment/recovery, and retains complete live forensic evidence.",
        "external_evidence_required",
        False,
        (
            ("SECURITY.md", "current-response-boundary"),
            (
                "docs/compliance/policies/incident-response-policy.md",
                "draft-policy",
            ),
            (
                "docs/compliance/procedures/incident-response-runbook.md",
                "draft-runbook",
            ),
        ),
        "staffing/on-call records, monitored intake, tabletop/live exercises, notification records, and complete evidence-custody proof",
        "operating organization and exact deployment",
        "No supported release, staffed response, exercised SLA, customer-notification operation, retained complete forensics, penetration test, or independent assessment is established.",
        "security-response",
    ),
    _claim(
        "production-ready-overall",
        "release",
        "Lightwork is production-ready for customer deployment.",
        "not_claimed",
        False,
        (
            ("release/production-readiness-evidence.json", "draft-readiness-evidence"),
            ("release/release-state.json", "draft-release-state"),
        ),
        "all code, deployment, external, and release gates approved for one immutable revision",
        "exact release candidate and production-like environment",
        "Code hardening is substantial, but live qualification, independent evidence, and a signed release remain open.",
        "release-governance",
    ),
)

ALL_CLAIM_IDS = {item["id"] for item in CLAIM_DEFINITIONS}

DOC_CLAIMS = {
    "README.md": ("release-unavailable", "first-profile-boundary", "integrations-excluded", "adaptive-systems-research-only", "adaptive-improvement", "production-ready-overall"),
    "SECURITY.md": (
        "release-unavailable",
        "audit-signing-source",
        "compliance-certification",
        "incident-response-materials-source-only",
        "incident-response-operation-live",
        "production-ready-overall",
    ),
    "docs/DISTRIBUTION.md": ("release-unavailable", "registry-and-release-published", "production-ready-overall"),
    "docs/FEATURES.md": ("first-profile-boundary", "integrations-excluded", "adaptive-systems-research-only", "production-ready-overall"),
    "docs/api.md": ("release-unavailable", "first-profile-boundary", "versioned-api-contract-source", "production-ready-overall"),
    "docs/comparison.md": ("release-unavailable", "first-profile-boundary", "integrations-excluded", "adaptive-systems-research-only", "compliance-certification", "performance-slo", "customer-roi", "production-ready-overall"),
    "docs/connectors.md": ("integrations-excluded",),
    "docs/handbook.md": ("release-unavailable", "first-profile-boundary", "integrations-excluded", "adaptive-systems-research-only", "compliance-certification", "production-ready-overall"),
    "docs/index.md": ("release-unavailable", "first-profile-boundary", "integrations-excluded", "adaptive-improvement", "production-ready-overall"),
    "docs/press-kit.md": ("release-unavailable", "first-profile-boundary", "production-package-inventory", "dashboard-route-inventory", "integrations-excluded", "audit-tamper-evidence-live", "production-ready-overall"),
    "docs/product-portfolio.md": ("first-profile-boundary", "integrations-excluded", "adaptive-systems-research-only", "production-ready-overall"),
    "proof/RESULTS.md": ("proof-harness-results", "adaptive-improvement"),
    "release/README.md": ("release-unavailable", "first-profile-boundary", "registry-and-release-published", "production-ready-overall"),
    "docs/enterprise/architecture.md": ("first-profile-boundary", "postgres-tenant-controls-source", "arbitrary-execution-disabled", "durability-controls-source"),
    "docs/enterprise/client-access-rollout.md": ("identity-qualified-live", "support-bundle-custody-live", "production-ready-overall"),
    "docs/enterprise/dependency-failure-contract.md": ("dependency-failure-drills-live", "durability-recovery-live"),
    "docs/enterprise/deployment-playbook.md": ("first-profile-boundary", "postgres-tenant-isolation-live", "network-no-egress-live", "secret-rotation-live", "migration-drill-live"),
    "docs/enterprise/diligence.md": ("release-unavailable", "first-profile-boundary", "compliance-certification", "performance-slo", "customer-roi", "production-ready-overall"),
    "docs/enterprise/editions.md": ("first-profile-boundary", "integrations-excluded", "production-ready-overall"),
    "docs/enterprise/operator-cli-contract.md": ("operator-tool-set",),
    "docs/enterprise/production-migration-safety.md": ("migration-safety-source", "migration-drill-live"),
    "docs/enterprise/production-runtime-contract.md": ("first-profile-boundary", "single-control-plane-no-ha", "arbitrary-execution-disabled", "tenant-quota-source", "tenant-capacity-live", "production-ready-overall"),
    "docs/enterprise/production-support-matrix.md": ("first-profile-boundary", "integrations-excluded", "identity-qualified-live", "model-policy-approved", "production-ready-overall"),
    "docs/enterprise/product-operations.md": ("support-bundle-source", "support-bundle-custody-live", "secret-rotation-live", "production-ready-overall"),
    "docs/enterprise/production-data-boundary.md": (
        "first-profile-boundary",
        "postgres-tenant-controls-source",
        "encryption-controls-source",
        "application-egress-controls-source",
        "integrations-excluded",
        "production-ready-overall",
    ),
    "docs/enterprise/production-operations-runbook.md": (
        "operator-tool-set",
        "audit-signing-source",
        "support-bundle-source",
        "performance-slo",
        "durability-controls-source",
        "durability-recovery-live",
        "incident-response-materials-source-only",
        "incident-response-operation-live",
        "production-ready-overall",
    ),
    "docs/enterprise/secret-rotation-contract.md": ("secret-rotation-source", "secret-rotation-live"),
    "docs/enterprise/tenant-quota-contract.md": ("tenant-quota-source", "tenant-capacity-live"),
    "docs/enterprise/security-overview.md": ("release-unavailable", "first-profile-boundary", "postgres-tenant-controls-source", "encryption-controls-source", "audit-signing-source", "audit-tamper-evidence-live", "application-egress-controls-source", "network-no-egress-live", "compliance-certification", "production-ready-overall"),
    "docs/enterprise/service-objectives.md": ("performance-slo", "durability-recovery-live", "dependency-failure-drills-live"),
    "docs/enterprise/single-client-deployment.md": ("first-profile-boundary", "single-control-plane-no-ha", "identity-qualified-live", "production-ready-overall"),
}
for _strategy_doc in STRATEGY_BUYER_DOCS:
    DOC_CLAIMS[_strategy_doc] = (
        "release-unavailable",
        "strategy-documents-source-only",
        "strategy-documents-historical-only",
        "strategy-documents-proposal-only",
        "production-ready-overall",
    )
for _program_doc in PROGRAM_BUYER_DOCS:
    DOC_CLAIMS[_program_doc] = (
        "release-unavailable",
        "program-documents-proposal-only",
        "production-ready-overall",
    )
for _legal_doc in LEGAL_BUYER_DOCS:
    DOC_CLAIMS[_legal_doc] = (
        "release-unavailable",
        "legal-materials-template-only",
        "compliance-certification",
        "production-ready-overall",
    )
for _compliance_doc in COMPLIANCE_BUYER_DOCS:
    DOC_CLAIMS[_compliance_doc] = (
        "compliance-documents-source-only",
        "compliance-operation-and-assessment-live",
        "compliance-certification",
        "production-ready-overall",
    )
for _historical_doc in COMPLIANCE_HISTORICAL_DOCS:
    DOC_CLAIMS[_historical_doc] = (
        *DOC_CLAIMS[_historical_doc],
        "compliance-snapshots-historical-only",
    )
for _incident_doc in COMPLIANCE_INCIDENT_DOCS:
    DOC_CLAIMS[_incident_doc] = (
        *DOC_CLAIMS[_incident_doc],
        "incident-response-materials-source-only",
        "incident-response-operation-live",
    )


class ClaimEvidenceLedgerError(ValueError):
    """The claim/evidence ledger is incomplete, stale, or overclaims readiness."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ClaimEvidenceLedgerError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _walk_json(value: object, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        raise ClaimEvidenceLedgerError("JSON exceeds the maximum depth")
    nodes = 1
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ClaimEvidenceLedgerError("JSON object keys must be strings")
            nodes += _walk_json(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            nodes += _walk_json(child, depth + 1)
    if nodes > MAX_JSON_NODES:
        raise ClaimEvidenceLedgerError("JSON exceeds the maximum node count")
    return nodes


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ClaimEvidenceLedgerError(f"ledger is unavailable: {path}") from exc
    if len(raw) > MAX_JSON_BYTES:
        raise ClaimEvidenceLedgerError("ledger exceeds the maximum size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ClaimEvidenceLedgerError(f"non-finite JSON number: {token}")
            ),
        )
    except ClaimEvidenceLedgerError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ClaimEvidenceLedgerError("ledger is not strict UTF-8 JSON") from exc
    if not isinstance(value, Mapping):
        raise ClaimEvidenceLedgerError("ledger root must be an object")
    _walk_json(value)
    return value


def _repo_file(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ClaimEvidenceLedgerError(f"path escapes repository: {relative}") from exc
    if not candidate.is_file():
        raise ClaimEvidenceLedgerError(f"required evidence is unavailable: {relative}")
    return candidate


def _digest_mode(relative: str) -> str:
    return (
        EXACT_BYTE_DIGEST_MODE
        if relative in EXACT_BYTE_BINDINGS
        else SOURCE_TEXT_DIGEST_MODE
    )


def _sha256(root: Path, relative: str, *, digest_mode: str) -> str:
    raw = _repo_file(root, relative).read_bytes()
    expected_mode = _digest_mode(relative)
    if digest_mode != expected_mode:
        raise ClaimEvidenceLedgerError(
            f"unsupported digest mode for {relative}: {digest_mode}"
        )
    if digest_mode == EXACT_BYTE_DIGEST_MODE:
        payload = raw
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ClaimEvidenceLedgerError(
                f"ledger source binding is not UTF-8: {relative}"
            ) from exc
        payload = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _buyer_docs(root: Path) -> set[str]:
    enterprise = {
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "enterprise").glob("*.md")
        if path.resolve() != (root / LEDGER_DOC_PATH).resolve()
    }
    programs = {
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "programs").glob("*.md")
    }
    legal = {
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "enterprise" / "legal").glob("*.md")
    }
    compliance = {
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "compliance").rglob("*.md")
    }
    strategy = {
        path.relative_to(root).as_posix()
        for path in (root / "docs" / "strategy").rglob("*.md")
    }
    return CORE_BUYER_DOCS | enterprise | programs | legal | compliance | strategy


def build_claim_evidence_ledger(root: Path) -> dict[str, Any]:
    """Build the deterministic ledger expected for this checkout."""
    root = root.resolve()
    docs = _buyer_docs(root)
    if docs != set(DOC_CLAIMS):
        missing = sorted(docs - set(DOC_CLAIMS))
        stale = sorted(set(DOC_CLAIMS) - docs)
        raise ClaimEvidenceLedgerError(
            f"buyer-document map is incomplete; missing={missing}, stale={stale}"
        )

    claims: list[dict[str, Any]] = []
    seen: set[str] = set()
    for definition in CLAIM_DEFINITIONS:
        claim_id = definition["id"]
        if claim_id in seen:
            raise ClaimEvidenceLedgerError(f"duplicate claim definition: {claim_id}")
        seen.add(claim_id)
        status = definition["status"]
        allowed = definition["buyer_claim_allowed"]
        if status not in ALLOWED_STATUSES:
            raise ClaimEvidenceLedgerError(f"unsupported claim status: {status}")
        if allowed != (status in BUYER_ALLOWED_STATUSES):
            raise ClaimEvidenceLedgerError(
                f"buyer-claim permission contradicts status: {claim_id}"
            )
        evidence = []
        for path, kind in definition["evidence"]:
            digest_mode = _digest_mode(path)
            evidence.append(
                {
                    "path": path,
                    "sha256": _sha256(root, path, digest_mode=digest_mode),
                    "digest_mode": digest_mode,
                    "kind": kind,
                }
            )
        claim = {key: value for key, value in definition.items() if key != "evidence"}
        claim["evidence"] = evidence
        claim["refresh_by"] = REFRESH_BY
        claims.append(claim)

    if seen != ALL_CLAIM_IDS:
        raise ClaimEvidenceLedgerError("claim inventory is inconsistent")
    used_claims = {claim for values in DOC_CLAIMS.values() for claim in values}
    if used_claims != ALL_CLAIM_IDS:
        missing = sorted(ALL_CLAIM_IDS - used_claims)
        unknown = sorted(used_claims - ALL_CLAIM_IDS)
        raise ClaimEvidenceLedgerError(
            f"document claim coverage is incomplete; missing={missing}, unknown={unknown}"
        )

    bindings = []
    for relative in sorted(docs):
        text = _repo_file(root, relative).read_text(encoding="utf-8")
        for marker in DISCLAIMER_MARKERS.get(relative, ()):
            if marker not in text:
                raise ClaimEvidenceLedgerError(
                    f"buyer-document disclaimer marker is missing: {relative}: {marker}"
                )
        if relative in STRATEGY_BUYER_DOCS:
            for label, pattern in FORBIDDEN_STRATEGY_DYNAMIC_TOTALS.items():
                match = pattern.search(text)
                if match is not None:
                    raise ClaimEvidenceLedgerError(
                        "strategy document contains a stale dynamic total; "
                        f"path={relative}, kind={label}, value={match.group(0)!r}"
                    )
        claim_ids = list(DOC_CLAIMS[relative])
        if not claim_ids or len(claim_ids) != len(set(claim_ids)):
            raise ClaimEvidenceLedgerError(
                f"buyer document has invalid claim mapping: {relative}"
            )
        bindings.append(
            {
                "path": relative,
                "sha256": _sha256(
                    root,
                    relative,
                    digest_mode=_digest_mode(relative),
                ),
                "digest_mode": _digest_mode(relative),
                "claim_ids": claim_ids,
            }
        )

    return {
        "schema": SCHEMA,
        "profile_id": PROFILE_ID,
        "source_version": SOURCE_VERSION,
        "status": "draft",
        "reviewed_at": REVIEWED_AT,
        "refresh_by": REFRESH_BY,
        "approval": {
            "status": "draft",
            "approved_at": None,
            "approvers": [],
            "external_reference": None,
        },
        "status_definitions": {
            "repository_verified": "A narrowly worded repository fact is supported by exact checked-in evidence.",
            "source_inventory_only": "A deterministic source inventory fact is allowed only with an explicit non-production limitation.",
            "deployment_evidence_required": "Code or a runbook exists, but the claim requires evidence from the exact deployment.",
            "external_evidence_required": "The claim requires an external system, assessor, customer, registry, or release record.",
            "historical_only": "The evidence is historical or not bound to the candidate revision and cannot qualify a release.",
            "not_claimed": "The repository explicitly does not make this claim.",
            "draft": "The policy or objective is incomplete and cannot be represented as approved.",
        },
        "claims": claims,
        "buyer_documents": bindings,
    }


def verify_claim_evidence_ledger(
    root: Path,
    *,
    release_candidate: bool = False,
) -> dict[str, int | str]:
    """Verify exact hashes, coverage, conservative statuses, and approval state."""
    root = root.resolve()
    actual = _load_json(root / LEDGER_PATH)
    expected = build_claim_evidence_ledger(root)
    comparable = dict(actual)
    if release_candidate:
        comparable["status"] = expected["status"]
        comparable["approval"] = expected["approval"]
    if comparable != expected:
        raise ClaimEvidenceLedgerError(
            "claim/evidence ledger is stale or differs from the reviewed contract; "
            "regenerate it and review the diff"
        )
    if release_candidate:
        if actual["status"] != "approved":
            raise ClaimEvidenceLedgerError("release candidate requires an approved ledger")
        approval = actual["approval"]
        if not isinstance(approval, Mapping) or set(approval) != {
            "status",
            "approved_at",
            "approvers",
            "external_reference",
        }:
            raise ClaimEvidenceLedgerError(
                "release candidate approval fields are not exact"
            )
        if approval["status"] != "approved":
            raise ClaimEvidenceLedgerError("release candidate approval is incomplete")
        approvers = approval["approvers"]
        if (
            not isinstance(approvers, list)
            or any(not isinstance(item, str) or not item.strip() for item in approvers)
            or len(approvers) < 2
            or len(set(approvers)) < 2
        ):
            raise ClaimEvidenceLedgerError(
                "release candidate requires two distinct approvers"
            )
        approved_at = approval["approved_at"]
        external_reference = approval["external_reference"]
        if (
            not isinstance(approved_at, str)
            or not isinstance(external_reference, str)
            or not external_reference.strip()
        ):
            raise ClaimEvidenceLedgerError(
                "release candidate approval requires a date and external reference"
            )
        try:
            approval_date = date.fromisoformat(approved_at)
        except ValueError as exc:
            raise ClaimEvidenceLedgerError(
                "release candidate approval date is invalid"
            ) from exc
        if approval_date > date.today():
            raise ClaimEvidenceLedgerError(
                "release candidate approval date cannot be in the future"
            )
        if date.fromisoformat(actual["refresh_by"]) < date.today():
            raise ClaimEvidenceLedgerError("claim/evidence ledger approval is stale")
    return {
        "schema": SCHEMA,
        "status": str(actual["status"]),
        "claims": len(actual["claims"]),
        "buyer_documents": len(actual["buyer_documents"]),
        "buyer_allowed_claims": sum(
            bool(item["buyer_claim_allowed"]) for item in actual["claims"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--release-candidate", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.write:
            payload = build_claim_evidence_ledger(root)
            target = root / LEDGER_PATH
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        summary = verify_claim_evidence_ledger(
            root,
            release_candidate=args.release_candidate,
        )
    except ClaimEvidenceLedgerError as exc:
        print(f"claim/evidence ledger verification failed: {exc}")
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
