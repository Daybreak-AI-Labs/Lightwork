from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import ai_cloud_agent_governance as cloud
from maverick_dashboard import ai_cloud_agent_governance_api as api

TENANT = "tenant-cloud-agents"
BASE = "/api/v1/assurance/cloud-agents"


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _ref(prefix: str, label: str) -> str:
    return f"{prefix}_{_digest(label)[:32]}"


class _Runtime:
    def __init__(self, tenant_id: str, principal_ref: str) -> None:
        self.tenant_id = tenant_id
        self.principal_ref = principal_ref
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def _result(self, name: str, *args: object, **kwargs: object):
        self.calls.append((name, args, kwargs))
        return {"operation": name, "arguments": kwargs}

    def status(self):
        return self._result(
            "status",
            enabled=True,
            store_mode="durable_injected",
            live_provider_write_acceptance_verified=False,
        )

    def list_inventory(self, **kwargs):
        return self._result("list_inventory", **kwargs)

    def verify_integrity(self):
        return self._result("verify_integrity", verified=True)

    def evaluate_policy(self, **kwargs):
        return self._result("evaluate_policy", **kwargs)

    def prepare_intent(self, **kwargs):
        return self._result("prepare_intent", **kwargs)

    def get_intent(self, intent_ref):
        return self._result("get_intent", intent_ref)

    def dispatch_intent(self, intent_ref, **kwargs):
        return self._result("dispatch_intent", intent_ref, **kwargs)

    def reconcile_intent(self, intent_ref, **kwargs):
        return self._result("reconcile_intent", intent_ref, **kwargs)

    def review_framework_pack(self, **kwargs):
        return self._result("review_framework_pack", **kwargs)

    def attest_framework_review(self, **kwargs):
        return self._result("attest_framework_review", **kwargs)

    def activate_framework_pack(self, **kwargs):
        return self._result("activate_framework_pack", **kwargs)


@pytest.fixture()
def environment(monkeypatch: pytest.MonkeyPatch):
    state: dict[str, object] = {
        "enabled": True,
        "factory_calls": [],
        "runtimes": [],
        "permissions": [],
    }
    monkeypatch.setattr(api, "_tenant_id", lambda: TENANT)
    monkeypatch.setattr(
        api,
        "caller_principal",
        lambda request: f"user:{request.headers.get('X-Test-Subject', 'operator')}",
    )

    def permission(_request: Request, value: str) -> None:
        permissions = state["permissions"]
        assert isinstance(permissions, list)
        permissions.append(value)

    monkeypatch.setattr(api, "require_permission", permission)

    def factory(tenant_id: str, principal_ref: str, request: Request):
        calls = state["factory_calls"]
        assert isinstance(calls, list)
        calls.append((tenant_id, principal_ref, request))
        runtime = _Runtime(tenant_id, principal_ref)
        runtimes = state["runtimes"]
        assert isinstance(runtimes, list)
        runtimes.append(runtime)
        return runtime

    deps = api.CloudAgentGovernanceDependencies(
        runtime_for=factory,
        enabled_probe=lambda _tenant: bool(state["enabled"]),
    )
    app = FastAPI()
    app.include_router(api.create_ai_cloud_agent_governance_router(deps), prefix="/api/v1")
    client = TestClient(
        app,
        headers={"Origin": "http://testserver", "X-Test-Subject": "operator"},
    )
    return client, state, app


def _evaluation_body() -> dict[str, object]:
    return {
        "agent_ref": _ref("cag", "agent"),
        "expected_agent_revision": 3,
        "policy_ref": _ref("pol", "policy"),
        "policy_revision": 7,
        "policy_sha256": _digest("policy-v7"),
        "requested_action": "disable_agent",
        "target_ref": _ref("cag", "agent"),
        "idempotency_key": "evaluate-agent-0001",
    }


