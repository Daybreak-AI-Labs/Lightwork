"""Copilot code practice uses the native budgeted review and exact task recovery."""
from contextlib import nullcontext

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from maverick_dashboard import product_operations as ops
from maverick_dashboard import product_operations_learning_program as learning


def test_copilot_reviews_and_starts_code_practice_once_then_reads_original(monkeypatch):
    codec = Fernet(Fernet.generate_key())
    context = "a" * 64
    revision = "b" * 64
    calls = {"review": [], "run": []}
    job = {"id": "0123456789abcdef", "kind": "self_modify_run", "state": "queued",
           "result": None, "started_at": 1234567890}
    saved_review = {"objective": "make examples easier", "cycles": 2,
                    "model": "Configured coding model", "spending_limit": 3.0}
    current = {"context": context, "revision": revision, "state": "ready",
               "running_allowed": True, "blockers": [], "level": "starter",
               "areas": 2, "results": 0, "spending_limit": 3.0,
               "model": "Configured coding model"}

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
    adapter = next(row for row in learning.entries() if row.id == "learning.run_code_practice")
    monkeypatch.setattr(ops, "adapters", lambda: {adapter.id: adapter})

    from maverick_dashboard import learning_ops_api
    monkeypatch.setattr(learning_ops_api, "_modify_current", lambda _request, context=None: dict(current))

    async def review(_request, body):
        calls["review"].append(body.model_dump())
        return JSONResponse({"token": "sealed:native-practice", "job_id": job["id"],
                             "review": dict(saved_review)})

    async def run(_request, body):
        calls["run"].append(body.model_dump())
        job["state"] = "queued"
        return {"job": dict(job)}

    monkeypatch.setattr(learning_ops_api, "modify_review", review)
    monkeypatch.setattr(learning_ops_api, "modify_run", run)
    monkeypatch.setattr(learning_ops_api.jobs, "get",
                        lambda job_id: object() if job_id == job["id"] and calls["run"] else None)
    monkeypatch.setattr(learning_ops_api, "_modify_original",
                        lambda _request, _job_id: {"job": dict(job), "review": dict(saved_review)})
    from maverick import authority_effects
    monkeypatch.setattr(authority_effects, "authority_effect_fence", nullcontext)

    app = FastAPI()
    app.include_router(ops.router)
    base = "/api/v1/copilot/product-operations"
    with TestClient(app) as client:
        catalog = client.post(base + "/catalog", json={"page": "/learning", "selection": {}}).json()
        row = next(item for item in catalog["operations"] if item["id"] == adapter.id)
        assert row["available"] is True
        assert row["confirmation_required"] is True
        assert row["label"] == "Try a Lightwork improvement"
        assert row["fields"][0]["label"] == "What should Lightwork try to improve?"
        assert row["fields"][0]["kind"] == "textarea"
        assert row["fields"][1]["label"] == "How many practice rounds?"
        assert row["fields"][1]["value"] == 1

        review_response = client.post(base + "/prepare", json={
            "page": "/learning", "selection": {}, "operation": adapter.id,
            "context": catalog["context"], "revision": row["revision"],
            "arguments": {"objective": "  make   examples easier  ", "cycles": 2},
        })
        assert review_response.status_code == 200, review_response.text
        review = review_response.json()
        assert {item["label"]: item["after"] for item in review["changes"]}["Per-run spending limit"] == "$3.00"
        assert "will not install" in str(review["changes"])
        assert calls["review"][0]["objective"] == "make examples easier"
        assert calls["run"] == []

        payload = {"token": review["token"], "confirmed": True}
        started = client.post(base + "/execute", json=payload)
        assert started.status_code == 200, started.text
        assert started.json()["status"] == "queued"
        assert started.json()["details"]["practice_task"]["href"].endswith(job["id"] + "#learning-job-history")
        assert calls["run"][0]["token"] == "sealed:native-practice"
        assert calls["run"][0]["confirm"] is True

        replay = client.post(base + "/execute", json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "replayed"
        original = client.post(base + "/recover", json={"token": review["token"]})
        assert original.status_code == 200, original.text
        assert original.json()["status"] == "queued"
        assert len(calls["run"]) == 1

        job.update(state="done", result={"ran": True, "tested": 4, "spent": 1.25,
                   "rounds": [{"ideas": 3, "tested": 4, "note": "internal evaluator detail"}]})
        finished = client.post(base + "/recover", json={"token": review["token"]})
        assert finished.status_code == 200, finished.text
        assert finished.json()["status"] == "confirmed"
        result = finished.json()["details"]["practice_result"]
        assert result["rounds"] == [{"round": 1, "ideas_considered": 3, "changes_checked": 4}]
        assert "internal evaluator detail" not in finished.text
        assert len(calls["run"]) == 1


def test_copilot_hides_code_practice_start_until_native_experiment_is_ready(monkeypatch):
    from maverick_dashboard import learning_ops_api

    current = {"context": "a" * 64, "revision": "b" * 64, "state": "blocked",
               "running_allowed": False, "blockers": ["Add at least two practice tests."],
               "level": "starter", "areas": 0, "results": 0,
               "spending_limit": None, "model": "Configured coding model"}
    monkeypatch.setattr(learning_ops_api, "_modify_current", lambda _request, context=None: dict(current))
    adapter = next(row for row in learning.entries() if row.id == "learning.run_code_practice")

    state = adapter.snapshot(object(), {})

    assert state.blocked == "Add at least two practice tests."
    assert state.details["practice_status"]["running_allowed"] is False
    assert state.details["spending_limit"] == "No separate practice cap is set; general task limits apply"
    assert state.fields[0]["kind"] == "textarea"
