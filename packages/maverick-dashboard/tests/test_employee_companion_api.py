"""Companion HTTP boundaries with real, temporary policy/history and task stores.

Identity headers are a test-only authentication seam. The router's permission,
current membership, same-origin, context, and policy checks remain real. The
small app deliberately omits the production lifespan and its background jobs.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from maverick.paths import current_tenant_id_strict, tenant_scope
from maverick.world_model import WorldModel
from maverick_dashboard import employee_companion_api as companion

BASE = "/api/v1/employee-companion"
CONTEXT = "X-Lightwork-Companion-Context"
ALICE = "user:alice"
BOB = "user:bob"
ADMIN = "user:administrator"


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _headers(actor=ALICE, tenant="acme", *, context=None):
    headers = {"X-Test-Principal": actor, "X-Test-Tenant": tenant}
    if context is not None:
        headers[CONTEXT] = context
    return headers


def _policy_body(owner=ALICE, revision=0, **changes):
    return {
        "owner": owner,
        "expected_revision": revision,
        "enabled": True,
        "allowed_sources": ["own_tasks"],
        "allowed_tools": ["task_status"],
        "retention_days": 7,
        **changes,
    }


@dataclass
class Environment:
    client: TestClient
    worlds: dict[str, WorldModel]
    features: dict
    memberships: dict[str, dict[str, str]]
    active: dict[str, bool]
    suites: dict[str, frozenset[str] | None]

    def view(self, actor=ALICE, tenant="acme"):
        return _ok(self.client.get(BASE, headers=_headers(actor, tenant)))

    def admin(self, tenant="acme"):
        return _ok(self.client.get(BASE + "/admin", headers=_headers(ADMIN, tenant)))

    def policy(self, owner=ALICE, tenant="acme", **changes):
        revision = self.view(owner, tenant)["policy"]["revision"]
        context = self.admin(tenant)["context"]
        return _ok(self.client.put(
            BASE + "/admin/policy",
            headers=_headers(ADMIN, tenant, context=context),
            json=_policy_body(owner, revision, **changes),
        ))["policy"]

    def message(self, message="what is running?", *, actor=ALICE, tenant="acme", view=None):
        snapshot = self.view(actor, tenant) if view is None else view
        return self.client.post(
            BASE + "/messages",
            headers=_headers(actor, tenant, context=snapshot["context"]),
            json={"message": message, "expected_revision": snapshot["policy"]["revision"]},
        )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from maverick import crypto_at_rest, providers, runner
    from maverick.llm import LLM
    from maverick.tools import ToolRegistry
    from maverick_dashboard import app as dashboard

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MAVERICK_REPLICA_COUNT", "1")
    monkeypatch.delenv("MAVERICK_TENANT", raising=False)
    monkeypatch.delenv("MAVERICK_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("MAVERICK_ENCRYPT_PER_TENANT", "0")
    monkeypatch.setattr(crypto_at_rest, "_KEY_PATH", tmp_path / "test-at-rest.key")

    features = {"employee_companions": True}
    memberships = {
        "acme": {ALICE: "operator", BOB: "viewer", ADMIN: "admin"},
        "globex": {ALICE: "operator", "user:globex-only": "viewer", ADMIN: "admin"},
    }
    active = {}
    suites = {}
    monkeypatch.setattr(companion.config, "get_features", lambda: dict(features))
    monkeypatch.setattr(
        companion.auth, "caller_principal",
        lambda request: request.headers.get("X-Test-Principal") or None,
    )
    monkeypatch.setattr(companion.auth, "anonymous_local_access_allowed", lambda: True)
    monkeypatch.setattr(companion.auth, "is_dashboard_admin", lambda principal: principal == ADMIN)
    # Preserve the legacy authenticated default; absence from the live tenant
    # roster must still be denied by the additional companion membership gate.
    monkeypatch.setattr(companion.auth, "global_role_for_principal", lambda _principal: "operator")
    monkeypatch.setattr(
        companion.rbac, "get_tenant_role",
        lambda tenant, principal: memberships.get(tenant, {}).get(principal),
    )
    monkeypatch.setattr(
        companion.rbac, "list_tenant_roles",
        lambda tenant: dict(memberships.get(tenant, {})),
    )
    monkeypatch.setattr(
        companion.scim_groups, "active_for_principal", lambda principal: active.get(principal, True),
    )
    monkeypatch.setattr(companion, "granted_suites", lambda principal: suites.get(principal))

    worlds = {}
    for tenant in memberships:
        with tenant_scope(tenant=tenant):
            worlds[tenant] = WorldModel(tmp_path / f"{tenant}-world.db")
    monkeypatch.setattr(dashboard, "_world", lambda: worlds[current_tenant_id_strict()])

    def forbidden(*_args, **_kwargs):
        pytest.fail("The bounded companion must not invoke a provider, model, runner, or tool")

    monkeypatch.setattr(providers, "get_provider_client", forbidden)
    monkeypatch.setattr(LLM, "complete", forbidden)
    monkeypatch.setattr(LLM, "complete_async", forbidden)
    monkeypatch.setattr(ToolRegistry, "run", forbidden)
    monkeypatch.setattr(runner, "run_goal_in_thread", forbidden)
    monkeypatch.setattr(runner, "run_goal_in_background", forbidden)
    monkeypatch.setattr(runner, "run_goal_in_background_async", forbidden)

    app = FastAPI()

    @app.middleware("http")
    async def selected_workspace(request, call_next):
        with tenant_scope(tenant=request.headers.get("X-Test-Tenant", "acme")):
            return await call_next(request)

    app.include_router(companion.router)
    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        yield Environment(client, worlds, features, memberships, active, suites)
    for world in worlds.values():
        world.close()


def test_missing_feature_flag_disables_reads_and_messages(environment, monkeypatch):
    environment.policy()
    environment.features.clear()

    def no_sources(*_args, **_kwargs):
        pytest.fail("A disabled companion must not fetch task sources")

    monkeypatch.setattr(companion, "_tasks", no_sources)
    view = environment.view()
    assert view["available"] is False
    assert view["tasks"] == []
    assert environment.message(view=view).status_code == 403
    assert environment.admin()["available"] is False


def test_employee_policy_starts_disabled_and_bounded(environment):
    view = environment.view()
    policy = view["policy"]
    assert view["available"] is False
    assert view["tasks"] == view["history"] == []
    assert policy["revision"] == 0
    assert policy["enabled"] is False
    assert policy["allowed_sources"] == policy["allowed_tools"] == []
    assert policy["memory_enabled"] is False
    assert policy["admin_content_access"] is False
    assert policy["action_mode"] == "read_only"
    assert policy["approvals"] == "writes_blocked"
    assert policy["retention_days"] == 0
    assert environment.message(view=view).status_code == 403


@pytest.mark.parametrize("actor", ["", "service:worker", "user:unassigned"])
def test_named_explicit_member_required_even_with_default_operator(environment, actor):
    response = environment.client.get(BASE, headers=_headers(actor))
    assert response.status_code == 403


def test_config_admin_still_needs_explicit_workspace_membership(environment):
    del environment.memberships["acme"][ADMIN]
    assert environment.client.get(BASE, headers=_headers(ADMIN)).status_code == 403
    assert environment.client.get(BASE + "/admin", headers=_headers(ADMIN)).status_code == 403


@pytest.mark.parametrize("actor", [ALICE, BOB])
def test_policy_administration_requires_admin_permission(environment, actor):
    context = environment.view(actor)["context"]
    assert environment.client.get(BASE + "/admin", headers=_headers(actor)).status_code == 403
    response = environment.client.put(
        BASE + "/admin/policy", headers=_headers(actor, context=context), json=_policy_body(),
    )
    assert response.status_code == 403
    assert environment.view()["policy"]["revision"] == 0


def test_admin_cannot_target_other_tenant_or_inactive_employee(environment):
    context = environment.admin()["context"]
    environment.active[BOB] = False
    for owner in ("user:globex-only", BOB, "user:unassigned"):
        response = environment.client.put(
            BASE + "/admin/policy", headers=_headers(ADMIN, context=context),
            json=_policy_body(owner=owner),
        )
        assert response.status_code == 404, response.text
    assert environment.admin()["policies"] == []
    assert {employee["principal"] for employee in environment.admin()["employees"]} == {ALICE, ADMIN}


def test_owner_only_tasks_and_history_even_for_configuration_admin(environment):
    world = environment.worlds["acme"]
    alice_goal = world.create_goal("Alice private task title", owner=ALICE)
    bob_goal = world.create_goal("Bob private task title", owner=BOB)
    admin_goal = world.create_goal("Administrator own task", owner=ADMIN)
    world.create_goal("Legacy ownerless task")
    for actor in (ALICE, BOB, ADMIN):
        environment.policy(actor)
        assert environment.message(f"Private conversation belonging to {actor}", actor=actor).status_code == 200

    for actor, expected_goal in ((ALICE, alice_goal), (BOB, bob_goal), (ADMIN, admin_goal)):
        view = environment.view(actor)
        assert [task["goal_id"] for task in view["tasks"]] == [expected_goal]
        assert [turn["content"] for turn in view["history"] if turn["role"] == "user"] == [
            f"Private conversation belonging to {actor}",
        ]
        # Query parameters cannot select a different employee's personal view.
        forged = _ok(environment.client.get(
            BASE + "?owner=user:alice", headers=_headers(actor),
        ))
        assert forged["history"] == view["history"]
        assert [task["goal_id"] for task in forged["tasks"]] == [expected_goal]


def test_same_employee_isolated_between_tenants(environment):
    environment.worlds["acme"].create_goal("Acme private task", owner=ALICE)
    environment.worlds["globex"].create_goal("Globex private task", owner=ALICE)
    environment.policy(tenant="acme")
    environment.policy(tenant="globex", retention_days=1)
    assert environment.message("Acme-only private conversation", tenant="acme").status_code == 200

    acme = environment.view(tenant="acme")
    globex = environment.view(tenant="globex")
    assert acme["context"] != globex["context"]
    assert [task["title"] for task in acme["tasks"]] == ["Acme private task"]
    assert [task["title"] for task in globex["tasks"]] == ["Globex private task"]
    assert globex["history"] == []
    assert globex["policy"]["retention_days"] == 1
    assert acme["policy"]["retention_days"] == 7
    assert all(event["action"] != "reply" for event in environment.admin("globex")["events"])


def test_current_department_and_project_scope_applies_to_admin_owned_tasks(environment, monkeypatch):
    world = environment.worlds["acme"]
    environment.policy(ADMIN)
    environment.suites[ADMIN] = frozenset({"finance"})
    own_project = world.create_project("Own project", owner=ADMIN)
    foreign_project = world.create_project("Employee private project", owner=ALICE)
    missing_project = world.create_project("Unavailable project", owner=ADMIN)
    allowed = world.create_goal("Finance task", owner=ADMIN, domain="finance_planning", project_id=own_project)
    world.create_goal("No legal access", owner=ADMIN, domain="legal_review")
    world.create_goal("Foreign project", owner=ADMIN, domain="finance_planning", project_id=foreign_project)
    world.create_goal("Missing project", owner=ADMIN, domain="finance_planning", project_id=missing_project)
    original_get_project = world.get_project
    monkeypatch.setattr(
        world, "get_project",
        lambda project_id: None if project_id == missing_project else original_get_project(project_id),
    )
    assert [task["goal_id"] for task in environment.view(ADMIN)["tasks"]] == [allowed]


def test_policy_compare_and_swap_and_stale_message_fail_closed(environment, monkeypatch):
    first = environment.policy()
    stale = environment.view()
    current = environment.policy(retention_days=1)
    assert current["revision"] == first["revision"] + 1
    context = environment.admin()["context"]
    response = environment.client.put(
        BASE + "/admin/policy", headers=_headers(ADMIN, context=context),
        json=_policy_body(revision=first["revision"], enabled=False),
    )
    assert response.status_code == 409
    assert environment.view()["policy"] == current

    def no_sources(*_args, **_kwargs):
        pytest.fail("A stale policy revision must be rejected before reading sources")

    monkeypatch.setattr(companion, "_tasks", no_sources)
    assert environment.message(view=stale).status_code == 409
    assert all(event["action"] != "reply" for event in environment.admin()["events"])


@pytest.mark.parametrize("actor,tenant", [(BOB, "acme"), (ALICE, "globex")])
def test_stale_account_or_workspace_context_rejected_for_reads_and_writes(environment, actor, tenant):
    environment.policy()
    environment.policy(actor, tenant)
    original = environment.view()
    wrong_context = _headers(actor, tenant, context=original["context"])
    assert environment.client.get(BASE, headers=wrong_context).status_code == 409
    assert environment.client.post(
        BASE + "/messages", headers=wrong_context,
        json={"message": "Private stale-tab message", "expected_revision": 1},
    ).status_code == 409
    assert environment.client.delete(BASE + "/history", headers=wrong_context).status_code == 409
    assert environment.view(actor, tenant)["history"] == []
    assert all(event["action"] != "reply" for event in environment.admin(tenant)["events"])


@pytest.mark.parametrize("operation", ["message", "clear", "policy"])
def test_writes_require_context_from_current_get(environment, operation):
    environment.policy()
    if operation == "message":
        response = environment.client.post(
            BASE + "/messages", headers=_headers(),
            json={"message": "what is running?", "expected_revision": 1},
        )
    elif operation == "clear":
        response = environment.client.delete(BASE + "/history", headers=_headers())
    else:
        response = environment.client.put(
            BASE + "/admin/policy", headers=_headers(ADMIN), json=_policy_body(revision=1),
        )
    assert response.status_code == 409


@pytest.mark.parametrize("operation", ["message", "clear", "policy"])
def test_foreign_origin_blocked_even_with_valid_context(environment, operation):
    environment.policy()
    actor = ADMIN if operation == "policy" else ALICE
    headers = _headers(actor, context=environment.view(actor)["context"])
    headers["Origin"] = "https://untrusted.example"
    if operation == "message":
        response = environment.client.post(
            BASE + "/messages", headers=headers,
            json={"message": "what is running?", "expected_revision": 1},
        )
    elif operation == "clear":
        response = environment.client.delete(BASE + "/history", headers=headers)
    else:
        response = environment.client.put(
            BASE + "/admin/policy", headers=headers, json=_policy_body(revision=1),
        )
    assert response.status_code == 403


@pytest.mark.parametrize("extra", [
    {"owner": BOB}, {"tenant": "globex"}, {"tools": ["shell"]},
    {"memory_enabled": True}, {"expected_revision": True}, {"message": 123},
])
def test_message_payload_rejects_authority_spoofing_and_coercion(environment, extra):
    environment.policy()
    view = environment.view()
    response = environment.client.post(
        BASE + "/messages", headers=_headers(context=view["context"]),
        json={"message": "what is running?", "expected_revision": 1, **extra},
    )
    assert response.status_code == 422
    assert environment.view()["history"] == []


@pytest.mark.parametrize("changes", [
    {"allowed_sources": ["all_documents"]}, {"allowed_tools": ["shell"]},
    {"memory_enabled": True}, {"admin_content_access": True},
    {"approvals": "automatic"}, {"action_mode": "write"},
    {"tenant": "globex"}, {"retention_days": 365}, {"retention_days": True},
    {"enabled": "true"}, {"expected_revision": True},
])
def test_policy_payload_cannot_expand_supported_capabilities(environment, changes):
    context = environment.admin()["context"]
    response = environment.client.put(
        BASE + "/admin/policy", headers=_headers(ADMIN, context=context),
        json=_policy_body(**changes),
    )
    assert response.status_code == 422
    assert environment.view()["policy"]["revision"] == 0


@pytest.mark.parametrize("revocation", ["membership", "offboarding", "policy", "sources", "tools"])
def test_current_revocation_prevents_source_fetch(environment, monkeypatch, revocation):
    environment.policy()
    previous = environment.view()
    if revocation == "membership":
        del environment.memberships["acme"][ALICE]
    elif revocation == "offboarding":
        environment.active[ALICE] = False
    else:
        change = {"enabled": False} if revocation == "policy" else {f"allowed_{revocation}": []}
        environment.policy(**change)

    def no_sources(*_args, **_kwargs):
        pytest.fail("Revoked authority must be denied before fetching any task source")

    monkeypatch.setattr(companion, "_tasks", no_sources)
    expected = 403 if revocation in {"membership", "offboarding"} else 409
    assert environment.message(view=previous).status_code == expected
    if expected == 409:
        now = environment.view()
        assert now["available"] is False
        assert now["tasks"] == []
        assert environment.message(view=now).status_code == 403


def test_department_revocation_hides_current_sources_and_historical_links(environment):
    world = environment.worlds["acme"]
    world.create_goal("Previously allowed confidential finance title", owner=ALICE, domain="finance_planning")
    environment.policy()
    environment.suites[ALICE] = frozenset({"finance"})
    first = _ok(environment.message())
    assert len(first["message"]["sources"]) == 1
    environment.suites[ALICE] = frozenset()
    second = _ok(environment.message())
    assert second["message"]["sources"] == []
    assert "Previously allowed confidential finance title" not in str(second)
    assert all(turn.get("sources", []) == [] for turn in second["history"])


def test_department_revoke_waits_for_authority_bound_read(environment, monkeypatch):
    from maverick import authority_effects, suite_grants

    monkeypatch.setattr(suite_grants, "_audit_grant_change", lambda *_args: None)
    suite_grants.set_suites(ALICE, ["finance"], actor=ADMIN)
    original_fence = authority_effects.authority_effect_fence
    original_write = suite_grants._write
    revoke_reached_fence = Event()
    read_in_progress = Event()

    @contextmanager
    def observed_fence():
        revoke_reached_fence.set()
        with original_fence():
            yield

    def guarded_write(data):
        assert not read_in_progress.is_set(), "Department revoke passed an active authority fence"
        original_write(data)

    monkeypatch.setattr(authority_effects, "authority_effect_fence", observed_fence)
    monkeypatch.setattr(suite_grants, "_write", guarded_write)
    with ThreadPoolExecutor(max_workers=1) as worker:
        with original_fence():
            read_in_progress.set()
            future = worker.submit(suite_grants.set_suites, ALICE, [], actor=ADMIN)
            try:
                assert revoke_reached_fence.wait(timeout=5), "Revoke did not use the authority fence"
                assert not future.done()
                assert suite_grants.get_grant(ALICE) == frozenset({"finance"})
            finally:
                read_in_progress.clear()
        future.result(timeout=5)
    assert suite_grants.get_grant(ALICE) == frozenset()


def test_active_durable_work_does_not_block_replies_or_mutate_work(environment):
    world = environment.worlds["acme"]
    goal = world.create_goal("Already running task", owner=ALICE)
    world.set_goal_status(goal, "active")
    environment.policy()
    for text in ("what is running?", "what needs attention?", f"cancel goal {goal}"):
        reply = _ok(environment.message(text))
        assert reply["message"]["trace"]["decision"] == "allowed"
        assert world.get_goal(goal).status == "active"
    assert len(world.list_goals(owner=ALICE)) == 1
    assert len(environment.view()["history"]) == 6


def test_admin_metadata_never_contains_private_conversation_or_task_titles(environment, monkeypatch):
    from maverick import crypto_at_rest

    world = environment.worlds["acme"]
    world.create_goal("Private merger plan title", owner=ALICE)
    environment.policy()
    private_text = "Private health and family discussion"
    assert environment.message(private_text).status_code == 200
    assert environment.message().status_code == 200

    def no_decryption(*_args, **_kwargs):
        pytest.fail("Administrative metadata must not decrypt employee conversations")

    monkeypatch.setattr(crypto_at_rest, "unseal_bound", no_decryption)
    admin = environment.admin()
    assert private_text not in str(admin)
    assert "Private merger plan title" not in str(admin)
    assert "history" not in admin
    assert any(event["action"] == "reply" for event in admin["events"])
    forbidden_fields = {"message", "response", "content", "history", "source_refs", "sources"}
    assert all(not forbidden_fields.intersection(event) for event in admin["events"])
    assert all(policy["admin_content_access"] is False for policy in admin["policies"])


def test_clear_only_removes_callers_history_and_works_when_feature_is_off(environment):
    for actor in (ALICE, BOB):
        environment.policy(actor)
        assert environment.message(f"Private text for {actor}", actor=actor).status_code == 200
    view = environment.view()
    environment.features.clear()
    response = environment.client.delete(
        BASE + "/history?owner=user:bob", headers=_headers(context=view["context"]),
    )
    assert _ok(response)["history"] == []
    assert environment.view()["history"] == []
    assert len(environment.view(BOB)["history"]) == 2


def test_zero_retention_returns_reply_without_saving_private_text(environment):
    environment.policy(retention_days=0)
    reply = _ok(environment.message("Do not retain this private text"))
    assert reply["message"]["content"]
    assert reply["history"] == []
    assert environment.view()["history"] == []
    assert "Do not retain this private text" not in str(environment.admin())


def test_required_state_and_audit_publication_failure_returns_no_answer(environment, monkeypatch):
    from maverick import employee_companion

    environment.policy()
    view = environment.view()
    events = environment.admin()["events"]

    def cannot_publish(*_args, **_kwargs):
        raise OSError("Synthetic companion authority publication failure")

    monkeypatch.setattr(employee_companion.file_lock, "atomic_write_text", cannot_publish)
    response = environment.message("Never publish this private text", view=view)
    assert response.status_code == 503
    assert "message" not in response.json()
    assert "Never publish this private text" not in response.text
    assert environment.view()["history"] == []
    assert environment.admin()["events"] == events


def test_personal_and_administrative_responses_are_not_cacheable(environment):
    for suffix, actor in (("", ALICE), ("/admin", ADMIN)):
        response = environment.client.get(BASE + suffix, headers=_headers(actor))
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "private, no-store"