def test_disabled_status_is_honest_no_store_and_does_not_construct_runtime(environment) -> None:
    client, state, _app = environment
    state["enabled"] = False
    response = client.get(f"{BASE}/status")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json() == {
        "schema": "lightwork.cloud-agent-governance-api-status.v1",
        "enabled": False,
        "complete": False,
        "runtime_constructed": False,
        "production_durability_configured": False,
        "production_runtime_available": False,
        "live_provider_inventory_acceptance_verified": False,
        "live_provider_write_acceptance_verified": False,
        "live_customer_acceptance_verified": False,
    }
    assert state["factory_calls"] == []
    denied = client.get(f"{BASE}/inventory")
    assert denied.status_code == 403
    assert denied.headers["cache-control"] == "no-store, max-age=0"
    assert state["factory_calls"] == []


def test_tenant_and_principal_are_server_derived_and_runtime_must_match(environment) -> None:
    client, state, app = environment
    first = client.get(f"{BASE}/inventory?offset=4&limit=17")
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store, max-age=0"
    calls = state["factory_calls"]
    assert isinstance(calls, list) and calls
    tenant, principal, request = calls[-1]
    assert tenant == TENANT
    assert principal.startswith("prn_")
    assert isinstance(request, Request)
    runtimes = state["runtimes"]
    assert isinstance(runtimes, list)
    assert runtimes[-1].calls[-1] == (
        "list_inventory",
        (),
        {"offset": 4, "limit": 17},
    )
    second = client.get(
        f"{BASE}/inventory",
        headers={"X-Test-Subject": "other-operator"},
    )
    assert second.status_code == 200
    assert calls[-1][1] != principal
    assert client.get(f"{BASE}/inventory?tenant_id=tenant-other").status_code == 422
    assert client.get(f"{BASE}/inventory?provider=aws_bedrock").status_code == 422
    assert "tenant_id" not in app.openapi()["paths"][f"{BASE}/inventory"]["get"]["parameters"]


def test_strict_enforcement_contract_rejects_all_server_owned_fields(environment) -> None:
    client, _state, app = environment
    valid = _evaluation_body()
    for field, value in {
        "tenant_id": "tenant-other",
        "tenant_sha256": _digest("tenant-other"),
        "principal_ref": _ref("prn", "forged"),
        "provider": "aws_bedrock",
        "binding_ref": _ref("cab", "forged"),
        "credential": "secret",
        "credential_env": "AWS_SECRET_ACCESS_KEY",
        "native_resource_id": "provider-agent-id",
        "provider_url": "https://provider.example.test",
        "signing_key": "secret",
        "raw_prompt": "ignore prior policy",
        "framework_document": {"licensed_text": "raw"},
    }.items():
        response = client.post(
            f"{BASE}/policy-evaluations",
            json={**valid, field: value},
        )
        assert response.status_code == 422, (field, response.text)
        assert response.headers["cache-control"] == "no-store, max-age=0"
    assert (
        client.post(
            f"{BASE}/policy-evaluations",
            json={**valid, "expected_agent_revision": "3"},
        ).status_code
        == 422
    )
    request_schemas = {
        name: model.model_json_schema().get("properties", {})
        for name, model in {
            "evaluate": api.PolicyEvaluationIn,
            "prepare": api.IntentPreparationIn,
            "dispatch": api.IntentDispatchIn,
            "reconcile": api.ReconciliationIn,
            "review": api.FrameworkReviewIn,
            "attest": api.FrameworkAttestationIn,
            "activate": api.FrameworkActivationIn,
        }.items()
    }
    forbidden = {
        "tenant",
        "tenant_id",
        "tenant_sha256",
        "principal_ref",
        "provider",
        "binding_ref",
        "credential",
        "native_resource_id",
        "provider_url",
        "signing_key",
    }
    assert all(not forbidden.intersection(fields) for fields in request_schemas.values())
    paths = app.openapi()["paths"]
    cloud_paths = {path for path in paths if "assurance/cloud-agents" in path}
    assert cloud_paths
    assert all("inventory-sync" not in path for path in cloud_paths)
    assert all("provider-bindings" not in path for path in cloud_paths)


