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

import json

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
    uid = _mk_user(client, "alice@example.com", external_id="entra-oid-1")
    _mk_group(client, "Finance Team", [uid])
    from maverick_dashboard import scim_groups
    from maverick_dashboard.subject_directory import record_login
    # externalId carries the IdP's immutable object id / sub (Okta/Entra).
    assert scim_groups.group_names_for_principal(
        "user:entra-oid-1") == frozenset({"Finance Team"})
    # Pairwise-sub IdPs (Entra): the login sub differs from externalId, so we
    # bridge through the subject directory — but ONLY on the non-forgeable
    # externalId (the immutable oid), never email/userName.
    record_login("pairwise-sub-9", ["entra-oid-1"])
    assert scim_groups.group_names_for_principal(
        "user:pairwise-sub-9") == frozenset({"Finance Team"})
    # An unknown principal belongs to nothing.
    assert scim_groups.group_names_for_principal("user:nobody") == frozenset()


def test_forged_email_claim_cannot_inherit_group_access(client):
    # SECURITY: the subject directory records login email/upn claims WITHOUT an
    # email_verified check. If group matching bridged via email, an attacker who
    # sets their unverified email claim to a victim's address would inherit the
    # victim's group-mapped access. Granting must bridge only on the
    # non-forgeable externalId/id, so an email-keyed directory entry confers
    # NOTHING (over-matching stays safe for revocation only).
    uid = _mk_user(client, "cfo@corp.com", external_id="entra-oid-cfo")
    _mk_group(client, "Finance Admins", [uid])
    from maverick_dashboard import scim_groups
    from maverick_dashboard.subject_directory import record_login
    # Attacker logs in; their unverified email claim is recorded as the CFO's.
    record_login("attacker-sub", ["cfo@corp.com"])
    assert scim_groups.group_names_for_principal("user:attacker-sub") == frozenset()
    # The direct userName/email path is likewise refused (the sub is not the
    # externalId), so a bare email-as-principal cannot inherit the group either.
    assert scim_groups.group_names_for_principal("user:cfo@corp.com") == frozenset()


def test_deactivated_user_confers_no_group_access(client):
    # A deprovisioned (active=false) SCIM user must not confer group-derived
    # access even while still listed in a mapped group.
    uid = _mk_user(client, "bob@corp.com", external_id="oid-bob")
    _mk_group(client, "Finance Team", [uid])
    from maverick_dashboard import scim_groups
    assert scim_groups.group_names_for_principal(
        "user:oid-bob") == frozenset({"Finance Team"})
    # Deactivate via SCIM PATCH.
    r = client.patch(f"/scim/v2/Users/{uid}", json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}]},
        headers=_auth())
    assert r.status_code == 200
    assert scim_groups.group_names_for_principal("user:oid-bob") == frozenset()
    assert scim_groups.active_for_principal("user:oid-bob") is False

    # A fresh token from a lagging/misconfigured IdP must not regain the global
    # default role after SCIM has deprovisioned the identity.
    from fastapi import HTTPException
    from maverick.oidc import VerifiedPrincipal
    from maverick_dashboard import auth

    with pytest.raises(HTTPException) as exc:
        auth._enforce_scim_active(
            VerifiedPrincipal(sub="oid-bob", issuer="issuer", audience="aud")
        )
    assert exc.value.status_code == 401


def test_corrupt_subject_binding_directory_blocks_lifecycle_resolution(client, tmp_path):
    _mk_user(client, "alice@example.com", external_id="oid-alice")
    (tmp_path / "oidc-subjects.json").write_text('{"bad":NaN}', encoding="utf-8")

    from fastapi import HTTPException
    from maverick.oidc import VerifiedPrincipal
    from maverick_dashboard import auth, subject_directory

    with pytest.raises(subject_directory.SubjectDirectoryError):
        subject_directory.load_index()
    with pytest.raises(HTTPException) as exc:
        auth._enforce_scim_active(
            VerifiedPrincipal(sub="oid-alice", issuer="issuer", audience="aud")
        )
    assert exc.value.status_code == 503


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


# ---------- SCIM Group store hardening ----------

