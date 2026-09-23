"""Durable HTTP receipt recovery never repeats a start or crosses authority."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from maverick.attachment_submissions import SubmissionStore
from maverick.world_model import WorldModel
from maverick_dashboard import api, attachment_submission
from maverick_dashboard import app as dashboard


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    world = WorldModel(tmp_path / "world.db")
    for module in (api, dashboard):
        monkeypatch.setattr(module, "_world", lambda: world)
        monkeypatch.setattr(module, "require_provider_or_400", lambda: None)
    monkeypatch.setattr(dashboard, "check_goal_rate_limit", lambda request: None)
    runs = []

    async def run(goal_id, *args, **kwargs):
        runs.append((goal_id, args, kwargs))

    monkeypatch.setattr("maverick.runner.run_goal_in_background_async", run)
    monkeypatch.setattr("maverick.runner.run_goal_in_thread", lambda *a, **kw: pytest.fail("unexpected native runner"))
    client = TestClient(dashboard.app, headers={"Origin": "http://testserver"})
    yield client, world, runs
    world.close()


def test_accepted_api_start_reconciles_without_provider_and_without_second_dispatch(isolated, monkeypatch):
    client, world, runs = isolated
    headers = {"Idempotency-Key": "accepted-lost-response"}
    response = client.post("/api/v1/goals", headers=headers, json={"title": "Review", "description": "Exact brief"})
    assert response.status_code == 201
    goal_id = response.json()["id"]
    # The POST response may be lost; read after simulated credential/config
    # loss still does not require a provider or trigger a second run.
    monkeypatch.setattr(api, "require_provider_or_400", lambda: pytest.fail("receipt read must not require a provider"))
    receipt = client.get("/api/v1/goal-submissions/receipt", headers=headers)
    assert receipt.status_code == 200 and receipt.headers["cache-control"] == "no-store"
    assert receipt.json() == {"state": "accepted", "submission_state": "dispatching", "goal": {
        "id": goal_id, "status": "pending"}, "replayed": True, "execution_confirmed": False}
    assert len(runs) == len(world.list_goals()) == 1


@pytest.mark.parametrize("changed", [{"title": "Other"}, {"description": "Different"}, {"max_dollars": 0.2}])
def test_exact_key_payload_conflicts_do_not_create_loser_goals(isolated, changed):
    client, world, runs = isolated
    payload = {"title": "Review", "description": "Exact brief", "max_dollars": 0.1}
    headers = {"Idempotency-Key": "exact-request"}
    first = client.post("/api/v1/goals", headers=headers, json=payload)
    conflict = client.post("/api/v1/goals", headers=headers, json={**payload, **changed})
    assert first.status_code == 201 and conflict.status_code == 409
    assert len(world.list_goals()) == len(runs) == 1


def test_concurrent_identical_requests_create_one_goal_and_one_dispatch(isolated):
    client, world, runs = isolated

    def submit(_):
        return client.post("/api/v1/goals", headers={"Idempotency-Key": "same-concurrent-key"}, json={"title": "Review"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, range(8)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == len(world.list_goals()) == len(runs) == 1
    assert sum(r.headers["Idempotency-Replayed"] == "false" for r in results) == 1


def test_chat_redirect_lost_reconciles_original_conversation_without_retry(isolated):
    client, world, runs = isolated
    response = client.post("/chat/send", data={"title": "My chat", "submission_key": "widget-key"}, follow_redirects=False)
    assert response.status_code == 303
    goal_id = int(response.headers["location"].rsplit("/", 1)[1])
    receipt = client.get("/api/v1/goal-submissions/receipt?kind=chat", headers={"Idempotency-Key": "widget-key"})
    assert receipt.status_code == 200 and receipt.json()["goal"]["id"] == goal_id
    assert len(world.list_goals()) == len(runs) == 1
    conversation = world.get_or_create_conversation("web", f"local/{goal_id}")
    assert len(world.recent_turns(conversation.id)) == 1


def test_interrupted_chat_admission_returns_draft_receipt_without_dispatch(isolated):
    client, world, runs = isolated
    original = asyncio.run(attachment_submission.submit(world, [], token="unstarted", owner="", title="Draft", description="Draft"))
    response = client.get("/api/v1/goal-submissions/receipt?kind=chat", headers={"Idempotency-Key": "unstarted"})
    assert response.status_code == 200
    assert response.json()["goal"] == {"id": original["goal_id"], "status": "draft"}
    assert response.json()["submission_state"] == "admitted" and not runs


def test_missing_receipt_stays_unknown_and_denied_owner_cannot_read_private_goal(isolated, monkeypatch):
    client, world, _runs = isolated
    store = SubmissionStore(world)
    receipt = store.create(token="private", fingerprint="a" * 64, owner="user:alice", title="Private", description="secret", manifest=[])
    headers = {"Idempotency-Key": "private"}
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:bob")
    missing = client.get("/api/v1/goal-submissions/receipt?kind=chat", headers=headers)
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
    response = client.get("/api/v1/goal-submissions/receipt?kind=chat", headers=headers)
    assert response.status_code == 200 and response.json()["goal"]["id"] == receipt["goal_id"]
    assert "secret" not in response.text and "Private" not in response.text
    monkeypatch.setattr(api, "require_permission", lambda *a: (_ for _ in ()).throw(HTTPException(403)))
    assert client.get("/api/v1/goal-submissions/receipt?kind=chat", headers=headers).status_code == 403


def test_receipt_read_rechecks_current_goal_owner(isolated):
    client, world, _runs = isolated
    goal = SubmissionStore(world).create(token="changed-owner", fingerprint="a" * 64, owner="", title="Private", description="secret", manifest=[])
    with world._writing() as connection:
        connection.execute("UPDATE goals SET owner=? WHERE id=?", ("user:bob", goal["goal_id"]))
    response = client.get("/api/v1/goal-submissions/receipt?kind=chat", headers={"Idempotency-Key": "changed-owner"})
    assert response.status_code == 404
