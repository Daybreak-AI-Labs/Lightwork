"""Copilot's connected-app check uses the owner's audited native route."""
from contextlib import nullcontext

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick import authority_effects
from maverick.audit import EventKind
from maverick.flow.store import FlowRun
from maverick_dashboard import product_operations as operations
from maverick_dashboard import product_operations_flow_external_effects as owner_check

BASE = "/api/v1/copilot/product-operations"
RUN_ID = "a" * 32


def _install(monkeypatch):
    run = FlowRun(
        run_id=RUN_ID, flow_id="orders", owner="workspace:test", status="indeterminate",
        updated=1234.5, cursor="send-invoice", definition_digest="", release_id="release-1",
        nodes={"send-invoice": {"status": "indeterminate"}},
    )
    state = {"run": run, "audit": [], "audit_ok": True, "actor": "user:owner"}
    codec = Fernet(Fernet.generate_key())
    monkeypatch.setattr(operations, "_read_gate", lambda _request: None)
    monkeypatch.setattr(operations, "_gate", lambda _request: None)
    monkeypatch.setattr(operations, "_context", lambda _request: "c" * 64)
    monkeypatch.setattr(operations, "enabled_families", lambda: {"artifacts"})
    monkeypatch.setattr(operations.crypto_at_rest, "seal_to_str", lambda value: "sealed:" + codec.encrypt(value.encode()).decode())
    monkeypatch.setattr(operations.crypto_at_rest, "is_sealed_str", lambda value: value.startswith("sealed:"))
    monkeypatch.setattr(operations.crypto_at_rest, "unseal_from_str", lambda value: codec.decrypt(value[7:].encode()).decode())
    monkeypatch.setattr(authority_effects, "authority_effect_fence", nullcontext)
    monkeypatch.setattr(owner_check.api, "_require_flows", lambda: None)
    monkeypatch.setattr(owner_check.api, "_load_owned_run", lambda _request, run_id: run if run_id == RUN_ID else None)
    monkeypatch.setattr(owner_check.api, "_request_actor", lambda _request: state["actor"])
    from maverick.flow import store
    monkeypatch.setattr(store, "run_lock", lambda _run_id: nullcontext())
    monkeypatch.setattr(store, "save_run", lambda _run: None)
    monkeypatch.setattr(operations, "adapters", lambda: {entry.id: entry for entry in owner_check.entries()})

    from maverick import audit

    def audit_event(kind, **fields):
        state["audit"].append((kind, fields))
        return state["audit_ok"]

    monkeypatch.setattr(audit, "audit_event", audit_event)
    app = FastAPI()
    app.include_router(operations.router)
    return TestClient(app, headers={"Origin": "http://testserver"}), state


def _selection():
    return {"kind": "flow_run", "id": RUN_ID, "flow_id": "orders"}


def _prepare(client, catalog, decision="applied", personally_checked=True):
    return client.post(BASE + "/prepare", json={
        "page": f"/flows/orders/runs/{RUN_ID}", "selection": _selection(),
        "operation": "artifacts.workflow_external_effect_check", "context": catalog["context"],
        "revision": catalog["operations"][0]["revision"],
        "arguments": {"decision": decision, "personally_checked": personally_checked},
    })


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_message"),
    [
        ("applied", "reconciled_applied", "later steps did not run"),
        ("not_applied", "failed", "resume from this step"),
    ],
)
def test_owner_check_is_reviewed_recorded_and_deduplicated(
    monkeypatch, decision, expected_status, expected_message,
):
    client, state = _install(monkeypatch)
    catalog_response = client.post(BASE + "/catalog", json={
        "page": f"/flows/orders/runs/{RUN_ID}", "selection": _selection(),
    })
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    action = catalog["operations"][0]
    assert action["available"] is True and action["confirmation_required"] is True
    assert action["fields"][0]["choices"][0]["value"] == ""
    assert "does not read" in action["details"]["Scope"]

    review_response = _prepare(client, catalog, decision=decision)
    assert review_response.status_code == 200
    review = review_response.json()
    assert "will not verify" in review["summary"]
    assert state["audit"] == []

    assert _prepare(client, catalog, personally_checked=False).status_code == 422
    assert client.post(BASE + "/execute", json={"token": review["token"], "confirmed": False}).status_code == 400
    assert state["audit"] == []

    completed = client.post(BASE + "/execute", json={"token": review["token"], "confirmed": True})
    assert completed.status_code == 200
    assert completed.json()["status"] == "confirmed"
    assert expected_message in completed.json()["message"]
    assert len(state["audit"]) == 1
    event, fields = state["audit"][0]
    assert event == EventKind.FLOW_EXTERNAL_EFFECT_RECONCILED
    assert fields["run_id"] == RUN_ID
    assert fields["expected_updated"] == 1234.5
    assert fields["node_id"] == "send-invoice"
    assert fields["decision"] == decision
    assert state["run"].status == expected_status

    repeated = client.post(BASE + "/execute", json={"token": review["token"], "confirmed": True})
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "replayed"
    assert len(state["audit"]) == 1


def test_absent_action_review_says_resume_is_separate_and_not_automatic(monkeypatch):
    client, _state = _install(monkeypatch)
    catalog = client.post(BASE + "/catalog", json={
        "page": f"/flows/orders/runs/{RUN_ID}", "selection": _selection(),
    }).json()
    review = _prepare(client, catalog, decision="not_applied").json()
    assert "will not retry automatically" in review["changes"][1]["What happens next"]
    assert review["changes"][0]["What you reported"] == "You did not find the action in the connected app."


def test_activity_history_refusal_keeps_the_run_held_and_allows_exact_review_retry(monkeypatch):
    client, state = _install(monkeypatch)
    catalog = client.post(BASE + "/catalog", json={
        "page": f"/flows/orders/runs/{RUN_ID}", "selection": _selection(),
    }).json()
    review = _prepare(client, catalog).json()
    state["audit_ok"] = False

    refused = client.post(BASE + "/execute", json={"token": review["token"], "confirmed": True})
    assert refused.status_code == 503
    assert state["run"].status == "indeterminate"
    assert state["run"].reconciliation == {}

    state["audit_ok"] = True
    retried = client.post(BASE + "/execute", json={"token": review["token"], "confirmed": True})
    assert retried.status_code == 200
    assert retried.json()["status"] == "confirmed"
    assert state["run"].status == "reconciled_applied"
    assert len({fields["operation_id"] for _event, fields in state["audit"]}) == 1


def test_non_uncertain_run_cannot_prepare_an_owner_check(monkeypatch):
    client, state = _install(monkeypatch)
    state["run"].status = "completed"
    catalog = client.post(BASE + "/catalog", json={
        "page": f"/flows/orders/runs/{RUN_ID}", "selection": _selection(),
    }).json()
    assert catalog["operations"][0]["available"] is False
    assert "does not currently need" in catalog["operations"][0]["blocked"]
