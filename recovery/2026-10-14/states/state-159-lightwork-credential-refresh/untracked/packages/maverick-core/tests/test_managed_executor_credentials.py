"""Proposal-only tests: every AWS request and kubectl execution is faked."""
from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from maverick.sandbox import kubernetes as backend_module
from maverick.sandbox import kubernetes_authority as authority
from maverick.sandbox import managed_executor_credentials as reader

ARN = "arn:aws:secretsmanager:us-east-2:589997396906:secret:lightwork-prod/executor-kubeconfig-Abc123"
NOW = 2000000000
NAMESPACE = "lightwork-execution"
IMAGE = "example.invalid/worker@sha256:" + "a" * 64


def encode(value):
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def config(iat=NOW - 200, expires=None, slot="a"):
    claims = {"iat": iat, "nbf": iat, "exp": expires or iat + 86400,
              "iss": "https://oidc.eks.us-east-2.amazonaws.com/id/TRUSTED",
              "aud": ["https://kubernetes.default.svc"],
              "sub": f"system:serviceaccount:{NAMESPACE}:lightwork-controller",
              "kubernetes.io": {"namespace": NAMESPACE,
                                "serviceaccount": {"name": "lightwork-controller", "uid": "11111111-1111-1111-1111-111111111111"},
                                "secret": {"name": "lightwork-prod-controller-binding-" + slot, "uid": "22222222-2222-2222-2222-222222222222"}}}
    token = encode({"alg": "RS256", "kid": "test-only"}) + "." + encode(claims) + ".c2lnbmF0dXJl"
    return {"apiVersion": "v1", "kind": "Config", "current-context": "executor",
            "contexts": [{"name": "executor", "context": {"namespace": NAMESPACE, "cluster": "cluster", "user": "controller"}}],
            "clusters": [{"name": "cluster", "cluster": {"server": "https://test.eks.amazonaws.com", "certificate-authority-data": "dGVzdA=="}}],
            "users": [{"name": "controller", "user": {"token": token}}]}


class FakeSecrets:
    def __init__(self):
        self.calls = []
        self.document = config()
        self.version = "a" * 32
        self.error = None

    def get_secret_value(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"ARN": ARN, "VersionStages": ["AWSCURRENT"], "VersionId": self.version,
                "SecretString": json.dumps(self.document)}


@pytest.fixture
def setup(monkeypatch, tmp_path):
    reader._CACHE.clear()
    fake = FakeSecrets()
    clock = {"wall": NOW, "mono": 100.0}
    monkeypatch.setenv("MAVERICK_DEPLOYMENT_ISOLATION", "kubernetes")
    monkeypatch.setenv("MAVERICK_EXECUTOR_NAMESPACE", NAMESPACE)
    monkeypatch.setenv("MAVERICK_EXECUTOR_IMAGE", IMAGE)
    monkeypatch.setenv("MAVERICK_EXECUTOR_KUBECONFIG_SECRET_ARN", ARN)
    monkeypatch.setenv("MAVERICK_EXECUTOR_KUBECONFIG_SECRET", json.dumps(config()))
    monkeypatch.setattr(reader, "maverick_home", lambda: tmp_path)
    monkeypatch.setattr(reader, "_client", lambda arn: fake)
    # Do not replace shared stdlib monotonic: the file-lock implementation uses it.
    monkeypatch.setattr(reader, "time", SimpleNamespace(time=lambda: clock["wall"], monotonic=lambda: clock["mono"]))
    return fake, clock, tmp_path


def test_refresh_cache_rotation_and_private_file(setup):
    fake, clock, _ = setup
    first = Path(reader.managed_path(NAMESPACE, 120))
    assert fake.calls == [{"SecretId": ARN, "VersionStage": "AWSCURRENT"}]
    fake.document, fake.version = config(NOW - 100, slot="b"), "b" * 32
    assert reader.managed_path(NAMESPACE, 120) == str(first)
    assert len(fake.calls) == 1
    clock["mono"] += 31
    assert reader.managed_path(NAMESPACE, 120) == str(first)
    assert json.loads(first.read_text())["users"] == fake.document["users"]
    if os.name != "nt":
        assert first.stat().st_mode & 0o777 == 0o600


def test_concurrent_refresh_is_serialized(setup):
    fake, _, _ = setup
    with ThreadPoolExecutor(max_workers=8) as executor:
        paths = list(executor.map(lambda _: reader.managed_path(NAMESPACE, 30), range(12)))
    assert len(set(paths)) == 1
    assert len(fake.calls) == 1
    assert json.loads(Path(paths[0]).read_text()) == fake.document


