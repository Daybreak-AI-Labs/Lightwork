"""Dashboard RBAC: the role store, role resolution, and gate enforcement.

Safety invariants under test:
  * auth OFF (no principal) -> every gate is a no-op (single-user local mode);
  * a config-pinned bootstrap admin is always admin (can't be locked out);
  * viewer is read-only, operator can run goals, only admin reaches settings/users.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


# ---------- store + role logic (no HTTP) ----------

def test_store_roundtrip_and_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import rbac
    assert rbac.list_users() == {}
    rbac.set_role("user:alice", "viewer")
    assert rbac.list_users() == {"user:alice": "viewer"}
    rbac.set_role("user:alice", "operator")
    assert rbac.get_stored_role("user:alice") == "operator"
    rbac.remove_user("user:alice")
    assert rbac.list_users() == {}
    with pytest.raises(ValueError):
        rbac.set_role("user:x", "superuser")
    with pytest.raises(ValueError):
        rbac.set_role("", "viewer")


def test_final_global_admin_cannot_self_demote_or_remove(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import rbac

    rbac.set_role("user:last-admin", "admin", actor="local")

    with pytest.raises(ValueError, match="final global administrator"):
        rbac.set_role(
            "user:last-admin", "viewer", actor="user:last-admin"
        )
    assert rbac.get_stored_role("user:last-admin") == "admin"

    with pytest.raises(ValueError, match="final global administrator"):
        rbac.remove_user("user:last-admin", actor="user:last-admin")
    assert rbac.get_stored_role("user:last-admin") == "admin"


def test_admin_can_demote_when_another_recovery_admin_remains(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import rbac

    rbac.set_role("user:first", "admin")
    rbac.set_role("user:second", "admin")
    rbac.set_role("user:first", "viewer", actor="user:first")

    assert rbac.get_stored_role("user:first") == "viewer"
    assert rbac.get_stored_role("user:second") == "admin"


def test_corrupt_roster_denies_and_mutator_refuses_overwrite(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path / ".maverick"))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    path = rbac.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = '{"user:viewer":"viewer","user:viewer":"operator"}'
    path.write_text(original, encoding="utf-8")
    with pytest.raises(rbac.RbacStoreError):
        rbac.list_users()
    assert auth.role_for_principal("user:viewer") is None
    with pytest.raises(rbac.RbacStoreError):
        rbac.set_role("user:new", "admin")
    assert path.read_text(encoding="utf-8") == original


def test_concurrent_set_role_does_not_lose_assignments(monkeypatch, tmp_path):
    """set_role does a load-modify-save; without the lock two concurrent role
    assignments both load the same roster and the second drops the first -- a
    lost role grant/revoke on a security store. All N must survive."""
    import threading

    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick_dashboard import rbac
    n = 24

    def assign(i: int):
        rbac.set_role(f"user:u{i:03d}", "viewer")

    threads = [threading.Thread(target=assign, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(rbac.list_users()) == n
    assert list((tmp_path / ".maverick").glob("*.tmp")) == []


def test_permissions_map():
    from maverick_dashboard import rbac
    assert "admin" in rbac.permissions_for("admin")
    assert rbac.permissions_for("operator") == frozenset({"operate", "view"})
    assert rbac.permissions_for("viewer") == frozenset({"view"})
    assert rbac.permissions_for(None) == frozenset()
    # Separation of duties: only admin and the dedicated auditor role hold the
    # "audit" permission; operator/viewer never do. The auditor grants ONLY
    # read access (audit + view), nothing operational.
    assert rbac.permissions_for("auditor") == frozenset({"audit", "view"})
    assert "audit" in rbac.permissions_for("admin")
    assert "audit" not in rbac.permissions_for("operator")
    assert "audit" not in rbac.permissions_for("viewer")
    assert "operate" not in rbac.permissions_for("auditor")
    assert "admin" not in rbac.permissions_for("auditor")
    assert "auditor" in rbac.ROLES


def test_role_for_principal(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    from maverick_dashboard import auth, rbac
    assert auth.role_for_principal(None) is None              # auth off -> unrestricted
    assert auth.role_for_principal("user:boss") == "admin"     # bootstrap admin
    rbac.set_role("user:v", "viewer")
    assert auth.role_for_principal("user:v") == "viewer"       # stored
    assert auth.role_for_principal("user:new") == "operator"   # default


# ---------- HTTP enforcement ----------

def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick_dashboard import app as dash_app
    dash_app._world_cache.clear()
    return TestClient(dash_app.app, headers={"Origin": "http://testserver"})


def _as(monkeypatch, principal):
    """Simulate an authenticated caller with a given principal (the gates read
    caller_principal in auth.py)."""
    from maverick_dashboard import auth
    monkeypatch.setattr(auth, "caller_principal", lambda request: principal)


def test_auth_off_is_a_noop(monkeypatch, tmp_path):
    # No principal (local mode): admin surfaces stay reachable, exactly as before.
    c = _client(monkeypatch, tmp_path)
    assert c.get("/settings").status_code == 200
    assert c.get("/users").status_code == 200


def test_viewer_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vy", "viewer")
    _as(monkeypatch, "user:vy")
    assert c.get("/settings").status_code == 403           # admin surface
    assert c.get("/users").status_code == 403
    assert c.post("/settings/capabilities", data={}).status_code == 403
    assert c.post("/chat/send", data={"title": "hi"}).status_code == 403   # operate


def test_viewer_cannot_run_goals_via_compose_or_resume(monkeypatch, tmp_path):
    # compose and resume both spend provider money (they queue run_goal_in_thread),
    # so they are "operate" actions: a read-only viewer must be 403'd before any
    # provider-key / goal-state check, exactly like /chat/send and POST /goals.
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vz", "viewer")
    _as(monkeypatch, "user:vz")
    assert c.post("/api/v1/goals/compose",
                  json={"title": "spend money"}).status_code == 403
    assert c.post("/api/v1/goals/1/resume").status_code == 403


def test_viewer_cannot_run_create_or_delete_fleets(monkeypatch, tmp_path):
    # The fleet routes (run/create/delete) dispatch governed goals and mutate
    # fleet config -- "operate" actions. They are owner-scoped, but owner-scoping
    # is not authz: a viewer could self-own a created fleet and then run it. A
    # read-only viewer must be 403'd before any provider-key / owner check, just
    # like compose/resume. Regression for the missing require_permission gate.
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vf", "viewer")
    _as(monkeypatch, "user:vf")
    assert c.post("/api/v1/fleets/myfleet/run",
                  json={"agent": "a", "prompt": "spend money"}).status_code == 403
    assert c.post("/api/v1/fleets",
                  json={"name": "myfleet", "agents": []}).status_code == 403
    assert c.delete("/api/v1/fleets/myfleet").status_code == 403


def test_operator_can_operate_but_not_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op", "operator")
    _as(monkeypatch, "user:op")
    assert c.get("/settings").status_code == 403           # not admin
    # operate is allowed: chat/send clears the role gate (then 400s on no key, not 403)
    assert c.post("/chat/send", data={"title": "hi"}).status_code != 403


def test_admin_manages_users_and_cannot_be_locked_out(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    c = _client(monkeypatch, tmp_path)
    _as(monkeypatch, "user:boss")
    assert c.get("/settings").status_code == 200
    assert c.get("/users").status_code == 200
    assert c.post("/users/set",
                  data={"principal": "user:alice", "role": "operator"}).status_code == 200
    from maverick_dashboard import auth, rbac
    assert rbac.get_stored_role("user:alice") == "operator"
    # the bootstrap admin stays admin no matter what the store says
    rbac.set_role("user:boss", "viewer")
    assert auth.role_for_principal("user:boss") == "admin"


def test_users_page_refuses_last_admin_self_demotion(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac

    rbac.set_role("user:last-admin", "admin")
    _as(monkeypatch, "user:last-admin")
    response = c.post(
        "/users/set",
        data={"principal": "user:last-admin", "role": "viewer"},
    )

    assert response.status_code == 409
    assert "final global administrator" in response.json()["detail"]
    assert rbac.get_stored_role("user:last-admin") == "admin"


def test_users_link_in_nav(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert 'href="/users"' in c.get("/").text


def test_audit_endpoints_require_audit_permission(monkeypatch, tmp_path):
    # The audit trail (who-did-what-when) is gated by the "audit" permission:
    # an operator/viewer is 403'd, auth-off (local mode) and admin get through.
    # Regression for the gap where /api/v1/audit/tail + /audit/grep had no gate.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op2", "operator")
    _as(monkeypatch, "user:op2")
    assert c.get("/api/v1/audit/tail").status_code == 403
    assert c.get("/api/v1/audit/grep?pattern=x").status_code == 403
    # a viewer is likewise 403'd: read-of-the-trail is a distinct grant.
    rbac.set_role("user:vw2", "viewer")
    _as(monkeypatch, "user:vw2")
    assert c.get("/api/v1/audit/tail").status_code == 403
    # auth-off (no principal -> local single-user mode) is unrestricted.
    _as(monkeypatch, None)
    assert c.get("/api/v1/audit/tail").status_code == 200


def test_audit_html_page_requires_audit_permission(monkeypatch, tmp_path):
    # The /audit HTML page serves the same audit-log tail as /api/v1/audit/tail,
    # so it must enforce the same "audit" gate -- otherwise a view-only caller
    # reads the trail via HTML despite the API denying them.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vw3", "viewer")
    _as(monkeypatch, "user:vw3")
    assert c.get("/audit").status_code == 403
    # auth-off (local single-user) still renders the page.
    _as(monkeypatch, None)
    assert c.get("/audit").status_code == 200


def test_approvals_html_page_requires_operate_permission(monkeypatch, tmp_path):
    # The /approvals HTML page lists the shared supervision queue (no per-user
    # owner, may carry another user's goal detail), matching /api/v1/approvals,
    # so a view-only caller must be 403'd here too.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vw4", "viewer")
    _as(monkeypatch, "user:vw4")
    assert c.get("/approvals").status_code == 403
    _as(monkeypatch, None)
    assert c.get("/approvals").status_code == 200


def test_auditor_reads_audit_but_nothing_operational(monkeypatch, tmp_path):
    # Separation of duties: the auditor role reaches the audit trail (read-only)
    # but is 403'd from every operate/admin surface -- it can review the
    # who-did-what-when record without being able to run goals, change settings,
    # or manage users.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:aud", "auditor")
    _as(monkeypatch, "user:aud")
    # audit surface: reachable (200, not 403).
    assert c.get("/api/v1/audit/tail").status_code == 200
    assert c.get("/api/v1/audit/grep?pattern=x").status_code == 200
    # admin surfaces: denied.
    assert c.get("/settings").status_code == 403
    assert c.get("/users").status_code == 403
    # operate surfaces: denied (no running goals, no spending).
    assert c.post("/chat/send", data={"title": "hi"}).status_code == 403
    assert c.post("/api/v1/goals/compose",
                  json={"title": "spend money"}).status_code == 403


def test_viewer_cannot_mutate_owned_goal(monkeypatch, tmp_path):
    # answer/retitle/reparent are "operate" actions. A viewer who OWNS a goal
    # (so the access check passes) must still be 403'd by the role gate -- owner
    # access is not authz. Regression for the missing require_permission gate.
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vm", "viewer")
    from maverick.world_model import WorldModel
    gid = WorldModel(tmp_path / "world.db").create_goal("owned", owner="user:vm")
    _as(monkeypatch, "user:vm")
    assert c.post(f"/api/v1/goals/{gid}/answer",
                  json={"question_id": 1, "answer": "x"}).status_code == 403
    assert c.post(f"/api/v1/goals/{gid}/retitle",
                  json={"title": "new", "expected_updated_at": 1}).status_code == 403
    assert c.post(f"/api/v1/goals/{gid}/reparent",
                  json={"parent_id": None, "expected_updated_at": 1}).status_code == 403


def test_redact_preview_requires_operate(monkeypatch, tmp_path):
    # /redact/preview ran the detector pipeline on caller text unauthenticated;
    # it now requires "operate" (a viewer is 403'd).
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:vp", "viewer")
    _as(monkeypatch, "user:vp")
    assert c.post("/api/v1/redact/preview", json={"text": "hi"}).status_code == 403


def test_validate_agent_override_requires_pack_admin(monkeypatch, tmp_path):
    # /agents/{name}/validate (pack-edit lint) was unauthenticated; it now uses
    # the same pack-admin gate as save/delete override.
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import api
    monkeypatch.setattr(api, "caller_principal", lambda request: "user:alice")
    monkeypatch.setattr(api, "is_dashboard_admin", lambda principal: False)
    assert c.post("/api/v1/agents/orchestrator/validate", json={}).status_code == 403


def test_enable_learning_requires_admin(monkeypatch, tmp_path):
    # POST /api/v1/learning writes the deployment-wide config overlay (like
    # [capabilities]/[features], which are admin-gated). A tenant operator must
    # not be able to flip it (force-enable trajectory capture deployment-wide).
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op-lrn", "operator")
    _as(monkeypatch, "user:op-lrn")
    assert c.post("/api/v1/learning", json={"enabled": True}).status_code == 403

    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:ad-lrn")
    _as(monkeypatch, "user:ad-lrn")
    assert c.post("/api/v1/learning", json={"enabled": True}).status_code == 200


def test_oauth_provider_trigger_requires_admin(monkeypatch, tmp_path):
    # A trigger that reads a vaulted OAuth token (config.provider) attaches that
    # token to a caller-supplied url/token_url at poll time. That's the trust of
    # authorizing the token, so it must require admin -- an operator must not be
    # able to name a provider and exfiltrate its token.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op-oauth", "operator")
    _as(monkeypatch, "user:op-oauth")
    r = c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "oauth_http_json",
        "config": {"provider": "slack", "url": "https://x", "token_url": "https://x",
                   "client_id": "x"}})
    assert r.status_code == 403
    # A non-vault trigger stays operate-allowed (not 403; it may 404 on template).
    r2 = c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "http_json", "config": {"url": "https://x"}})
    assert r2.status_code != 403


def test_imap_password_env_trigger_requires_admin(monkeypatch, tmp_path):
    # IMAP triggers read a process env var named by password_env and send it to
    # the configured IMAP host as the password. That shared-secret boundary is
    # admin trust, not operator trust.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac
    rbac.set_role("user:op-imap", "operator")
    _as(monkeypatch, "user:op-imap")

    r = c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "imap_email",
        "config": {"host": "attacker.example", "username": "u",
                   "password_env": "OPENAI_API_KEY"}})
    assert r.status_code == 403

    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:admin-imap")
    _as(monkeypatch, "user:admin-imap")
    r2 = c.post("/api/v1/event-triggers", json={
        "template": "triage", "source": "imap_email",
        "config": {"host": "mail.example", "username": "u",
                   "password_env": "IMAP_PASSWORD"}})
    assert r2.status_code != 403


@pytest.mark.parametrize(
    ("source", "config"),
    [
        (
            "imap_email",
            {
                "host": "mail.example",
                "username": "u",
                "password_env": "IMAP_PASSWORD",
            },
        ),
        ("github_issues", {"owner": "acme", "repo": "private"}),
        ("file_dir", {"path": "incoming"}),
    ],
)
def test_host_resource_event_sources_reject_tenant_local_admin(
    monkeypatch, tmp_path, source, config
):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import rbac

    principal = "user:tenant-admin"
    rbac.set_role(principal, "viewer")
    rbac.set_tenant_role("acme", principal, "admin")
    _as(monkeypatch, principal)

    response = c.post(
        "/api/v1/event-triggers",
        json={"template": "triage", "source": source, "config": config},
    )

    assert response.status_code == 403


def test_file_dir_trigger_rejects_paths_outside_tenant_event_input(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:root")
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    c = _client(monkeypatch, tmp_path)
    _as(monkeypatch, "user:root")

    outside = tmp_path / "outside"
    response = c.post(
        "/api/v1/event-triggers",
        json={
            "template": "triage",
            "source": "file_dir",
            "config": {"path": str(outside)},
        },
    )

    assert response.status_code == 400
    assert "event-input root" in response.json()["detail"]


def test_file_dir_trigger_rejects_parent_traversal_glob(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:root")
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_TENANT", "acme")
    c = _client(monkeypatch, tmp_path)
    _as(monkeypatch, "user:root")

    response = c.post(
        "/api/v1/event-triggers",
        json={
            "template": "triage",
            "source": "file_dir",
            "config": {"path": "incoming", "glob": "../*"},
        },
    )

    assert response.status_code == 400
    assert "cannot traverse" in response.json()["detail"]


def test_shared_halt_requires_admin(monkeypatch, tmp_path):
    # The shared Postgres halt is a global, untenanted fleet control. A tenant
    # operator may operate local goals, but must not arm the cluster-wide halt.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import api, rbac

    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setattr(api, "_shared_halt_backend", lambda: True)
    rbac.set_role("user:op-halt", "operator")
    _as(monkeypatch, "user:op-halt")

    r = c.post("/api/v1/halt", json={"reason": "tenant halt"})
    assert r.status_code == 403
    assert not (tmp_path / "HALT").exists()

    r = c.delete("/api/v1/halt")
    assert r.status_code == 403


def test_workspace_viewer_can_see_stop_without_incident_details(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import api, rbac

    path = tmp_path / "HALT-viewer"
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(path))
    monkeypatch.setattr(api, "_shared_halt_backend", lambda: False)
    rbac.set_role("user:viewer-halt", "viewer")
    _as(monkeypatch, "user:viewer-halt")
    path.write_text('{"reason":"private incident","actor":"private actor"}', encoding="utf-8")

    status = c.get("/api/v1/halt")
    assert status.status_code == 200
    assert status.json() == {"active": True, "scope": "local"}
    assert c.delete("/api/v1/halt").status_code == 403


def test_shared_halt_allows_admin(monkeypatch, tmp_path):
    # Dashboard admins retain the ability to toggle the global shared halt.
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:root")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import api

    class FakeWorld:
        def __init__(self):
            self.armed = False
            self.generation = None

        def arm_halt(self, reason="", source="manual", armed_by=""):
            self.armed = True
            self.generation = "shared-generation-admin"
            self.reason = reason
            self.source = source
            self.armed_by = armed_by
            return self.generation

        def active_halt(self):
            if not self.armed:
                return None
            return {
                "reason": self.reason,
                "source": self.source,
                "armed_by": self.armed_by,
                "armed_at": 123.0,
                "generation": self.generation,
            }

        def disarm_halt(self, *, expected_generation, durable_clear=None):
            assert expected_generation == self.generation
            assert durable_clear is not None
            durable_clear()
            self.armed = False
            self.generation = None
            return True

    fake_world = FakeWorld()
    monkeypatch.setenv("MAVERICK_HALT_FILE", str(tmp_path / "HALT"))
    monkeypatch.setattr(api, "_shared_halt_backend", lambda: True)
    monkeypatch.setattr(api, "_world", lambda: fake_world)
    _as(monkeypatch, "user:root")

    r = c.post("/api/v1/halt", json={"reason": "admin halt"})
    assert r.status_code == 204
    assert fake_world.armed is True
    assert fake_world.reason == "admin halt"
    assert (tmp_path / "HALT").exists()

    r = c.delete("/api/v1/halt")
    assert r.status_code == 204
    assert fake_world.armed is False
    assert not (tmp_path / "HALT").exists()


# ---------- a missing roster after roles were assigned ----------

def test_fresh_install_without_a_roster_keeps_the_default(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    assert not rbac.store_path().exists()
    assert rbac.list_users() == {}
    assert rbac.missing_rosters() == []
    assert auth.role_for_principal("user:new") == "operator"


def test_deleted_roster_after_assignment_denies_instead_of_promoting(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:demoted", "viewer")
    rbac.store_path().unlink()

    with pytest.raises(rbac.RosterMissingError):
        rbac.list_users()
    assert auth.role_for_principal("user:demoted") is None       # not the operator default
    assert auth.role_for_principal("user:someone-else") is None
    assert rbac.permissions_for(auth.role_for_principal("user:demoted")) == frozenset()
    with pytest.raises(rbac.RbacStoreError):
        rbac.set_role("user:new", "admin")
    assert not rbac.store_path().exists()                       # never silently recreated
    assert auth.role_for_principal("user:boss") == "admin"      # bootstrap admin keeps access
    assert rbac.missing_rosters() == ["global"]


def test_roster_from_before_the_marker_is_recorded_on_first_read(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    path = rbac.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"user:demoted": "viewer"}', encoding="utf-8")
    path.chmod(0o600)
    assert auth.role_for_principal("user:demoted") == "viewer"
    path.unlink()
    assert auth.role_for_principal("user:demoted") is None


def test_corrupt_roster_with_marker_still_denies(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:demoted", "viewer")
    rbac.store_path().write_text("{not json", encoding="utf-8")
    assert auth.role_for_principal("user:demoted") is None
    with pytest.raises(rbac.RbacStoreError):
        rbac.reset_missing_rosters(actor="user:boss") or rbac.list_users()
    assert rbac.store_path().read_text(encoding="utf-8") == "{not json"   # reset never replaces it


def test_deleted_workspace_roster_denies_instead_of_falling_back(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    from maverick_dashboard import auth, rbac

    monkeypatch.setattr("maverick.paths.current_tenant_id", lambda: "acme")
    rbac.set_tenant_role("acme", "user:demoted", "viewer")
    assert auth.role_for_principal("user:demoted") == "viewer"
    rbac.tenant_store_path().unlink()
    assert auth.role_for_principal("user:demoted") is None       # not the global default
    with pytest.raises(rbac.RosterMissingError):
        rbac.list_tenant_roles("acme")


def test_bootstrap_admin_recovers_a_missing_roster(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:boss")
    c = _client(monkeypatch, tmp_path)
    from maverick_dashboard import auth, rbac

    rbac.set_role("user:demoted", "viewer")
    rbac.store_path().unlink()

    # Ordinary role changes are a plain refusal, not a 500 or a recreated roster.
    _as(monkeypatch, "user:boss")
    refused = c.post("/users/set", data={"principal": "user:x", "role": "viewer"},
                     headers={"Accept": "application/json"})
    assert refused.status_code == 503
    assert refused.json()["code"] == "access_roles_unavailable"
    assert not rbac.store_path().exists()

    # Others cannot see or start the recovery.
    _as(monkeypatch, "user:demoted")
    assert c.get("/users").status_code == 403
    assert c.post("/users/roster/reset", data={"confirm": "reset"}).status_code == 403

    _as(monkeypatch, "user:boss")
    page = c.get("/users")
    assert page.status_code == 503
    assert 'action="/users/roster/reset"' in page.text
    assert c.post("/users/roster/reset", data={}).status_code == 422
    assert not rbac.store_path().exists()
    done = c.post("/users/roster/reset", data={"confirm": "reset"}, follow_redirects=False)
    assert done.status_code == 303
    assert rbac.list_users() == {}
    assert c.post("/users/roster/reset", data={"confirm": "reset"}).status_code == 409
    assert c.post("/users/set", data={"principal": "user:demoted", "role": "viewer"}).status_code == 200
    assert auth.role_for_principal("user:demoted") == "viewer"


def test_lost_workspace_membership_history_refuses_new_provisioning(monkeypatch, tmp_path):
    from maverick.tenant import registry
    from maverick_dashboard import rbac

    assert rbac.tenant_store_path().parent == rbac.store_path().parent
    assert registry._legacy_role_names() == set()          # fresh install: clean history
    rbac.set_tenant_role("acme", "user:a", "viewer")
    assert registry._legacy_role_names()
    rbac.tenant_store_path().unlink()
    with pytest.raises(registry.TenantRegistryError):
        registry._legacy_role_names()
