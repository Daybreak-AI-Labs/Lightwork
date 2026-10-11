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
    monkeypatch.setattr(auth, "global_role_for_principal", lambda p, **kw: ROLES.get(p))
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
    # People who may change tool permissions (operate), never yourself or a viewer.
    assert [p["principal"] for p in thread["people"]] == [ANA, LEE]
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


def _lee_reads_anas_task(world, monkeypatch):
    from maverick_dashboard import content_access

    goal_id = world.create_goal("Reconcile vendor spend", owner=ANA)
    monkeypatch.setattr(content_access, "enabled", lambda: True)
    monkeypatch.setattr(content_access.STORE, "list", lambda: [{"requester": LEE}])
    monkeypatch.setattr(content_access, "_grant_current", lambda grant, **kw: True)
    return goal_id


def test_a_reader_the_company_directory_turned_off_is_not_offered_or_mentionable(env, monkeypatch):
    # Lee holds a reviewed read grant for Ana's task, but company sign-in
    # (SCIM) turned Lee off: Lee can no longer sign in to read a mention.
    from maverick_dashboard import scim_groups

    client, world = env
    goal_id = _lee_reads_anas_task(world, monkeypatch)
    _as(monkeypatch, ANA)
    params = {"kind": "task", "id": str(goal_id)}
    assert [p["principal"] for p in client.get("/api/v1/comments", params=params).json()["people"]] == [LEE]
    monkeypatch.setattr(scim_groups, "active_check", lambda: lambda principal: False if principal == LEE else None)
    assert client.get("/api/v1/comments", params=params).json()["people"] == []
    assert client.post("/api/v1/comments", json={**params, "body": "@Lee", "mentions": [LEE]}).status_code == 422
    assert client.post("/api/v1/comments", json={**params, "body": "Note", "mentions": []}).status_code == 201


def test_an_unreadable_company_directory_offers_nobody_and_holds_mentions(env, monkeypatch):
    from maverick_dashboard import scim_groups

    client, world = env
    goal_id = _lee_reads_anas_task(world, monkeypatch)
    _as(monkeypatch, ANA)

    def unreadable():
        raise OSError("directory unreadable")

    monkeypatch.setattr(scim_groups, "active_check", unreadable)
    params = {"kind": "task", "id": str(goal_id)}
    thread = client.get("/api/v1/comments", params=params)
    assert thread.status_code == 200 and thread.json()["people"] == []
    assert client.post("/api/v1/comments", json={**params, "body": "@Lee", "mentions": [LEE]}).status_code == 503
    assert client.post("/api/v1/comments", json={**params, "body": "Note", "mentions": []}).status_code == 201


def test_an_unreadable_company_directory_holds_a_mention_on_any_thread_with_the_same_answer(env, monkeypatch):
    # On an approval or policy thread the readers themselves come from the
    # directory, so an unreadable one is a 503, not "not allowed".
    from maverick_dashboard import scim_groups

    client, world = env
    _as(monkeypatch, DANA)

    def unreadable():
        raise OSError("directory unreadable")

    monkeypatch.setattr(scim_groups, "active_check", unreadable)
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "@Ana",
                                                   "mentions": [ANA]})
    assert posted.status_code == 503


def test_a_thread_with_nobody_else_to_offer_does_not_read_the_directory(env, monkeypatch):
    from maverick_dashboard import scim_groups

    client, world = env
    goal_id = world.create_goal("Reconcile vendor spend", owner=ANA)
    _as(monkeypatch, ANA)
    reads = []
    real = scim_groups.active_check
    monkeypatch.setattr(scim_groups, "active_check", lambda: reads.append(1) or real())
    assert client.get("/api/v1/comments", params={"kind": "task", "id": str(goal_id)}).json()["people"] == []
    assert reads == []


