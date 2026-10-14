"""The explicit production artifact gate rejects drift before startup."""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest
from maverick import deployment, production_scope


def _eligible_manifest(tmp_path):
    source = production_scope.load_manifest()
    document = json.loads(source.path.read_text(encoding="utf-8"))
    document["release_state"] = {
        "eligible": True,
        "reason": "test evidence packet admitted",
        "latest_repository_tag": "v0.1.7",
        "github_release": "v0.1.7",
    }
    target = tmp_path / "production.json"
    target.write_text(json.dumps(document), encoding="utf-8")
    return target


def _production_environment(monkeypatch, manifest) -> None:
    from maverick import production_model_policy
    from maverick.file_lock import atomic_write_text, prepare_private_directory

    secret_dir = manifest.parent / "secrets"
    prepare_private_directory(secret_dir)
    atomic_write_text(
        secret_dir / "MAVERICK_PG_DSN",
        "host=db.example.test dbname=lightwork sslmode=verify-full "
        "sslrootcert=system",
    )
    atomic_write_text(
        secret_dir / "MAVERICK_KMS_KEY_ID", "lightwork-production"
    )
    atomic_write_text(
        secret_dir / "VAULT_TOKEN", "test-vault-token"
    )
    atomic_write_text(
        secret_dir / "MAVERICK_OIDC_CLIENT_SECRET", "test-oidc-secret"
    )
    atomic_write_text(
        secret_dir / "MAVERICK_OIDC_SESSION_SECRET", "test-oidc-session-secret"
    )
    atomic_write_text(secret_dir / "VLLM_API_KEY", "test-vllm-key")
    model_policy = manifest.parent / "production-model-policy.json"
    now = dt.datetime.now(dt.timezone.utc)
    model_document = {
        "schema": production_model_policy.SCHEMA,
        "policy_id": "test-production-model-policy",
        "profile_id": "enterprise-single-control-plane-v1",
        "source_version": "0.1.7",
        "production_surface_sha256": (
            production_model_policy.production_surface_sha256(
                manifest.read_bytes()
            )
        ),
        "production_surface_digest_mode": (
            production_model_policy.PRODUCTION_SURFACE_DIGEST_MODE
        ),
        "status": "approved",
        "approved_at": (now - dt.timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "expires_at": (now + dt.timedelta(days=30)).isoformat().replace(
            "+00:00", "Z"
        ),
        "approvers": [
            {"principal": "user:model-risk", "role": "model-risk-owner"},
            {"principal": "user:security", "role": "security-owner"},
        ],
        "models": [
            {
                "spec": "vllm:approved-model",
                "artifact_digest": "sha256:" + "a" * 64,
                "base_url": "https://vllm.example.test/v1",
                "region": "us-east-1",
                "retention_mode": "self-hosted-no-provider-retention",
                "credential_source": "mounted_file",
                "max_spend_usd": 100.0,
                "spend_period": "month",
                "failover": [],
            }
        ],
        "limitations": ["test deployment evidence"],
    }
    model_policy.write_text(json.dumps(model_document), encoding="utf-8")
    values = {
        production_scope.REQUIRE_PRODUCTION_SURFACE_ENV: "1",
        production_scope.MANIFEST_ENV: str(manifest),
        production_scope.EXPECTED_DIGEST_ENV: hashlib.sha256(
            manifest.read_bytes()
        ).hexdigest(),
        "MAVERICK_PRODUCTION_PROFILE_ID": "enterprise-single-control-plane-v1",
        "MAVERICK_CONTROL_PLANE_REPLICAS": "1",
        "MAVERICK_PRODUCTION_CONTAINER": "1",
        "MAVERICK_TLS_TERMINATED": "1",
        "MAVERICK_EXTERNAL_NETWORK_POLICY": "1",
        "MAVERICK_AUDIT_SIGN": "1",
        "MAVERICK_AUDIT_REQUIRE_OFFHOST_KEY": "1",
        "MAVERICK_REQUIRE_REDACTION": "1",
        "MAVERICK_ENCRYPT_AT_REST": "1",
        "MAVERICK_ENCRYPT_PER_TENANT": "1",
        "MAVERICK_ENCRYPT_STRICT": "1",
        "MAVERICK_SECRETS_REQUIRED": "1",
        "MAVERICK_SECRETS_BACKEND": "file",
        "MAVERICK_SECRETS_DIR": str(secret_dir),
        "MAVERICK_KMS_PROVIDER": "vault",
        "MAVERICK_KMS_DEK_CACHE_TTL": "60",
        "VAULT_ADDR": "https://vault.example.test",
        "MAVERICK_WORLD_BACKEND": "postgres",
        "MAVERICK_PG_RLS": "1",
        "MAVERICK_PG_POOL_SIZE": "10",
        "MAVERICK_PG_CONNECT_TIMEOUT_SECONDS": "10",
        "MAVERICK_PG_STATEMENT_TIMEOUT_MS": "30000",
        "MAVERICK_PG_LOCK_TIMEOUT_MS": "5000",
        "MAVERICK_TENANT_BY_USER": "1",
        "MAVERICK_SANDBOX_BACKEND": "disabled",
        "MAVERICK_MODEL_OVERRIDE": "vllm:approved-model",
        production_model_policy.POLICY_ENV: str(model_policy),
        production_model_policy.POLICY_SHA256_ENV: hashlib.sha256(
            model_policy.read_bytes()
        ).hexdigest(),
        production_model_policy.ARTIFACT_DIGEST_ENV: "sha256:" + "a" * 64,
        production_model_policy.REGION_ENV: "us-east-1",
        production_model_policy.RETENTION_ENV: (
            "self-hosted-no-provider-retention"
        ),
        production_model_policy.CREDENTIAL_SOURCE_ENV: "mounted_file",
        "VLLM_BASE_URL": "https://vllm.example.test/v1",
        "MAVERICK_SELF_MODIFY": "0",
        "MAVERICK_DURABLE": "1",
        "MAVERICK_REQUIRE_DURABLE_CHECKPOINTS": "1",
        "MAVERICK_CHILD_ENV_ALLOWLIST_ONLY": "1",
        "MAVERICK_INDETERMINATE_RECONCILE_SECONDS": "900",
        "MAVERICK_INDETERMINATE_ESCALATE_SECONDS": "3600",
        "MAVERICK_INDETERMINATE_ESCALATION_TARGET": "production-incident-response",
        "MAVERICK_INDETERMINATE_ADJUDICATORS": "user:incident-commander,user:risk-owner",
        "MAVERICK_OIDC_ENABLED": "1",
        "MAVERICK_OIDC_ISSUER": "https://idp.example.test/",
        "MAVERICK_OIDC_AUDIENCE": "lightwork",
        "MAVERICK_OIDC_JWKS_URI": "https://idp.example.test/jwks.json",
        "MAVERICK_OIDC_CLIENT_ID": "lightwork-dashboard",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    config = {
        "budget": {
            "provider_caps_period": "month",
            "provider_caps": {"vllm": 100.0},
        },
        "dashboard": {
            "default_role": "viewer",
            "admins": ["user:break-glass"],
        },
    }
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: config)
    from maverick.production_identity import (
        POSTURE_DIGEST_ENV,
        PROVIDER_ENV,
        runtime_identity_posture,
    )

    posture = runtime_identity_posture(config=config)
    monkeypatch.setenv(PROVIDER_ENV, posture.provider)
    monkeypatch.setenv(POSTURE_DIGEST_ENV, posture.sha256)
    monkeypatch.setattr(deployment.platform, "system", lambda: "Linux")
    monkeypatch.setattr(deployment.platform, "machine", lambda: "x86_64")


def test_source_manifest_blocks_a_production_artifact_until_release(monkeypatch) -> None:
    monkeypatch.setenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, "1")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "release is not eligible" in check.detail


