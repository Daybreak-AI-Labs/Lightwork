from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient


def _client():
    from maverick_dashboard.app import app

    return TestClient(app, headers={"Origin": "http://testserver"})


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_FLOWS", "1")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model

    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _reset_queue(monkeypatch, automation_queue):
    monkeypatch.setattr(automation_queue, "_queue", None)
    monkeypatch.setattr(automation_queue, "_worker", None)
    monkeypatch.setattr(automation_queue, "_worker_threads", [])


def _drain(automation_queue):
    from maverick.worker import Worker

    worker = Worker(queue=automation_queue.queue())
    worker.register(automation_queue.FLOW_RUN_KIND, automation_queue._handle_flow_run)
    worker.register(automation_queue.FLOW_RESUME_KIND, automation_queue._handle_flow_resume)
    worker.drain()


FLOW = {
    "id": "saved-flow-test-inputs",
    "name": "Check example amount",
    "start": "review",
    "inputs": [{"key": "amount", "label": "Amount", "type": "number", "required": True}],
    "nodes": [{"id": "review", "kind": "agent", "brief": "Review amount {{amount}}"}],
}


def test_saved_input_examples_are_atomic_private_and_run_only_through_dry_run(
    monkeypatch, tmp_path
):
    _isolate(monkeypatch, tmp_path)
    from maverick import crypto_at_rest, llm
    from maverick.flow import store
    from maverick_dashboard import api, auth, flow_test_inputs, rbac
    from maverick_dashboard import automation_queue as aq

    _reset_queue(monkeypatch, aq)
    principal = ["user:flow-owner"]
    monkeypatch.setattr(auth, "caller_principal", lambda _request: principal[0])
    monkeypatch.setattr(api, "caller_principal", lambda _request: principal[0])
    rbac.set_role(principal[0], "admin")
    client = _client()
    created = client.post("/api/v1/flows", json={"flow": FLOW})
    assert created.status_code == 201, created.text

    path = "/api/v1/flows/saved-flow-test-inputs/test-inputs"
    empty = client.get(path)
    assert empty.status_code == 200, empty.text
    assert empty.json()["cases"] == []
    revision = empty.json()["case_set_revision"]
    first = {
        "identity": "1" * 32,
        "name": "Small amount",
        "data": {"amount": "125.50"},
        "expected_case_set_revision": revision,
    }
    saved = client.post(path, json=first)
    assert saved.status_code == 201, saved.text
    assert saved.json()["replayed"] is False
    assert saved.json()["case"]["data"] == {"amount": 125.5}
    saved_flow = store.load_flow("saved-flow-test-inputs")
    protected_path = flow_test_inputs._path(
        saved_flow.id, saved_flow.revision, saved_flow.owner
    )
    stored = protected_path.read_bytes()
    assert crypto_at_rest.is_bound(stored)
    assert b"125.5" not in stored
    replay = client.post(path, json=first)
    assert replay.status_code == 201, replay.text
    assert replay.json()["replayed"] is True
    assert len(client.get(path).json()["cases"]) == 1
    changed = {**first, "data": {"amount": "126"}}
    assert client.post(path, json=changed).status_code == 409
    stale = {**first, "identity": "2" * 32, "name": "Large amount"}
    assert client.post(path, json=stale).status_code == 409

    current = client.get(path).json()
    second = {
        "identity": "2" * 32,
        "name": "Large amount",
        "data": {"amount": "950.00"},
        "expected_case_set_revision": current["case_set_revision"],
    }
    assert client.post(path, json=second).status_code == 201
    bad = {
        "identity": "3" * 32,
        "name": "Invalid amount",
        "data": {"amount": "not a number"},
        "expected_case_set_revision": client.get(path).json()["case_set_revision"],
    }
    assert client.post(path, json=bad).status_code == 422
    cases = client.get(path).json()
    assert [row["name"] for row in cases["cases"]] == ["Small amount", "Large amount"]

    principal[0] = "user:other"
    rbac.set_role(principal[0], "admin")
    assert client.get(path).status_code == 404
    principal[0] = "user:flow-owner"

    def forbidden_provider_call(*_args, **_kwargs):
        raise AssertionError("safe workflow tests must not call a provider")

    monkeypatch.setattr(llm.LLM, "complete", forbidden_provider_call)
    flow = client.get("/api/v1/flows/saved-flow-test-inputs").json()
    request_body = {
        "flow": flow,
        "case_ids": [row["id"] for row in cases["cases"]],
        "case_set_revision": cases["case_set_revision"],
        "batch_id": "a" * 32,
        "expected_flow_version": flow["version"],
        "expected_flow_revision": flow["revision"],
    }
    run_url = "/api/v1/flows/saved-flow-test-inputs/test-inputs/run"
    stale_flow = {**request_body, "expected_flow_version": flow["version"] - 1}
    assert client.post(run_url, json=stale_flow).status_code == 409
    batch = client.post(run_url, json=request_body)
    assert batch.status_code == 200, batch.text
    first_results = batch.json()["results"]
    assert batch.json()["dry_run"] is True
    assert all(result["dry_run"] for result in first_results)
    assert len(first_results) == 2
    repeated = client.post(run_url, json=request_body)
    assert repeated.status_code == 200, repeated.text
    assert [row["run_id"] for row in repeated.json()["results"]] == [
        row["run_id"] for row in first_results
    ]
    assert all(row["deduplicated"] for row in repeated.json()["results"])

    _drain(aq)
    for result in first_results:
        run = store.load_run(result["run_id"])
        assert run is not None and run.dry_run and run.status == "completed"
        assert run.input_data["amount"] in {125.5, 950.0}
        assert run.nodes["review"]["status"] == "done"
    assert client.post(run_url, json={**request_body, "case_set_revision": "0" * 64}).status_code == 409

    damaged = bytearray(protected_path.read_bytes())
    damaged[-1] ^= 1
    protected_path.write_bytes(damaged)
    assert client.get(path).status_code == 503
    assert client.post(run_url, json=request_body).status_code == 503


