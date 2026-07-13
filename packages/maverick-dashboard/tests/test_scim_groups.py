"""SCIM Groups + the group -> role / department mapping.

The /Groups resource stores IdP-pushed team membership; ``scim_groups`` maps
those teams to dashboard roles ([dashboard] group_roles) and department grants
([dashboard] group_suites). Under test:
  * the Okta/Entra Group lifecycle (create/filter/patch members/delete);
  * principal matching by externalId and via the subject directory
    (pairwise-sub IdPs);
  * resolution order — explicit assignment beats group-derived beats default —
    for both roles and suites, including through the KERNEL grant chain.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

_TOKEN = "scim-secret-token-xyz"  # pragma: allowlist secret
_GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


def _client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", _TOKEN)
    return _client()


def _mk_user(client, username, external_id=""):
    r = client.post("/scim/v2/Users", json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": username,
        "externalId": external_id,
        "emails": [{"value": username, "primary": True}],
        "active": True,
    }, headers=_auth())
    assert r.status_code == 201
    return r.json()["id"]


def _mk_group(client, name, members=()):
    r = client.post("/scim/v2/Groups", json={
        "schemas": [_GROUP_SCHEMA],
        "displayName": name,
        "members": [{"value": m} for m in members],
    }, headers=_auth())
    assert r.status_code == 201
    return r.json()["id"]


def _map_groups(monkeypatch, roles=None, suites=None):
    """Point the mapping tables at a test dict (targeted; avoids swapping the
    whole config for every subsystem a request touches)."""
    from maverick_dashboard import scim_groups
    tables = {"group_roles": roles or {}, "group_suites": suites or {}}
    monkeypatch.setattr(scim_groups, "_config_table",
                        lambda key: tables.get(key, {}))


# ---------- /Groups resource lifecycle ----------

def test_group_crud_and_filter(client):
    uid = _mk_user(client, "alice@example.com", external_id="okta-sub-1")
    gid = _mk_group(client, "Finance Team", [uid])

    listed = client.get("/scim/v2/Groups", headers=_auth()).json()
    assert listed["totalResults"] == 1
    assert listed["Resources"][0]["displayName"] == "Finance Team"
    assert listed["Resources"][0]["members"][0]["value"] == uid

    # displayName eq filter (the IdP existence probe).
    r = client.get('/scim/v2/Groups?filter=displayName eq "Finance Team"',
                   headers=_auth())
    assert r.json()["totalResults"] == 1
    r = client.get('/scim/v2/Groups?filter=displayName eq "Nope"',
                   headers=_auth())
    assert r.json()["totalResults"] == 0

    # Duplicate displayName -> SCIM uniqueness conflict.
    r = client.post("/scim/v2/Groups", json={
        "schemas": [_GROUP_SCHEMA], "displayName": "finance team"},
        headers=_auth())
    assert r.status_code == 409

    assert client.get(f"/scim/v2/Groups/{gid}",
                      headers=_auth()).status_code == 200
    assert client.delete(f"/scim/v2/Groups/{gid}",
                         headers=_auth()).status_code == 204
    assert client.get(f"/scim/v2/Groups/{gid}",
                      headers=_auth()).status_code == 404


def test_group_patch_member_sync(client):
    a = _mk_user(client, "a@example.com")
    b = _mk_user(client, "b@example.com")
    gid = _mk_group(client, "Team", [a])

    def patch(*ops):
        return client.patch(f"/scim/v2/Groups/{gid}", json={
            "schemas": [_PATCH_SCHEMA], "Operations": list(ops)},
            headers=_auth())

    # Okta: add a member.
    r = patch({"op": "add", "path": "members", "value": [{"value": b}]})
    assert {m["value"] for m in r.json()["members"]} == {a, b}
    # Okta: remove one member via the path filter form.
    r = patch({"op": "remove", "path": f'members[value eq "{a}"]'})
    assert {m["value"] for m in r.json()["members"]} == {b}
    # Azure: replace the whole member list.
    r = patch({"op": "replace", "path": "members", "value": [{"value": a}]})
    assert {m["value"] for m in r.json()["members"]} == {a}
    # Rename.
    r = patch({"op": "replace", "path": "displayName", "value": "Renamed"})
    assert r.json()["displayName"] == "Renamed"
    # Garbage -> 400, membership unchanged.
    r = patch({"op": "replace", "path": "nickName", "value": "x"})
    assert r.status_code == 400


def test_groups_require_bearer(client):
    assert _client().get("/scim/v2/Groups").status_code == 401
    r = _client().get("/scim/v2/Groups",
                      headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_resource_types_advertise_groups(client):
    r = client.get("/scim/v2/ResourceTypes", headers=_auth())
    assert {rt["id"] for rt in r.json()} == {"User", "Group"}


# ---------- group -> access resolution ----------

def test_group_names_match_by_external_id_and_subject_directory(client):
    uid = _mk_user(client, "alice@example.com", external_id="okta-sub-1")
    _mk_group(client, "Finance Team", [uid])
    from maverick_dashboard import scim_groups
    # externalId carries the OIDC sub (Okta).
    assert scim_groups.group_names_for_principal(
        "user:okta-sub-1") == frozenset({"Finance Team"})
    # Pairwise-sub IdPs (Entra): the sub only exists in the subject directory,
    # recorded against the user's stable identifiers at login.
    from maverick_dashboard.subject_directory import record_login
    record_login("pairwise-sub-9", ["alice@example.com"])
    assert scim_groups.group_names_for_principal(
        "user:pairwise-sub-9") == frozenset({"Finance Team"})
    # An unknown principal belongs to nothing.
    assert scim_groups.group_names_for_principal("user:nobody") == frozenset()


def test_group_role_most_privileged_and_stored_wins(client, monkeypatch):
    uid = _mk_user(client, "alice@example.com", external_id="okta-sub-1")
    _mk_group(client, "Finance Team", [uid])
    _mk_group(client, "Auditors", [uid])
    _map_groups(monkeypatch, roles={"Finance Team": "operator",
                                    "Auditors": "auditor"})
    from maverick_dashboard import auth, scim_groups
    # Most privileged mapped role wins across the user's groups.
    assert scim_groups.role_for_principal("user:okta-sub-1") == "operator"
    assert auth.global_role_for_principal("user:okta-sub-1") == "operator"
    # An explicit Users-page assignment beats the group-derived role.
    from maverick_dashboard import rbac
    rbac.set_role("user:okta-sub-1", "viewer")
    assert auth.global_role_for_principal("user:okta-sub-1") == "viewer"
    # No mapped groups -> the configured default, unchanged.
    assert auth.global_role_for_principal("user:unmapped") == rbac.default_role()


def test_group_suites_flow_through_kernel_chain(client, monkeypatch):
    uid = _mk_user(client, "alice@example.com", external_id="okta-sub-1")
    _mk_group(client, "Finance Team", [uid])
    _map_groups(monkeypatch, suites={"Finance Team": ["finance", "tax"]})
    import maverick_dashboard.auth  # noqa: F401 - arms the kernel resolver
    from maverick import suite_grants as core
    # Group-derived grant resolves through the KERNEL chain (deploy/dispatch
    # gates use this same path).
    assert core.granted_suites("user:okta-sub-1") == frozenset({"finance", "tax"})
    # An explicit grant beats the group-derived one.
    core.set_suites("user:okta-sub-1", ["legal"])
    assert core.granted_suites("user:okta-sub-1") == frozenset({"legal"})
    # No opinion from groups -> unrestricted (no config default here).
    assert core.granted_suites("user:unmapped") is None


def test_group_scoped_user_sees_only_their_departments(client, monkeypatch):
    uid = _mk_user(client, "alice@example.com", external_id="okta-sub-1")
    _mk_group(client, "Finance Team", [uid])
    _map_groups(monkeypatch, suites={"Finance Team": ["finance"]})
    from maverick_dashboard import api, auth
    monkeypatch.setattr(auth, "caller_principal",
                        lambda request: "user:okta-sub-1")
    monkeypatch.setattr(api, "caller_principal",
                        lambda request: "user:okta-sub-1")
    keys = {d["key"] for d in client.get("/api/v1/departments").json()}
    assert keys == {"finance"}
    assert client.get("/api/v1/departments/legal").status_code == 403