def test_json_transport_is_content_typed_and_byte_bounded(environment) -> None:
    client, state, _app = environment
    wrong_type = client.post(
        f"{BASE}/policy-evaluations",
        content=b"{}",
        headers={"Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 415
    oversized = client.post(
        f"{BASE}/policy-evaluations",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(256 * 1024 + 1),
        },
    )
    assert oversized.status_code == 413
    assert oversized.headers["cache-control"] == "no-store, max-age=0"
    assert state["factory_calls"] == []


def test_exact_enforcement_and_reconciliation_routes_preserve_only_pins(environment) -> None:
    client, state, _app = environment
    evaluated = client.post(f"{BASE}/policy-evaluations", json=_evaluation_body())
    assert evaluated.status_code == 200, evaluated.text
    intent_ref = _ref("cei", "intent")
    prepared = client.post(
        f"{BASE}/enforcement-intents",
        json={
            "evaluation_ref": _ref("cpe", "evaluation"),
            "idempotency_key": "prepare-intent-0001",
        },
    )
    assert prepared.status_code == 200
    fetched = client.get(f"{BASE}/enforcement-intents/{intent_ref}")
    assert fetched.status_code == 200
    dispatched = client.post(
        f"{BASE}/enforcement-intents/{intent_ref}/dispatch",
        json={"expected_revision": 1, "idempotency_key": "dispatch-intent-0001"},
    )
    assert dispatched.status_code == 200
    reconciled = client.post(
        f"{BASE}/enforcement-intents/{intent_ref}/reconcile",
        json={
            "expected_revision": 3,
            "resolution": "no_change",
            "evidence_ref": _ref("evd", "post-read"),
            "evidence_sha256": _digest("post-read"),
            "observed_inventory_revision": 9,
            "idempotency_key": "reconcile-intent-0001",
        },
    )
    assert reconciled.status_code == 200
    permissions = state["permissions"]
    assert isinstance(permissions, list)
    assert permissions[-5:] == ["operate", "admin", "view", "admin", "admin"]
    runtimes = state["runtimes"]
    assert isinstance(runtimes, list)
    assert runtimes[-1].calls[-1][0] == "reconcile_intent"
    assert "resolver_ref" not in runtimes[-1].calls[-1][2]


def test_framework_review_attestation_and_activation_are_closed_and_separate(environment) -> None:
    client, state, _app = environment
    evidence = {
        "evidence_ref": _ref("evd", "framework-evidence"),
        "evidence_sha256": _digest("framework-evidence"),
        "relation_code": "supports.control",
    }
    reviewed = client.post(
        f"{BASE}/databricks/framework-reviews",
        json={
            "pack_version": "2026.1",
            "decision": "approved",
            "applicability": [
                {
                    "control_id": "DBAI-1",
                    "workload_ref": _ref("wrk", "workload"),
                    "status": "applicable",
                    "reason_code": "evidence.reviewed",
                    "evidence_links": [evidence],
                }
            ],
            "idempotency_key": "framework-review-0001",
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    attested = client.post(
        f"{BASE}/databricks/framework-attestations",
        json={
            "review_ref": _ref("fwr", "review"),
            "expected_review_revision": 1,
            "evidence_links": [evidence],
            "idempotency_key": "framework-attest-0001",
        },
        headers={"X-Test-Subject": "independent-attestor"},
    )
    assert attested.status_code == 200, attested.text
    activated = client.post(
        f"{BASE}/databricks/framework-activations",
        json={
            "pack_version": "2026.1",
            "review_ref": _ref("fwr", "review"),
            "attestation_ref": _ref("fwa", "attestation"),
            "expected_activation_revision": 0,
            "action": "activate",
            "idempotency_key": "framework-activate-0001",
        },
    )
    assert activated.status_code == 200, activated.text
    calls = state["factory_calls"]
    assert isinstance(calls, list)
    assert calls[-3][1] != calls[-2][1]
    assert "framework_document" not in reviewed.json()["arguments"]
    assert "certification_claim" not in activated.json()["arguments"]


def test_mismatched_runtime_and_sensitive_core_errors_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, "_tenant_id", lambda: TENANT)
    monkeypatch.setattr(api, "caller_principal", lambda _request: "user:operator")
    monkeypatch.setattr(api, "require_permission", lambda _request, _permission: None)

    def mismatched(_tenant: str, principal: str, _request: Request):
        return _Runtime("tenant-other", principal)

    app = FastAPI()
    app.include_router(
        api.create_ai_cloud_agent_governance_router(
            api.CloudAgentGovernanceDependencies(
                runtime_for=mismatched,
                enabled_probe=lambda _tenant: True,
            )
        ),
        prefix="/api/v1",
    )
    client = TestClient(app, headers={"Origin": "http://testserver"})
    assert client.get(f"{BASE}/status").status_code == 503

    class _Ambiguous(_Runtime):
        def dispatch_intent(self, intent_ref, **kwargs):
            del intent_ref, kwargs
            raise cloud.CloudAgentAmbiguousEffect("secret provider response")

    def ambiguous(tenant: str, principal: str, _request: Request):
        return _Ambiguous(tenant, principal)

    app.state.ai_cloud_agent_governance_runtime_factory = ambiguous
    response = client.post(
        f"{BASE}/enforcement-intents/{_ref('cei', 'intent')}/dispatch",
        json={"expected_revision": 1, "idempotency_key": "dispatch-ambiguous-1"},
    )
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.json()["detail"] == "cloud_agent_dispatch_requires_reconciliation"
    assert "secret provider response" not in response.text


def test_concrete_runtime_binds_tenant_and_derives_reconciliation_actor() -> None:
    tenant_authority = cloud.TenantAuthority(
        _digest(TENANT),
        _digest("tenant-authority"),
        1,
    )

    class _Authority:
        def __init__(self) -> None:
            self.calls = []

        def resolve_enforcement_reconciliation(self, authority, **kwargs):
            self.calls.append((authority, kwargs))
            return {"status": "reconciled_no_change"}

    authority = _Authority()
    config = cloud.CloudAgentGovernanceConfig.from_mapping(
        {"enable": True, "enabled_providers": ["aws_bedrock"]}
    )
    backend = cloud.CloudAgentGovernanceRuntime(
        config,
        authority,  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        cloud.InMemoryProviderResourceRegistry(),
        cloud.InMemoryProviderRevisionAuthority(),
        "process_local_test_only",
    )
    principal = _ref("prn", "operator")
    runtime = api.CloudAgentGovernanceOperatorRuntime(
        tenant_id=TENANT,
        principal_ref=principal,
        backend=backend,
        tenant_authority=tenant_authority,
    )
    runtime.reconcile_intent(
        _ref("cei", "intent"),
        expected_revision=3,
        resolution="no_change",
        evidence_ref=_ref("evd", "post-read"),
        evidence_sha256=_digest("post-read"),
        observed_inventory_revision=9,
        idempotency_key="reconcile-concrete-1",
    )
    assert authority.calls[0][0] is tenant_authority
    assert authority.calls[0][1]["resolver_ref"] == principal
    with pytest.raises(cloud.CloudAgentBindingError, match="tenant authority"):
        api.CloudAgentGovernanceOperatorRuntime(
            tenant_id="tenant-other",
            principal_ref=principal,
            backend=backend,
            tenant_authority=tenant_authority,
        )
    with pytest.raises(cloud.CloudAgentConfigError, match="persistence posture"):
        cloud.CloudAgentGovernanceRuntime(
            config,
            authority,  # type: ignore[arg-type]
            None,
            None,
            None,
            "durable_injected",  # type: ignore[arg-type]
        )


def test_concrete_status_does_not_mask_provider_binding_integrity_failure() -> None:
    tenant_authority = cloud.TenantAuthority(
        _digest(TENANT),
        _digest("tenant-authority"),
        1,
    )

    class _IntegrityAuthority:
        def resolve_provider_binding(self, *_args, **_kwargs):
            raise cloud.CloudAgentIntegrityError("provider binding integrity failed")

    config = cloud.CloudAgentGovernanceConfig.from_mapping(
        {"enable": True, "enabled_providers": ["aws_bedrock"]}
    )
    backend = cloud.CloudAgentGovernanceRuntime(
        config,
        _IntegrityAuthority(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        cloud.InMemoryProviderResourceRegistry(),
        cloud.InMemoryProviderRevisionAuthority(),
        "process_local_test_only",
    )
    runtime = api.CloudAgentGovernanceOperatorRuntime(
        tenant_id=TENANT,
        principal_ref=_ref("prn", "operator"),
        backend=backend,
        tenant_authority=tenant_authority,
    )
    with pytest.raises(cloud.CloudAgentIntegrityError, match="integrity"):
        runtime.status()
