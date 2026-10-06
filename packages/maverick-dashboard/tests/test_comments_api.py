"""Comment threads and @mentions on tasks, runs, approvals and policies (NOT-4).

Only people who can open the item read its thread or are offered as a
mention; the server refuses any other mention, and a mention reaches the
person through their own personal notifications with why, what and where.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}
ANA = "user:ana@corp.com"
DANA = "user:dana@corp.com"
LEE = "user:lee@corp.com"
VIC = "user:vic@corp.com"
ROLES = {ANA: "operator", DANA: "admin", LEE: "admin", VIC: "viewer"}


@pytest.fixture
def env(monkeypatch, tmp_path):
    from maverick import config, world_model
    from maverick.world_model import WorldModel

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("MAVERICK_ENCRYPT_AT_REST", "0")
    (tmp_path / "config.toml").write_text("[features]\npersonal_preferences = true\n", encoding="utf-8")
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT"):
        monkeypatch.delenv(name, raising=False)
    config.reset_config_cache()
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    world = WorldModel(tmp_path / "world.db")
    from maverick_dashboard import api, rbac
    api._world_cache.clear()
    monkeypatch.setattr(rbac, "list_users", lambda: dict(ROLES))
    from maverick_dashboard.app import app

    yield TestClient(app, headers=ORIGIN), world
    world.close()
    api._world_cache.clear()
    config.reset_config_cache()


def _as(monkeypatch, principal):
    from maverick_dashboard import api, auth

    role = ROLES[principal]
    monkeypatch.setattr(auth, "caller_principal", lambda request: principal)
    monkeypatch.setattr(auth, "_authorization_principal", lambda request: principal)
    monkeypatch.setattr(auth, "role_for_principal", lambda p: ROLES.get(p))
    monkeypatch.setattr(auth, "global_role_for_principal", lambda p: ROLES.get(p))
    monkeypatch.setattr(api, "_supervisor", lambda request: principal)
    monkeypatch.setattr(auth, "has_permission",
                        lambda request, perm, **kw: perm in __import__("maverick_dashboard.rbac", fromlist=["x"]).permissions_for(role))
    monkeypatch.setattr(auth, "has_global_permission",
                        lambda request, perm, **kw: perm in __import__("maverick_dashboard.rbac", fromlist=["x"]).permissions_for(role))


def test_a_policy_thread_offers_only_people_who_manage_it_and_refuses_others(env, monkeypatch):
    client, _world = env
    _as(monkeypatch, DANA)
    thread = client.get("/api/v1/comments", params={"kind": "policy", "id": "tools"}).json()
    assert thread["comments"] == [] and thread["can_comment"] is True
    assert [p["principal"] for p in thread["people"]] == [LEE]  # admins only, never yourself
    refused = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools",
                                                    "body": "@Vic please look", "mentions": [VIC]})
    assert refused.status_code == 422
    assert "only people who can open this item" in refused.json()["detail"]
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools",
                                                   "body": "@Lee should web fetch stay blocked?", "mentions": [LEE]})
    assert posted.status_code == 201, posted.text
    body = posted.json()
    assert body["posted"]["mine"] is True and body["posted"]["mentions"] == ["lee@corp.com"]
    assert [c["body"] for c in body["comments"]] == ["@Lee should web fetch stay blocked?"]


def test_a_mention_reaches_the_person_with_why_what_and_where(env, monkeypatch):
    client, _world = env
    _as(monkeypatch, DANA)
    comment_id = client.post("/api/v1/comments", json={"kind": "policy", "id": "spending-limits",
                                                       "body": "@Lee can we lower this?", "mentions": [LEE]}).json()["posted"]["id"]
    from maverick_dashboard.comments_api import mention_sources

    sources = mention_sources(LEE)
    assert len(sources) == 1
    source = sources[0]
    assert source["ref"] == f"comment:{comment_id}" and source["category"] == "mention"
    assert source["href"] == f"/spending-limits#comment-{comment_id}"
    assert source["reason"] == "dana@corp.com mentioned you in a comment"
    assert source["action"] == "Read the comment and reply"
    assert "lower this" not in str(source)  # the alert never carries the comment text
    # Someone who can no longer open the item no longer sees the mention.
    ROLES[LEE] = "viewer"
    try:
        assert mention_sources(LEE) == []
    finally:
        ROLES[LEE] = "admin"


def test_a_task_thread_is_private_to_who_can_open_the_task(env, monkeypatch):
    client, world = env
    goal_id = world.create_goal("Reconcile vendor spend", owner=ANA)
    _as(monkeypatch, ANA)
    thread = client.get("/api/v1/comments", params={"kind": "task", "id": str(goal_id)}).json()
    assert thread["people"] == []  # nobody else can open this task, so nobody else is offered
    assert client.post("/api/v1/comments", json={"kind": "task", "id": str(goal_id),
                                                 "body": "Note to self", "mentions": []}).status_code == 201
    assert client.post("/api/v1/comments", json={"kind": "task", "id": str(goal_id), "body": "@Lee",
                                                 "mentions": [LEE]}).status_code == 422
    _as(monkeypatch, LEE)
    assert client.get("/api/v1/comments", params={"kind": "task", "id": str(goal_id)}).status_code == 404


def test_only_the_author_deletes_and_unknown_items_have_no_thread(env, monkeypatch):
    client, _world = env
    _as(monkeypatch, DANA)
    comment = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "First"}).json()["posted"]
    _as(monkeypatch, LEE)
    assert client.delete(f"/api/v1/comments/{comment['id']}").status_code == 403
    _as(monkeypatch, DANA)
    assert client.delete(f"/api/v1/comments/{comment['id']}").json()["comments"] == []
    assert client.get("/api/v1/comments", params={"kind": "policy", "id": "nope"}).status_code == 404
    assert client.get("/api/v1/comments", params={"kind": "folder", "id": "1"}).status_code == 404


def test_turning_comments_off_says_so(env, monkeypatch, tmp_path):
    from maverick import config

    client, _world = env
    _as(monkeypatch, DANA)
    (tmp_path / "config.toml").write_text("[collaboration]\ncomments = false\n", encoding="utf-8")
    config.reset_config_cache()
    refused = client.get("/api/v1/comments", params={"kind": "policy", "id": "tools"})
    assert refused.status_code == 403
    assert refused.headers["X-Lightwork-Reason"] == "feature-off"
