from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from maverick import ai_runtime_guardrails as runtime
from maverick import external_agents
from maverick import model_risk_assurance as assurance
from maverick.external_gateway_runtime_pilot import (
    READ_RESULT_DIGEST_CONTRACT,
    ExternalGatewayPilotClient,
    FaultInjectingPilotTransport,
    PilotAmbiguousEffect,
    PilotFaultRule,
    PilotHTTPResponse,
    PilotProtocolError,
    RuntimePolicyAcceptancePilot,
    RuntimePolicyPilotScenario,
    read_result_digest,
)
from maverick_dashboard.app import app

ASSET_ID = "MAA-" + ("4" * 32)
ASSET_DIGEST = "5" * 64
ALLOW_PROBE = "pilot-allow-private-canary"
DENY_PROBE = "pilot-deny-private-canary"


class _ASGITransport:
    def __init__(self, client: TestClient):
        self.client = client

    def request(
        self,
        method,
        url,
        *,
        headers,
        body,
        timeout,
        max_response_bytes,
    ):
        del timeout
        response = self.client.request(method, url, headers=dict(headers), content=body)
        assert len(response.content) <= max_response_bytes
        return PilotHTTPResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )


class _PilotConnector:
    def __init__(self, *, fail_after_change: bool = False):
        self.state = "baseline"
        self.writes = []
        self.fail_after_change = fail_after_change

    def read(self, params):
        assert params == {"path": "/state"}
        return json.dumps({"state": self.state}, sort_keys=True)

    def preview_write(self, params):
        return f"would {params['op'].upper()} pilot{params['path']}"

    def write(self, params):
        self.writes.append(dict(params))
        self.state = str(params["body"]["state"])
        if self.fail_after_change and self.state == "changed":
            raise TimeoutError("simulated response loss after effect")
        return "pilot state updated"


class _RuntimeService:
    @staticmethod
    def evaluate(request):
        decision = "block" if DENY_PROBE in request.content else "allow"
        return runtime.RuntimeGuardrailResult(
            decision=decision,
            redacted_content=None,
            receipt={
                "stage": str(request.stage),
                "decision": decision,
                "categories": ["policy"] if decision == "block" else [],
                "rule_ids": ["pilot/block"] if decision == "block" else [],
                "control_ids": [],
                "input_sha256": "6" * 64,
                "output_sha256": "7" * 64,
                "policy_sha256": "8" * 64,
                "evaluated_at": 1_800_000_000.0,
                "asset_binding": {
                    # Bound to the current workspace, as a native receipt is.
                    "tenant_ref": runtime._tenant_ref(),
                    "asset_id": ASSET_ID,
                    "asset_revision": 1,
                    "asset_version_digest": ASSET_DIGEST,
                },
            },
        )


class _BlockActionRuntimeService:
    @staticmethod
    def evaluate(request):
        result = _RuntimeService.evaluate(request)
        if str(request.stage) != "action":
            return result
        return runtime.RuntimeGuardrailResult(
            decision="block",
            redacted_content=None,
            receipt={
                **result.receipt,
                "decision": "block",
                "categories": ["policy"],
                "rule_ids": ["pilot/current-policy-block"],
            },
        )


def _stub_review_is_current(monkeypatch, assurance):
    """These stubbed records stand for a current reviewed declaration only
    while it is approved for the observed version. The full review binding
    (record digests, profile and hierarchy) is model_risk_assurance's own
    check, which hand-written stub records cannot satisfy."""
    monkeypatch.setattr(assurance._DEFAULT, "_declaration_is_current", lambda declaration, observation: (
        declaration.get("status") == "approved"
        and declaration.get("observed_version_digest") == observation.get("version_digest")))


