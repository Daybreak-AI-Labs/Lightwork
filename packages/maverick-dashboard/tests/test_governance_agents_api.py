from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from maverick import governance_agents as agents
from maverick.budget import Budget
from maverick_dashboard import governance_agents_api as api

API = "/api/v1/governance-agents"


def _principal_ref(subject: str, tenant: str = "tenant-a") -> str:
    digest = hashlib.sha256(
        f"lightwork.governance-agent.identity.v1:{tenant}:user:{subject}".encode()
    ).hexdigest()
    return f"ga-principal-{digest[:32]}"


class Clock:
    value = 1_000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: C901
    state: dict[str, object] = {
        "enabled": True,
        "tenant": "tenant-a",
        "factory_calls": [],
        "permission_calls": [],
        "dispatch_tokens": [],
    }
    database = tmp_path / "governance-agents.sqlite3"
    clock = Clock()
    analysis_bindings: dict[tuple[str, str], tuple[object, object]] = {}
    execution_bindings: dict[tuple[str, str], tuple[object, object, object]] = {}
    token_vault: dict[tuple[str, str], str] = {}

    def require_enabled() -> None:
        if not state["enabled"]:
            raise agents.GovernanceAgentConfigError("disabled for test")

    monkeypatch.setattr(agents, "require_enabled", require_enabled)
    monkeypatch.setattr(agents, "enabled", lambda: bool(state["enabled"]))
    monkeypatch.setattr(api, "_tenant_id", lambda: str(state["tenant"]))

    def tenant_permission(_request, permission):
        state["permission_calls"].append(("tenant", permission))

    def global_permission(_request, permission):
        state["permission_calls"].append(("global", permission))

    monkeypatch.setattr(api, "require_permission", tenant_permission)
    monkeypatch.setattr(api, "require_global_permission", global_permission)

    app = FastAPI()

    @app.middleware("http")
    async def principal(request: Request, call_next):
        subject = request.headers.get("X-Test-Subject", "owner")
        request.state.principal = SimpleNamespace(principal=f"user:{subject}")
        return await call_next(request)

    def policy_approvers(tenant_id: str, _policy_id: str, _revision: int):
        return (_principal_ref("reviewer-a", tenant_id), _principal_ref("reviewer-b", tenant_id))

    def store_factory(**kwargs):
        state["factory_calls"].append(kwargs)
        return agents.GovernanceAgentStore(
            database,
            config_gate=lambda: None,
            principal_resolver=kwargs["principal_resolver"],
            policy_approver_resolver=kwargs["policy_approver_resolver"],
            analysis_binding_resolver=kwargs["analysis_binding_resolver"],
            execution_binding_resolver=kwargs["execution_binding_resolver"],
            execution_token_escrow=kwargs["execution_token_escrow"],
            execution_token_resolver=kwargs["execution_token_resolver"],
            clock=clock,
            budget=Budget(max_tool_calls=100),
        )

    def context_resolver(tenant_id: str, references):
        assert tenant_id == state["tenant"]
        return tuple(
            agents.GovernanceContextItem(
                item_id=item["item_id"],
                source_kind=item["source_kind"],
                source_ref=item["source_ref"],
                source_revision=item["source_revision"],
                collected_at=clock.value - 1,
                content=f"server-owned:{item['source_ref']}".encode(),
            )
            for item in references
        )

    def adapter_resolver(_tenant_id: str, agent_kind: str):
        key = (_tenant_id, agent_kind)
        if key in analysis_bindings:
            return analysis_bindings[key]
        authority = agents.build_adapter_authority(
            adapter_id=f"adapter:{agent_kind}",
            revision=1,
            agent_kind=agent_kind,
            implementation_sha256=hashlib.sha256(b"implementation").hexdigest(),
            configuration_sha256=hashlib.sha256(b"configuration").hexdigest(),
            deterministic=True,
            network_access=False,
            legal_conclusion_enabled=False,
            valid_until=clock.value + 600,
        )

        def adapter(**kwargs):
            return agents.GovernanceAgentAnalysis(
                findings=(
                    agents.build_finding(
                        agent_kind=agent_kind,
                        finding_id="finding:one",
                        finding_code=sorted(agents.FINDING_CODES[agent_kind])[0],
                        severity="high",
                        evidence_item_ids=(kwargs["context"][0].item_id,),
                        recommended_action_code=sorted(agents.ACTION_CODES[agent_kind])[0],
                        rationale_code="evidence_sufficient",
                        confidence_bps=8_000,
                    ),
                ),
                limitations=(),
                complete=True,
            )

        analysis_bindings[key] = (authority, adapter)
        return analysis_bindings[key]

    def _proof(material: dict) -> str:
        encoded = json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()

    def dispatcher_resolver(_tenant_id: str, _agent_kind: str):
        key = (_tenant_id, _agent_kind)
        if key in execution_bindings:
            return execution_bindings[key]
        authority = agents.build_dispatcher_authority(
            dispatcher_id=f"dispatcher:{_agent_kind}",
            revision=1,
            agent_kind=_agent_kind,
            implementation_sha256=hashlib.sha256(b"dispatcher-implementation").hexdigest(),
            configuration_sha256=hashlib.sha256(b"dispatcher-configuration").hexdigest(),
            execution_enabled=True,
            valid_until=clock.value + 600,
        )

        def dispatcher(*, run, idempotency_key):
            state["dispatch_tokens"].append(idempotency_key)
            material = agents.execution_receipt_signing_payload(
                run=run,
                dispatcher_authority=authority,
                outcome="no_change",
                provider_status_code="accepted_no_change",
                external_reference_sha256=None,
                retryable=False,
            )
            return agents.build_execution_outcome(
                run=run,
                dispatcher_authority=authority,
                outcome="no_change",
                provider_status_code="accepted_no_change",
                external_reference_sha256=None,
                retryable=False,
                receipt_proof_sha256=_proof(material),
            )

        def verifier(enrolled_authority, outcome):
            material = asdict(outcome)
            receipt_proof = material.pop("receipt_proof_sha256")
            material.pop("receipt_sha256")
            return enrolled_authority == authority and hmac.compare_digest(
                receipt_proof,
                _proof(material),
            )

        execution_bindings[key] = (authority, dispatcher, verifier)
        return execution_bindings[key]

    def token_escrow(tenant_id: str, run_id: str, token: str, token_sha256: str) -> bool:
        if hashlib.sha256(token.encode()).hexdigest() != token_sha256:
            return False
        key = (tenant_id, run_id)
        prior = token_vault.get(key)
        if prior is not None and prior != token:
            return False
        token_vault[key] = token
        return True

    def token_resolver(tenant_id: str, run_id: str) -> str:
        return token_vault[(tenant_id, run_id)]

    app.state.governance_agent_policy_approver_resolver = policy_approvers
    app.state.governance_agents_store_factory = store_factory
    app.state.governance_agent_context_resolver = context_resolver
    app.state.governance_agent_adapter_resolver = adapter_resolver
    app.state.governance_agent_dispatcher_resolver = dispatcher_resolver
    app.state.governance_agent_execution_token_escrow = token_escrow
    app.state.governance_agent_execution_token_resolver = token_resolver
    app.include_router(api.router, prefix="/api/v1")
    client = TestClient(
        app,
        headers={"Origin": "http://testserver", "X-Test-Subject": "owner"},
    )
    return client, state