def test_missing_group_store_retains_empty_bootstrap_semantics():
    from maverick_dashboard import scim

    assert not scim._groups_path().exists()
    assert scim._load_groups() == {}


def test_corrupt_group_store_fails_closed_and_mutator_does_not_overwrite(client):
    from maverick_dashboard import scim
    from maverick_dashboard.app import app

    damaged = '{"groups": ['
    scim._groups_path().write_text(damaged, encoding="utf-8")
    with pytest.raises(scim.ScimStoreError):
        scim._load_groups()
    no_raise_client = TestClient(
        app,
        headers={"Origin": "http://testserver"},
        raise_server_exceptions=False,
    )
    response = no_raise_client.post(
        "/scim/v2/Groups",
        json={"schemas": [_GROUP_SCHEMA], "displayName": "Must Not Commit"},
        headers=_auth(),
    )
    assert response.status_code == 500
    assert scim._groups_path().read_text(encoding="utf-8") == damaged


def test_unreadable_group_store_fails_closed(monkeypatch):
    from maverick_dashboard import scim

    scim._groups_path().write_text('{"groups": []}', encoding="utf-8")

    def denied(_path):
        raise PermissionError("simulated ACL denial")

    monkeypatch.setattr(scim, "atomic_read_text", denied)
    with pytest.raises(scim.ScimStoreError, match="unreadable"):
        scim._load_groups()


def test_group_store_schema_rejects_malformed_members(client):
    from maverick_dashboard import scim

    uid = _mk_user(client, "a@example.com", external_id="oid-a")
    _mk_group(client, "Finance Team", [uid])
    data = json.loads(scim._groups_path().read_text(encoding="utf-8"))
    data["groups"][0]["members"] = [{"value": uid}]
    scim._groups_path().write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(scim.ScimStoreError, match="invalid member id"):
        scim._load_groups()


def test_corrupt_scim_state_denies_group_derived_access(client, monkeypatch):
    uid = _mk_user(client, "a@example.com", external_id="oid-a")
    _mk_group(client, "Finance Team", [uid])
    _map_groups(
        monkeypatch,
        roles={"Finance Team": "operator"},
        suites={"Finance Team": ["finance"]},
    )
    from maverick import suite_grants
    from maverick_dashboard import auth, scim, scim_groups

    scim._groups_path().write_text("not-json", encoding="utf-8")
    with pytest.raises(scim.ScimStoreError):
        scim_groups.group_names_for_principal("user:oid-a")
    # The surrounding role/grant integrations otherwise catch exceptions and
    # fall through to operator/unrestricted defaults. The resolver itself must
    # return an explicit least-privilege answer at that seam.
    assert scim_groups.role_for_principal("user:oid-a") == "viewer"
    assert scim_groups.suites_for_principal("user:oid-a") == frozenset()
    assert auth.global_role_for_principal("user:oid-a") == "viewer"
    assert suite_grants.granted_suites("user:oid-a") == frozenset()


def test_config_source_error_and_malformed_mapping_deny(monkeypatch):
    import maverick.config as config
    from maverick_dashboard import scim_groups

    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(
        config,
        "config_source_errors",
        lambda: {"config.toml": "simulated parse error"},
    )
    assert scim_groups.role_for_principal("user:any") == "viewer"
    assert scim_groups.suites_for_principal("user:any") == frozenset()

    def invalid_table(key):
        return {"Finance Team": "owner" if key == "group_roles" else 42}

    monkeypatch.setattr(scim_groups, "_config_table", invalid_table)
    assert scim_groups.role_for_principal("user:any") == "viewer"
    assert scim_groups.suites_for_principal("user:any") == frozenset()


def test_group_store_and_parent_are_private(client):
    from maverick.file_lock import private_path_is_restricted
    from maverick_dashboard import scim

    assert _mk_group(client, "Private Team")
    assert private_path_is_restricted(scim._groups_path(), 0o600)
    assert private_path_is_restricted(scim._groups_path().parent, 0o700)