@pytest.fixture
def pilot_estate(tmp_path, monkeypatch):
    from maverick import agent_trust, config, governed_rest, world_model
    from maverick_dashboard import external_gateway

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_EXTERNAL_AGENTS", "1")
    # Safeguard activity is retained per workspace; unbound mode refuses it.
    monkeypatch.setenv("MAVERICK_TENANT", "pilot-workspace")
    monkeypatch.setenv("MAVERICK_EXTERNAL_CONNECTORS", "pilot")
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(agent_trust, "managed_path", lambda: tmp_path / "agent_trust.json")
    monkeypatch.setattr(external_agents, "registry_path", lambda: tmp_path / "external_agents.json")
    monkeypatch.setattr(external_gateway, "_rate_times", {})
    (tmp_path / "config.toml").write_text(
        "[ai_runtime_guardrails]\nenable = true\n"
        "[ai_policy_management]\nenable = true\n",
        encoding="utf-8",
    )
    config.reset_config_cache()

    connector = _PilotConnector()
    monkeypatch.setitem(governed_rest.GOVERNED_REST_FACTORIES, "pilot", lambda: connector)
    monkeypatch.setattr(runtime, "enabled", lambda: True)
    monkeypatch.setattr(runtime, "default_service", _RuntimeService())
    monkeypatch.setattr(runtime, "dynamic_risk_receipt", lambda _result: None)
    monkeypatch.setattr(assurance, "enabled", lambda: True)
    _stub_review_is_current(monkeypatch, assurance)
    monkeypatch.setattr(
        assurance,
        "get_observation",
        lambda _asset_id: {
            "asset_id": ASSET_ID,
            "revision": 1,
            "version_digest": ASSET_DIGEST,
            "identity": {"asset_type": "agent"},
        },
    )
    monkeypatch.setattr(
        assurance,
        "get_declaration",
        lambda _asset_id: {
            "status": "approved",
            "observed_version_digest": ASSET_DIGEST,
            "risk_tier": "high",
            "risk_context": {},
        },
    )
    admin = TestClient(app, headers={"Origin": "http://testserver"})
    enrolled = admin.post(
        "/api/v1/external-agents",
        json={
            "id": "pilot-agent",
            "platform": "custom",
            "description": "runtime policy acceptance pilot",
            "owner": "pilot@corp.test",
            "department": "assurance",
            "max_risk": "high",
            "max_dollars": 5.0,
        },
    )
    assert enrolled.status_code == 201
    credential = admin.post(
        "/api/v1/external-agents/pilot-agent/credentials",
        json={"surface": "rest"},
    )
    assert credential.status_code == 200
    yield admin, connector, credential.json()["token"], governed_rest
    config.reset_config_cache()


def _scenario(prefix="acceptance"):
    return RuntimePolicyPilotScenario(
        asset_id=ASSET_ID,
        asset_revision=1,
        asset_version_digest=ASSET_DIGEST,
        allow_probe=ALLOW_PROBE,
        deny_probe=DENY_PROBE,
        read_request={"connector": "pilot", "op": "get", "path": "/state"},
        change_request={
            "connector": "pilot",
            "op": "patch",
            "path": "/state",
            "body": {"state": "changed", "private_marker": "raw-body-canary"},
        },
        compensation_request={
            "connector": "pilot",
            "op": "patch",
            "path": "/state",
            "body": {"state": "baseline", "private_marker": "rollback-canary"},
        },
        operation_prefix=prefix,
    )


def _approve(approval_id, _label):
    from maverick.world_model import open_world

    # The workspace's own world, where the gateway parked the approval.
    return open_world().decide_approval(
        approval_id,
        "approved",
        decided_by="pilot-operator@corp.test",
    )


def test_read_result_digest_is_typed_and_canonical():
    distinct_json_values = ({}, [], False, 0, None, "")
    digests = {read_result_digest(value) for value in distinct_json_values}

    assert len(digests) == len(distinct_json_values)
    assert read_result_digest({"a": 1, "b": {"c": [False, None]}}) == (
        read_result_digest({"b": {"c": [False, None]}, "a": 1})
    )


@pytest.mark.parametrize(
    "unsupported",
    (float("nan"), float("inf"), ("not", "json"), object(), {1: "bad-key"}),
)
def test_read_result_digest_rejects_non_json_and_non_finite_values(unsupported):
    with pytest.raises(PilotProtocolError):
        read_result_digest(unsupported)


