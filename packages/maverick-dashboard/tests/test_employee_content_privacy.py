"""Scoped employee-content grants and metadata projection regressions."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from maverick_dashboard import auth, content_access


@pytest.fixture
def authority(monkeypatch):
    monkeypatch.setattr(auth, "_authorization_principal", lambda _: "user:admin")
    monkeypatch.setattr(auth, "has_global_permission", lambda *_: True)
    monkeypatch.setattr(content_access, "enabled", lambda: True)
    monkeypatch.setattr(content_access, "tenant", lambda: "workspace-a")
    monkeypatch.setattr(content_access, "_reviewer_current", lambda _: True)
    from maverick_dashboard import scim_groups
    monkeypatch.setattr(scim_groups, "active_for_principal", lambda _: True)
    return SimpleNamespace(method="GET")


def grant(**updates):
    return {"id": "ECA-example", "tenant": "workspace-a", "requester": "user:admin",
            "owner": "user:employee", "resource_kind": "goal", "resource_id": "12",
            "status": "approved", "expires_at": time.time() + 60, "purpose": "employee_support",
            **updates}


def test_admin_role_alone_does_not_reveal_task(authority, monkeypatch):
    monkeypatch.setattr(content_access.STORE, "list", list)
    goal = SimpleNamespace(id=12, owner="user:employee")
    assert auth.goal_owner_filter(authority) == "user:admin"
    assert not auth.can_access_goal(authority, goal)
    assert not auth.can_access_goal_principal("user:admin", goal)


@pytest.mark.parametrize("updates", [
    {"tenant": "workspace-b"}, {"resource_id": "13"}, {"status": "revoked"},
    {"expires_at": 0}, {"_audit_pending": [{"event_id": "pending"}]},
])
def test_stale_or_wrong_scope_grant_denies(authority, monkeypatch, updates):
    monkeypatch.setattr(content_access.STORE, "list", lambda: [grant(**updates)])
    assert not content_access.may_read_employee_content(authority, "user:employee", resource_kind="goal", resource_id="12")


def test_exceptional_read_requires_confirmed_receipt(authority, monkeypatch):
    import maverick.audit
    monkeypatch.setattr(content_access.STORE, "list", lambda: [grant()])
    monkeypatch.setattr(maverick.audit, "record", lambda *_, **__: False)
    assert not auth.can_access_goal(authority, SimpleNamespace(id=12, owner="user:employee"))
    monkeypatch.setattr(maverick.audit, "record", lambda *_, **__: True)
    assert auth.can_access_goal(authority, SimpleNamespace(id=12, owner="user:employee"))
    authority.method = "POST"
    assert not auth.can_access_goal(authority, SimpleNamespace(id=12, owner="user:employee"))


def test_metadata_projection_removes_content_before_search():
    event = {"ts": 1, "kind": "tool_call", "agent": "private prompt", "goal_id": 12,
             "payload": {"actor": "user:employee", "args": {"text": "secret"},
                         "result": "private response", "title": "sensitive title", "cost_usd": 0.1}}
    projected = content_access.audit_metadata(event)
    assert projected["payload"] == {"actor": "user:employee", "cost_usd": 0.1}
    assert "secret" not in str(projected) and "private" not in str(projected)


def test_installation_credential_is_not_employee_identity(monkeypatch):
    request = SimpleNamespace(method="GET")
    monkeypatch.setattr(auth, "_authorization_principal", lambda _: None)
    assert auth.goal_owner_filter(request) == ""
    assert not auth.can_access_goal_principal(None, SimpleNamespace(owner="user:employee"))
    assert auth.can_access_goal_principal(None, SimpleNamespace(owner=""))
    assert not content_access.may_read_employee_content(request, "user:employee", resource_kind="goal", resource_id="12")


def test_owner_approval_requires_current_workspace_membership(monkeypatch):
    from maverick_dashboard import rbac, scim_groups
    monkeypatch.setattr(scim_groups, "active_for_principal", lambda _: True)
    monkeypatch.setattr(rbac, "get_tenant_role", lambda *_: None)
    row = grant(reviewed_by="user:employee")
    assert not content_access._reviewer_current(row)
    monkeypatch.setattr(rbac, "get_tenant_role", lambda *_: "viewer")
    assert content_access._reviewer_current(row)


def test_approval_read_is_exact_card_and_does_not_authorize_task_mutation(authority, monkeypatch):
    import maverick.audit
    monkeypatch.setattr(auth, "has_permission", lambda *_: True)
    monkeypatch.setattr(maverick.audit, "record", lambda *_, **__: True)
    approval = SimpleNamespace(id=9, requested_by="agent:worker", goal_id=12,
        action="send", scope="one reviewed target", detail="private card", status="pending")
    world = SimpleNamespace(get_goal=lambda _: SimpleNamespace(owner="user:employee"))
    resource = content_access.approval_resource(approval, world)
    monkeypatch.setattr(content_access.STORE, "list", lambda: [grant(**resource)])
    assert content_access.can_read_approval(authority, approval, world)
    authority.method = "POST"
    assert not auth.can_access_goal(authority, SimpleNamespace(id=12, owner="user:employee"))
    approval.detail = "changed private card"
    assert not content_access.can_read_approval(authority, approval, world)


def test_private_approval_has_no_ambient_administrator_read(authority, monkeypatch):
    monkeypatch.setattr(auth, "has_permission", lambda *_: True)
    monkeypatch.setattr(content_access.STORE, "list", list)
    approval = SimpleNamespace(id=9, requested_by="user:employee", goal_id=None,
        action="send", scope="one reviewed target", detail="private card", status="pending")
    assert not content_access.can_read_approval(authority, approval, SimpleNamespace())


def test_unidentified_caller_cannot_access_a_missing_record():
    assert not auth.can_access_goal_principal(None, None)
