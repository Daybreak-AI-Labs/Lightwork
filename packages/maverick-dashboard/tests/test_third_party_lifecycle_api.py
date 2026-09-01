from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import third_party_lifecycle as lifecycle
from maverick_dashboard import third_party_lifecycle_api as api

API = "/api/v1/third-party/lifecycle"
DAY = 86_400


@pytest.fixture()
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state: dict[str, object] = {
        "enabled": True,
        "tenant": "tenant-a",
        "factory_calls": [],
        "permission_calls": [],
    }
    database = tmp_path / "third-party-lifecycle.sqlite3"

    def require_enabled() -> None:
        if not state["enabled"]:
            raise lifecycle.ThirdPartyLifecycleConfigError("disabled for test")

    monkeypatch.setattr(lifecycle, "require_enabled", require_enabled)
    monkeypatch.setattr(lifecycle, "enabled", lambda: bool(state["enabled"]))
    monkeypatch.setattr(api, "_tenant_id", lambda: str(state["tenant"]))

    def tenant_permission(_request, permission):
        calls = state["permission_calls"]
        assert isinstance(calls, list)
        calls.append(("tenant", permission))

    def global_permission(_request, permission):
        calls = state["permission_calls"]
        assert isinstance(calls, list)
        calls.append(("global", permission))

    monkeypatch.setattr(api, "require_permission", tenant_permission)
    monkeypatch.setattr(api, "require_global_permission", global_permission)

    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        subject = request.headers.get("X-Test-Subject", "owner")
        request.state.principal = SimpleNamespace(principal=f"user:{subject}")
        return await call_next(request)

    def store_factory(**kwargs):
        calls = state["factory_calls"]
        assert isinstance(calls, list)
        calls.append(kwargs)
        return lifecycle.ThirdPartyLifecycleStore(
            database,
            config_gate=lambda: None,
            principal_resolver=kwargs["principal_resolver"],
        )

    app.state.third_party_lifecycle_store_factory = store_factory
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(
        app,
        headers={"Origin": "http://testserver", "X-Test-Subject": "owner"},
    )
    return client, state


def _template_body() -> dict:
    return {
        "title_ref": "title.standard",
        "questions": [
            {
                "question_id": "security-control",
                "domain": "security",
                "prompt_ref": "prompt.security-control",
                "response_type": "boolean",
                "required": True,
                "options": [],
                "condition": None,
                "risk_rules": [],
            }
        ],
        "mitigation_rules": [],
        "expected_latest_version": 0,
        "idempotency_key": "template-create",
    }