def _policy_body(*, execution_enabled: bool = True) -> dict:
    return {
        "revision": 1,
        "agent_kind": "breach_response",
        "allowed_capabilities": [
            "containment_plan",
            "evidence_preservation_plan",
            "incident_triage",
            "notification_draft",
            "regulatory_clock_review",
        ],
        "required_approvals": 2 if execution_enabled else 1,
        "execution_enabled": execution_enabled,
        "approver_principals": [
            _principal_ref("reviewer-a"),
            _principal_ref("reviewer-b"),
        ],
        "valid_until": 1_600.0,
        "idempotency_key": "publish:breach",
    }


def _analysis_body() -> dict:
    return {
        "policy_id": "breach",
        "policy_revision": 1,
        "capability": "containment_plan",
        "context": [
            {
                "item_id": "evidence:one",
                "source_kind": "incident",
                "source_ref": "incident:one",
                "source_revision": 1,
            }
        ],
        "idempotency_key": "analysis:one",
    }


def _publish_and_analyze(client: TestClient):
    published = client.put(f"{API}/policies/breach", json=_policy_body())
    assert published.status_code == 200, published.text
    analyzed = client.put(f"{API}/runs/run-one/analysis", json=_analysis_body())
    assert analyzed.status_code == 200, analyzed.text
    return analyzed.json()


