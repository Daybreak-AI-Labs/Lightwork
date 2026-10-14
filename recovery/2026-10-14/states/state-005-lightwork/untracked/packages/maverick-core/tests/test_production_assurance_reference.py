"""Static contracts for the single-control-plane assurance reference.

The reference is intentionally fail-closed until an operator supplies an image
digest, source commit, deployment identity, external Secret, and non-secret
config content. These tests pin that boundary without requiring Helm/PyYAML.
"""
from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_REFERENCE = (
    _REPO_ROOT / "deploy" / "reference-architectures" / "production-assurance"
)
_CHART = _REPO_ROOT / "deploy" / "helm" / "maverick"


def _read(name: str) -> str:
    return (_REFERENCE / name).read_text(encoding="utf-8")


def test_reference_files_and_index_are_present():
    assert {path.name for path in _REFERENCE.iterdir()} == {
        "README.md",
        "RUNBOOK.md",
        "config.toml",
        "values.yaml",
    }
    index = (
        _REPO_ROOT / "deploy" / "reference-architectures" / "README.md"
    ).read_text(encoding="utf-8")
    assert "[Production assurance](./production-assurance/)" in index
    assert "explicitly not HA" in index


def test_values_preserve_single_writer_and_external_secret_boundaries():
    values = _read("values.yaml")
    assert re.search(r"(?m)^replicaCount: 1$", values)
    assert re.search(r"(?ms)^productionAssurance:\n  enabled: true$", values)
    assert re.search(r"(?ms)^autoscaling:\n  enabled: false$", values)
    assert re.search(r"(?ms)^podDisruptionBudget:\n  enabled: false$", values)
    assert re.search(r"(?ms)^enterprise:\n  profile: standard\n  required: false$", values)
    assert re.search(
        r"(?ms)^secret:\n  create: false\n  name: maverick-production-secrets",
        values,
    )
    assert re.search(r'(?m)^  digest: ""  # REQUIRED:', values)
    assert re.search(r"(?ms)^config:\n  create: true\n", values)
    assert re.search(
        r"(?ms)^worldModel:\n  backend: postgres\n  postgres:\n    rls: true$",
        values,
    )
    assert "MAVERICK_IMAGE_DIGEST:" not in values  # chart derives it from image.digest
    assert "MAVERICK_PG_DSN:" not in values
    assert "MAVERICK_AUDIT_SIGNING_KEY:" not in values


def test_non_secret_config_enables_assurance_and_strict_authority():
    config = tomllib.loads(_read("config.toml"))
    assert config["world_model"] == {"backend": "postgres", "rls": True}
    assert config["governed_records"] == {"backend": "postgres"}
    assert config["authority"]["mode"] == "strict"
    assert config["capabilities"]["enforce"] is True
    assert config["tenancy"]["by_user"] is True
    assert config["encryption"] == {"at_rest": True, "per_tenant": True}
    assert config["audit"] == {"sign": True, "require_offhost_key": True}
    for section in (
        "security_ops",
        "evidence_graph",
        "evidence_factory",
        "agent_trust_auditor",
        "cra_ops",
        "model_risk_assurance",
    ):
        assert config[section]["enable"] is True
    assert config["model_risk_assurance"]["gate_promotions"] is True
    assert config["connectors"]["certification"] == {
        "certified_only": True,
        "allow_uncertified": False,
    }
    assert config["governed_connectors"] == {
        "enable": True,
        "connectors": [],
    }
    lowered = _read("config.toml").lower()
    for secret_assignment in ("password =", "token =", "api_key =", "dsn ="):
        assert secret_assignment not in lowered


