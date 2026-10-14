"""The ordinary Copilot review can start and recover the native learning cycle."""
from contextlib import nullcontext

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_learning_flywheel as flywheel


def test_copilot_can_review_start_and_recover_one_exact_learning_cycle(monkeypatch):
    codec = Fernet(Fernet.generate_key())
    state = {"started": [], "receipts": set()}
    context = "a" * 64
    current = {
        "context": context,
        "revision": "b" * 64,
        "ready": True,
        "tasks": 4,
        "steps": 12,
        "requests": [],
        "problem": "",
    }
    prepared = {
        "context": context,
        "revision": current["revision"],
        "step_count": current["steps"],
        "guidance_review": [{"label": "Safeguard: use approved source", "after": "Current guidance"}],
        "tasks": current["tasks"],
        "steps": current["steps"],
    }

    monkeypatch.setattr(ops, "_same_origin", lambda _request: None)
    monkeypatch.setattr(ops, "_read_gate", lambda _request: None)
    monkeypatch.setattr(ops, "_gate", lambda _request: None)
    monkeypatch.setattr(ops, "_writes_enabled", lambda _request: True)
    monkeypatch.setattr(ops, "_context", lambda _request: context)
    monkeypatch.setattr(ops, "enabled_families", lambda: {"learning"})
    monkeypatch.setattr(ops.crypto_at_rest, "seal_to_str",
                        lambda value: "sealed:" + codec.encrypt(value.encode()).decode())
    monkeypatch.setattr(ops.crypto_at_rest, "is_sealed_str", lambda value: value.startswith("sealed:"))
    monkeypatch.setattr(ops.crypto_at_rest, "unseal_from_str",
                        lambda value: codec.decrypt(value[7:].encode()).decode())
    monkeypatch.setattr(ops, "adapters", lambda: {row.id: row for row in flywheel.entries()})
    monkeypatch.setattr(flywheel.native, "catalog", lambda _request: dict(current))
    monkeypatch.setattr(flywheel.native, "prepare", lambda _request: dict(prepared))

    def start(_request, reviewed, operation, *, expires):
        state["started"].append((reviewed, operation, expires))
        state["receipts"].add(operation)
        return {
            "recorded": True,
            "confirmed": True,
            "message": "The original cycle task is recorded. Read its current progress; nothing was repeated.",
            "job": {"worker_available": True},
            "result": {
                "complete": True,
                "message": "The cycle finished. Read its recorded guidance changes and suggestions.",
                "stores": [{"Information": "Safeguards", "Recorded state": "Saved and activity recorded", "Items": 1}],
                "counts": {"avoid": 1, "habits": 2, "ideas": 3},
                "recovery": {"href": "/learning#learning-review", "label": "Review saved learning points and restoration"},
            },
        }

    def original(_request, operation, _reviewed=None):
        if operation not in state["receipts"]:
            return {"recorded": False, "confirmed": False, "message": "The original start is unconfirmed."}
        result = start_result
        return {**result, "operation": operation}

    start_result = {
        "recorded": True,
        "confirmed": True,
        "message": "The original cycle task is recorded. Read its current progress; nothing was repeated.",
        "job": {"worker_available": True},
        "result": {
            "complete": True,
            "message": "The cycle finished. Read its recorded guidance changes and suggestions.",
            "stores": [{"Information": "Safeguards", "Recorded state": "Saved and activity recorded", "Items": 1}],
            "counts": {"avoid": 1, "habits": 2, "ideas": 3},
            "recovery": {"href": "/learning#learning-review", "label": "Review saved learning points and restoration"},
        },
    }
    monkeypatch.setattr(flywheel.native, "start", start)
    monkeypatch.setattr(flywheel.native, "owned",
                        lambda _request, operation, _reviewed=None: {"id": operation} if operation in state["receipts"] else None)
    monkeypatch.setattr(flywheel.native, "original", original)
    from maverick import authority_effects
    monkeypatch.setattr(authority_effects, "authority_effect_fence", nullcontext)

    app = FastAPI()
    app.include_router(ops.router)
    base = "/api/v1/copilot/product-operations"
    with TestClient(app) as client:
        catalog = client.post(base + "/catalog", json={"page": "/learning", "selection": {}}).json()
        row = next(item for item in catalog["operations"] if item["id"] == "learning.run_improvement_cycle")
        assert row["available"] is True
        assert row["confirmation_required"] is True
        assert "no outside changes" in row["summary"]

        review = client.post(base + "/prepare", json={
            "page": "/learning", "selection": {}, "operation": row["id"],
            "context": catalog["context"], "revision": row["revision"], "arguments": {},
        })
        assert review.status_code == 200, review.text
        assert "12" in str(review.json()["changes"])
        assert state["started"] == []

        payload = {"token": review.json()["token"], "confirmed": True}
        executed = client.post(base + "/execute", json=payload)
        assert executed.status_code == 200, executed.text
        assert executed.json()["status"] == "queued"
        assert executed.json()["details"]["Safeguards saved"] == 1
        assert executed.json()["details"]["Ideas suggested for testing"] == 3
        assert len(state["started"]) == 1

        replay = client.post(base + "/execute", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "replayed"
        recovered = client.post(base + "/recover", json={"token": review.json()["token"]})
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "replayed"
        assert len(state["started"]) == 1