def test_a_mention_nobody_could_accept_is_refused_while_the_directory_is_unreadable(env, monkeypatch):
    # Someone who cannot open the item, or yourself, is refused whatever the
    # directory says: a 422 now, not a 503 that fails again after a retry.
    from maverick_dashboard import scim_groups

    client, world = env
    goal_id = world.create_goal("Reconcile vendor spend", owner=ANA)
    _as(monkeypatch, ANA)

    def unreadable():
        raise OSError("directory unreadable")

    monkeypatch.setattr(scim_groups, "active_check", unreadable)
    task = {"kind": "task", "id": str(goal_id)}
    assert client.post("/api/v1/comments", json={**task, "body": "@Lee", "mentions": [LEE]}).status_code == 422
    assert client.post("/api/v1/comments", json={**task, "body": "@Ana", "mentions": [ANA]}).status_code == 422
    _as(monkeypatch, DANA)
    policy = {"kind": "policy", "id": "tools"}
    assert client.post("/api/v1/comments", json={**policy, "body": "@Dana", "mentions": [DANA]}).status_code == 422


def test_a_mention_is_decided_by_one_fresh_read_of_the_directory(env, monkeypatch):
    # The directory read that lists the policy's readers fails; the one that
    # checks the mention a moment later succeeds. The mention is decided by
    # that read: Lee manages tool permissions, so it is posted.
    from maverick_dashboard import scim_groups

    client, _world = env
    _as(monkeypatch, DANA)
    real, failures = scim_groups.active_check, [1]

    def flaky():
        if failures:
            failures.pop()
            raise OSError("directory briefly unreadable")
        return real()

    monkeypatch.setattr(scim_groups, "active_check", flaky)
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "@Lee",
                                                   "mentions": [LEE]})
    assert posted.status_code == 201, posted.text
    assert failures == []


def test_an_unreadable_role_list_holds_a_mention_instead_of_refusing_it(env, monkeypatch):
    from maverick_dashboard import rbac

    client, _world = env
    _as(monkeypatch, DANA)

    def unreadable():
        raise OSError("role list unreadable")

    monkeypatch.setattr(rbac, "list_users", unreadable)
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "@Lee",
                                                   "mentions": [LEE]})
    assert posted.status_code == 503
    assert "can't be checked right now" in posted.json()["detail"]


def _current_grant(owner, requester, kind, resource_id, revision=None):
    import time

    from maverick_dashboard import content_access

    grant = {"tenant": content_access.tenant(), "owner": owner, "requester": requester, "resource_kind": kind,
             "resource_id": str(resource_id), "status": "approved", "expires_at": time.time() + 3600,
             "reviewed_by": DANA}
    if revision is not None:
        grant["resource_revision"] = revision
    return grant


def _directory_unreadable(monkeypatch):
    """Make the company directory unreadable; returns a function that restores it."""
    from maverick_dashboard import scim_groups

    real = scim_groups.active_check, scim_groups.active_for_principal

    def unreadable(*args):
        raise OSError("directory unreadable")

    monkeypatch.setattr(scim_groups, "active_check", unreadable)
    monkeypatch.setattr(scim_groups, "active_for_principal", unreadable)

    def restore():
        monkeypatch.setattr(scim_groups, "active_check", real[0])
        monkeypatch.setattr(scim_groups, "active_for_principal", real[1])
    return restore


def test_a_mention_of_a_reader_by_grant_is_held_while_the_directory_is_unreadable(env, monkeypatch):
    # Lee reads Ana's task through a reviewed grant. Whether that grant is
    # still current depends on the directory, so while it cannot be read the
    # mention is held (503), not refused as "not allowed".
    from maverick_dashboard import content_access

    client, world = env
    goal_id = world.create_goal("Reconcile vendor spend", owner=ANA)
    monkeypatch.setattr(content_access, "enabled", lambda: True)
    monkeypatch.setattr(content_access.STORE, "list", lambda: [_current_grant(ANA, LEE, "goal", goal_id)])
    _as(monkeypatch, ANA)
    task = {"kind": "task", "id": str(goal_id), "body": "@Lee"}
    assert client.post("/api/v1/comments", json={**task, "mentions": [VIC]}).status_code == 422
    restore = _directory_unreadable(monkeypatch)
    assert client.post("/api/v1/comments", json={**task, "mentions": [LEE]}).status_code == 503
    # Vic holds no grant: refused without reading the directory.
    assert client.post("/api/v1/comments", json={**task, "mentions": [VIC]}).status_code == 422
    restore()
    assert client.post("/api/v1/comments", json={**task, "mentions": [LEE]}).status_code == 201