def test_chart_mounts_configmap_and_derives_runtime_image_provenance():
    deployment = (_CHART / "templates" / "deployment.yaml").read_text(
        encoding="utf-8"
    )
    configmap = (_CHART / "templates" / "configmap.yaml").read_text(
        encoding="utf-8"
    )
    helpers = (_CHART / "templates" / "_helpers.tpl").read_text(
        encoding="utf-8"
    )
    assert "kind: ConfigMap" in configmap
    assert '{{- if .Values.config.create }}' in configmap
    assert "config.toml: |-" in configmap
    assert ".Values.config.content | nindent 4" in configmap
    assert "name: config" in deployment
    assert "mountPath: {{ .Values.config.mountPath }}" in deployment
    assert "subPath: config.toml" in deployment
    assert "readOnly: true" in deployment
    assert "name: {{ include \"maverick.configName\" . }}" in deployment
    assert "- name: MAVERICK_IMAGE_DIGEST" in deployment
    assert 'value: {{ include "maverick.image" . | quote }}' in deployment
    assert 'regexMatch "^sha256:[0-9a-f]{64}$"' in helpers
    assert "productionAssurance.enabled=true requires image.digest" in helpers
    assert "productionAssurance.enabled=true requires env.MAVERICK_GIT_COMMIT" in helpers
    assert "productionAssurance.enabled=true requires a non-empty safe" in helpers
    assert "checksum/config:" in deployment
    assert "sha256sum" in deployment
    assert re.search(r"(?ms)^  strategy:\n.*?    type: Recreate$", deployment)
    assert "type: RollingUpdate" not in deployment
    assert 'printf "%s-config"' in helpers
    assert '| trunc 63 | trimSuffix "-"' in helpers


def test_deployment_instructions_require_immutable_provenance_and_no_claimed_ha():
    readme = _read("README.md")
    assert "--set-string image.digest=" in readme
    assert "--set-file config.content=" in readme
    assert "signature/attestation" in readme
    assert "digest pins bytes but does not prove" in readme
    assert "exactly one dashboard/serve replica" in readme
    assert "does not assert" in readme
    assert "highly available" in readme
    assert "enterprise-ready" in readme
    assert "Postgres is required here" in readme
    assert "does not make the topology highly available" in readme
    assert "non-superuser" in readme and "`BYPASSRLS`" in readme
    for required_key in (
        "MAVERICK_DASHBOARD_TOKEN",
        "MAVERICK_ENCRYPTION_KEY",
        "MAVERICK_ENCRYPTION_KEY_DIGEST",
        "MAVERICK_PG_DSN",
        "MAVERICK_AUDIT_SIGNING_KEY",
    ):
        assert required_key in readme


def test_runbook_covers_consistent_backup_restore_and_reviewed_evidence():
    runbook = _read("RUNBOOK.md")
    for phrase in (
        "Scale the single control-plane Deployment to zero",
        "Postgres snapshot or point-in-time recovery",
        "Postgres integrity",
        "Never test a restore over the live environment",
        "Reconcile every effect in `SENT` or `INDETERMINATE` state",
        "default collectors run readiness",
        "pending_review",
        "different authorized human",
        "INSUFFICIENT_DATA",
        "actual RTO/RPO",
    ):
        assert phrase in runbook
    assert "never infer them from a successful restore" in runbook
    assert "shared effect" in runbook
    assert "not a highly available control plane" in runbook


def test_strict_reference_selects_postgres_without_embedding_a_dsn():
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in _REFERENCE.iterdir()
        if path.is_file()
    )
    assert 'backend = "postgres"' in combined
    assert "backend: postgres" in combined
    assert "MAVERICK_PG_DSN" in _read("README.md")
    assert "MAVERICK_PG_DSN:" not in _read("values.yaml")
    assert "postgres://" not in combined


def test_postgres_sealing_guidance_is_current():
    chart_values = (_CHART / "values.yaml").read_text(encoding="utf-8")
    reference_values = _read("values.yaml")
    combined = f"{chart_values}\n{reference_values}".lower()
    assert "content sealing is not implemented" not in combined
    assert "fails closed until postgres content sealing" not in combined


def test_reference_contains_no_secret_like_literal():
    suspicious = re.compile(
        r"(?:sk-ant-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{20,}|"
        r"AKIA[A-Z0-9]{16}|postgres(?:ql)?://[^\s:@]+:[^\s@]+@)"
    )
    for path in _REFERENCE.iterdir():
        if path.is_file():
            assert not suspicious.search(path.read_text(encoding="utf-8")), path