def test_exact_admitted_profile_passes_the_surface_gate(monkeypatch, tmp_path) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)

    check = deployment._verify_production_surface()

    assert check.passed is True, check.detail
    assert "enterprise-single-control-plane-v1" in check.detail


def test_unsupported_model_provider_is_rejected(monkeypatch, tmp_path) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_MODEL_OVERRIDE", "anthropic:some-model")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "production model" in check.detail


def test_static_operator_identity_is_rejected(monkeypatch, tmp_path) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "not-a-production-principal")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "shared dashboard operator token" in check.detail


def test_missing_principal_tenant_binding_is_rejected(monkeypatch, tmp_path) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "0")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "principal-isolated tenant" in check.detail


def test_missing_external_transport_boundaries_are_rejected(
    monkeypatch, tmp_path,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_TLS_TERMINATED", "0")
    monkeypatch.setenv("MAVERICK_EXTERNAL_NETWORK_POLICY", "0")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "authenticated TLS ingress" in check.detail
    assert "external ingress/egress network-policy" in check.detail


def test_missing_audit_custody_or_required_redaction_is_rejected(
    monkeypatch, tmp_path,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_AUDIT_SIGN", "0")
    monkeypatch.setenv("MAVERICK_AUDIT_REQUIRE_OFFHOST_KEY", "0")
    monkeypatch.setenv("MAVERICK_REQUIRE_REDACTION", "0")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "signed audit" in check.detail
    assert "off-host audit signing-key custody" in check.detail
    assert "fail-closed outbound redaction" in check.detail


def test_missing_strict_boundary_and_durable_checkpoint_flags_are_rejected(
    monkeypatch, tmp_path,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "0")
    monkeypatch.setenv("MAVERICK_ENCRYPT_STRICT", "0")
    monkeypatch.setenv("MAVERICK_DURABLE", "0")
    monkeypatch.setenv("MAVERICK_REQUIRE_DURABLE_CHECKPOINTS", "0")
    monkeypatch.setenv("MAVERICK_CHILD_ENV_ALLOWLIST_ONLY", "0")

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "per-tenant envelope encryption" in check.detail
    assert "strict encrypted-field reads" in check.detail
    assert "durable state mode" in check.detail
    assert "strict durable checkpoints" in check.detail
    assert "fixed child-process environment allowlisting" in check.detail


def test_local_kms_or_unverified_postgres_is_rejected(monkeypatch, tmp_path) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("MAVERICK_KMS_PROVIDER", "local")
    secret_dir = tmp_path / "secrets"
    (secret_dir / "MAVERICK_PG_DSN").write_text(
        "host=db.example.test dbname=lightwork sslmode=require",
        encoding="utf-8",
    )

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "KMS provider" in check.detail
    assert "verified TLS" in check.detail


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        (
            {"MAVERICK_INDETERMINATE_RECONCILE_SECONDS": "59"},
            "reconciliation deadlines",
        ),
        (
            {"MAVERICK_INDETERMINATE_ESCALATE_SECONDS": "604801"},
            "reconciliation deadlines",
        ),
        (
            {
                "MAVERICK_INDETERMINATE_RECONCILE_SECONDS": "900",
                "MAVERICK_INDETERMINATE_ESCALATE_SECONDS": "900",
            },
            "reconciliation deadlines",
        ),
        (
            {"MAVERICK_INDETERMINATE_ESCALATION_TARGET": "local incident owner"},
            "non-default indeterminate escalation target",
        ),
        (
            {"MAVERICK_INDETERMINATE_ADJUDICATORS": " , "},
            "indeterminate adjudicator allowlist",
        ),
    ],
)
def test_invalid_indeterminate_reconciliation_policy_is_rejected(
    monkeypatch, tmp_path, updates, expected,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    for name, value in updates.items():
        monkeypatch.setenv(name, value)

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert expected in check.detail


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (
            "MAVERICK_INDETERMINATE_RECONCILE_SECONDS",
            "reconciliation deadline is not explicit",
        ),
        (
            "MAVERICK_INDETERMINATE_ESCALATE_SECONDS",
            "escalation deadline is not explicit",
        ),
        (
            "MAVERICK_INDETERMINATE_ESCALATION_TARGET",
            "non-default indeterminate escalation target",
        ),
        (
            "MAVERICK_INDETERMINATE_ADJUDICATORS",
            "indeterminate adjudicator allowlist",
        ),
    ],
)
def test_missing_indeterminate_reconciliation_policy_is_rejected(
    monkeypatch, tmp_path, name, expected,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.delenv(name)

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert expected in check.detail


@pytest.mark.parametrize(
    "address",
    [
        "https://user:password@vault.example.test",
        "https://vault.example.test?token=leak",
        "https://vault.example.test/#fragment",
        "https://vault.example.test:not-a-port",
        "https://vault example.test",
    ],
)
def test_vault_endpoint_rejects_uri_credentials_and_suffixes(
    monkeypatch, tmp_path, address,
) -> None:
    manifest = _eligible_manifest(tmp_path)
    _production_environment(monkeypatch, manifest)
    monkeypatch.setenv("VAULT_ADDR", address)

    check = deployment._verify_production_surface()

    assert check.passed is False
    assert "authenticated https endpoint" in check.detail
