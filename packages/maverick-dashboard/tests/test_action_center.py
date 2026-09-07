"""Personal attention without widening business-record permissions."""
import copy
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from maverick import audit, config, world_model
from maverick.paths import tenant_scope
from maverick.privacy_ops import RecordConflict
from maverick_dashboard import action_center as center
from maverick_dashboard import action_center_api as api
from maverick_dashboard.app import app

CTX = hashlib.sha256(b"alice").hexdigest()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setattr(audit, "record", lambda *args, **kwargs: True)
    monkeypatch.setattr(audit, "record_global", lambda *args, **kwargs: True)
    monkeypatch.setattr(api.config, "get_features", lambda: {"action_center": True})
    monkeypatch.setattr(api.config, "config_source_errors", lambda **kwargs: [])
    config.reset_config_cache()
    yield
    config.reset_config_cache()


class World:
    def pending_approvals(self):
        return [SimpleNamespace(id=1, claimed_by=None, requested_by="user:submitter", action="publish_report",
            risk="high", detail="private details", requested_at=10)]

    def approval_state(self, identifier):
        return {"status": "pending", "approvers": [], "approvals_required": 1}

    def list_goals(self, **kwargs):
        assert kwargs["owner"] == "user:alice"
        return [SimpleNamespace(id=1, domain="forecast", title="Forecast", owner="user:alice", updated_at=20),
                SimpleNamespace(id=2, domain="restricted", title="Restricted", owner="user:alice", updated_at=20)]

    def signoffs_for_goals(self, ids):
        assert ids == [1]
        return {}


def model(world=None):
    assessments = [
        {"id":"a1", "type":"privacy", "subject":"Mine", "assignee":"user:alice", "status":"approved",
         "revision":1, "review_due":True, "next_review_at":50},
        {"id":"a2", "type":"privacy", "subject":"Other person's assessment", "assignee":"user:bob", "status":"needs_more", "revision":1},
        {"id":"a3", "type":"restricted", "subject":"Restricted assessment", "status":"pending_review", "revision":1},
    ]
    return center.collect(world or World(), principal="user:alice", owner="user:alice",
        specs=[{"domain":"forecast", "gate":"human", "deliverable":"Cash forecast"}],
        assessments=lambda: assessments, assessment_allowed=lambda row: row["type"] == "privacy", now=100)


def test_unified_items_respect_owner_suite_assignment_and_due_state():
    value = model()
    assert {row["kind"] for row in value["items"]} == {"approval", "assessment", "signoff"}
    assert value["items"][0]["title"] == "Mine"
    assert value["items"][0]["overdue"] is True
    assert value["items"][0]["assignment"] == "mine"
    assert all(row["status"] == "current" for row in value["coverage"])
    assert "Restricted" not in str(value) and "Other person's" not in str(value)


@pytest.mark.parametrize("relationship", ["claimed", "requested", "voted"])
def test_approvals_do_not_ask_for_others_claims_self_approval_or_duplicate_votes(relationship):
    world = World()
    row = world.pending_approvals()[0]
    if relationship == "claimed":
        row.claimed_by = "user:bob"
    elif relationship == "requested":
        row.requested_by = "user:alice"
    else:
        world.approval_state = lambda identifier: {"status":"pending", "approvers":["user:alice"]}
    world.pending_approvals = lambda: [row]
    assert not any(item["kind"] == "approval" for item in model(world)["items"])


def test_failed_source_is_not_reported_clear():
    class Broken(World):
        def pending_approvals(self):
            raise OSError("private database error")
    result = model(Broken())
    assert result["coverage"][0] == {"kind":"approval", "status":"unavailable"}
    assert "private database error" not in str(result)
    assert len(result["items"]) == 2


def test_project_review_actions_use_authorized_inbox_and_current_assignment():
    rows = [{"id": "PRP-test", "title": "Selected result", "reviewer": "user:alice", "status": "pending",
             "due_at": 50, "updated_at": 20, "revision": 2, "source_version": "a" * 64, "stale": True},
            {"id": "PRP-other", "title": "Another reviewer", "reviewer": "user:bob", "status": "pending"}]
    value = center.collect(World(), principal="user:alice", owner="user:alice", specs=[],
        assessments=list, assessment_allowed=lambda row: True, project_reviews=lambda: rows, now=100)
    reviews = [row for row in value["items"] if row["kind"] == "project_review"]
    assert len(reviews) == 1 and reviews[0]["overdue"]
    assert reviews[0]["href"] == "/project-reviews#packet=PRP-test"
    assert reviews[0]["reason"].startswith("Source changed")


