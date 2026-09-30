"""Local registry contracts, with native effect/provider seams injected."""
from contextlib import nullcontext
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops


class Value(ops.StrictInput):
    value: str


@pytest.fixture
def registry(monkeypatch):
    state = {"revision": "1", "context": "a" * 64, "calls": 0, "receipt": None, "allowed": True}
    codec = Fernet(Fernet.generate_key())
    monkeypatch.setattr(ops, "_same_origin", lambda request: None)
    monkeypatch.setattr(ops, "_gate", lambda request: None)
    monkeypatch.setattr(ops, "_context", lambda request: state["context"])
    monkeypatch.setattr(ops, "enabled_families", lambda: {"preferences"})
    monkeypatch.setattr(ops.crypto_at_rest, "seal_to_str", lambda value: "sealed:" + codec.encrypt(value.encode()).decode())
    monkeypatch.setattr(ops.crypto_at_rest, "is_sealed_str", lambda value: value.startswith("sealed:"))
    monkeypatch.setattr(ops.crypto_at_rest, "unseal_from_str", lambda value: codec.decrypt(value[7:].encode()).decode())
    from maverick import authority_effects
    monkeypatch.setattr(authority_effects, "authority_effect_fence", nullcontext)
    def snapshot(request, selection):
        if not state["allowed"]:
            raise HTTPException(403, "denied")
        return ops.Snapshot(state["revision"], "Saved preference", "Current source", {"value": "old"})
    def execute(request, selection, args, native, operation):
        state["calls"] += 1
        state["receipt"] = operation
        state["revision"] = "2"
        return ops.Outcome("confirmed", "saved", {"value": args["value"]})
    def recover(request, selection, args, native, operation):
        return ops.Outcome("replayed", "already saved") if state["receipt"] == operation else None
    adapter = ops.Adapter("preferences.example", "Example", "preferences", "/preferences", ("/preferences",), Value,
                          snapshot, lambda req, sel, args: ops.Preview("Review", [{"before": "old", "after": args["value"]}]), execute, recover)
    state["adapter"] = adapter
    monkeypatch.setattr(ops, "adapters", lambda: {adapter.id: state["adapter"]})
    app = FastAPI()
    app.include_router(ops.router)
    with TestClient(app) as client:
        yield client, state


BASE = "/api/v1/copilot/product-operations"


def prepare(client, state, **changes):
    body = {"page": "/preferences", "selection": {}, "operation": "preferences.example",
            "context": state["context"], "revision": "1", "arguments": {"value": "new"}}
    body.update(changes)
    return client.post(BASE + "/prepare", json=body)


def test_review_does_not_execute_and_native_receipt_recovers_exact_retry(registry):
    client, state = registry
    review = prepare(client, state)
    assert review.status_code == 200 and state["calls"] == 0
    payload = {"token": review.json()["token"], "confirmed": True}
    assert client.post(BASE + "/execute", json=payload).json()["status"] == "confirmed"
    assert client.post(BASE + "/execute", json=payload).json()["status"] == "replayed"
    assert state["calls"] == 1


@pytest.mark.parametrize("change", ["confirmation", "tampered", "context", "revision", "access"])
def test_unreviewed_stale_or_foreign_context_never_mutates(registry, change):
    client, state = registry
    payload = {"token": prepare(client, state).json()["token"], "confirmed": True}
    if change == "confirmation":
        payload["confirmed"] = False
    elif change == "tampered":
        payload["token"] = payload["token"][:-7] + "changed"
    elif change == "context":
        state["context"] = "b" * 64
    elif change == "revision":
        state["revision"] = "updated"
    else:
        state["allowed"] = False
    assert client.post(BASE + "/execute", json=payload).status_code in {400, 403, 409}
    assert state["calls"] == 0


def test_model_or_client_cannot_supply_handler_authority_or_extra_arguments(registry):
    client, state = registry
    assert prepare(client, state, arguments={"value": "new", "actor": "admin"}).status_code == 422
    assert prepare(client, state, operation="shell.execute").status_code == 404
    assert prepare(client, state, page="https://outside.invalid").status_code == 422
    assert state["calls"] == 0


def test_readonly_prepared_comparison_and_bounded_source_failure(registry):
    client, state = registry
    state["adapter"] = replace(state["adapter"], execute=None, prepare=lambda *args: ops.Preview("Compared", native={"requested": 5}))
    result = prepare(client, state).json()
    assert result["read_only"] and result["details"] == {"requested": 5} and "token" not in result
    def fail(*args): raise ValueError("private details must not escape")
    state["adapter"] = replace(state["adapter"], snapshot=fail)
    response = client.post(BASE + "/catalog", json={"page": "/preferences"})
    assert response.status_code == 200 and response.json()["operations"][0]["available"] is False
    assert "private details" not in response.text


def test_snapshot_rechecks_before_readonly_publication(registry):
    client, state = registry
    def changed(*args):
        state["revision"] = "new"
        return ops.Preview("Outdated")
    state["adapter"] = replace(state["adapter"], execute=None, prepare=changed)
    assert prepare(client, state).status_code == 409