def test_status_is_honest_store_free_and_feature_off_is_403(environment) -> None:
    client, state = environment
    response = client.get(f"{API}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["network_operations_implemented"] is False
    assert status["provider_writes_implemented"] is False
    assert status["live_provider_acceptance_verified"] is False
    assert status["live_customer_acceptance_verified"] is False
    assert status["callback_transport_worker_implemented"] is False
    assert status["callbacks_are_governed_intent_only"] is True
    assert state["factory_calls"] == []

    state["enabled"] = False
    assert client.get(f"{API}/status").json()["enabled"] is False
    denied = client.get(f"{API}/lifecycles/lifecycle-1")
    assert denied.status_code == 403


def test_exact_tenant_and_stable_principal_resolver_binding(environment) -> None:
    client, state = environment
    status = client.get(f"{API}/status").json()
    expected = (
        "tpl-principal-"
        + hashlib.sha256(
            b"lightwork.third-party-lifecycle.identity.v1:tenant-a:user:owner"
        ).hexdigest()[:32]
    )
    assert status["principal_ref"] == expected

    created = client.put(
        f"{API}/lifecycles/lifecycle-1",
        json={
            "vendor_ref": "vendor-1",
            "owner_ref": expected,
            "dependencies": [],
            "retention_seconds": DAY,
            "idempotency_key": "lifecycle-create",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["tenant_id"] == "tenant-a"
    calls = state["factory_calls"]
    assert isinstance(calls, list) and calls
    assert calls[-1]["tenant_id"] == "tenant-a"
    assert calls[-1]["principal_ref"] == expected
    assert calls[-1]["principal_resolver"]() == expected

    state["tenant"] = "tenant-b"
    assert client.get(f"{API}/lifecycles/lifecycle-1").status_code == 404


@pytest.mark.parametrize("field", ["tenant", "tenant_id", "actor", "actor_ref"])
def test_strict_bodies_reject_tenant_and_actor_spoofing(environment, field) -> None:
    client, _state = environment
    response = client.put(
        f"{API}/templates/standard",
        json={**_template_body(), field: "forged"},
    )
    assert response.status_code == 422


def test_strict_types_and_four_eyes_error_translation(environment) -> None:
    client, _state = environment
    numeric_string = {**_template_body(), "expected_latest_version": "0"}
    assert client.put(f"{API}/templates/standard", json=numeric_string).status_code == 422

    created = client.put(f"{API}/templates/standard", json=_template_body())
    assert created.status_code == 200, created.text
    assert created.json()["authored_by"] == client.get(f"{API}/status").json()["principal_ref"]
    approval = {
        "expected_revision": 1,
        "idempotency_key": "template-approve",
    }
    same_actor = client.post(f"{API}/templates/standard/versions/1/approve", json=approval)
    assert same_actor.status_code == 403
    assert "cannot approve their own version" in same_actor.json()["detail"]

    approved = client.post(
        f"{API}/templates/standard/versions/1/approve",
        headers={"X-Test-Subject": "reviewer"},
        json=approval,
    )
    assert approved.status_code == 200, approved.text
    assert (
        approved.json()["approved_by"]
        == client.get(f"{API}/status", headers={"X-Test-Subject": "reviewer"}).json()[
            "principal_ref"
        ]
    )
    stale = client.post(
        f"{API}/templates/standard/versions/1/approve",
        headers={"X-Test-Subject": "reviewer-2"},
        json={**approval, "idempotency_key": "stale", "expected_revision": 1},
    )
    assert stale.status_code == 409

    unsupported = client.post(
        f"{API}/lifecycles/lifecycle-1/transition",
        json={
            "target_stage": "offboarded",
            "reason_code": "skip_controls",
            "expected_revision": 1,
            "idempotency_key": "unsupported-transition",
        },
    )
    assert unsupported.status_code == 422


def test_callback_claim_fail_uses_core_integer_contract_and_redacts_get(
    environment,
) -> None:
    client, _state = environment
    principal = client.get(f"{API}/status").json()["principal_ref"]
    lifecycle_response = client.put(
        f"{API}/lifecycles/lifecycle-callback",
        json={
            "vendor_ref": "vendor-callback",
            "owner_ref": principal,
            "dependencies": [
                {
                    "dependency_id": "access-crm",
                    "kind": "access",
                    "reference": "ref-crm",
                    "evidence_sha256": "a" * 64,
                }
            ],
            "retention_seconds": DAY,
            "idempotency_key": "callback-lifecycle-create",
        },
    )
    assert lifecycle_response.status_code == 200, lifecycle_response.text
    plan = client.post(
        f"{API}/lifecycles/lifecycle-callback/offboarding/plan-callback",
        json={
            "tasks": [
                {
                    "task_id": "revoke-crm",
                    "dependency_id": "access-crm",
                    "action": "revoke_access",
                    "assignee_ref": principal,
                    "callback_required": True,
                }
            ],
            "legal_hold": False,
            "legal_hold_evidence_ref": None,
            "maximum_callback_attempts": 2,
            "expected_lifecycle_revision": 1,
            "idempotency_key": "callback-plan-create",
        },
    )
    assert plan.status_code == 200, plan.text

    assert (
        client.post(f"{API}/callbacks/claim", json={"limit": 1, "lease_seconds": 30.0}).status_code
        == 422
    )
    claimed_response = client.post(
        f"{API}/callbacks/claim",
        headers={"X-Test-Subject": "callback-worker"},
        json={"limit": 1, "lease_seconds": 30},
    )
    assert claimed_response.status_code == 200, claimed_response.text
    claimed = claimed_response.json()["items"][0]
    assert claimed["lease_token"]

    detail = client.get(f"{API}/callbacks/{claimed['callback_id']}")
    assert detail.status_code == 200
    assert "lease_token" not in detail.json()

    failed = client.post(
        f"{API}/callbacks/{claimed['callback_id']}/fail",
        headers={"X-Test-Subject": "callback-worker"},
        json={
            "lease_token": claimed["lease_token"],
            "error_code": "temporary-outage",
            "retry_after_seconds": 10,
            "expected_revision": claimed["revision"],
            "idempotency_key": "callback-fail",
        },
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["state"] == "pending"

    too_many_attempts = client.post(
        f"{API}/lifecycles/lifecycle-callback/offboarding/another-plan",
        json={
            "tasks": [],
            "legal_hold": False,
            "legal_hold_evidence_ref": None,
            "maximum_callback_attempts": 21,
            "expected_lifecycle_revision": 2,
            "idempotency_key": "too-many-attempts",
        },
    )
    assert too_many_attempts.status_code == 422


def test_read_paths_are_local_and_major_routes_are_exposed(environment) -> None:
    client, _state = environment
    paths = set(client.app.openapi()["paths"])
    expected = {
        f"{API}/templates/{{template_id}}",
        f"{API}/assessments/{{assessment_id}}/required-questions",
        f"{API}/assessments/{{assessment_id}}/responses/{{question_id}}",
        f"{API}/assessments/{{assessment_id}}/evaluate",
        f"{API}/assessments/{{assessment_id}}/tasks/{{task_id}}",
        f"{API}/lifecycles/{{lifecycle_id}}/transition",
        f"{API}/lifecycles/{{lifecycle_id}}/offboarding/{{plan_id}}",
        f"{API}/offboarding/{{plan_id}}/legal-hold",
        f"{API}/offboarding/{{plan_id}}/tasks/{{task_id}}",
        f"{API}/offboarding/{{plan_id}}/complete",
        f"{API}/callbacks/claim",
        f"{API}/callbacks/{{callback_id}}/fail",
        f"{API}/callbacks/{{callback_id}}/acknowledge",
        f"{API}/callbacks/{{callback_id}}/requeue",
        f"{API}/exports/{{export_id}}",
        f"{API}/retention/purge",
        f"{API}/integrity",
    }
    assert expected <= paths
    source = Path(api.__file__).read_text(encoding="utf-8")
    for network_primitive in (
        "requests.",
        "httpx.",
        "urllib.request",
        "socket.",
        "execute_callback",
    ):
        assert network_primitive not in source


def test_view_operate_and_admin_permission_floors(environment) -> None:
    client, state = environment
    principal = client.get(f"{API}/status").json()["principal_ref"]
    created = client.put(
        f"{API}/lifecycles/lifecycle-role",
        json={
            "vendor_ref": "vendor-role",
            "owner_ref": principal,
            "dependencies": [],
            "retention_seconds": DAY,
            "idempotency_key": "role-create",
        },
    )
    assert created.status_code == 200
    active = client.post(
        f"{API}/lifecycles/lifecycle-role/transition",
        json={
            "target_stage": "active",
            "reason_code": "onboarding-approved",
            "expected_revision": 1,
            "idempotency_key": "role-active",
        },
    )
    assert active.status_code == 200, active.text
    suspended = client.post(
        f"{API}/lifecycles/lifecycle-role/transition",
        json={
            "target_stage": "suspended",
            "reason_code": "risk-review",
            "expected_revision": 2,
            "idempotency_key": "role-suspend",
        },
    )
    assert suspended.status_code == 200, suspended.text
    calls = state["permission_calls"]
    assert isinstance(calls, list)
    assert ("tenant", "view") in calls
    assert calls.count(("tenant", "operate")) >= 2
    assert ("global", "admin") in calls