def test_patch_remove_members_with_value_does_not_clear_all(client):
    # A remove op whose value is present but yields no parseable ids (a single
    # dict, a display-only member, an empty list) must remove NOTHING, not wipe
    # the group — the alternative silently revokes every member's access.
    a = _mk_user(client, "a@x.com")
    b = _mk_user(client, "b@x.com")
    gid = _mk_group(client, "Team", [a, b])

    def patch(op):
        return client.patch(f"/scim/v2/Groups/{gid}", json={
            "schemas": [_PATCH_SCHEMA], "Operations": [op]}, headers=_auth())

    # value is a single dict (not a list) -> no ids -> members unchanged.
    r = patch({"op": "remove", "path": "members", "value": {"value": a}})
    assert {m["value"] for m in r.json()["members"]} == {a, b}
    # value is an empty list -> members unchanged.
    r = patch({"op": "remove", "path": "members", "value": []})
    assert {m["value"] for m in r.json()["members"]} == {a, b}
    # No value at all -> RFC clear-all still works.
    r = patch({"op": "remove", "path": "members"})
    assert r.json()["members"] == []


def test_group_rename_enforces_uniqueness(client):
    _mk_group(client, "Finance Team", [])
    gid2 = _mk_group(client, "Other Team", [])
    # PUT rename onto an existing name -> 409.
    r = client.put(f"/scim/v2/Groups/{gid2}", json={
        "schemas": [_GROUP_SCHEMA], "displayName": "finance team"},
        headers=_auth())
    assert r.status_code == 409
    # PATCH rename onto an existing name -> 409.
    r = client.patch(f"/scim/v2/Groups/{gid2}", json={
        "schemas": [_PATCH_SCHEMA],
        "Operations": [{"op": "replace", "path": "displayName",
                        "value": "Finance Team"}]}, headers=_auth())
    assert r.status_code == 409
    # A no-op rename to its own current name is allowed.
    r = client.patch(f"/scim/v2/Groups/{gid2}", json={
        "schemas": [_PATCH_SCHEMA],
        "Operations": [{"op": "replace", "path": "displayName",
                        "value": "Other Team"}]}, headers=_auth())
    assert r.status_code == 200


def test_group_membership_changes_are_audited(client, monkeypatch, tmp_path):
    import json as _json

    import maverick.audit.writer as w
    monkeypatch.setattr(w, "_default", w.AuditLog(audit_dir=tmp_path / "audit"))

    a = _mk_user(client, "a@x.com")
    gid = _mk_group(client, "Team", [a])          # create with a member
    b = _mk_user(client, "b@x.com")
    client.patch(f"/scim/v2/Groups/{gid}", json={   # add b
        "schemas": [_PATCH_SCHEMA],
        "Operations": [{"op": "add", "path": "members",
                        "value": [{"value": b}]}]}, headers=_auth())
    client.delete(f"/scim/v2/Groups/{gid}", headers=_auth())  # remove a + b

    rows = []
    for f in sorted((tmp_path / "audit").glob("*.ndjson")):
        rows += [_json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    access = [r for r in rows if r.get("kind") == "access_grant_changed"]
    # create(add a) + patch(add b) + delete(remove a, b) = 4 membership rows.
    assert len(access) == 4
    assert all(r["field"] == "group_membership" and r["actor"] == "scim"
               for r in access)


def test_string_group_suites_is_one_suite_not_unrestricted(client, monkeypatch):
    # A group_suites value written as a bare string must resolve to that one
    # suite, never to "no opinion" -> unrestricted.
    uid = _mk_user(client, "a@x.com", external_id="oid-a")
    _mk_group(client, "Finance Team", [uid])
    _map_groups(monkeypatch, suites={"Finance Team": "finance"})  # a string
    from maverick_dashboard import scim_groups
    assert scim_groups.suites_for_principal("user:oid-a") == frozenset({"finance"})


def test_case_insensitive_group_and_role_mapping(client, monkeypatch):
    uid = _mk_user(client, "a@x.com", external_id="oid-a")
    _mk_group(client, "Finance Team", [uid])
    # Mapping key differs in case from the SCIM displayName; role value is
    # capitalized. Both must still resolve.
    _map_groups(monkeypatch, roles={"finance team": "Operator"},
                suites={"FINANCE TEAM": ["finance"]})
    from maverick_dashboard import scim_groups
    assert scim_groups.role_for_principal("user:oid-a") == "operator"
    assert scim_groups.suites_for_principal("user:oid-a") == frozenset({"finance"})