def test_an_approval_thread_mentions_only_its_readers_and_holds_one_it_cannot_confirm(env, monkeypatch):
    from maverick_dashboard import content_access

    client, world = env
    approval_id = world.create_approval("Pay invoice", requested_by=ANA)
    approval = world.get_approval(approval_id)
    revision = content_access.approval_resource(approval, world)["resource_revision"]
    monkeypatch.setattr(content_access, "enabled", lambda: True)
    monkeypatch.setattr(content_access.STORE, "list",
                        lambda: [_current_grant(ANA, LEE, "approval", approval_id, revision)])
    _as(monkeypatch, ANA)
    card = {"kind": "approval", "id": str(approval_id), "body": "@Lee"}
    assert client.post("/api/v1/comments", json={**card, "mentions": [VIC]}).status_code == 422
    assert client.post("/api/v1/comments", json={**card, "mentions": [LEE]}).status_code == 201
    _directory_unreadable(monkeypatch)
    assert client.post("/api/v1/comments", json={**card, "mentions": [LEE]}).status_code == 503
    assert client.post("/api/v1/comments", json={**card, "mentions": [VIC]}).status_code == 422


def test_a_policy_mention_is_checked_for_the_person_named_not_against_a_capped_list(env, monkeypatch):
    # The mention list shows at most 200 people; a mention of someone past
    # them who holds the permission is still accepted.
    from maverick_dashboard import auth, rbac

    client, _world = env
    roles = {**ROLES, **{f"user:op{index:03}@corp.com": "operator" for index in range(250)}}
    monkeypatch.setattr(rbac, "list_users", lambda: dict(roles))
    _as(monkeypatch, DANA)
    monkeypatch.setattr(auth, "global_role_for_principal", lambda p, **kw: roles.get(p))
    last = "user:op249@corp.com"
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "@op249",
                                                   "mentions": [last]})
    assert posted.status_code == 201, posted.text


def test_a_role_from_an_unreadable_group_mapping_holds_a_mention_instead_of_refusing_it(env, monkeypatch):
    # Lee's operator role comes from a company-directory group. While the
    # group mapping cannot be read, sign-in treats Lee as a viewer (the safe
    # answer for access), but a mention is held, not refused.
    from maverick_dashboard import auth, rbac, scim_groups

    client, _world = env
    roles = {key: value for key, value in ROLES.items() if key != LEE}
    monkeypatch.setattr(rbac, "list_users", lambda: dict(roles))
    real_role = auth.global_role_for_principal
    _as(monkeypatch, DANA)
    monkeypatch.setattr(auth, "global_role_for_principal", real_role)
    monkeypatch.setattr(rbac, "get_stored_role", lambda p: roles.get(p))
    monkeypatch.setattr(scim_groups, "_ci_mapping", lambda key: {"ops": "operator"})
    monkeypatch.setattr(scim_groups, "group_names_for_principal",
                        lambda p: frozenset({"ops"}) if p == LEE else frozenset())
    mention = {"kind": "policy", "id": "tools", "body": "@Lee", "mentions": [LEE]}
    assert client.post("/api/v1/comments", json=mention).status_code == 201

    def unreadable(key):
        raise OSError("group mapping unreadable")

    monkeypatch.setattr(scim_groups, "_ci_mapping", unreadable)
    assert scim_groups.role_for_principal(LEE) == "viewer"
    assert client.post("/api/v1/comments", json=mention).status_code == 503


def test_posting_a_mention_reads_the_directory_once_for_the_check(env, monkeypatch):
    # One read lists the policy's people, one decides the mention; the
    # posted thread reuses the first instead of reading again.
    from maverick_dashboard import scim_groups

    client, _world = env
    _as(monkeypatch, DANA)
    reads, real = [], scim_groups.active_check
    monkeypatch.setattr(scim_groups, "active_check", lambda: reads.append(1) or real())
    posted = client.post("/api/v1/comments", json={"kind": "policy", "id": "tools", "body": "@Lee",
                                                   "mentions": [LEE]})
    assert posted.status_code == 201 and len(reads) == 2
