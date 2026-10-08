"""Real native API/builder boundaries with synthetic audit and identity inputs."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from maverick import audit, config, world_model
from maverick.flow import store
from maverick.intent_value import opportunities, service
from maverick.oidc import VerifiedPrincipal
from maverick_dashboard import api, auth
from maverick_dashboard import app as app_module

BASE = "/api/v1/intent-value"


def headers(user="alice"):
    return {"Authorization": "Bearer " + user, "Origin": "http://testserver"}


@pytest.fixture
def context(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text('[value]\nhourly_rate=60.0\n[flows]\nenable=true\n[audit]\nsign=false\n[dashboard]\nadmins=["user:root"]\n', encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(path))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_ADMINS", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    api._world_cache.clear()
    app_module._world_cache.clear()
    config.reset_config_cache()
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_oidc_token", lambda token, **kwargs: VerifiedPrincipal(
        sub=token, issuer="https://issuer.test", audience="maverick", claims={"sub": token}))
    with world_model.WorldModel(tmp_path / "world.db") as world:
        yield TestClient(app_module.app), world
    config.reset_config_cache()


def seed(world, *, owner="user:alice", count=1, performed=True):
    ids = []
    for _ in range(count):
        gid = world.create_goal("Draft an RFP with CONFIDENTIAL-EMPLOYEE-MATERIAL", owner=owner)
        eid = world.start_episode(gid)
        world.end_episode(eid, "PRIVATE-RESULT", "success" if performed else "failed", cost_dollars=2)
        if performed:
            world.set_goal_status(gid, "done")
            audit.audit_event("goal_end", goal_id=gid, agent="orchestrator", status="succeeded")
        ids.append(gid)
    return ids


def get(client, path="", user="alice"):
    response = client.get(BASE + path, headers=headers(user))
    assert response.status_code == 200, response.text
    return response.json()


def test_company_metadata_has_no_employee_content_or_mutation_authority(context):
    client, world = context
    seed(world)
    own = get(client)
    assert own["totals"]["performed_work"] == 1
    identifier = own["rows"][0]["id"]
    assert get(client, user="bob")["totals"]["performed_work"] == 0
    assert client.get(BASE + "/work/" + identifier, headers=headers("bob")).status_code == 404
    company = get(client, "?scope=company", user="root")
    detail = get(client, "/work/" + identifier + "?scope=company", user="root")
    assert company["totals"] == own["totals"]
    assert not detail["can_correct"] and not detail["sources"]
    assert "CONFIDENTIAL" not in str(company) + str(detail)
    assert "PRIVATE-RESULT" not in str(company) + str(detail)
    denied = client.post(BASE + "/work/" + identifier + "/correct", headers=headers("root"),
        json={"revision": detail["revision"], "intents": ["rfp.review"], "reason": "not my work"})
    assert denied.status_code == 404


def test_correction_cost_explanation_and_default_confirmation(context):
    client, world = context
    seed(world)
    row = get(client)["rows"][0]
    detail = get(client, "/work/" + row["id"])
    assert not detail["confirmation_enabled"]
    assert len(detail["evidence"]) == 1 and detail["costs"][0]["amount"] == 2
    result = client.post(BASE + "/work/" + row["id"] + "/correct", headers=headers(),
        json={"revision": detail["revision"], "intents": ["rfp.review"], "size": "standard", "reason": "Existing RFP review"})
    assert result.status_code == 200, result.text
    updated = get(client)["rows"][0]
    assert updated["gross"] < row["gross"]
    assert len(get(client, "/work/" + row["id"])["estimates"]) == 2
    assert client.post(BASE + "/work/" + row["id"] + "/confirm", headers=headers(), json={"revision": updated["revision"]}).status_code == 422


def test_metric_copilot_matches_dashboard_and_retains_followup_scope(context):
    client, world = context
    seed(world)
    expected = get(client)["totals"]
    response = client.post("/api/v1/copilot", headers=headers(), json={"message": "What is Lightwork delivering across our company?", "page": "/work-value"})
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["metrics"]["totals"] == expected
    assert "benchmark" in answer["reply"].lower()
    next_response = client.post("/api/v1/copilot", headers=headers(), json={"message": "What about last month?", "page": "/work-value", "conversation_token": answer["conversation_token"]})
    assert next_response.status_code == 200, next_response.text
    assert next_response.json()["metrics"]["scope"] == "company"
    assert next_response.json()["metrics"]["totals"]["performed_work"] == 0
    replay = client.post("/api/v1/copilot", headers=headers("bob"), json={"message": "Why?", "page": "/work-value", "conversation_token": answer["conversation_token"]})
    assert replay.status_code == 409


def test_build_is_native_idempotent_draft_with_retained_manual_steps(context):
    client, world = context
    seed(world, count=3)
    proposal = get(client, "/opportunities")["opportunities"][0]
    assert proposal["current_evidence"]["draft_supported"]
    choice = client.post(BASE + "/opportunities/" + proposal["id"] + "/decision", headers=headers(),
        json={"revision": proposal["revision"], "decision": "adjust", "manual_steps": ["inputs", "prepare", "review"]})
    assert choice.status_code == 200, choice.text
    body = {"revision": choice.json()["revision"]}
    first = client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers(), json=body)
    assert first.status_code == 200, first.text
    second = client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers(), json=body)
    assert second.status_code == 200, second.text
    assert first.json()["draft_id"] == second.json()["draft_id"]
    flow = store.load_flow(first.json()["draft_id"])
    assert flow.owner == "user:alice" and not flow.schedule
    assert all(node.kind == "approval" for node in flow.nodes.values())
    assert store.load_published_bundle(flow.id) is None
    assert list(store.iter_run_metadata()) == []
    assert len(store.list_flows()) == 1
    assert get(client)["totals"]["performed_work"] == 3


def test_interrupted_native_save_resumes_same_reserved_draft(context, monkeypatch):
    client, world = context
    seed(world, count=3)
    proposal = get(client, "/opportunities")["opportunities"][0]
    real = api.save_flow_endpoint
    calls = []
    def interrupted(*args, **kwargs):
        saved = real(*args, **kwargs)
        calls.append(saved)
        raise OSError("simulated interruption after native save")
    monkeypatch.setattr(api, "save_flow_endpoint", interrupted)
    path = BASE + "/opportunities/" + proposal["id"] + "/build"
    assert client.post(path, headers=headers(), json={"revision": proposal["revision"]}).status_code == 503
    monkeypatch.setattr(api, "save_flow_endpoint", real)
    retry = client.post(path, headers=headers(), json={"revision": proposal["revision"]})
    assert retry.status_code == 200, retry.text
    assert len(calls) == len(store.list_flows()) == 1


def test_decline_suppresses_unchanged_proposal_and_cross_owner_build(context):
    client, world = context
    seed(world, count=3)
    proposal = get(client, "/opportunities")["opportunities"][0]
    response = client.post(BASE + "/opportunities/" + proposal["id"] + "/decision", headers=headers(),
        json={"revision": proposal["revision"], "decision": "decline"})
    assert response.status_code == 200
    assert get(client, "/opportunities")["opportunities"] == []
    assert get(client, "/opportunities?include_decided=true")["opportunities"][0]["status"] == "declined"
    assert client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers("bob"),
        json={"revision": proposal["revision"]}).status_code == 404


def test_unsupported_or_deleted_evidence_cannot_build(context):
    client, world = context
    seed(world, count=3)
    proposal = get(client, "/opportunities")["opportunities"][0]
    with world._writing() as conn:
        conn.execute("DELETE FROM episodes")
        conn.execute("DELETE FROM goals")
    response = client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers(), json={"revision": proposal["revision"]})
    assert response.status_code == 409
    assert get(client)["totals"]["performed_work"] == 0


def test_native_outcome_requires_work_receipt_and_preserves_forecast(context):
    client, world = context
    seed(world, count=3)
    proposal = get(client, "/opportunities")["opportunities"][0]
    response = client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers(), json={"revision": proposal["revision"]})
    assert response.status_code == 200, response.text
    current = opportunities.RECOMMENDATIONS.get(proposal["id"])
    flow = store.load_flow(current["draft_id"])
    publish = client.post("/api/v1/flows/" + flow.id + "/publish", headers=headers(),
        json={"version": flow.version, "revision": flow.revision})
    assert publish.status_code == 200, publish.text
    assert opportunities.outcome(current, service.refresh(world))["state"] == "adopted"
    from maverick.flow.store import FlowRun
    run = FlowRun(run_id="value-outcome", flow_id=flow.id, owner=flow.owner,
        status="completed", created=time.time(), updated=time.time(), definition_revision=flow.revision)
    store.save_run(run)
    assert opportunities.outcome(current, service.refresh(world))["observed_runs"] == 0
    audit.audit_event("control_plane_action", agent="flow", action="intent_value.flow_state", run_id=run.run_id,
        status="completed", definition_revision=flow.revision, release_id="", performed_work_nodes=1)
    outcome = opportunities.outcome(current, service.refresh(world))
    assert outcome["observed_runs"] == 1
    assert opportunities.RECOMMENDATIONS.get(proposal["id"])["forecast"] == proposal["forecast"]
    assert get(client)["totals"]["performed_work"] == 4


def test_cross_tenant_metric_evidence_and_recommendation_boundaries(context, monkeypatch):
    from maverick.paths import tenant_scope
    client, _world = context
    monkeypatch.setenv("MAVERICK_TENANT_BY_USER", "1")
    with tenant_scope(tenant="api:user:alice"):
        seed(api._world(), count=3)
    alice = get(client)
    assert alice["totals"]["performed_work"] == 3
    proposal = get(client, "/opportunities")["opportunities"][0]
    assert get(client, "?scope=company", user="bob")["totals"]["performed_work"] == 0
    work_id = alice["rows"][0]["id"]
    assert client.get(BASE + "/work/" + work_id + "?scope=company", headers=headers("bob")).status_code == 404
    assert client.post(BASE + "/opportunities/" + proposal["id"] + "/build", headers=headers("bob"),
        json={"revision": proposal["revision"]}).status_code == 404
    answer = client.post("/api/v1/copilot", headers=headers("bob"), json={"message": "What is Lightwork delivering across our company?", "page": "/work-value"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["metrics"]["totals"]["performed_work"] == 0


def test_source_failure_is_unavailable_not_zero(context, monkeypatch):
    from maverick.intent_value import sources
    client, world = context
    seed(world)
    get(client)
    def unavailable():
        raise OSError("synthetic unreadable audit history")
    monkeypatch.setattr(sources, "audit_population", unavailable)
    response = client.get(BASE, headers=headers())
    assert response.status_code == 503 and "not zero" in response.text
    response = client.post("/api/v1/copilot", headers=headers(), json={"message": "What value delivered this month?", "page": "/work-value"})
    assert response.status_code == 503


def test_existing_chat_handoff_links_execution_and_converts_potential_once(context):
    from maverick_dashboard import intent_value_handoff
    from starlette.requests import Request
    client, world = context
    seed(world, performed=False)
    before = get(client)
    proposal = get(client, "/opportunities")["opportunities"][0]
    selected = client.post(BASE + "/opportunities/" + proposal["id"] + "/use", headers=headers(), json={"revision": proposal["revision"]})
    assert selected.status_code == 200, selected.text
    assert "value_opportunity=" in selected.json()["href"]
    assert get(client)["totals"]["performed_work"] == 0
    request = Request({"type": "http", "method": "POST", "headers": [(b"authorization", b"Bearer alice")],
                       "path": "/chat/send", "query_string": b""})
    request.state.principal = VerifiedPrincipal(sub="alice", issuer="https://issuer.test", audience="maverick", claims={"sub": "alice"})
    gid = seed(world)[0]
    intent_value_handoff.bind(request, proposal["id"], world, gid)
    intent_value_handoff.bind(request, proposal["id"], world, gid)
    result = get(client)
    assert result["totals"]["performed_work"] == 1
    assert result["totals"]["opportunity_value"] == 0 < before["totals"]["opportunity_value"]
    converted = next(row for row in result["rows"] if row["converted_to"])
    explanation = get(client, "/work/" + converted["id"])
    assert explanation["converted_to"] == converted["converted_to"]
    assert opportunities.RECOMMENDATIONS.get(proposal["id"])["linked_goal_ids"] == [gid]
    current = get(client, "/opportunities")["opportunities"][0]
    assert current["outcome"]["observed_runs"] == 1
    assert current["next_review_reference"]["sample_count"] == 1


def test_copilot_native_read_and_reviewed_correction(context, monkeypatch):
    from maverick_dashboard import product_operations as operations
    client, world = context
    seed(world)
    monkeypatch.setattr(operations, "_gate", lambda request: auth.require_permission(request, "operate"))
    base = "/api/v1/copilot/product-operations"
    context_body = {"page": "/work-value", "selection": {}}
    response = client.post(base + "/catalog", headers=headers(), json=context_body)
    assert response.status_code == 200, response.text
    catalog = response.json()
    correction = next(row for row in catalog["operations"] if row["id"] == "learning.work_value_correct")
    row = get(client)["rows"][0]
    response = client.post(base + "/prepare", headers=headers(), json={**context_body,
        "context": catalog["context"], "operation": correction["id"], "revision": correction["revision"],
        "arguments": {"identifier": row["id"], "intents": "rfp.review", "size": "standard", "reason": "Actual requested review"}})
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert get(client)["rows"][0]["intents"] == ["rfp.draft"]
    response = client.post(base + "/execute", headers=headers(), json={"token": prepared["token"], "confirmed": True})
    assert response.status_code == 200, response.text
    assert get(client)["rows"][0]["intents"] == ["rfp.review"]
