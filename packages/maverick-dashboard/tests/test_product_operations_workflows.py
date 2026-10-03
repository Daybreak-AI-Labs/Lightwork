"""Copilot can hand a reviewed release to the native learned run form only."""
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_workflows as workflows

BASE = "/api/v1/copilot/product-operations"
PAGE = "/flows/designer/owned"
SELECTION = {"kind": "flow", "id": "owned"}


def _harness(monkeypatch):
    state = {"published": True, "learned": True, "release": "a" * 64, "allowed": True, "calls": []}

    def draft(_request, flow_id):
        state["calls"].append("draft")
        if not state["allowed"] or flow_id != "owned":
            raise HTTPException(404, "no such flow")
        return {"id": flow_id, "version": 2, "revision": "draft-2", "published": state["published"]}

    def published(_request, flow_id):
        state["calls"].append("published")
        if not state["allowed"] or flow_id != "owned":
            raise HTTPException(404, "no such flow")
        if not state["published"]:
            raise HTTPException(409, "not published")
        return {"flow": {"id": flow_id, "name": "Order review",
                         "learned_recipe": {"schema": "lightwork.action-recipe.v1"} if state["learned"] else None},
                "release_id": state["release"]}

    monkeypatch.setattr(workflows.native, "get_flow_endpoint", draft)
    monkeypatch.setattr(workflows.native, "get_published_flow_endpoint", published)
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