def test_aws_failure_never_uses_stale_file(setup):
    fake, clock, _ = setup
    reader.managed_path(NAMESPACE)
    fake.error = RuntimeError("TOP_SECRET_BODY_AND_TOKEN")
    clock["mono"] += 31
    for _ in range(2):
        with pytest.raises(reader.CredentialUnavailable) as exc:
            reader.managed_path(NAMESPACE)
        assert "TOP_SECRET" not in str(exc.value)
        assert exc.value.__suppress_context__
    assert not reader._CACHE


def test_version_rollback_survives_process_cache_reset(setup):
    fake, clock, _ = setup
    reader.managed_path(NAMESPACE)
    first = copy.deepcopy(fake.document)
    fake.document, fake.version = config(NOW - 100, slot="b"), "b" * 32
    clock["mono"] += 31
    reader.managed_path(NAMESPACE)
    reader._CACHE.clear()
    fake.document, fake.version = first, "a" * 32
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


def test_operation_must_fit_expiry_including_reserve(setup):
    fake, clock, _ = setup
    fake.document = config(NOW - 200, NOW + 180)
    reader.managed_path(NAMESPACE, 120)
    clock["wall"] += 1
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE, 120)


@pytest.mark.parametrize("mutation", [
    lambda c: c["clusters"][0]["cluster"].update(server="https://attacker.example"),
    lambda c: c["clusters"][0]["cluster"].update(**{"certificate-authority-data": "ZXZpbA=="}),
    lambda c: c["clusters"][0]["cluster"].update(**{"insecure-skip-tls-verify": True}),
    lambda c: c["clusters"][0]["cluster"].update(**{"proxy-url": "https://attacker.example"}),
    lambda c: c["users"][0]["user"].update(exec={"command": "attacker"}),
    lambda c: c["contexts"][0]["context"].update(namespace="other"),
    lambda c: c.update(extensions=[{"exec": "attacker"}]),
])
def test_identity_or_kubeconfig_policy_change_rejected(setup, mutation):
    fake, _, _ = setup
    mutation(fake.document)
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


