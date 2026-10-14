"""Production model/provider policy is exact, immutable, and fail closed."""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from maverick import production_model_policy as policy

MODEL_SPEC = "vllm:approved-model"
MODEL_DIGEST = "sha256:" + "a" * 64
MODEL_URL = "https://vllm.internal.example/v1"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _surface_digest() -> str:
    from maverick.production_scope import load_manifest

    manifest = load_manifest()
    return policy.production_surface_sha256(manifest.path.read_bytes())


def _load_verifier():
    path = REPO_ROOT / "scripts" / "verify_production_model_policy.py"
    spec = importlib.util.spec_from_file_location(
        "test_verify_production_model_policy",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _approved_document(surface_digest: str) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    return {
        "schema": policy.SCHEMA,
        "policy_id": "test-production-model-policy",
        "profile_id": "enterprise-single-control-plane-v1",
        "source_version": "0.1.7",
        "production_surface_sha256": surface_digest,
        "production_surface_digest_mode": policy.PRODUCTION_SURFACE_DIGEST_MODE,
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
                "spec": MODEL_SPEC,
                "artifact_digest": MODEL_DIGEST,
                "base_url": MODEL_URL,
                "region": "us-east-1",
                "retention_mode": "self-hosted-no-provider-retention",
                "credential_source": "mounted_file",
                "max_spend_usd": 100.0,
                "spend_period": "month",
                "failover": [],
            }
        ],
        "limitations": ["Deployment evidence remains required."],
    }


def _write_policy(path: Path, document: dict) -> str:
    path.write_text(json.dumps(document), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    document: dict | None = None,
) -> Path:
    from maverick import production_scope, provider_cost_cap

    manifest = production_scope.load_manifest()
    target = tmp_path / "model-policy.json"
    digest = _write_policy(
        target,
        document
        or _approved_document(
            policy.production_surface_sha256(manifest.path.read_bytes())
        ),
    )
    monkeypatch.setenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, "1")
    monkeypatch.setenv(production_scope.MANIFEST_ENV, str(manifest.path))
    monkeypatch.setenv(production_scope.EXPECTED_DIGEST_ENV, manifest.sha256)
    monkeypatch.setenv(policy.POLICY_ENV, str(target))
    monkeypatch.setenv(policy.POLICY_SHA256_ENV, digest)
    monkeypatch.setenv(policy.ARTIFACT_DIGEST_ENV, MODEL_DIGEST)
    monkeypatch.setenv(policy.REGION_ENV, "us-east-1")
    monkeypatch.setenv(
        policy.RETENTION_ENV,
        "self-hosted-no-provider-retention",
    )
    monkeypatch.setenv(policy.CREDENTIAL_SOURCE_ENV, "mounted_file")
    monkeypatch.setenv("MAVERICK_SECRETS_BACKEND", "file")
    monkeypatch.setattr(
        provider_cost_cap,
        "caps_from_config",
        lambda: {"vllm": 100.0},
    )
    monkeypatch.setattr(provider_cost_cap, "period_from_config", lambda: "month")
    return target


def test_repository_policy_is_a_valid_unapproved_draft() -> None:
    root = REPO_ROOT
    surface = root / "release" / "production-surface-manifest.json"
    surface_digest = policy.production_surface_sha256(surface.read_bytes())

    parsed = policy.load_policy_file(
        root / "release" / "production-model-policy.json",
        production_surface_sha256=surface_digest,
    )

    assert parsed.status == "draft"
    assert (
        parsed.production_surface_digest_mode
        == policy.PRODUCTION_SURFACE_DIGEST_MODE
    )
    assert parsed.models == {}
    with pytest.raises(policy.ProductionModelPolicyError, match="not approved"):
        policy.load_policy_file(
            root / "release" / "production-model-policy.json",
            production_surface_sha256=surface_digest,
            require_approved=True,
        )