def test_versioned_preferences_survive_reload_and_changed_items_become_unread():
    item = model()["items"][0]
    center.save_preference(CTX, item, expected_revision=0, operation_id="first-operation",
                           read=True, snoozed_until=500, actor="user:alice")
    preference = center.preferences(CTX)
    saved = center.apply_preferences(model(), preference, now=100)["items"][0]
    assert saved["read"] and saved["snoozed"]
    changed = model()
    changed["items"][0]["source_version"] = "f" * 64
    restored = center.apply_preferences(changed, center.preferences(CTX), now=100)["items"][0]
    assert not restored["read"] and not restored["snoozed"]
    expired = center.apply_preferences(model(), center.preferences(CTX), now=501)["items"][0]
    assert expired["read"] and not expired["snoozed"]


def test_exact_retry_and_concurrent_changes_are_safe():
    item = model()["items"][0]
    kwargs = dict(expected_revision=0, operation_id="first-operation", read=True, snoozed_until=0, actor="user:alice")
    result = center.save_preference(CTX, item, **kwargs)
    assert center.save_preference(CTX, item, **kwargs) == result
    with pytest.raises(RecordConflict):
        center.save_preference(CTX, item, **{**kwargs, "read":False})

    def attempt(value):
        try:
            return center.save_preference(CTX, item, expected_revision=1, operation_id=value,
                read=False, snoozed_until=0, actor="user:alice")
        except RecordConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["concurrent-one", "concurrent-two"]))
    assert sum(value is not None for value in results) == 1


def test_preferences_are_isolated_by_workspace_and_account():
    item = model()["items"][0]
    with tenant_scope(tenant="alpha"):
        center.save_preference(CTX, item, expected_revision=0, operation_id="first-operation",
            read=True, snoozed_until=0, actor="user:alice")
        assert center.preferences(hashlib.sha256(b"bob").hexdigest())["revision"] == 0
    with tenant_scope(tenant="beta"):
        assert center.preferences(CTX)["revision"] == 0


def _client_model(monkeypatch):
    value = model()
    monkeypatch.setattr(api, "_model", lambda request: copy.deepcopy(value))
    monkeypatch.setattr(api, "context_for", lambda request: CTX)
    return TestClient(app, headers={"Origin":"http://testserver", "X-Lightwork-Action-Context":CTX}), value


def test_api_preferences_recheck_current_source_then_paginate_and_keep_snoozed_overdue_visible(monkeypatch):
    client, value = _client_model(monkeypatch)
    snapshot = client.get("/api/v1/action-center?limit=1").json()
    assert snapshot["total"] == 3 and snapshot["has_more"]
    item = snapshot["items"][0]
    body = {"expected_revision":0, "operation_id":"operation-00000001", "source_version":item["source_version"],
            "read":True, "snoozed_until":time.time()+3600}
    response = client.put(f"/api/v1/action-center/{item['id']}/preference", json=body)
    assert response.status_code == 200, response.text
    active = client.get("/api/v1/action-center").json()
    assert active["counts"]["snoozed"] == 1 and len(active["items"]) == 3
    assert active["items"][0]["read"] is True
    assert client.get("/api/v1/action-center?view=snoozed").json()["total"] == 1
    assert client.get("/api/v1/action-center?kind=approval").json()["total"] == 1
    value["items"][0]["source_version"] = "f" * 64
    assert client.put(f"/api/v1/action-center/{item['id']}/preference", json=body).status_code == 409
    value["items"].pop(0)
    assert client.put(f"/api/v1/action-center/{item['id']}/preference", json=body).status_code == 404


def test_api_refuses_changed_context_or_disabled_feature(monkeypatch):
    client, value = _client_model(monkeypatch)
    item = value["items"][0]
    body = {"expected_revision":0, "operation_id":"operation-00000001", "source_version":item["source_version"], "read":True}
    assert client.put(f"/api/v1/action-center/{item['id']}/preference", json=body,
                       headers={"X-Lightwork-Action-Context":"wrong"}).status_code == 409
    monkeypatch.setattr(api.config, "get_features", lambda: {"action_center":False})
    assert client.get("/action-center").status_code == 403
    assert client.get("/api/v1/action-center").status_code == 403


def test_api_and_page_permission_floor_and_private_caching(monkeypatch):
    from maverick_dashboard import auth
    client, _ = _client_model(monkeypatch)
    assert client.get("/action-center").status_code == 200
    assert client.get("/static/lightwork-action-center.js").status_code == 200
    assert client.get("/api/v1/action-center").headers["cache-control"] == "private, no-store"
    monkeypatch.setattr(auth, "has_permission", lambda request, permission: False)
    assert client.get("/api/v1/action-center").status_code == 403
