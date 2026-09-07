"""Frozen samples, private observations, and honest denominators."""
import copy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from maverick import audit, config, world_model
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict
from maverick_dashboard import outcome_pilot_sources as sources
from maverick_dashboard import outcome_pilots as pilots
from maverick_dashboard import outcome_pilots_api as api
from maverick_dashboard.app import app


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit, "record", lambda *a, **k: True)
    monkeypatch.setattr(audit, "record_global", lambda *a, **k: True)
    monkeypatch.setattr(config, "get_features", lambda: {"outcome_measurement": True})
    monkeypatch.setattr(config, "config_source_errors", lambda **k: [])
    config.reset_config_cache()
    yield
    config.reset_config_cache()


def plan():
    return {"title": "Vendor evidence pilot", "customer": "Example team", "workflow": "Review evidence",
        "criteria": "Complete citations and correct decision", "operation_id": "frozen-pilot-operation",
        "cases": [{"kind": "goal", "reference": str(i), "label": "Case " + str(i)} for i in range(1, 5)]}


@pytest.fixture
def records():
    return {str(i): {"unstarted": True, "input_version": "input-" + str(i), "version": "a" * 64,
        "finished": False, "accepted": False, "first_started_at": None, "sample": False,
        "title": "Private case " + str(i), "runtime_cost_usd": None} for i in range(1, 5)}


def create(records):
    return pilots.create(plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])


def observation(revision, **kwargs):
    return api.ObservationIn(expected_revision=revision, operation_id="first-observation-operation",
        source_version="b" * 64, baseline_minutes=90., ai_hands_on_minutes=20., review_minutes=10.,
        rework_minutes=5., corrections=1, evidence="Observed timing sheet, row 1", note="Same case and scope.",
        **kwargs).model_dump()