def test_verifier_accepts_lf_and_crlf_surface_checkouts(tmp_path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    source_surface = REPO_ROOT / "release" / "production-surface-manifest.json"
    lf_surface = source_surface.read_bytes().replace(b"\r\n", b"\n")
    crlf_surface = lf_surface.replace(b"\n", b"\r\n")
    surface_path = release / "production-surface-manifest.json"
    surface_path.write_bytes(crlf_surface)
    surface_digest = policy.production_surface_sha256(crlf_surface)
    assert surface_digest == policy.production_surface_sha256(lf_surface)

    policy_document = json.loads(
        (REPO_ROOT / "release" / "production-model-policy.json").read_bytes()
    )
    policy_document["production_surface_sha256"] = surface_digest
    (release / "production-model-policy.json").write_text(
        json.dumps(policy_document),
        encoding="utf-8",
        newline="\n",
    )

    verifier = _load_verifier()
    crlf_result = verifier.verify_production_model_policy(tmp_path)
    surface_path.write_bytes(lf_surface)
    lf_result = verifier.verify_production_model_policy(tmp_path)

    assert crlf_result == lf_result
    assert (
        crlf_result["production_surface_digest_mode"]
        == policy.PRODUCTION_SURFACE_DIGEST_MODE
    )


def test_surface_digest_mode_substitution_is_refused() -> None:
    surface_digest = _surface_digest()
    document = _approved_document(surface_digest)
    document["production_surface_digest_mode"] = "exact-byte-sha256"

    with pytest.raises(policy.ProductionModelPolicyError, match="unsupported"):
        policy.validate_policy_document(
            document,
            production_surface_sha256=surface_digest,
        )


def test_runtime_policy_accepts_crlf_surface_with_exact_release_pin(
    monkeypatch,
    tmp_path,
) -> None:
    from maverick import production_scope

    source_manifest = production_scope.load_manifest()
    lf_surface = source_manifest.path.read_bytes().replace(b"\r\n", b"\n")
    crlf_manifest = tmp_path / "production-surface-manifest.json"
    crlf_manifest.write_bytes(lf_surface.replace(b"\n", b"\r\n"))
    _runtime(monkeypatch, tmp_path)
    monkeypatch.setenv(production_scope.MANIFEST_ENV, str(crlf_manifest))
    monkeypatch.setenv(
        production_scope.EXPECTED_DIGEST_ENV,
        hashlib.sha256(crlf_manifest.read_bytes()).hexdigest(),
    )

    parsed = policy.load_runtime_policy()

    assert parsed.production_surface_sha256 == policy.production_surface_sha256(
        crlf_manifest.read_bytes()
    )


def test_exact_runtime_model_policy_is_admitted(monkeypatch, tmp_path) -> None:
    _runtime(monkeypatch, tmp_path)

    assert policy.require_model_admitted(MODEL_SPEC) == MODEL_SPEC
    policy.require_failover_chain(MODEL_SPEC, [])
    policy.require_provider_configuration(
        "vllm",
        base_url=MODEL_URL,
        api_key_present=True,
    )


@pytest.mark.parametrize(
    ("environment", "value", "match"),
    [
        (policy.ARTIFACT_DIGEST_ENV, "sha256:" + "b" * 64, "ARTIFACT"),
        (policy.REGION_ENV, "eu-west-1", "REGION"),
        (policy.RETENTION_ENV, "zero-data-retention", "RETENTION"),
        (policy.CREDENTIAL_SOURCE_ENV, "workload_identity", "CREDENTIAL"),
    ],
)
def test_runtime_assertion_substitution_is_refused(
    monkeypatch,
    tmp_path,
    environment,
    value,
    match,
) -> None:
    _runtime(monkeypatch, tmp_path)
    monkeypatch.setenv(environment, value)

    with pytest.raises(policy.ProductionModelPolicyError, match=match):
        policy.require_model_admitted(MODEL_SPEC)


def test_unapproved_or_unlisted_model_is_refused(monkeypatch, tmp_path) -> None:
    _runtime(monkeypatch, tmp_path)
    with pytest.raises(policy.ProductionModelPolicyError, match="outside"):
        policy.require_model_admitted("vllm:substituted-model")

    draft = _approved_document(_surface_digest())
    draft.update(
        {
            "status": "draft",
            "approved_at": None,
            "expires_at": None,
            "approvers": [],
            "models": [],
        }
    )
    _runtime(monkeypatch, tmp_path, document=draft)
    with pytest.raises(policy.ProductionModelPolicyError, match="not approved"):
        policy.require_model_admitted(MODEL_SPEC)


def test_policy_digest_substitution_is_refused(monkeypatch, tmp_path) -> None:
    _runtime(monkeypatch, tmp_path)
    monkeypatch.setenv(policy.POLICY_SHA256_ENV, "0" * 64)

    with pytest.raises(policy.ProductionModelPolicyError, match="digest differs"):
        policy.require_model_admitted(MODEL_SPEC)


def test_endpoint_and_mounted_credential_are_bound(monkeypatch, tmp_path) -> None:
    _runtime(monkeypatch, tmp_path)
    with pytest.raises(policy.ProductionModelPolicyError, match="endpoint differs"):
        policy.require_provider_configuration(
            "vllm",
            base_url="https://other.example/v1",
            api_key_present=True,
        )
    with pytest.raises(policy.ProductionModelPolicyError, match="unavailable"):
        policy.require_provider_configuration(
            "vllm",
            base_url=MODEL_URL,
            api_key_present=False,
        )
    monkeypatch.setenv("MAVERICK_SECRETS_BACKEND", "env")
    with pytest.raises(policy.ProductionModelPolicyError, match="file secret"):
        policy.require_provider_configuration(
            "vllm",
            base_url=MODEL_URL,
            api_key_present=True,
        )


def test_spend_cap_and_period_cannot_expand(monkeypatch, tmp_path) -> None:
    from maverick import provider_cost_cap

    _runtime(monkeypatch, tmp_path)
    monkeypatch.setattr(
        provider_cost_cap,
        "caps_from_config",
        lambda: {"vllm": 100.01},
    )
    with pytest.raises(policy.ProductionModelPolicyError, match="spend cap"):
        policy.require_model_admitted(MODEL_SPEC)

    monkeypatch.setattr(
        provider_cost_cap,
        "caps_from_config",
        lambda: {"vllm": 99.0},
    )
    monkeypatch.setattr(provider_cost_cap, "period_from_config", lambda: "day")
    with pytest.raises(policy.ProductionModelPolicyError, match="spend period"):
        policy.require_model_admitted(MODEL_SPEC)


def test_failover_must_match_and_have_full_policy(monkeypatch, tmp_path) -> None:
    document = _approved_document(_surface_digest())
    document["models"][0]["failover"] = ["vllm:fallback"]
    document["models"].append(
        {
            **document["models"][0],
            "spec": "vllm:fallback",
            "artifact_digest": "sha256:" + "b" * 64,
            "failover": [],
        }
    )
    _runtime(monkeypatch, tmp_path, document=document)

    policy.require_failover_chain(MODEL_SPEC, ["vllm:fallback"])
    with pytest.raises(policy.ProductionModelPolicyError, match="differs"):
        policy.require_failover_chain(MODEL_SPEC, [])
    with pytest.raises(policy.ProductionModelPolicyError, match="differs"):
        policy.require_failover_chain(MODEL_SPEC, ["vllm:other"])


def test_approval_separation_and_expiry_are_enforced() -> None:
    surface_digest = _surface_digest()
    document = _approved_document(surface_digest)
    document["approvers"][1]["principal"] = "user:model-risk"
    with pytest.raises(policy.ProductionModelPolicyError, match="distinct"):
        policy.validate_policy_document(
            document,
            production_surface_sha256=surface_digest,
        )

    document = _approved_document(surface_digest)
    document["expires_at"] = document["approved_at"]
    with pytest.raises(policy.ProductionModelPolicyError, match="window"):
        policy.validate_policy_document(
            document,
            production_surface_sha256=surface_digest,
        )


def test_development_dispatch_does_not_require_release_policy(monkeypatch) -> None:
    from maverick import production_scope

    monkeypatch.delenv(production_scope.REQUIRE_PRODUCTION_SURFACE_ENV, raising=False)
    monkeypatch.delenv(policy.POLICY_ENV, raising=False)
    assert policy.require_model_admitted("vllm:developer-model") == (
        "vllm:developer-model"
    )


def test_policy_error_is_not_provider_failover_retryable() -> None:
    from maverick.provider_failover import should_retry_llm_error

    assert not should_retry_llm_error(policy.ProductionModelPolicyError("denied"))