def test_real_gateway_pilot_exercises_retry_ambiguity_idempotency_and_rollback(
    pilot_estate,
):
    admin, connector, token, _ = pilot_estate
    transport = FaultInjectingPilotTransport(
        _ASGITransport(TestClient(app)),
        (
            PilotFaultRule(
                name="evaluate-outage",
                method="POST",
                path_match="/api/v1/external/runtime/evaluate",
                timing="before",
            ),
            PilotFaultRule(
                name="commit-response-loss",
                method="POST",
                path_match="/commit",
                match_mode="suffix",
                timing="after",
            ),
        ),
    )
    client = ExternalGatewayPilotClient(
        origin="http://localhost",
        bearer_token=token,
        transport=transport,
        sleeper=lambda _seconds: None,
    )
    report = RuntimePolicyAcceptancePilot(
        client,
        approval_callback=_approve,
        sleeper=lambda _seconds: None,
    ).run(_scenario())

    assert report["summary"] == {
        "passed": 14,
        "failed": 0,
        "pending": 0,
        "indeterminate": 0,
    }, json.dumps(report, sort_keys=True)
    assert report["coverage"] == {
        "safe_retry_exercised": True,
        "ambiguous_commit_recovered": True,
        "exact_park_idempotency_exercised": True,
        "compensation_exercised": True,
    }
    assert report["read_result_digest_contract"] == READ_RESULT_DIGEST_CONTRACT
    assert transport.triggered == ["evaluate-outage", "commit-response-loss"]
    assert connector.state == "baseline"
    assert len(connector.writes) == 2
    assert [item["body"]["state"] for item in connector.writes] == [
        "changed",
        "baseline",
    ]
    rendered = json.dumps(report, sort_keys=True)
    for raw in (
        token,
        ALLOW_PROBE,
        DENY_PROBE,
        "raw-body-canary",
        "rollback-canary",
        "http://localhost",
    ):
        assert raw not in rendered
    assert admin.get("/api/v1/external-agents/pilot-agent").status_code == 200


def test_connector_outage_after_effect_stays_indeterminate_and_is_not_retried(
    pilot_estate,
    monkeypatch,
):
    _, old_connector, token, governed_rest = pilot_estate
    connector = _PilotConnector(fail_after_change=True)
    monkeypatch.setitem(governed_rest.GOVERNED_REST_FACTORIES, "pilot", lambda: connector)
    client = ExternalGatewayPilotClient(
        origin="http://localhost",
        bearer_token=token,
        transport=_ASGITransport(TestClient(app)),
        sleeper=lambda _seconds: None,
    )
    report = RuntimePolicyAcceptancePilot(
        client,
        approval_callback=_approve,
        sleeper=lambda _seconds: None,
    ).run(_scenario(prefix="connector-outage"))

    assert old_connector.writes == []
    assert connector.state == "changed", json.dumps(report, sort_keys=True)
    assert len(connector.writes) == 1
    assert report["summary"]["indeterminate"] == 1
    change = next(item for item in report["events"] if item["name"] == "change_commit")
    assert change["verdict"] == "indeterminate"
    # The gateway records an outage after the effect as indeterminate, not failed.
    assert change["observed"]["status"] == "indeterminate"
    assert not any(item["name"].startswith("compensation") for item in report["events"])


def test_commit_rebinds_current_runtime_policy_before_external_effect(
    pilot_estate,
    monkeypatch,
):
    _, connector, token, _ = pilot_estate
    bare = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "connector": "pilot",
        "op": "patch",
        "path": "/state",
        "body": {"state": "changed"},
        "detail": ALLOW_PROBE,
        "idempotency_key": "policy-rebind-change",
        "runtime_asset_id": ASSET_ID,
        "runtime_asset_revision": 1,
        "runtime_asset_version_digest": ASSET_DIGEST,
    }
    parked = bare.post(
        "/api/v1/external/execute", headers=headers, json=request,
    ).json()
    assert parked["rule"] == "approval_required"
    assert _approve(parked["approval_id"], "change") is True

    monkeypatch.setattr(runtime, "default_service", _BlockActionRuntimeService())
    commit_body = {
        key: request[key]
        for key in (
            "connector",
            "op",
            "path",
            "body",
            "detail",
            "runtime_asset_id",
            "runtime_asset_revision",
            "runtime_asset_version_digest",
        )
    }
    blocked = bare.post(
        f"/api/v1/external/executions/{parked['execution_id']}/commit",
        headers=headers,
        json=commit_body,
    ).json()
    assert blocked["allowed"] is False
    assert blocked["rule"] == "runtime_guardrail"
    assert blocked["status"] == "block"
    assert connector.writes == []

    # Rolling the test policy back to the previously accepted service allows
    # the same digest-bound commit; no new approval or effect was created by
    # the blocked attempt.
    monkeypatch.setattr(runtime, "default_service", _RuntimeService())
    committed = bare.post(
        f"/api/v1/external/executions/{parked['execution_id']}/commit",
        headers=headers,
        json=commit_body,
    ).json()
    assert committed["status"] == "executed"
    assert len(connector.writes) == 1


