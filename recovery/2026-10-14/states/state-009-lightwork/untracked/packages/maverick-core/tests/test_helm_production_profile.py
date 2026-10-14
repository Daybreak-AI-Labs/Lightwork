"""Regression checks for the chart's fail-closed production posture."""

from __future__ import annotations

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib

from maverick.production_identity import (
    ProductionIdentityError,
    runtime_identity_posture,
    validate_runtime_identity_posture,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CHART = REPO_ROOT / "deploy" / "helm" / "maverick"
PRODUCTION_VALUES = CHART / "values-production.yaml"
VALID_DIGEST = "sha256:" + ("a" * 64)
VALID_AUTHORIZATION_SHA = "b" * 64
VALID_SOURCE_REVISION = "c" * 40
VALID_ONBOARDING_CONTRACT_SHA = "d" * 64
VALID_RELEASE_STATE_SHA = "e" * 64
VALID_AUTHORITY_PUBLIC_KEY = "f" * 64
VALID_IDENTITY_POSTURE_SHA = "1" * 64
VALID_DEPLOYMENT_ID = "urn:lightwork:deployment:production-test"
VALID_AUTHORITY_KEY_ID = "urn:lightwork:onboarding-authority:production-test"

STATIC_RBAC_ARGS = (
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
    *STATIC_RBAC_ARGS,
)

TRUSTED_PROXY_IDENTITY_ARGS = (
    "--set",
    "identity.mode=trustedProxy",
    "--set-string",
    "identity.config.trustedProxy.header=X-Authenticated-User",
    "--set-string",
    "identity.config.trustedProxy.trustedProxies[0]=10.44.0.20",
    "--set-string",
    "identity.config.trustedProxy.trustedProxies[1]=10.44.1.0/24",
    *STATIC_RBAC_ARGS,
)

IDENTITY_PRECEDENCE_ENV = (
    "MAVERICK_DASHBOARD_ADMINS",
    "MAVERICK_DASHBOARD_INVITES",
    "MAVERICK_DASHBOARD_TOKEN",
    "MAVERICK_OIDC_ALGORITHMS",
    "MAVERICK_OIDC_AUDIENCE",
    "MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",
    "MAVERICK_OIDC_CLIENT_ID",
    "MAVERICK_OIDC_ENABLED",
    "MAVERICK_OIDC_ISSUER",
    "MAVERICK_OIDC_JWKS_URI",
    "MAVERICK_OIDC_REDIRECT_URI",
    "MAVERICK_OIDC_TOKEN_ENDPOINT",
    "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
    "MAVERICK_PRODUCTION_IDENTITY_PROVIDER",
    "MAVERICK_PROXY_AUTH",
    "MAVERICK_PROXY_AUTH_HEADER",
    "MAVERICK_SAML_ENABLED",
)


def _production_template_args(identity_args: tuple[str, ...]) -> list[str]:
    return [
        "template",
        "maverick",
        str(CHART),
        "-f",
        str(PRODUCTION_VALUES),
        "--set",
        f"image.digest={VALID_DIGEST}",
        "--set",
        "secret.name=maverick-secrets",
        "--set",
        "secret.rotationGeneration=g000001",
        "--set-string",
        f"productionOnboarding.deploymentId={VALID_DEPLOYMENT_ID}",
        "--set-string",
        f"productionOnboarding.sourceRevision={VALID_SOURCE_REVISION}",
        "--set-string",
        f"productionOnboarding.contractSha256={VALID_ONBOARDING_CONTRACT_SHA}",
        "--set-string",
        f"productionOnboarding.releaseStateSha256={VALID_RELEASE_STATE_SHA}",
        "--set-string",
        f"productionOnboarding.authorityKeyId={VALID_AUTHORITY_KEY_ID}",
        "--set-string",
        f"productionOnboarding.authorityPublicKey={VALID_AUTHORITY_PUBLIC_KEY}",
        "--set-string",
        f"productionOnboarding.identityPostureSha256={VALID_IDENTITY_POSTURE_SHA}",
        *identity_args,
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
        "tenantQuota.limits.concurrentRuns=8",
        "--set",
        "tenantQuota.limits.queuedJobs=250",
        "--set",
        "tenantQuota.limits.dailyTokens=10000000",
        "--set",
        "tenantQuota.limits.dailySpendDollars=250",
        "--set",
        "tenantQuota.limits.toolCalls=50000",
        "--set",
        "tenantQuota.limits.storageBytes=10737418240",
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


def _manifest_document(rendered: str, *, kind: str, marker: str = "") -> str:
    for document in re.split(r"\n---\s*\n", rendered):
        if f"kind: {kind}" in document and marker in document:
            return document
    raise AssertionError(f"rendered {kind} document containing {marker!r} was absent")


def _annotation(document: str, name: str) -> str:
    match = re.search(rf"^\s*{re.escape(name)}: \"([^\"]+)\"$", document, re.MULTILINE)
    assert match is not None, f"annotation {name!r} was absent"
    return match.group(1)


def _env_value(deployment: str, name: str) -> str:
    match = re.search(
        rf"^\s*- name: {re.escape(name)}\n\s+value: \"([^\"]*)\"$",
        deployment,
        re.MULTILINE,
    )
    assert match is not None, f"environment variable {name!r} was absent"
    return match.group(1)


def _metadata_name(document: str) -> str:
    match = re.search(r"^metadata:\n\s+name: ([^\s]+)$", document, re.MULTILINE)
    assert match is not None
    return match.group(1)


def _identity_config(rendered: str) -> tuple[str, str, dict[str, object]]:
    document = _manifest_document(
        rendered,
        kind="ConfigMap",
        marker="config.toml: |",
    )
    payload = document.split("  config.toml: |\n", 1)[1]
    toml_text = textwrap.dedent(payload).strip() + "\n"
    return document, toml_text, tomllib.loads(toml_text)


def _clear_identity_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in IDENTITY_PRECEDENCE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_production_overlay_enables_every_release_invariant() -> None:
    values = PRODUCTION_VALUES.read_text(encoding="utf-8")

    required_fragments = (
        "enforce: true",
        "productionOnboarding:",
        "enabled: true",
        'deploymentId: ""',
        'sourceRevision: ""',
        'contractSha256: ""',
        'releaseStateSha256: ""',
        'authorityKeyId: ""',
        'authorityPublicKey: ""',
        'identityPostureSha256: ""',
        "observationsKey: MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
        'digest: ""',
        "replicaCount: 1",
        "backend: postgres",
        "rls: true",
        "poolSize: 10",
        "connectTimeoutSeconds: 10",
        "statementTimeoutMs: 30000",
        "lockTimeoutMs: 5000",
        "migration:",
        "authorizationSha256:",
        "activeDeadlineSeconds: 1800",
        "ttlSecondsAfterFinished: 3600",
        "stagingSizeLimit: 64Ki",
        "affinity: {}",
        "allowedEgressCIDRs: []",
        "databasePort: 5432",
        "perTenant: true",
        "strict: true",
        "provider: vault",
        "dekCacheTTLSeconds: 60",
        "backend: disabled",
        "pids: 256",
        "openFiles: 4096",
        "singleFileBytes: 8589934592",
        "maxToolResultBytes: 32000",
        "maxGoalWallSeconds: 1800",
        "maxToolCalls: 500",
        "maxAgentDepth: 3",
        "maxTotalChildAgents: 32",
        "maxConcurrentGoals: 8",
        "maxConcurrentGoalsPerPrincipal: 2",
        "runtimeClassName: gvisor",
        "provider: vllm",
        'artifactDigest: ""',
        'baseURL: ""',
        'region: ""',
        "retentionMode: self-hosted-no-provider-retention",
        "credentialSource: mounted_file",
        "maxSpendUSD: 0",
        "spendPeriod: month",
        'mode: ""',
        "byUser: true",
        "tenantQuota:",
        'policyGeneration: ""',
        'limitsJSON: ""',
        'concurrentRuns: ""',
        'storageBytes: ""',
        "reconcileSeconds: 900",
        "escalateSeconds: 3600",
        'escalationTarget: ""',
        "adjudicators: []",
        "create: false",
        'rotationGeneration: ""',
        "stagingSizeLimit: 2Mi",
        "profile: enterprise",
        "required: true",
        "runAsNonRoot: true",
        "runAsUser: 1000",
        "runAsGroup: 1000",
        "fsGroup: 1000",
        "privileged: false",
        "allowPrivilegeEscalation: false",
        "procMount: Default",
        'drop: ["ALL"]',
        "type: RuntimeDefault",
        "readOnlyRootFilesystem: true",
        "ephemeral-storage: 256Mi",
        "ephemeral-storage: 1Gi",
        "sizeLimit: 1Gi",
        "terminationGracePeriodSeconds: 60",
        "enabled: true",
        "restrictEgress: true",
        "allowPublicEgress: false",
        "allowedIngressNamespaces: []",
        "dnsCIDRs: []",
        "allowedEgressCIDRs: []",
        "type: ClusterIP",
        'className: ""',
        "tls: []",
        "serviceMonitor:",
        "key: MAVERICK_METRICS_TOKEN",
        "previousKey: MAVERICK_METRICS_TOKEN_PREVIOUS",
        "scrapeKey: MAVERICK_METRICS_TOKEN",
    )
    for fragment in required_fragments:
        assert fragment in values


def test_identity_values_expose_only_structured_non_secret_runtime_inputs() -> None:
    for values_path in (CHART / "values.yaml", PRODUCTION_VALUES):
        values = values_path.read_text(encoding="utf-8")
        identity = values.split("\nidentity:\n", 1)[1].split("\ntenancy:\n", 1)[0]
        for field in (
            "config:",
            "oidc:",
            'issuer: ""',
            'audience: ""',
            'jwksUri: ""',
            "algorithms: []",
            'clientId: ""',
            'redirectUri: ""',
            "trustedProxy:",
            'header: ""',
            "trustedProxies: []",
            "trustLoopback: false",
            "dashboard:",
            'defaultRole: ""',
            "admins: []",
            "defaultSuites: []",
            "groupRoles: {}",
            "groupSuites: {}",
            "kernel:",
            "roles: {}",
            "roleAssignments: {}",
        ):
            assert field in identity
        assert "clientSecret:" not in identity
        assert "sessionSecret:" not in identity

    config_map = (CHART / "templates" / "identity-configmap.yaml").read_text(
        encoding="utf-8",
    )
    assert config_map.lstrip().startswith('{{- include "maverick.validate" . -}}')
    assert 'include "maverick.identityConfigName" .' in config_map
    assert 'include "maverick.identityConfigSha256" .' in config_map
    assert "immutable: true" in config_map
    assert "config.toml: |" in config_map
    assert 'include "maverick.identityConfigToml" .' in config_map


def test_production_validator_is_wired_before_any_workload_output() -> None:
    helpers = (CHART / "templates" / "_helpers.tpl").read_text(encoding="utf-8")
    deployment = (CHART / "templates" / "deployment.yaml").read_text(
        encoding="utf-8",
    )

    for invariant in (
        "production.enforce",
        "image.digest",
        "worldModel.backend",
        "worldModel.postgres.rls",
        "worldModel.postgres.poolSize",
        "worldModel.postgres.connectTimeoutSeconds",
        "worldModel.postgres.statementTimeoutMs",
        "worldModel.postgres.lockTimeoutMs",
        "tenantQuota.enforce",
        "tenantQuota.policyGeneration",
        "tenantQuota.limitsJSON",
        "migration.enabled",
        "migration.secretName",
        "migration.authorizationSha256",
        "migration.activeDeadlineSeconds",
        "migration.ttlSecondsAfterFinished",
        "migration.stagingSizeLimit",
        "migration.allowedEgressCIDRs",
        "migration.databasePort",
        "encryption.perTenant",
        "encryption.strict",
        "kms.provider",
        "kms.address",
        "kms.dekCacheTTLSeconds",
        "indeterminateReconciliation.reconcileSeconds",
        "indeterminateReconciliation.escalateSeconds",
        "indeterminateReconciliation.escalationTarget",
        "indeterminateReconciliation.adjudicators",
        "sandbox.backend",
        "runtimeContract.pids",
        "runtimeContract.openFiles",
        "runtimeContract.singleFileBytes",
        "runtimeContract.maxToolResultBytes",
        "runtimeContract.maxGoalWallSeconds",
        "runtimeContract.maxToolCalls",
        "runtimeContract.maxAgentDepth",
        "runtimeContract.maxTotalChildAgents",
        "runtimeContract.maxConcurrentGoals",
        "runtimeContract.maxConcurrentGoalsPerPrincipal",
        "runtimeClassName",
        "service.type",
        "ingress.enabled",
        "ingress.className",
        "ingress.hosts",
        "ingress.tls",
        "model.provider",
        "model.name",
        "model.artifactDigest",
        "model.baseURL",
        "model.region",
        "model.retentionMode",
        "model.credentialSource",
        "model.maxSpendUSD",
        "model.spendPeriod",
        "identity.mode",
        "tenancy.byUser",
        "enterprise.required",
        "networkPolicy.enabled",
        "networkPolicy.restrictEgress",
        "networkPolicy.allowPublicEgress",
        "networkPolicy.allowedIngressNamespaces",
        "networkPolicy.dnsCIDRs",
        "networkPolicy.allowedEgressCIDRs",
        "secret.create",
        "secret.name",
        "secret.rotationGeneration",
        "secret.stagingSizeLimit",
        "privileged",
        "allowPrivilegeEscalation",
        "procMount",
        "capabilities.drop",
        "readOnlyRootFilesystem",
        "podSecurityContext.runAsNonRoot",
        "podSecurityContext.runAsUser",
        "podSecurityContext.runAsGroup",
        "podSecurityContext.fsGroup",
        "podSecurityContext.seccompProfile.type",
        "persistence.enabled",
        "persistence.size",
        "persistence.mountPath",
        "tmp.sizeLimit",
        "resources.requests",
        "resources.limits",
        "ephemeral-storage",
        "terminationGracePeriodSeconds",
        "probes.enabled",
        "serviceMonitor.enabled",
        "serviceMonitor.bearerTokenSecret.key",
        "serviceMonitor.bearerTokenSecret.scrapeKey",
        "productionOnboarding.enabled",
        "productionOnboarding.deploymentId",
        "productionOnboarding.sourceRevision",
        "productionOnboarding.contractSha256",
        "productionOnboarding.releaseStateSha256",
        "productionOnboarding.authorityKeyId",
        "productionOnboarding.authorityPublicKey",
        "productionOnboarding.identityPostureSha256",
        "productionOnboarding.observationsKey",
    ):
        assert invariant in helpers
    assert '(contains "@" .Values.kms.address)' in helpers
    assert '(contains "?" .Values.kms.address)' in helpers
    assert '(contains "#" .Values.kms.address)' in helpers
    assert 'define "maverick.productionDeploymentConfigSha256"' in helpers
    assert 'unset $values "productionOnboarding"' in helpers
    assert 'unset $secret "rotationGeneration"' in helpers
    assert "sha256sum (toJson $values)" in helpers
    assert 'define "maverick.validateIdentityConfig"' in helpers
    assert 'define "maverick.identityConfigToml"' in helpers
    assert 'define "maverick.identityConfigSha256"' in helpers
    assert 'include "maverick.validateIdentityConfig" .' in helpers
    assert deployment.lstrip().startswith('{{- include "maverick.validate" . -}}')


def test_migration_job_has_separate_custody_and_exact_release_binding() -> None:
    job = (CHART / "templates" / "migration-job.yaml").read_text(encoding="utf-8")

    assert job.lstrip().startswith('{{- include "maverick.validate" . -}}')
    assert '"helm.sh/hook": pre-install,pre-upgrade' in job
    assert '"helm.sh/hook-delete-policy": before-hook-creation' in job
    assert "kind: NetworkPolicy" in job
    assert 'include "maverick.name" . }}-migration' in job
    assert "ingress: []" in job
    assert "automountServiceAccountToken: false" in job
    assert "restartPolicy: Never" in job
    assert "backoffLimit: 0" in job
    assert "ttlSecondsAfterFinished: {{ .Values.migration.ttlSecondsAfterFinished }}" in job
    assert "{{- with .Values.migration.affinity }}" in job
    assert "{{- with .Values.affinity }}" not in job
    assert 'command: ["python", "-m", "maverick.secret_staging"]' in job
    assert 'command: ["python", "-m", "maverick.production_migration"]' in job
    assert '            - "apply"' in job
    assert '            - "--authorization-sha256"' in job
    assert '            - "--image-digest"' in job
    assert '            - "--release-version"' in job
    assert ".Values.migration.authorizationSha256" in job
    assert ".Values.migration.allowedEgressCIDRs" in job
    assert ".Values.migration.databasePort" in job
    assert ".Values.networkPolicy.allowedEgressCIDRs" not in job
    assert ".Values.image.digest" in job
    assert ".Chart.AppVersion" in job
    assert "MAVERICK_PG_MIGRATION_DSN" in job
    assert "MAVERICK_MIGRATION_AUTHORIZATION" in job
    assert "MAVERICK_PG_DSN\n" not in job
    assert "secretName: {{ .Values.migration.secretName }}" in job
    assert "subPath: custody" in job
    assert "medium: Memory" in job


def test_workload_drops_the_unused_cluster_credential_and_has_writable_tmp() -> None:
    deployment = (CHART / "templates" / "deployment.yaml").read_text(
        encoding="utf-8",
    )
    assert "automountServiceAccountToken: false" in deployment
    assert "hostNetwork: false" in deployment
    assert "hostPID: false" in deployment
    assert "hostIPC: false" in deployment
    assert "shareProcessNamespace: false" in deployment
    assert "enableServiceLinks: false" in deployment
    assert "workingDir: /workspace" in deployment
    assert "mountPath: /tmp" in deployment
    assert "sizeLimit: {{ .Values.tmp.sizeLimit | quote }}" in deployment
    assert "name: MAVERICK_TLS_TERMINATED" in deployment
    assert "name: MAVERICK_EXTERNAL_NETWORK_POLICY" in deployment
    assert "name: MAVERICK_AUDIT_SIGN" in deployment
    assert "name: MAVERICK_AUDIT_REQUIRE_OFFHOST_KEY" in deployment
    assert "name: MAVERICK_REQUIRE_REDACTION" in deployment
    assert "name: MAVERICK_ENCRYPT_PER_TENANT" in deployment
    assert "name: MAVERICK_ENCRYPT_STRICT" in deployment
    assert "name: MAVERICK_KMS_DEK_CACHE_TTL" in deployment
    assert "name: MAVERICK_SECRETS_REQUIRED" in deployment
    assert "mountPath: /run/secrets/lightwork" in deployment
    assert "name: MAVERICK_CONFIG" in deployment
    assert 'value: "/etc/lightwork/runtime-config/config.toml"' in deployment
    assert "name: MAVERICK_OIDC_ENABLED" in deployment
    assert "name: MAVERICK_PROXY_AUTH" in deployment
    assert "name: identity-config" in deployment
    assert "mountPath: /etc/lightwork/runtime-config" in deployment
    assert 'include "maverick.identityConfigName" .' in deployment
    assert 'include "maverick.identityConfigSha256" .' in deployment
    assert "defaultMode: 0444" in deployment
    assert "name: stage-runtime-secrets" in deployment
    assert "lightwork.daybreak.ai/secret-rotation-generation" in deployment
    assert ".Values.secret.rotationGeneration" in deployment
    assert 'command: ["python", "-m", "maverick.secret_staging"]' in deployment
    assert "mountPath: /run/secrets/lightwork-projected" in deployment
    assert "subPath: custody" in deployment
    assert "defaultMode: 0440" in deployment
    assert "medium: Memory" in deployment
    assert ".Values.secret.stagingSizeLimit" in deployment
    assert "name: MAVERICK_DURABLE" in deployment
    assert "name: MAVERICK_REQUIRE_DURABLE_CHECKPOINTS" in deployment
    assert "name: MAVERICK_CHILD_ENV_ALLOWLIST_ONLY" in deployment
    assert "name: MAVERICK_INDETERMINATE_RECONCILE_SECONDS" in deployment
    assert "name: MAVERICK_INDETERMINATE_ESCALATE_SECONDS" in deployment
    assert "name: MAVERICK_INDETERMINATE_ESCALATION_TARGET" in deployment
    assert "name: MAVERICK_INDETERMINATE_ADJUDICATORS" in deployment
    assert "name: MAVERICK_MODEL_ARTIFACT_SHA256" in deployment
    assert "name: VLLM_BASE_URL" in deployment
    assert "name: MAVERICK_MODEL_REGION" in deployment
    assert "name: MAVERICK_MODEL_RETENTION_MODE" in deployment
    assert "name: MAVERICK_MODEL_CREDENTIAL_SOURCE" in deployment
    assert "name: MAVERICK_PROVIDER_CAPS_JSON" in deployment
    assert "name: MAVERICK_PROVIDER_CAPS_PERIOD" in deployment
    assert "name: MAVERICK_REQUIRE_PRODUCTION_RUNTIME_CONTRACT" in deployment
    assert "name: MAVERICK_PRODUCTION_RUNTIME_CONTRACT" in deployment
    assert "name: MAVERICK_RUNTIME_PIDS" in deployment
    assert "name: MAVERICK_RUNTIME_NOFILE" in deployment
    assert "name: MAVERICK_RUNTIME_FILE_SIZE_BYTES" in deployment
    assert "name: MAVERICK_MAX_TOOL_RESULT_BYTES" in deployment
    assert "name: MAVERICK_DEFAULT_MAX_WALL_SECONDS" in deployment
    assert "name: MAVERICK_PRODUCTION_MAX_TOOL_CALLS" in deployment
    assert "name: MAVERICK_DEFAULT_MAX_DEPTH" in deployment
    assert "name: MAVERICK_MAX_TOTAL_SPAWNS" in deployment
    assert "name: MAVERICK_MAX_CONCURRENT_GOALS" in deployment
    assert "name: MAVERICK_MAX_CONCURRENT_GOALS_PER_PRINCIPAL" in deployment
    assert "name: MAVERICK_PRODUCTION_DEPLOYMENT_ID" in deployment
    assert "name: MAVERICK_PRODUCTION_DEPLOYMENT_CONFIG_SHA256" in deployment
    assert "name: MAVERICK_SOURCE_REVISION" in deployment
    assert "name: MAVERICK_PRODUCTION_ONBOARDING_CONTRACT_SHA256" in deployment
    assert "name: MAVERICK_PRODUCTION_RELEASE_STATE_SHA256" in deployment
    assert "name: MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_KEY_ID" in deployment
    assert "name: MAVERICK_PRODUCTION_ONBOARDING_AUTHORITY_PUBLIC_KEY" in deployment
    assert "name: MAVERICK_PRODUCTION_IDENTITY_PROVIDER" in deployment
    assert "name: MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256" in deployment
    assert "name: MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS" in deployment
    assert 'include "maverick.productionDeploymentConfigSha256" .' in deployment
    assert ".Values.productionOnboarding.deploymentConfigSha256" not in deployment

    projection = deployment.split("items:", 1)[1].split("# fsGroup", 1)[0]
    projected_keys = set(re.findall(r"- key: ([A-Z0-9_]+)", projection))
    assert projected_keys == {
        "MAVERICK_AUDIT_SIGNING_KEY_WRAPPED",
        "MAVERICK_KMS_KEY_ID",
        "MAVERICK_OIDC_CLIENT_SECRET",
        "MAVERICK_OIDC_SESSION_SECRET",
        "MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS",
        "MAVERICK_PG_DSN",
        "VAULT_TOKEN",
        "VLLM_API_KEY",
    }
    assert projected_keys.isdisjoint(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "DOCKER_AUTH_CONFIG",
            "GOOGLE_APPLICATION_CREDENTIALS",
            "KUBECONFIG",
            "MAVERICK_AUDIT_SIGNING_KEY",
            "MAVERICK_DASHBOARD_TOKEN",
            "OPENAI_API_KEY",
            "SSH_AUTH_SOCK",
        }
    )


def test_network_policy_pins_ingress_and_dns_in_production() -> None:
    policy = (CHART / "templates" / "networkpolicy.yaml").read_text(
        encoding="utf-8",
    )
    ingress = (CHART / "templates" / "ingress.yaml").read_text(
        encoding="utf-8",
    )

    assert "allowedIngressNamespaces" in policy
    assert "kubernetes.io/metadata.name" in policy
    assert "dnsCIDRs" in policy
    assert "kind: Ingress" in ingress
    assert "ingressClassName" in ingress
    assert "tls:" in ingress


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_helm_renders_the_complete_production_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(OIDC_IDENTITY_ARGS),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    config_map, config_toml, config = _identity_config(proc.stdout)
    deployment = _manifest_document(proc.stdout, kind="Deployment")
    _clear_identity_precedence(monkeypatch)
    posture = runtime_identity_posture(config=config)

    assert posture.provider == "oidc"
    identity_mapping = posture.document["identity_mapping"]
    assert identity_mapping["algorithms"] == ["ES256", "RS256"]
    assert identity_mapping["audience"] == "lightwork"
    assert identity_mapping["issuer"] == "https://idp.example.test/"
    assert identity_mapping["jwks_uri"] == "https://idp.example.test/jwks.json"
    assert identity_mapping["client_id"] == "lightwork-dashboard"
    assert identity_mapping["redirect_uri"] == (
        "https://lightwork.example.test/auth/callback"
    )
    assert posture.sha256 != VALID_IDENTITY_POSTURE_SHA
    monkeypatch.setenv("MAVERICK_PRODUCTION_IDENTITY_PROVIDER", "oidc")
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
        VALID_IDENTITY_POSTURE_SHA,
    )
    with pytest.raises(ProductionIdentityError, match="posture differs"):
        validate_runtime_identity_posture(config=config)
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
        posture.sha256,
    )
    assert validate_runtime_identity_posture(config=config) == posture
    assert config["dashboard"] == {
        "admins": ["user:break-glass"],
        "default_role": "viewer",
        "default_suites": ["finance"],
        "group_roles": {"PlatformAdmins": "admin"},
        "group_suites": {"Finance": ["finance"]},
    }
    assert config["roles"] == {
        "restricted": {
            "allow_hosts": [],
            "allow_paths": ["/workspace/**"],
            "allow_tools": ["knowledge_search"],
            "deny_tools": ["exec"],
            "max_risk": "low",
        }
    }
    assert config["role_assignments"] == {"default": "restricted"}
    assert "[auth.oidc]" in config_toml
    assert "[auth.proxy]" not in config_toml
    assert 'client_id = "lightwork-dashboard"' in config_toml
    assert (
        'redirect_uri = "https://lightwork.example.test/auth/callback"'
        in config_toml
    )
    assert "client_secret" not in config_map.lower()
    assert "session_secret" not in config_map.lower()
    assert "MAVERICK_OIDC_CLIENT_SECRET" not in config_map
    assert "MAVERICK_OIDC_SESSION_SECRET" not in config_map

    identity_sha = _annotation(
        config_map,
        "lightwork.daybreak.ai/identity-config-sha256",
    )
    assert re.fullmatch(r"[0-9a-f]{64}", identity_sha)
    assert (
        _annotation(deployment, "lightwork.daybreak.ai/identity-config-sha256")
        == identity_sha
    )
    config_map_name = _metadata_name(config_map)
    assert config_map_name.endswith(identity_sha[:12])
    assert f"name: {config_map_name}" in deployment
    assert _env_value(deployment, "MAVERICK_CONFIG") == (
        "/etc/lightwork/runtime-config/config.toml"
    )
    assert _env_value(deployment, "MAVERICK_PRODUCTION_IDENTITY_PROVIDER") == "oidc"
    assert _env_value(deployment, "MAVERICK_OIDC_ENABLED") == "1"
    assert _env_value(deployment, "MAVERICK_PROXY_AUTH") == "0"
    assert "mountPath: /etc/lightwork/runtime-config" in deployment
    assert deployment.count("MAVERICK_OIDC_CLIENT_SECRET") == 2
    assert deployment.count("MAVERICK_OIDC_SESSION_SECRET") == 2
    assert f"image: ghcr.io/daybreak-ai-labs/lightwork@{VALID_DIGEST}" in proc.stdout
    assert "automountServiceAccountToken: false" in proc.stdout
    assert "hostNetwork: false" in proc.stdout
    assert "hostPID: false" in proc.stdout
    assert "hostIPC: false" in proc.stdout
    assert "shareProcessNamespace: false" in proc.stdout
    assert "enableServiceLinks: false" in proc.stdout
    assert 'runtimeClassName: "gvisor"' in proc.stdout
    assert "kind: NetworkPolicy" in proc.stdout
    assert "kind: Ingress" in proc.stdout
    assert "kind: Job" in proc.stdout
    assert "ttlSecondsAfterFinished: 3600" in proc.stdout
    assert "name: maverick-migration" in proc.stdout
    assert "MAVERICK_PG_MIGRATION_DSN" in proc.stdout
    assert "MAVERICK_MIGRATION_AUTHORIZATION" in proc.stdout
    assert f'lightwork.daybreak.ai/migration-authorization-sha256: "{VALID_AUTHORIZATION_SHA}"' in proc.stdout
    assert 'host: "lightwork.example.test"' in proc.stdout
    assert "name: MAVERICK_REQUIRE_PRODUCTION_SURFACE" in proc.stdout
    assert "name: MAVERICK_REQUIRE_PRODUCTION_RUNTIME_CONTRACT" in proc.stdout
    assert "name: MAVERICK_TLS_TERMINATED" in proc.stdout
    assert "name: MAVERICK_KMS_PROVIDER" in proc.stdout
    assert "name: MAVERICK_SECRETS_REQUIRED" in proc.stdout
    assert "mountPath: /run/secrets/lightwork" in proc.stdout
    assert 'lightwork.daybreak.ai/secret-rotation-generation: "g000001"' in proc.stdout
    assert 'lightwork.daybreak.ai/tenant-quota-policy-generation: "1"' in proc.stdout
    assert "name: MAVERICK_TENANT_QUOTA_POLICY_GENERATION" in proc.stdout
    assert "name: MAVERICK_TENANT_QUOTA_MAX_STORAGE_BYTES" in proc.stdout
    assert 'value: "vllm:approved-model"' in proc.stdout
    assert f'value: "{VALID_DIGEST}"' in proc.stdout
    assert 'value: "https://vllm.example.test/v1"' in proc.stdout
    assert 'value: "us-east-1"' in proc.stdout
    assert 'value: "self-hosted-no-provider-retention"' in proc.stdout
    assert 'value: "mounted_file"' in proc.stdout
    assert 'value: "{\\"vllm\\":100}"' in proc.stdout
    assert 'value: "month"' in proc.stdout
    assert "kind: ServiceMonitor" in proc.stdout
    assert "key: MAVERICK_METRICS_TOKEN" in proc.stdout
    assert "name: MAVERICK_METRICS_TOKEN" in proc.stdout
    assert "name: MAVERICK_METRICS_TOKEN_PREVIOUS" in proc.stdout
    assert "key: MAVERICK_AUDIT_SIGNING_KEY_WRAPPED" in proc.stdout
    assert "key: MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS" in proc.stdout
    assert "key: MAVERICK_AUDIT_SIGNING_KEY\n" not in proc.stdout
    assert f'value: "{VALID_DEPLOYMENT_ID}"' in proc.stdout
    assert f'value: "{VALID_SOURCE_REVISION}"' in proc.stdout
    assert f'value: "{VALID_ONBOARDING_CONTRACT_SHA}"' in proc.stdout
    assert f'value: "{VALID_RELEASE_STATE_SHA}"' in proc.stdout
    assert f'value: "{VALID_AUTHORITY_KEY_ID}"' in proc.stdout
    assert f'value: "{VALID_AUTHORITY_PUBLIC_KEY}"' in proc.stdout
    assert 'name: MAVERICK_PRODUCTION_IDENTITY_PROVIDER' in proc.stdout
    assert 'value: "oidc"' in proc.stdout
    assert 'name: MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256' in proc.stdout
    assert f'value: "{VALID_IDENTITY_POSTURE_SHA}"' in proc.stdout
    assert (
        'value: "/run/secrets/lightwork/'
        'MAVERICK_PRODUCTION_ONBOARDING_OBSERVATIONS"'
    ) in proc.stdout
    config_pins = re.findall(
        r'production-deployment-config-sha256: "([0-9a-f]{64})"',
        proc.stdout,
    )
    assert len(config_pins) == 1
    assert config_pins[0] != "0" * 64
    assert f'value: "{config_pins[0]}"' in proc.stdout


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_helm_renders_trusted_proxy_config_without_oidc_secret_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(TRUSTED_PROXY_IDENTITY_ARGS),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr

    config_map, config_toml, config = _identity_config(proc.stdout)
    deployment = _manifest_document(proc.stdout, kind="Deployment")
    _clear_identity_precedence(monkeypatch)
    posture = runtime_identity_posture(config=config)

    assert posture.provider == "trusted_proxy_sso"
    identity_mapping = posture.document["identity_mapping"]
    assert identity_mapping["header"] == "X-Authenticated-User"
    assert identity_mapping["trusted_proxies"] == ["10.44.0.20", "10.44.1.0/24"]
    assert identity_mapping["trust_loopback"] is False
    assert posture.sha256 != VALID_IDENTITY_POSTURE_SHA
    monkeypatch.setenv("MAVERICK_PRODUCTION_IDENTITY_PROVIDER", "trusted_proxy_sso")
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
        VALID_IDENTITY_POSTURE_SHA,
    )
    with pytest.raises(ProductionIdentityError, match="posture differs"):
        validate_runtime_identity_posture(config=config)
    monkeypatch.setenv(
        "MAVERICK_PRODUCTION_IDENTITY_POSTURE_SHA256",
        posture.sha256,
    )
    assert validate_runtime_identity_posture(config=config) == posture
    assert config["role_assignments"] == {"default": "restricted"}
    assert "[auth.proxy]" in config_toml
    assert "[auth.oidc]" not in config_toml
    assert 'header = "X-Authenticated-User"' in config_toml
    assert 'trusted_proxies = ["10.44.0.20","10.44.1.0/24"]' in config_toml
    assert "trust_loopback = false" in config_toml
    assert "client_secret" not in config_map.lower()
    assert "session_secret" not in config_map.lower()
    assert "MAVERICK_OIDC_CLIENT_SECRET" not in proc.stdout
    assert "MAVERICK_OIDC_SESSION_SECRET" not in proc.stdout

    identity_sha = _annotation(
        config_map,
        "lightwork.daybreak.ai/identity-config-sha256",
    )
    assert (
        _annotation(deployment, "lightwork.daybreak.ai/identity-config-sha256")
        == identity_sha
    )
    config_map_name = _metadata_name(config_map)
    assert config_map_name.endswith(identity_sha[:12])
    assert f"name: {config_map_name}" in deployment
    assert _env_value(deployment, "MAVERICK_CONFIG") == (
        "/etc/lightwork/runtime-config/config.toml"
    )
    assert _env_value(deployment, "MAVERICK_PRODUCTION_IDENTITY_PROVIDER") == (
        "trusted_proxy_sso"
    )
    assert _env_value(deployment, "MAVERICK_OIDC_ENABLED") == "0"
    assert _env_value(deployment, "MAVERICK_PROXY_AUTH") == "1"
    assert "mountPath: /etc/lightwork/runtime-config" in deployment


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_identity_change_rotates_configmap_and_deployment_config_digests() -> None:
    baseline = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(OIDC_IDENTITY_ARGS),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    changed = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(OIDC_IDENTITY_ARGS),
            "--set-string",
            "identity.config.oidc.audience=lightwork-v2",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert baseline.returncode == 0, baseline.stderr
    assert changed.returncode == 0, changed.stderr

    baseline_config, _, _ = _identity_config(baseline.stdout)
    changed_config, _, _ = _identity_config(changed.stdout)
    baseline_deployment = _manifest_document(baseline.stdout, kind="Deployment")
    changed_deployment = _manifest_document(changed.stdout, kind="Deployment")
    assert _metadata_name(baseline_config) != _metadata_name(changed_config)
    assert _annotation(
        baseline_deployment,
        "lightwork.daybreak.ai/identity-config-sha256",
    ) != _annotation(
        changed_deployment,
        "lightwork.daybreak.ai/identity-config-sha256",
    )
    assert _annotation(
        baseline_deployment,
        "lightwork.daybreak.ai/production-deployment-config-sha256",
    ) != _annotation(
        changed_deployment,
        "lightwork.daybreak.ai/production-deployment-config-sha256",
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
@pytest.mark.parametrize(
    ("flag", "override", "expected"),
    [
        (
            "--set-string",
            "identity.config.trustedProxy.header=X-Authenticated-User",
            "inactive trustedProxy",
        ),
        (
            "--set-string",
            "identity.config.oidc.clientSecret=must-not-render",
            "credential field",
        ),
        (
            "--set-string",
            "identity.config.oidc.audience=${MAVERICK_OIDC_CLIENT_SECRET}",
            "forbids environment interpolation",
        ),
        (
            "--set-string",
            "identity.config.dashboard.defaultRole=operator",
            "defaultRole=viewer",
        ),
        (
            "--set-string",
            "identity.config.dashboard.admins[0]=group:admins",
            "dashboard admins",
        ),
    ],
)
def test_helm_refuses_secret_or_ambiguous_identity_config(
    flag: str,
    override: str,
    expected: str,
) -> None:
    proc = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(OIDC_IDENTITY_ARGS),
            flag,
            override,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert expected in proc.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("serviceMonitor.enabled=false", "serviceMonitor.enabled"),
        ("migration.enabled=false", "migration.enabled"),
        ("migration.secretName=maverick-secrets", "to differ"),
        ("migration.authorizationSha256=bad", "migration.authorizationSha256"),
        ("migration.ttlSecondsAfterFinished=0", "migration.ttlSecondsAfterFinished"),
        ("migration.allowedEgressCIDRs[0]=0.0.0.0/0", "migration egress"),
        ("migration.databasePort=0", "migration.databasePort"),
        ("productionOnboarding.enabled=false", "productionOnboarding.enabled"),
        ("productionOnboarding.deploymentId=deployment", "deploymentId"),
        ("productionOnboarding.sourceRevision=0000000000000000000000000000000000000000", "sourceRevision"),
        ("productionOnboarding.contractSha256=bad", "contractSha256"),
        ("productionOnboarding.releaseStateSha256=bad", "releaseStateSha256"),
        ("productionOnboarding.authorityKeyId=authority", "authorityKeyId"),
        ("productionOnboarding.authorityPublicKey=bad", "authorityPublicKey"),
        (
            "productionOnboarding.identityPostureSha256=bad",
            "identityPostureSha256",
        ),
        ("productionOnboarding.observationsKey=OTHER", "observationsKey"),
        ("identity.mode=evaluation", "explicit operator selection"),
        ("secret.rotationGeneration=BAD_VALUE", "secret.rotationGeneration"),
        (
            "serviceMonitor.bearerTokenSecret.key=MAVERICK_DASHBOARD_TOKEN",
            "MAVERICK_METRICS_TOKEN",
        ),
        (
            "serviceMonitor.bearerTokenSecret.scrapeKey=MAVERICK_DASHBOARD_TOKEN",
            "current or previous metrics credential",
        ),
        ("runtimeContract.pids=257", "runtimeContract.pids=256"),
        ("tenantQuota.enforce=false", "tenantQuota.enforce=true"),
        ("tenantQuota.policyGeneration=0", "tenantQuota.policyGeneration"),
        ("tenantQuota.limits.storageBytes=-1", "limits.storageBytes"),
        ("resources.limits.memory=3Gi", "exact reviewed CPU/memory"),
        ("env.KUBECONFIG=/host/.kube/config", "forbids arbitrary env"),
    ],
)
def test_helm_refuses_production_contract_drift(
    override: str,
    expected: str,
) -> None:
    override_flag = (
        "--set-string"
        if override.startswith("productionOnboarding.")
        and override != "productionOnboarding.enabled=false"
        else "--set"
    )
    proc = subprocess.run(
        [
            shutil.which("helm") or "helm",
            *_production_template_args(OIDC_IDENTITY_ARGS),
            override_flag,
            override,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert expected in proc.stderr


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")
def test_helm_refuses_a_production_release_without_an_image_digest() -> None:
    proc = subprocess.run(
        [
            shutil.which("helm") or "helm",
            "template",
            "maverick",
            str(CHART),
            "-f",
            str(PRODUCTION_VALUES),
            "--set",
            "secret.name=maverick-secrets",
            "--set",
            "secret.rotationGeneration=g000001",
            "--set-string",
            f"productionOnboarding.deploymentId={VALID_DEPLOYMENT_ID}",
            "--set-string",
            f"productionOnboarding.sourceRevision={VALID_SOURCE_REVISION}",
            "--set-string",
            f"productionOnboarding.contractSha256={VALID_ONBOARDING_CONTRACT_SHA}",
            "--set-string",
            f"productionOnboarding.releaseStateSha256={VALID_RELEASE_STATE_SHA}",
            "--set-string",
            f"productionOnboarding.authorityKeyId={VALID_AUTHORITY_KEY_ID}",
            "--set-string",
            f"productionOnboarding.authorityPublicKey={VALID_AUTHORITY_PUBLIC_KEY}",
            "--set-string",
            f"productionOnboarding.identityPostureSha256={VALID_IDENTITY_POSTURE_SHA}",
            "--set",
            "identity.mode=oidc",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "image.digest" in proc.stderr