def test_status_is_honest_store_free_and_feature_off_is_denied(environment):
    client, state = environment
    response = client.get(f"{API}/status")
    assert response.status_code == 200
    status = response.json()
    assert status["execution_default_off"] is True
    assert status["raw_context_accepted_over_http"] is False
    assert status["caller_selected_adapters"] is False
    assert status["live_provider_acceptance_verified"] is False
    assert state["factory_calls"] == []

    state["enabled"] = False
    assert client.get(f"{API}/status").json()["enabled"] is False
    assert client.get(f"{API}/runs/anything").status_code == 403


@pytest.mark.parametrize("field", ["tenant", "tenant_id", "actor", "adapter_id"])
def test_strict_bodies_reject_authority_spoofing(environment, field):
    client, _state = environment
    response = client.put(
        f"{API}/policies/breach",
        json={**_policy_body(), field: "forged"},
    )
    assert response.status_code == 422


def test_policy_approvers_must_match_server_authority(environment):
    client, _state = environment
    body = _policy_body()
    body["approver_principals"] = ["fake-a", "fake-b"]
    response = client.put(f"{API}/policies/breach", json=body)
    assert response.status_code == 403
    assert "independent authority" in response.json()["detail"]


def test_analysis_uses_server_context_and_adapter(environment):
    client, state = environment
    run = _publish_and_analyze(client)
    assert run["state"] == "pending_review"
    expected = hashlib.sha256(b"server-owned:incident:one").hexdigest()
    assert run["context"][0]["content_sha256"] == expected
    assert run["findings"][0]["evidence_item_ids"] == ["evidence:one"]
    assert state["permission_calls"][-1] == ("tenant", "operate")


def test_only_enrolled_reviewers_can_approve_and_two_are_required(environment):
    client, _state = environment
    run = _publish_and_analyze(client)
    denied = client.post(
        f"{API}/runs/run-one/review",
        headers={"X-Test-Subject": "outsider"},
        json={
            "expected_revision": run["revision"],
            "decision": "approve",
            "rationale_code": "evidence_sufficient",
        },
    )
    assert denied.status_code == 403
    assert "not enrolled" in denied.json()["detail"]

    first = client.post(
        f"{API}/runs/run-one/review",
        headers={"X-Test-Subject": "reviewer-a"},
        json={
            "expected_revision": run["revision"],
            "decision": "approve",
            "rationale_code": "evidence_sufficient",
        },
    )
    assert first.status_code == 200
    assert first.json()["state"] == "pending_review"
    second = client.post(
        f"{API}/runs/run-one/review",
        headers={"X-Test-Subject": "reviewer-b"},
        json={
            "expected_revision": first.json()["revision"],
            "decision": "approve",
            "rationale_code": "policy_threshold",
        },
    )
    assert second.status_code == 200
    assert second.json()["state"] == "approved"


def test_execution_uses_server_dispatcher_and_integrity_is_queryable(environment):
    client, state = environment
    run = _publish_and_analyze(client)
    for subject, rationale in (
        ("reviewer-a", "evidence_sufficient"),
        ("reviewer-b", "policy_threshold"),
    ):
        response = client.post(
            f"{API}/runs/run-one/review",
            headers={"X-Test-Subject": subject},
            json={
                "expected_revision": run["revision"],
                "decision": "approve",
                "rationale_code": rationale,
            },
        )
        assert response.status_code == 200, response.text
        run = response.json()
    executed = client.post(
        f"{API}/runs/run-one/execute",
        json={"expected_revision": run["revision"]},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["state"] == "executed"
    assert executed.json()["execution_outcome"]["outcome"] == "no_change"
    assert len(state["dispatch_tokens"]) == 1

    integrity = client.get(f"{API}/integrity")
    assert integrity.status_code == 200
    assert integrity.json()["valid"] is True
    assert integrity.json()["verified_runs"] == 1


def test_tenant_isolation_and_no_raw_context_field(environment):
    client, state = environment
    _publish_and_analyze(client)
    state["tenant"] = "tenant-b"
    assert client.get(f"{API}/runs/run-one").status_code == 404

    state["tenant"] = "tenant-a"
    body = _analysis_body()
    body["context"][0]["content"] = "forged raw bytes"
    assert client.put(f"{API}/runs/run-two/analysis", json=body).status_code == 422