def test_commit_cannot_swap_the_runtime_context_that_was_approved(pilot_estate):
    _, connector, token, _ = pilot_estate
    bare = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    request = {
        "connector": "pilot",
        "op": "patch",
        "path": "/state",
        "body": {"state": "changed"},
        "detail": ALLOW_PROBE,
        "idempotency_key": "context-binding-change",
        "runtime_asset_id": ASSET_ID,
        "runtime_asset_revision": 1,
        "runtime_asset_version_digest": ASSET_DIGEST,
    }
    parked = bare.post(
        "/api/v1/external/execute", headers=headers, json=request,
    ).json()
    assert _approve(parked["approval_id"], "change") is True
    swapped = {
        key: request[key]
        for key in (
            "connector",
            "op",
            "path",
            "body",
            "detail",
            "runtime_asset_id",
            "runtime_asset_revision",
            "runtime_asset_version_digest",
        )
    }
    swapped["detail"] = ALLOW_PROBE + "-different-context"
    refused = bare.post(
        f"/api/v1/external/executions/{parked['execution_id']}/commit",
        headers=headers,
        json=swapped,
    ).json()
    assert refused["allowed"] is False
    assert refused["rule"] == "digest_mismatch"
    assert connector.writes == []


def test_pilot_stops_pending_without_impersonating_a_human_approver(pilot_estate):
    _, connector, token, _ = pilot_estate
    client = ExternalGatewayPilotClient(
        origin="http://localhost",
        bearer_token=token,
        transport=_ASGITransport(TestClient(app)),
        sleeper=lambda _seconds: None,
    )
    report = RuntimePolicyAcceptancePilot(client).run(
        _scenario(prefix="human-boundary")
    )

    assert report["summary"]["pending"] == 1
    assert connector.writes == []
    approval = next(item for item in report["events"] if item["name"] == "change_approval")
    assert approval["verdict"] == "pending"


def test_commit_and_status_outage_remains_ambiguous_without_blind_retry():
    class OutageTransport:
        def __init__(self):
            self.calls = []

        def request(self, method, url, **_kwargs):
            self.calls.append((method, url))
            raise ConnectionError("unavailable")

    transport = OutageTransport()
    client = ExternalGatewayPilotClient(
        origin="http://localhost",
        bearer_token="opaque-pilot-token",
        transport=transport,
        max_attempts=2,
        sleeper=lambda _seconds: None,
    )
    with pytest.raises(PilotAmbiguousEffect):
        client.commit(
            "execution-1",
            {"connector": "pilot", "op": "patch", "path": "/state", "body": {}},
        )

    commit_calls = [url for method, url in transport.calls if method == "POST"]
    status_calls = [url for method, url in transport.calls if method == "GET"]
    assert len(commit_calls) == 1
    assert len(status_calls) == 2


@pytest.mark.parametrize("execution_id", ("_leading-symbol", "-leading-symbol"))
def test_client_accepts_every_urlsafe_execution_id_the_gateway_can_generate(
    execution_id,
):
    class StatusTransport:
        def __init__(self):
            self.urls = []

        def request(self, method, url, **_kwargs):
            self.urls.append((method, url))
            payload = (
                {"execution_id": execution_id, "status": "executed"}
                if method == "POST"
                else {
                    "execution_id": execution_id,
                    "status": "pending",
                    "approval_status": "pending",
                }
            )
            return PilotHTTPResponse(
                status_code=200,
                headers={"content-type": "application/json"},
                body=json.dumps(payload).encode("utf-8"),
            )

    transport = StatusTransport()
    client = ExternalGatewayPilotClient(
        origin="http://localhost",
        bearer_token="opaque-pilot-token",
        transport=transport,
        sleeper=lambda _seconds: None,
    )

    status = client.execution_status(execution_id)
    committed = client.commit(
        execution_id,
        {"connector": "pilot", "op": "patch", "path": "/state", "body": {}},
    )

    assert status.value["execution_id"] == execution_id
    assert committed.value["status"] == "executed"
    assert transport.urls == [
        (
            "GET",
            f"http://localhost/api/v1/external/executions/{execution_id}",
        ),
        (
            "POST",
            f"http://localhost/api/v1/external/executions/{execution_id}/commit",
        ),
    ]