def test_plan_freezes_groups_and_exact_retries_do_not_reallocate(records):
    record = create(records)
    assert sum(c["allocation"] == "holdout" for c in record["cases"]) == 2
    assert create(records)["plan_version"] == record["plan_version"]
    changed = plan()
    changed["criteria"] = "easier criteria after viewing results"
    with pytest.raises(RecordConflict):
        pilots.create(changed, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert pilots.get(record["id"], "alice")["criteria"] == plan()["criteria"]


def protected_plan():
    return {**plan(), "learning_mode": "exclude_local_learning",
            "prior_exposure_statement": "Fresh task inputs checked against the local corpus; provider prior exposure remains unverified."}


def test_protected_plan_binds_exclusions_to_only_frozen_holdout_goals(records):
    from maverick import pilot_learning
    record = pilots.create(protected_plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert record["state"] == "frozen"
    for case in record["cases"]:
        assert pilot_learning.excluded(int(case["reference"])) is (case["allocation"] == "holdout")
    report = pilots.report(record, resolve=lambda k, r: records[r])
    assert report["learning_policy"]["automatic_local_exclusion_enforced"] is True
    assert report["holdout_exposure_verified"] is False
    assert report["learning_policy"]["provider_training_verified"] is False
    held = next(case for case in record["cases"] if case["allocation"] == "holdout")
    records[held["reference"]]["input_version"] = "changed"
    assert not pilots.report(record, resolve=lambda k, r: records[r])["learning_policy"]["automatic_local_exclusion_enforced"]


def test_protected_preparation_resumes_exact_plan_after_interrupted_policy_publication(records, monkeypatch):
    from maverick import pilot_learning
    original = pilot_learning.protect
    failed = []
    def interrupt(*args, **kwargs):
        saved = original(*args, **kwargs)
        if not failed:
            failed.append(True)
            raise OSError("synthetic interruption after exclusion publication")
        return saved
    monkeypatch.setattr(pilot_learning, "protect", interrupt)
    with pytest.raises(OSError):
        pilots.create(protected_plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    pending = pilots.STORE.list()[0]
    assert pending["state"] == "preparing"
    assert not pilots.report(pending, resolve=lambda k, r: records[r])["learning_policy"]["automatic_local_exclusion_enforced"]
    resumed = pilots.create(protected_plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert resumed["state"] == "frozen" and resumed["plan_version"] == pending["plan_version"]
    assert [case["allocation"] for case in resumed["cases"]] == [case["allocation"] for case in pending["cases"]]


@pytest.mark.parametrize("change,actor", [({"prior_exposure_statement": ""}, "user:alice"), ({}, "local:dashboard"),
    ({"cases": [{"kind": "vendor", "reference": str(i), "label": "Vendor"} for i in range(4)]}, "user:alice")])
def test_unsupported_protected_plan_cannot_claim_local_exclusion(records, change, actor):
    with pytest.raises(ValueError):
        pilots.create({**protected_plan(), **change}, context="alice", actor=actor, resolve=lambda k, r: records[r])
    assert pilots.listing("alice")["items"] == []


def test_independent_policy_review_requires_different_named_actor_current_evidence_and_confirmation(records):
    record = pilots.create(protected_plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    report = pilots.report(record, resolve=lambda k, r: records[r])
    body = {"expected_revision": record["revision"], "operation_id": "independent-review-operation",
            "evidence_version": report["learning_policy"]["evidence_version"], "decision": "accepted_local_scope",
            "notes": "Checked frozen task exclusions and the owner's prior-exposure limits.", "confirm": True}
    with pytest.raises(PermissionError):
        pilots.independent_review(record["id"], body, actor="user:alice", resolve=lambda k, r: records[r])
    with pytest.raises(ValueError, match="confirm"):
        pilots.independent_review(record["id"], {**body, "confirm": False}, actor="user:bob", resolve=lambda k, r: records[r])
    saved = pilots.independent_review(record["id"], body, actor="user:bob", resolve=lambda k, r: records[r])
    assert pilots.report(saved, resolve=lambda k, r: records[r])["learning_policy"]["independent_review"]["current"]
    assert pilots.independent_review(record["id"], body, actor="user:bob", resolve=lambda k, r: records[r])["revision"] == saved["revision"]
    records["1"]["version"] = "changed-source"
    assert not pilots.report(saved, resolve=lambda k, r: records[r])["learning_policy"]["independent_review"]["current"]
    with pytest.raises(RecordConflict):
        pilots.independent_review(record["id"], {**body, "operation_id": "different-review-operation", "expected_revision": saved["revision"]}, actor="user:bob", resolve=lambda k, r: records[r])


def test_independent_review_api_requires_global_admin_and_reauthorizes_every_source(records, monkeypatch):
    from fastapi import HTTPException
    record = pilots.create(protected_plan(), context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    monkeypatch.setattr(api, "context_for", lambda request: "bob")
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:bob")
    monkeypatch.setattr(api, "require_permission", lambda *a: None)
    monkeypatch.setattr(api, "_resolver", lambda request: lambda k, r: records[r])
    monkeypatch.setattr(sources, "resolve", lambda request, k, r: records[r])
    route = "/api/v1/outcome-pilots/independent-review/" + record["id"]
    client = TestClient(app, headers={"Origin": "http://testserver", "X-Lightwork-Pilot-Context": "bob"})
    def denied(*_a):
        raise HTTPException(403, "Global administrator required")
    monkeypatch.setattr(api, "require_global_permission", denied)
    assert client.get(route).status_code == 403
    monkeypatch.setattr(api, "require_global_permission", lambda *a: None)
    packet = client.get(route)
    assert packet.status_code == 200 and packet.json()["context"] == "bob"
    body = {"expected_revision": record["revision"], "operation_id": "actual-api-review-operation",
            "evidence_version": packet.json()["learning_policy"]["evidence_version"], "decision": "accepted_local_scope",
            "notes": "Independent local policy evidence checked; provider limitations retained.", "confirm": True}
    assert client.post(route, json=body).status_code == 200
    def source_revoked(*_a):
        raise PermissionError("Revoked source")
    monkeypatch.setattr(sources, "resolve", source_revoked)
    assert client.get(route).status_code == 403
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
    assert client.get(route).status_code == 403


@pytest.mark.parametrize("problem", ["started", "duplicate"])
def test_plan_refuses_post_outcome_selection_or_repeated_case(records, problem):
    body = plan()
    if problem == "started":
        records["1"]["unstarted"] = False
    else:
        body["cases"][1] = body["cases"][0]
    with pytest.raises(ValueError):
        pilots.create(body, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert pilots.listing("alice")["items"] == []


def test_rejected_and_missing_cases_stay_in_denominator_cost_not_invented(records):
    record = create(records)
    records["1"].update(finished=True, accepted=False, version="b" * 64)
    saved = pilots.observe(record["id"], "1", observation(record["revision"]),
        context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    result = pilots.report(saved, resolve=lambda k, r: records[r])
    assert result["groups"]["all"]["planned"] == 4
    assert result["groups"]["all"]["paired"] == 1
    assert result["groups"]["all"]["accepted"] == 0
    assert result["groups"]["all"]["reported_minutes_difference"] == 55
    assert result["groups"]["all"]["reported_cost_difference"] is None
    assert result["provider_invoice_verified"] is False
    assert result["holdout_exposure_verified"] is False
    records["1"]["version"] = "c" * 64
    assert pilots.report(saved, resolve=lambda k, r: records[r])["cases"][0]["status"] == "stale"


def test_observation_cas_and_retry_identity_preserve_history(records):
    record = create(records)
    records["1"].update(finished=True, version="b" * 64)
    body = observation(record["revision"])
    saved = pilots.observe(record["id"], "1", body, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert pilots.observe(record["id"], "1", body, context="alice", actor="user:alice", resolve=lambda k, r: records[r])["revision"] == saved["revision"]
    with pytest.raises(RecordConflict):
        pilots.observe(record["id"], "1", {**body, "note": "Different measurement"}, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    with pytest.raises(RecordConflict):
        pilots.observe(record["id"], "1", {**body, "operation_id": "new-observation-operation"}, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    revised = pilots.observe(record["id"], "1", {**body, "operation_id": "new-observation-operation", "expected_revision": saved["revision"], "review_minutes": 20.},
        context="alice", actor="user:alice", resolve=lambda k, r: records[r])
    assert revised["cases"][0]["history"][0]["review_minutes"] == 10
    assert revised["cases"][0]["measurement"]["review_minutes"] == 20


def test_account_tenant_and_revoked_source_never_expose_observations(records):
    record = create(records)
    with pytest.raises(LookupError):
        pilots.get(record["id"], "bob")
    assert not pilots.listing("bob")["items"]
    with tenant_scope(tenant="other-tenant"):
        with pytest.raises(LookupError):
            pilots.get(record["id"], "alice")
    def revoked(*args):
        raise PermissionError("Private customer data")
    result = pilots.report(record, resolve=revoked)
    assert result["cases"][0] == {"id": "1", "allocation": record["cases"][0]["allocation"], "status": "unavailable"}
    assert result["groups"]["all"]["unavailable"] == 4
    assert "Private customer data" not in str(result)


@pytest.mark.parametrize("change,expected", [({"input_version": "changed"}, "inputs_changed"),
    ({"first_started_at": 1}, "started_before_plan"), ({"sample": True}, "sample")])
def test_invalid_pairs_do_not_enter_comparison(records, change, expected):
    record = create(records)
    records["1"].update(change)
    result = pilots.report(record, resolve=lambda k, r: records[r])
    assert result["cases"][0]["status"] == expected
    assert result["groups"]["all"]["paired"] == 0


@pytest.mark.parametrize("values", [{"baseline_minutes": float("nan")}, {"corrections": 1.5},
    {"baseline_cost": "100"}, {"ai_total_cost": "-1", "currency": "USD"}, {"baseline_cost": "1e8", "currency": "USD"}])
def test_invalid_or_ambiguous_measurements_are_rejected(values):
    body = observation(1)
    body.update(values)
    with pytest.raises(ValueError):
        api.ObservationIn.model_validate(body)


def test_api_context_validation_and_rendered_page(records, monkeypatch):
    monkeypatch.setattr(api, "context_for", lambda request: "alice")
    monkeypatch.setattr(api, "require_permission", lambda *a: None)
    monkeypatch.setattr(api, "_resolver", lambda request: lambda k, r: records[r])
    client = TestClient(app, headers={"Origin": "http://testserver"})
    assert client.get("/outcome-pilots").status_code == 200
    assert client.post("/api/v1/outcome-pilots", json=plan()).status_code == 409
    response = client.post("/api/v1/outcome-pilots", json=plan(), headers={"X-Lightwork-Pilot-Context": "alice"})
    assert response.status_code == 200
    identifier = response.json()["id"]
    report = client.get("/api/v1/outcome-pilots/" + identifier)
    assert report.status_code == 200 and report.headers["Cache-Control"] == "private, no-store"
    assert report.json()["groups"]["all"]["planned"] == 4
    monkeypatch.setattr(api, "context_for", lambda request: "bob")
    assert client.get("/api/v1/outcome-pilots/" + identifier).status_code == 404
    assert client.get("/static/lightwork-outcome-pilots.js").status_code == 200


def test_goal_acceptance_requires_current_human_signoff_and_suite(monkeypatch):
    goal = SimpleNamespace(id=1, title="Private", description="Evidence", domain="privacy", result="Answer", status="done")
    decision = {"decision": "approved", "decided_by": "agent:reviewer"}
    world = SimpleNamespace(get_goal=lambda i: goal, list_episodes=lambda **k: [], signoff_for=lambda i: copy.deepcopy(decision))
    monkeypatch.setattr("maverick_dashboard.app._world", lambda: world)
    monkeypatch.setattr("maverick.domain.suite_for", lambda d: "privacy")
    monkeypatch.setattr(sources, "can_access_goal", lambda *a: True)
    monkeypatch.setattr(sources, "suite_allowed", lambda *a: True)
    monkeypatch.setattr(sources, "caller_principal", lambda *a: "user:alice")
    assert not sources.goal_source(None, "1")["accepted"]
    decision["decided_by"] = "user:bob"
    old = sources.goal_source(None, "1")
    assert old["accepted"]
    goal.result = "Revised result"
    decision.clear()
    assert not sources.goal_source(None, "1")["accepted"]
    assert sources.goal_source(None, "1")["version"] != old["version"]
    monkeypatch.setattr(sources, "suite_allowed", lambda *a: False)
    with pytest.raises(LookupError):
        sources.goal_source(None, "1")


def test_prepare_task_in_app_is_private_idempotent_and_does_not_run(monkeypatch):
    world = world_model.WorldModel(world_model.DEFAULT_DB)
    monkeypatch.setattr("maverick_dashboard.app._world", lambda: world)
    monkeypatch.setattr("maverick_dashboard.app._deliverable_specs", lambda: [{"domain": "finance_sox", "gate": "human", "suite": "finance"}])
    monkeypatch.setattr("maverick.domain.enabled_domains", lambda: ["finance_sox"])
    monkeypatch.setattr(sources, "suite_allowed", lambda *a: True)
    monkeypatch.setattr(sources, "caller_principal", lambda *a: "user:alice")
    monkeypatch.setattr(sources, "can_access_goal", lambda request, goal: goal.owner == "user:alice")
    body = {"operation_id": "prepare-task-reference", "title": "Assess controls", "description": "Review the supplied control evidence.", "domain": "finance_sox"}
    saved = sources.prepare_task(None, body, "alice")
    assert sources.prepare_task(None, body, "alice")["id"] == saved["id"]
    goal = world.get_goal(saved["id"])
    assert goal.owner == "user:alice"
    assert goal.status == "blocked" and goal.result == sources.PREPARED
    assert world.list_episodes(goal_id=goal.id) == []
    source = sources.goal_source(None, str(goal.id))
    assert source["unstarted"] and not source["finished"] and not source["accepted"]
    with pytest.raises(ValueError, match="different task inputs"):
        sources.prepare_task(None, {**body, "description": "Changed after an uncertain response"}, "alice")
    assert len(world.list_goals()) == 1
    monkeypatch.setattr(sources, "suite_allowed", lambda *a: False)
    with pytest.raises(PermissionError):
        sources.prepare_task(None, body, "alice")


def test_total_cost_per_accepted_includes_rejected_cases_and_requires_complete_coverage(records):
    record = create(records)
    for number in range(1, 5):
        records[str(number)].update(finished=True, accepted=number == 1, version="b" * 64)
        body = observation(record["revision"], baseline_cost="100", ai_total_cost="25", currency="USD", criteria_met=number == 1)
        body["operation_id"] = "observation-operation-" + str(number)
        record = pilots.observe(record["id"], str(number), body, context="alice", actor="user:alice", resolve=lambda k, r: records[r])
        group = pilots.report(record, resolve=lambda k, r: records[r])["groups"]["all"]
        if number < 4:
            assert group["reported_ai_cost_per_accepted"] is None
    assert group["accepted"] == 1 and group["criteria_met"] == 1
    assert group["reported_ai_cost_per_accepted"] == "100"
    assert group["reported_cost_difference"] == "300"
    assert group["acceptance_rate"] == .25