@pytest.mark.parametrize("field,value", [
    ("sub", "system:serviceaccount:lightwork-execution:admin"),
    ("aud", ["other-audience"]), ("iss", "https://other-issuer.example"),
    ("iat", NOW + 61), ("exp", float("inf")),
])
def test_token_claim_mismatch_rejected(setup, field, value):
    fake, _, _ = setup
    user = fake.document["users"][0]["user"]
    parts = user["token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    claims[field] = value
    user["token"] = parts[0] + "." + encode(claims) + "." + parts[2]
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


def test_deployment_config_does_not_restore_original_token(setup, monkeypatch):
    fake, clock, _ = setup
    monkeypatch.setattr(authority, "_kubeconfig", lambda raw: pytest.fail("old writer invoked"))
    initial = authority.deployment_kubernetes_config()
    fake.document, fake.version = config(NOW - 100, slot="c"), "c" * 32
    clock["mono"] += 31
    updated = authority.deployment_kubernetes_config()
    assert initial["kubeconfig"] == updated["kubeconfig"]
    assert json.loads(Path(updated["kubeconfig"]).read_text()) == fake.document


def make_backend(monkeypatch, tmp_path):
    monkeypatch.setattr(backend_module.KubernetesBackend, "_verify_kubectl", lambda self: None)
    return backend_module.KubernetesBackend(image=IMAGE, namespace=NAMESPACE, workdir=tmp_path)


def test_backend_command_reserves_export_budget(setup, monkeypatch, tmp_path):
    fake, _, _ = setup
    backend = make_backend(monkeypatch, tmp_path)
    fake.document = config(NOW - 200, NOW + 299)
    calls = []
    monkeypatch.setattr(backend_module.subprocess, "run", lambda *a, **k: calls.append(a))
    with pytest.raises(reader.CredentialUnavailable):
        backend._run_command("pod", "echo ok", 120)
    assert not calls


def test_backend_rejects_context_override(setup, monkeypatch, tmp_path):
    backend = make_backend(monkeypatch, tmp_path)
    backend.extra_kubectl_args = ["--server", "https://attacker.example"]
    with pytest.raises(reader.CredentialUnavailable):
        backend._kubectl_prefix()


def test_export_refresh_failure_preserves_recovery(setup, monkeypatch, tmp_path):
    fake, _, _ = setup
    fake.error = RuntimeError("secret")
    backend = make_backend(monkeypatch, tmp_path)
    captured = []
    monkeypatch.setattr(backend_module.workspace_transfer, "capture_archive", lambda *a: captured.append(a))
    result = backend._copy_out("pod", {}, tmp_path)
    assert "CredentialUnavailable" in result
    assert not captured
    assert not (tmp_path / "export.complete").exists()


def test_unmanaged_backend_does_not_fetch(setup, monkeypatch, tmp_path):
    fake, _, _ = setup
    monkeypatch.delenv("MAVERICK_EXECUTOR_KUBECONFIG_SECRET_ARN")
    backend = make_backend(monkeypatch, tmp_path)
    backend.kubeconfig = "unchanged.json"
    assert backend._kubectl_prefix()[:3] == ["kubectl", "--kubeconfig", "unchanged.json"]
    assert not fake.calls


def test_backend_success_uses_managed_path_without_restart(setup, monkeypatch, tmp_path):
    fake, _, _ = setup
    backend = make_backend(monkeypatch, tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, "ok", "")

    monkeypatch.setattr(backend_module.subprocess, "run", run)
    result = backend._run_command("pod", "echo ok", 120)
    assert result.stdout == "ok"
    assert calls[0][0][0] == "kubectl"
    assert "--kubeconfig" in calls[0][0]
    assert json.loads(Path(calls[0][0][2]).read_text()) == fake.document


@pytest.mark.parametrize("field,value", [
    ("serviceaccount", {"name": "lightwork-controller", "uid": "99999999-9999-9999-9999-999999999999"}),
    ("secret", {"name": "unapproved-binding", "uid": "22222222-2222-2222-2222-222222222222"}),
    ("secret", {"name": "lightwork-prod-controller-binding-b", "uid": "malformed"}),
])
def test_bound_identity_rejected(setup, field, value):
    fake, _, _ = setup
    user = fake.document["users"][0]["user"]
    parts = user["token"].split(".")
    claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    claims["kubernetes.io"][field] = value
    user["token"] = parts[0] + "." + encode(claims) + "." + parts[2]
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


def test_same_issue_time_new_version_rejected(setup):
    fake, clock, _ = setup
    reader.managed_path(NAMESPACE)
    clock["mono"] += 31
    fake.version = "b" * 32
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


def test_unmanaged_authority_keeps_inline_path(setup, monkeypatch):
    monkeypatch.delenv("MAVERICK_EXECUTOR_KUBECONFIG_SECRET_ARN")
    monkeypatch.setattr(authority, "_kubeconfig", lambda raw: "unchanged-inline.json")
    assert authority.deployment_kubernetes_config()["kubeconfig"] == "unchanged-inline.json"


def test_aws_client_ignores_endpoint_and_ca_overrides(monkeypatch):
    import boto3
    calls = []
    monkeypatch.setenv("AWS_ENDPOINT_URL", "https://attacker.example")
    monkeypatch.setenv("AWS_CA_BUNDLE", "attacker.pem")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: calls.append((args, kwargs)))
    reader._client(ARN)
    args, kwargs = calls[0]
    assert args == ("secretsmanager",)
    assert kwargs["endpoint_url"] == "https://secretsmanager.us-east-2.amazonaws.com"
    assert kwargs["region_name"] == "us-east-2"
    assert kwargs["verify"] is True
    assert kwargs["config"].retries == {"total_max_attempts": 1}
    assert kwargs["config"].connect_timeout == 2
    assert kwargs["config"].read_timeout == 3
    assert kwargs["config"].proxies == {}


def test_duplicate_json_key_rejected(setup):
    fake, _, _ = setup
    raw = json.dumps(fake.document).replace('"kind": "Config"', '"kind": "Config", "kind": "Config"')
    fake.get_secret_value = lambda **kwargs: {"ARN": ARN, "VersionStages": ["AWSCURRENT"],
                                             "VersionId": "a" * 32, "SecretString": raw}
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)


def test_wrong_secret_response_rejected(setup):
    fake, _, _ = setup
    fake.get_secret_value = lambda **kwargs: {"ARN": ARN + "other", "VersionStages": ["AWSCURRENT"],
                                             "VersionId": "a" * 32, "SecretString": json.dumps(config())}
    with pytest.raises(reader.CredentialUnavailable):
        reader.managed_path(NAMESPACE)
