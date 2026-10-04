"""Copilot can hand a reviewed release to the native learned run form only."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_workflows as workflows

BASE = "/api/v1/copilot/product-operations"
PAGE = "/flows/designer/owned"
SELECTION = {"kind": "flow", "id": "owned"}


def _harness(monkeypatch):
    state = {"published": True, "learned": True, "release": "a" * 64, "allowed": True,
             "deleted": False, "pending_forms": 0, "calls": []}

    def draft(_request, flow_id):
        state["calls"].append("draft")
        if not state["allowed"] or state["deleted"] or flow_id != "owned":
            raise HTTPException(404, "no such flow")
        return {"id": flow_id, "version": 2, "revision": "draft-2", "published": state["published"]}

    def published(_request, flow_id):
        state["calls"].append("published")
        if not state["allowed"] or state["deleted"] or flow_id != "owned":
            raise HTTPException(404, "no such flow")
        if not state["published"]:
            raise HTTPException(409, "not published")
        return {"flow": {"id": flow_id, "name": "Order review",
                         "learned_recipe": {"schema": "lightwork.action-recipe.v1"} if state["learned"] else None},
                "release_id": state["release"]}

    def retirement(_request, flow_id):
        state["calls"].append("retirement")
        if not state["allowed"] or state["deleted"] or flow_id != "owned":
            raise HTTPException(404, "no such flow")
        return {"id": flow_id, "name": "Order review", "version": 2,
                "revision": "draft-2", "published": state["published"],
                "release_id": state["release"] if state["published"] else "",
                "scheduled": state["published"], "connected_starts": 1,
                "pending_forms": state["pending_forms"], "active_runs": 1,
                "published_parent_flows": 0}

    def turn_off(_request, flow_id, payload):
        state["calls"].append("turn_off")
        if (not state["allowed"] or state["deleted"] or flow_id != "owned"
                or payload.expected_release_id != state["release"]):
            raise HTTPException(409, "flow changed")
        state["published"] = False
        return {"id": flow_id, "published": False}

    def delete(_request, flow_id, response, *, version, revision, expected_release_id):
        state["calls"].append("delete")
        if (not state["allowed"] or state["deleted"] or flow_id != "owned"
                or version != 2 or revision != "draft-2"
                or expected_release_id != (state["release"] if state["published"] else "")
                or state["pending_forms"]):
            raise HTTPException(409, "flow changed")
        state["deleted"] = True
        response.headers["X-Lightwork-Recovery-Copy"] = "cleared"

    monkeypatch.setattr(workflows.native, "get_flow_endpoint", draft)
    monkeypatch.setattr(workflows.native, "get_published_flow_endpoint", published)
    monkeypatch.setattr(workflows.native, "flow_retirement_preview_endpoint", retirement)
    monkeypatch.setattr(workflows.native, "unpublish_flow_endpoint", turn_off)
    monkeypatch.setattr(workflows.native, "delete_flow_endpoint", delete)
    monkeypatch.setattr(workflows.native, "run_flow_endpoint", lambda *_args: (_ for _ in ()).throw(
        AssertionError("review must never start work")))
    monkeypatch.setattr(ops, "_same_origin", lambda _request: None)
    monkeypatch.setattr(ops, "_gate", lambda _request: None)
    monkeypatch.setattr(ops, "_context", lambda _request: "c" * 64)
    monkeypatch.setattr(ops, "enabled_families", lambda: {"learning"})
    monkeypatch.setattr(ops, "adapters", lambda: {entry.id: entry for entry in workflows.entries()})
    app = FastAPI()
    app.include_router(ops.router)
    return TestClient(app), state


def test_published_learned_run_is_catalogued_and_prepared_without_execution(monkeypatch):
    client, state = _harness(monkeypatch)
    with client:
        catalog = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        item = catalog["operations"][0]
        assert item["id"] == "learning.workflow_run" and item["available"]
        result = client.post(BASE + "/prepare", json={"page": PAGE, "selection": SELECTION,
            "operation": item["id"], "context": catalog["context"], "revision": item["revision"], "arguments": {}})
        assert result.status_code == 200
        review = result.json()
        assert review["read_only"] is True and "token" not in review
        assert review["details"] == {"flow_id": "owned", "release_id": "a" * 64, "name": "Order review"}
        assert "run" not in state["calls"]


def test_changed_release_unpublished_or_nonlearned_workflow_cannot_launch(monkeypatch):
    client, state = _harness(monkeypatch)
    with client:
        catalog = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        item = catalog["operations"][0]
        state["release"] = "b" * 64
        stale = client.post(BASE + "/prepare", json={"page": PAGE, "selection": SELECTION,
            "operation": item["id"], "context": catalog["context"], "revision": item["revision"]})
        assert stale.status_code == 409
        state["published"] = False
        blocked = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        assert blocked["operations"][0]["available"] is False
        state["published"], state["learned"] = True, False
        blocked = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        assert blocked["operations"][0]["available"] is False
        state["allowed"] = False
        denied = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        assert denied["operations"] == []


def test_copilot_checks_the_same_recent_examples_without_a_mutation(monkeypatch):
    from maverick_dashboard import flow_teach_ops_api as teach

    client, _state = _harness(monkeypatch)
    rows = [{"flow_id": "owned", "run_id": "run-1", "flow_name": "Order review",
             "status": "awaiting_review", "training_available": False, "audit_status": "delivered"}]
    monkeypatch.setattr(teach, "learning_examples_inbox", lambda _request: {
        "examples": rows, "recent_only": False, "available": True})
    with client:
        catalog = client.post(BASE + "/catalog", json={"page": "/learning/examples", "selection": {}}).json()
        item = catalog["operations"][0]
        assert item["id"] == "learning.examples_review" and item["available"]
        reviewed = client.post(BASE + "/prepare", json={"page": "/learning/examples", "selection": {},
            "operation": item["id"], "context": catalog["context"], "revision": item["revision"], "arguments": {}})
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["read_only"] is True
        assert reviewed.json()["details"]["examples"][0]["href"] == "/flows/owned/runs/run-1"
        rows[0] = {**rows[0], "status": "accepted", "training_available": True}
        stale = client.post(BASE + "/prepare", json={"page": "/learning/examples", "selection": {},
            "operation": item["id"], "context": catalog["context"], "revision": item["revision"], "arguments": {}})
        assert stale.status_code == 409


def test_copilot_turn_off_and_delete_require_fresh_review_and_confirmation(monkeypatch):
    client, state = _harness(monkeypatch)
    monkeypatch.setattr(ops.crypto_at_rest, "seal_to_str", lambda value: "sealed:" + value)
    monkeypatch.setattr(ops.crypto_at_rest, "is_sealed_str", lambda value: value.startswith("sealed:"))
    monkeypatch.setattr(ops.crypto_at_rest, "unseal_from_str", lambda value: value[len("sealed:"):])

    def review(operation):
        catalog = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        item = next(row for row in catalog["operations"] if row["id"] == operation)
        prepared = client.post(BASE + "/prepare", json={"page": PAGE, "selection": SELECTION,
            "operation": operation, "context": catalog["context"], "revision": item["revision"],
            "arguments": {}})
        assert prepared.status_code == 200, prepared.text
        return prepared.json()

    with client:
        turn_off = review("learning.workflow_turn_off")
        assert turn_off["read_only"] is False and "token" in turn_off
        denied = client.post(BASE + "/execute", json={"token": turn_off["token"], "confirmed": False})
        assert denied.status_code == 400 and state["published"] is True
        state["release"] = "b" * 64
        stale = client.post(BASE + "/execute", json={"token": turn_off["token"], "confirmed": True})
        assert stale.status_code == 409 and "turn_off" not in state["calls"]
        turned_off = client.post(BASE + "/execute", json={
            "token": review("learning.workflow_turn_off")["token"], "confirmed": True})
        assert turned_off.status_code == 200 and turned_off.json()["status"] == "confirmed"
        assert state["published"] is False
        state["pending_forms"] = 1
        blocked = client.post(BASE + "/catalog", json={"page": PAGE, "selection": SELECTION}).json()
        deletion = next(row for row in blocked["operations"] if row["id"] == "learning.workflow_delete")
        assert deletion["available"] is False and "Automations" in deletion["blocked"]
        state["pending_forms"] = 0
        deleted = client.post(BASE + "/execute", json={
            "token": review("learning.workflow_delete")["token"], "confirmed": True})
        assert deleted.status_code == 200 and deleted.json()["status"] == "confirmed"
        assert state["deleted"] is True