def test_learned_workflow_examples_are_validated_against_the_exact_typed_input_shape(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import action_recipe, recipe_flow, store
    from maverick_dashboard import api, auth, rbac

    principal = ["user:learned-flow-owner"]
    monkeypatch.setattr(auth, "caller_principal", lambda _request: principal[0])
    monkeypatch.setattr(api, "caller_principal", lambda _request: principal[0])
    rbac.set_role(principal[0], "admin")
    episodes = [{"id": f"case-{value}", "inputs": {"amount": float(value)}, "steps": [
        {"tool": "record_case", "arguments": {"amount": float(value)}, "result": {}},
        {"tool": "finish_case", "arguments": {}, "result": {}},
    ]} for value in (1, 2)]
    artifact = action_recipe.synthesize(episodes, allowed_tools=["record_case", "finish_case"])
    learned = recipe_flow.make_flow(artifact, flow_id="learned-test-inputs", name="Learned inputs")
    learned.owner = principal[0]
    store.save_flow(learned)
    client = _client()

    path = "/api/v1/flows/learned-test-inputs/test-inputs"
    revision = client.get(path).json()["case_set_revision"]
    invalid = {"identity": "a" * 32, "name": "Wrong number type",
        "data": {"input": {"amount": 3}}, "expected_case_set_revision": revision}
    rejected = client.post(path, json=invalid)
    assert rejected.status_code == 422, rejected.text
    assert "saved fields" in rejected.json()["detail"]
    malformed = {**invalid, "identity": "b" * 32, "name": "Extra input",
        "data": {"input": {"amount": 3.0}, "amount": 3.0}}
    assert client.post(path, json=malformed).status_code == 422

    valid = {**invalid, "identity": "c" * 32, "name": "Whole-valued decimal",
        "data": {"input": {"amount": 3.0}}}
    saved = client.post(path, json=valid)
    assert saved.status_code == 201, saved.text
    assert type(saved.json()["case"]["data"]["input"]["amount"]) is float

    integer_episodes = [{"id": f"integer-{value}", "inputs": {"amount": value}, "steps": [
        {"tool": "record_case", "arguments": {"amount": value}, "result": {}},
        {"tool": "finish_case", "arguments": {}, "result": {}},
    ]} for value in (1, 2)]
    published_artifact = action_recipe.synthesize(
        integer_episodes, allowed_tools=["record_case", "finish_case"]
    )
    published_flow = recipe_flow.make_flow(
        published_artifact, flow_id="learned-test-inputs", name="Published integer inputs"
    )
    with pytest.raises(api.HTTPException) as incompatible:
        api._prepare_flow_test_inputs(
            store.load_flow("learned-test-inputs"), (published_flow, {}), [saved.json()["case"]]
        )
    assert incompatible.value.status_code == 422
    assert "published workflow" in incompatible.value.detail


def test_saved_examples_compare_the_exact_published_release_with_the_current_draft(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick.flow import store
    from maverick_dashboard import api, auth, rbac
    from maverick_dashboard import automation_queue as aq

    _reset_queue(monkeypatch, aq)
    principal = ["user:flow-owner"]
    monkeypatch.setattr(auth, "caller_principal", lambda _request: principal[0])
    monkeypatch.setattr(api, "caller_principal", lambda _request: principal[0])
    rbac.set_role(principal[0], "admin")
    client = _client()
    created = client.post("/api/v1/flows", json={"flow": FLOW})
    assert created.status_code == 201, created.text
    flow_id = FLOW["id"]
    flow_path = f"/api/v1/flows/{flow_id}"

    saved_flow = client.get(flow_path).json()
    publication = client.post(f"{flow_path}/publish", json={
        "version": saved_flow["version"], "revision": saved_flow["revision"],
    })
    assert publication.status_code == 200, publication.text
    release_id = publication.json()["published_revision"]
    assert release_id

    examples_path = f"{flow_path}/test-inputs"
    cases = client.get(examples_path).json()
    saved = client.post(examples_path, json={
        "identity": "a" * 32,
        "name": "Ordinary amount",
        "data": {"amount": 125},
        "expected_case_set_revision": cases["case_set_revision"],
    })
    assert saved.status_code == 201, saved.text
    cases = client.get(examples_path).json()

    draft = client.get(flow_path).json()
    draft["nodes"][0]["brief"] = "Review revised amount {{amount}}"
    body = {
        "flow": draft,
        "case_ids": [saved.json()["case"]["id"]],
        "case_set_revision": cases["case_set_revision"],
        "batch_id": "b" * 32,
        "expected_flow_version": draft["version"],
        "expected_flow_revision": draft["revision"],
        "compare_to_published": True,
        "expected_published_release_id": release_id,
    }
    run_url = f"{flow_path}/test-inputs/run"
    stale_release = {**body, "expected_published_release_id": "0" * len(release_id)}
    assert client.post(run_url, json=stale_release).status_code == 409

    response = client.post(run_url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["batch_id"] == body["batch_id"]
    result = response.json()["results"][0]
    assert result["dry_run"] is True
    assert result["published"]["dry_run"] is True
    assert result["draft"]["dry_run"] is True
    repeated = client.post(run_url, json=body)
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["results"][0]["published"]["run_id"] == result["published"]["run_id"]
    assert repeated.json()["results"][0]["draft"]["run_id"] == result["draft"]["run_id"]

    _drain(aq)
    published_run = store.load_run(result["published"]["run_id"])
    draft_run = store.load_run(result["draft"]["run_id"])
    assert published_run is not None and published_run.dry_run and published_run.status == "completed"
    assert draft_run is not None and draft_run.dry_run and draft_run.status == "completed"
    published_flow = store.load_flow_snapshot(published_run.definition_digest)
    draft_flow = store.load_flow_snapshot(draft_run.definition_digest)
    assert published_flow.nodes["review"].brief == "Review amount {{amount}}"
    assert draft_flow.nodes["review"].brief == "Review revised amount {{amount}}"

    outcomes_path = f"{flow_path}/test-inputs/outcomes"
    outcomes_response = client.post(outcomes_path, json={
        "batch_id": body["batch_id"],
        "pairs": [{
            "case_id": saved.json()["case"]["id"],
            "published_run_id": result["published"]["run_id"],
            "draft_run_id": result["draft"]["run_id"],
        }],
    })
    assert outcomes_response.status_code == 200, outcomes_response.text
    comparison = outcomes_response.json()["results"][0]
    assert comparison["statuses_match"] is True
    assert comparison["paths_match"] is True
    assert comparison["published"]["steps"][0]["label"] == "AI task"
    assert comparison["draft"]["steps"][0]["label"] == "AI task"
    assert "data" not in comparison["published"]
    assert "input_data" not in comparison["published"]
    assert "prompt" not in comparison["published"]

    wrong_batch = client.post(outcomes_path, json={
        "batch_id": "c" * 32,
        "pairs": [{
            "case_id": saved.json()["case"]["id"],
            "published_run_id": result["published"]["run_id"],
            "draft_run_id": result["draft"]["run_id"],
        }],
    })
    assert wrong_batch.status_code == 404

    principal[0] = "user:other-flow-owner"
    rbac.set_role(principal[0], "admin")
    other_owner = client.post(outcomes_path, json={
        "batch_id": body["batch_id"],
        "pairs": [{
            "case_id": saved.json()["case"]["id"],
            "published_run_id": result["published"]["run_id"],
            "draft_run_id": result["draft"]["run_id"],
        }],
    })
    assert other_owner.status_code in {403, 404}


def test_unbound_legacy_examples_are_not_exposed_to_a_new_generation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import crypto_at_rest
    from maverick_dashboard import flow_test_inputs

    flow_id = "legacy-flow-test-inputs"
    identity = "4" * 32
    name = "Prior example"
    data = {"amount": 45.5}
    now = time.time()
    fingerprint = hashlib.sha256(json.dumps(
        {"flow_id": flow_id, "name": name, "data": data},
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    prior = {"id": identity, "name": name, "data": data, "revision": 1,
             "created_at": now, "updated_at": now, "created_by": "user:legacy",
             "fingerprint": fingerprint}
    path = flow_test_inputs._legacy_path(flow_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "cases": {identity: prior}}), encoding="utf-8")

    generation = "revision-one"
    owner = "user:legacy"
    cases, revision = flow_test_inputs.case_snapshot(
        flow_id, flow_revision=generation, owner=owner
    )
    assert cases == []
    flow_test_inputs.save_case(
        flow_id,
        flow_revision=generation,
        owner=owner,
        identity="5" * 32,
        name="New example",
        data={"amount": 80.0},
        actor="user:legacy",
        expected_revision=revision,
    )
    current_path = flow_test_inputs._path(flow_id, generation, owner)
    sealed = current_path.read_bytes()
    assert crypto_at_rest.is_bound(sealed)
    assert b"80.0" not in sealed
    assert path.exists()

    other_id = "different-flow-test-inputs"
    other_path = flow_test_inputs._path(other_id, generation, owner)
    other_path.parent.mkdir(parents=True, exist_ok=True)
    other_path.write_bytes(sealed)
    with pytest.raises(flow_test_inputs.FlowTestInputsUnavailable):
        flow_test_inputs.case_snapshot(other_id, flow_revision=generation, owner=owner)
    flow_test_inputs.purge_flow_cases(flow_id, flow_revision=generation, owner=owner)
    assert not path.exists() and not current_path.exists()


def test_protection_failure_does_not_write_or_claim_a_saved_example(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import flow_test_inputs

    flow_id = "unavailable-encryption-flow"
    generation = "revision-one"
    owner = "user:owner"
    path = flow_test_inputs._path(flow_id, generation, owner)
    _, revision = flow_test_inputs.case_snapshot(
        flow_id, flow_revision=generation, owner=owner
    )
    recorded = []

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("protection key unavailable")

    monkeypatch.setattr(flow_test_inputs.crypto_at_rest, "seal_bound", unavailable)
    with pytest.raises(flow_test_inputs.FlowTestInputsUnavailable):
        flow_test_inputs.save_case(
            flow_id,
            flow_revision=generation,
            owner=owner,
            identity="6" * 32,
            name="Must stay unsaved",
            data={"amount": 12},
            actor="user:owner",
            expected_revision=revision,
            after_save=lambda: recorded.append("audit"),
        )
    assert recorded == []
    assert not path.exists()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_deleting_and_recreating_a_flow_never_reuses_another_owners_examples(
    monkeypatch, tmp_path, cleanup_fails
):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import api, auth, flow_test_inputs, rbac

    principal = ["user:first-owner"]
    monkeypatch.setattr(auth, "caller_principal", lambda _request: principal[0])
    monkeypatch.setattr(api, "caller_principal", lambda _request: principal[0])
    rbac.set_role(principal[0], "admin")
    client = _client()
    flow_path = f"/api/v1/flows/{FLOW['id']}"
    examples_path = flow_path + "/test-inputs"
    created = client.post("/api/v1/flows", json={"flow": FLOW})
    assert created.status_code == 201, created.text
    first_revision = created.json()["revision"]
    current = client.get(examples_path).json()
    saved = client.post(examples_path, json={
        "identity": "7" * 32,
        "name": "Private amount",
        "data": {"amount": 777},
        "expected_case_set_revision": current["case_set_revision"],
    })
    assert saved.status_code == 201, saved.text
    old_path = flow_test_inputs._path(FLOW["id"], first_revision, principal[0])
    assert old_path.exists()

    if cleanup_fails:
        def cannot_cleanup(*_args, **_kwargs):
            raise OSError("disk unavailable")

        monkeypatch.setattr(flow_test_inputs, "purge_flow_cases", cannot_cleanup)
    deleted = client.delete(flow_path)
    assert deleted.status_code == 204, deleted.text
    assert deleted.headers["X-Lightwork-Test-Examples"] == (
        "needs-review" if cleanup_fails else "cleared"
    )
    assert old_path.exists() is cleanup_fails

    principal[0] = "user:second-owner"
    rbac.set_role(principal[0], "admin")
    recreated = client.post("/api/v1/flows", json={"flow": FLOW})
    assert recreated.status_code == 201, recreated.text
    assert recreated.json()["revision"] != first_revision
    fresh = client.get(examples_path)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["cases"] == []
    assert fresh.json()["case_set_revision"] != current["case_set_revision"]
    assert client.post(examples_path, json={
        "identity": "8" * 32,
        "name": "Stale client",
        "data": {"amount": 8},
        "expected_case_set_revision": current["case_set_revision"],
    }).status_code == 409
    second = client.post(examples_path, json={
        "identity": "8" * 32,
        "name": "New amount",
        "data": {"amount": 8},
        "expected_case_set_revision": fresh.json()["case_set_revision"],
    })
    assert second.status_code == 201, second.text
    assert [row["name"] for row in client.get(examples_path).json()["cases"]] == ["New amount"]


def test_failed_example_write_has_no_success_audit_and_audit_failure_is_honest(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick_dashboard import flow_test_inputs

    flow_id, generation, owner = "audit-flow", "revision-one", "user:owner"
    _, revision = flow_test_inputs.case_snapshot(
        flow_id, flow_revision=generation, owner=owner
    )
    path = flow_test_inputs._path(flow_id, generation, owner)
    recorded = []

    def disk_failed(*_args, **_kwargs):
        raise OSError("disk failed")

    original_write = flow_test_inputs.atomic_write_bytes
    monkeypatch.setattr(flow_test_inputs, "atomic_write_bytes", disk_failed)
    with pytest.raises(OSError):
        flow_test_inputs.save_case(
            flow_id, flow_revision=generation, owner=owner, identity="9" * 32,
            name="Unwritten", data={"amount": 9}, actor=owner,
            expected_revision=revision, after_save=lambda: recorded.append("saved"),
        )
    assert recorded == [] and not path.exists()

    monkeypatch.setattr(flow_test_inputs, "atomic_write_bytes", original_write)

    def audit_failed():
        raise OSError("audit unavailable")

    with pytest.raises(flow_test_inputs.FlowTestInputsAuditUnconfirmed, match="changed"):
        flow_test_inputs.save_case(
            flow_id, flow_revision=generation, owner=owner, identity="9" * 32,
            name="Written", data={"amount": 9}, actor=owner,
            expected_revision=revision, after_save=audit_failed,
        )
    assert [row["name"] for row in flow_test_inputs.case_snapshot(
        flow_id, flow_revision=generation, owner=owner
    )[0]] == ["Written"]
