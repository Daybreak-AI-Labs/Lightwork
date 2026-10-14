"""Fleet-shared SCIM/OIDC authorization-authority regressions."""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from urllib.parse import parse_qs, urlparse

import pytest


class _AtomicDocument:
    """Small atomic document implementing the authority adapter contract."""

    def __init__(self) -> None:
        self.kind = "postgres"
        self.document: dict = {}
        self.lock = threading.RLock()
        self.clock_time = 1_000.0
        self.fail_read = False
        self.fail_mutate = False
        self.fail_clock = False

    @property
    def backend_kind(self) -> str:
        return self.kind

    def read(self) -> dict:
        if self.fail_read:
            raise OSError("database unavailable")
        with self.lock:
            return copy.deepcopy(self.document)

    def authoritative_time(self) -> float:
        if self.fail_clock:
            raise OSError("database clock unavailable")
        with self.lock:
            return self.clock_time

    def mutate(self, callback, *, action, actor="system", max_retries=16):
        del action, actor, max_retries
        if self.fail_mutate:
            raise OSError("database unavailable")
        with self.lock:
            document = copy.deepcopy(self.document)
            result = callback(document)
            self.document = copy.deepcopy(document)
            return result

    def mutate_with_authoritative_time(
        self,
        callback,
        *,
        action,
        actor="system",
        max_retries=16,
    ):
        del action, actor, max_retries
        if self.fail_mutate or self.fail_clock:
            raise OSError("database unavailable")
        with self.lock:
            document = copy.deepcopy(self.document)
            result = callback(document, self.clock_time)
            self.document = copy.deepcopy(document)
            return result


class _Tenants:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lock = threading.Lock()
        self.events: list[tuple[str, str]] = []

    def get(self, tenant_id: str):
        with self.lock:
            value = self.values.get(tenant_id)
            return None if value is None else {"id": tenant_id, "status": value}

    def create(self, tenant_id: str, **_kwargs):
        with self.lock:
            if tenant_id in self.values:
                raise RuntimeError("tenant exists")
            self.values[tenant_id] = "active"
            self.events.append(("create", tenant_id))

    def suspend(self, tenant_id: str):
        with self.lock:
            if tenant_id in self.values:
                self.values[tenant_id] = "suspended"
            self.events.append(("suspend", tenant_id))

    def resume(self, tenant_id: str):
        with self.lock:
            if tenant_id not in self.values:
                raise RuntimeError("tenant missing")
            self.values[tenant_id] = "active"
            self.events.append(("resume", tenant_id))

    def delete(self, tenant_id: str, **_kwargs):
        with self.lock:
            existed = tenant_id in self.values
            self.values.pop(tenant_id, None)
            self.events.append(("delete", tenant_id))
            return existed


def _policy() -> dict:
    return {
        "dashboard": {
            "admins": ["user:breakglass"],
            "default_role": "viewer",
            "default_suites": ["tax"],
            "group_roles": {"Finance Team": "auditor"},
            "group_suites": {"Finance Team": ["finance"]},
            "invites": False,
            "require_auth": True,
        },
        "auth": {"oidc": {"enabled": True}},
    }


@pytest.fixture
def shared_federation(monkeypatch, tmp_path):
    from maverick import identity_authority, identity_federation, suite_grants
    from maverick.tenant import registry
    from maverick_dashboard import rbac, scim, scim_groups, subject_directory

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", "a" * 40)
    config = _policy()
    monkeypatch.setattr(
        identity_federation,
        "_config_snapshot",
        lambda supplied=None: config if supplied is None else supplied,
    )

    federation = _AtomicDocument()
    explicit_suites = _AtomicDocument()
    explicit_roles = _AtomicDocument()
    monkeypatch.setattr(identity_federation, "_STORE", federation)
    monkeypatch.setattr(suite_grants, "_SHARED_AUTHORITY", explicit_suites)
    monkeypatch.setattr(identity_authority, "_GLOBAL_AUTHORITY", explicit_roles)

    monkeypatch.setattr(scim, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(subject_directory, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(scim_groups, "_shared_mode", lambda: True)
    monkeypatch.setattr(suite_grants, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(rbac, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(
        suite_grants,
        "store_path",
        lambda: tmp_path / "dashboard-user-suites.json",
    )
    monkeypatch.setattr(rbac, "store_path", lambda: tmp_path / "dashboard-users.json")
    monkeypatch.setattr(
        rbac,
        "_refuse_global_identity_legacy_state",
        lambda: identity_federation.refuse_legacy_state(),
    )

    tenants = _Tenants()
    monkeypatch.setattr(registry, "get_tenant", tenants.get)
    monkeypatch.setattr(registry, "create_tenant", tenants.create)
    monkeypatch.setattr(registry, "suspend_tenant", tenants.suspend)
    monkeypatch.setattr(registry, "resume_tenant", tenants.resume)
    monkeypatch.setattr(registry, "delete_tenant", tenants.delete)

    revoked: list[str] = []
    audits: list[tuple[str, set[str], set[str]]] = []
    monkeypatch.setattr(
        scim,
        "_revoke_user_sessions",
        lambda rec: revoked.extend(scim._identity_values(rec)),
    )
    monkeypatch.setattr(
        scim,
        "_audit_group_membership",
        lambda name, added, removed: audits.append((name, set(added), set(removed))),
    )

    identity_federation.bind_deployment_contract(config)
    return {
        "store": federation,
        "suite_store": explicit_suites,
        "role_store": explicit_roles,
        "tenants": tenants,
        "revoked": revoked,
        "audits": audits,
        "config": config,
        "root": tmp_path,
    }


def _resource(
    username: str,
    *,
    external_id: str,
    active: bool = True,
    email: str | None = None,
) -> dict:
    return {
        "userName": username,
        "externalId": external_id,
        "displayName": username,
        "emails": [{"value": email or username, "primary": True}],
        "active": active,
    }


def _create_user(username: str, external_id: str, *, active: bool = True) -> str:
    from maverick_dashboard import scim

    uid = uuid.uuid4().hex
    scim._shared_create_user(
        _resource(username, external_id=external_id, active=active),
        uid,
    )
    return uid


def test_shared_user_and_group_timestamps_use_database_clock(
    shared_federation,
    monkeypatch,
):
    from maverick import identity_federation
    from maverick_dashboard import scim

    monkeypatch.setattr(scim.time, "time", lambda: 9_999_999_999.0)
    shared_federation["store"].clock_time = 2_345.5
    uid = _create_user("clock@example.com", "oid-clock")
    gid = uuid.uuid4().hex
    group = scim._shared_create_group(
        {"displayName": "Clock", "members": [{"value": uid}]},
        gid,
    )

    state = identity_federation.read_state()
    assert state["users"][uid]["created_at"] == 2_345.5
    assert state["users"][uid]["updated_at"] == 2_345.5
    assert group["created_at"] == 2_345.5
    assert group["updated_at"] == 2_345.5


def test_concurrent_shared_user_uniqueness_has_one_winner_and_no_orphan_tenant(
    shared_federation,
):
    from maverick import identity_federation
    from maverick_dashboard import scim

    barrier = threading.Barrier(2)
    successes: list[str] = []
    failures: list[Exception] = []
    lock = threading.Lock()

    def create() -> None:
        uid = uuid.uuid4().hex
        barrier.wait()
        try:
            scim._shared_create_user(
                _resource("same@example.com", external_id=f"oid-{uid}"),
                uid,
            )
        except Exception as exc:  # one uniqueness loser is expected
            with lock:
                failures.append(exc)
        else:
            with lock:
                successes.append(uid)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == len(failures) == 1
    assert set(identity_federation.read_state()["users"]) == set(successes)
    assert set(shared_federation["tenants"].values) == set(successes)


def test_shared_scim_routes_preserve_protocol_errors_and_deny_first_lifecycle(
    shared_federation,
):
    from fastapi import FastAPI
    from maverick import identity_federation
    from maverick_dashboard import scim
    from starlette.testclient import TestClient

    app = FastAPI()
    app.include_router(scim.router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + "a" * 40}
    payload = _resource("route@example.com", external_id="oid-route")

    created = client.post("/scim/v2/Users", json=payload, headers=headers)
    assert created.status_code == 201
    uid = created.json()["id"]
    duplicate = client.post(
        "/scim/v2/Users",
        json={**payload, "externalId": "oid-route-other"},
        headers=headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["scimType"] == "uniqueness"

    deactivated = client.patch(
        f"/scim/v2/Users/{uid}",
        json={
            "schemas": [scim._PATCH_SCHEMA],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=headers,
    )
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    assert identity_federation.read_state()["users"][uid]["active"] is False
    assert shared_federation["tenants"].values[uid] == "suspended"


def test_concurrent_group_member_patches_preserve_both_additions(shared_federation):
    from maverick import identity_federation
    from maverick_dashboard import scim

    first = _create_user("first@example.com", "oid-first")
    second = _create_user("second@example.com", "oid-second")
    gid = uuid.uuid4().hex
    scim._shared_create_group({"displayName": "Concurrent"}, gid)
    barrier = threading.Barrier(2)

    def add(uid: str) -> None:
        barrier.wait()
        scim._shared_patch_group(
            gid,
            {
                "schemas": [scim._PATCH_SCHEMA],
                "Operations": [
                    {"op": "add", "path": "members", "value": [{"value": uid}]}
                ],
            },
        )

    threads = [threading.Thread(target=add, args=(uid,)) for uid in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert identity_federation.read_state()["groups"][gid]["members"] == sorted(
        [first, second]
    )


def test_policy_drift_fails_closed_and_rotation_requires_expected_digest(
    shared_federation,
):
    from maverick import identity_federation

    original = identity_federation.verified_policy(shared_federation["config"])
    digest = identity_federation.policy_contract_digest(original)
    replacement = copy.deepcopy(shared_federation["config"])
    replacement["dashboard"]["default_role"] = "auditor"

    with pytest.raises(identity_federation.IdentityFederationError, match="does not match"):
        identity_federation.verified_policy(replacement)
    with pytest.raises(identity_federation.IdentityFederationError, match="lost CAS"):
        identity_federation.rotate_policy_contract("sha256:" + "0" * 64, replacement)

    rotated = identity_federation.rotate_policy_contract(digest, replacement)
    assert identity_federation.verified_policy(replacement)["default_role"] == "auditor"
    assert rotated != digest


def test_scim_secret_set_rotation_uses_db_grace_and_never_persists_secret(
    shared_federation,
    monkeypatch,
):
    from maverick import identity_federation

    old_secret = "a" * 40
    new_secret = "b" * 40
    old_digest = identity_federation.scim_secret_set_digest([old_secret])
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", new_secret)
    shared_federation["store"].clock_time = 5_000.0
    new_digest = identity_federation.rotate_scim_secret_set(
        old_digest,
        grace_seconds=30,
        config=shared_federation["config"],
    )

    assert identity_federation.verify_scim_secret_set([new_secret]) == new_digest
    assert identity_federation.verify_scim_secret_set([old_secret]) == old_digest
    assert old_secret not in json.dumps(shared_federation["store"].document)
    assert new_secret not in json.dumps(shared_federation["store"].document)

    shared_federation["store"].clock_time = 5_031.0
    with pytest.raises(identity_federation.IdentityFederationError, match="does not match"):
        identity_federation.verify_scim_secret_set([old_secret])
    identity_federation.finalize_scim_secret_rotation(new_digest)
    rotation = identity_federation.read_state()["scim_secret_rotation"]
    assert rotation["previous_digest"] is None
    assert rotation["grace_until"] is None


def test_scim_secret_rotation_accepts_replica_union_only_during_db_grace(
    shared_federation,
    monkeypatch,
):
    from fastapi import FastAPI
    from maverick import identity_federation
    from maverick_dashboard import scim
    from starlette.testclient import TestClient

    old_secret = "a" * 40
    new_secret = "b" * 40
    old_digest = identity_federation.scim_secret_set_digest([old_secret])
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", new_secret)
    shared_federation["store"].clock_time = 6_000.0
    new_digest = identity_federation.rotate_scim_secret_set(
        old_digest,
        grace_seconds=30,
        config=shared_federation["config"],
    )

    # A rolling replica exposes both independently bound sets. The shared row
    # stores only the aggregate old/new digests, so admission proves a bounded
    # partition without persisting either bearer.
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", f"{old_secret},{new_secret}")
    assert identity_federation.bind_deployment_contract(
        shared_federation["config"]
    ) == (
        identity_federation.policy_contract_digest(
            identity_federation.resolve_policy_contract(
                shared_federation["config"]
            )
        ),
        new_digest,
    )
    app = FastAPI()
    app.include_router(scim.router)
    client = TestClient(app)
    path = "/scim/v2/ServiceProviderConfig"
    assert client.get(
        path,
        headers={"Authorization": f"Bearer {old_secret}"},
    ).status_code == 200
    assert client.get(
        path,
        headers={"Authorization": f"Bearer {new_secret}"},
    ).status_code == 200

    # Once database time crosses the deadline the replica must remove the old
    # value. The authority admits the active set, then request authentication
    # rejects the retired bearer while continuing to accept the new one.
    shared_federation["store"].clock_time = 6_031.0
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", new_secret)
    assert client.get(
        path,
        headers={"Authorization": f"Bearer {old_secret}"},
    ).status_code == 401
    assert client.get(
        path,
        headers={"Authorization": f"Bearer {new_secret}"},
    ).status_code == 200


def test_scim_secret_set_rejects_more_than_eight_entries(shared_federation):
    from maverick import identity_federation

    secrets = [str(index) * 40 for index in range(1, 10)]
    with pytest.raises(identity_federation.IdentityFederationError, match="too many"):
        identity_federation.verify_scim_secret_set(secrets)


def test_scim_secret_grace_is_rechecked_after_lost_cas(
    shared_federation,
    monkeypatch,
):
    from maverick import identity_federation

    old_secret = "a" * 40
    new_secret = "b" * 40
    old_digest = identity_federation.scim_secret_set_digest([old_secret])
    monkeypatch.setenv("MAVERICK_SCIM_TOKEN", new_secret)
    shared_federation["store"].clock_time = 7_000.0
    identity_federation.rotate_scim_secret_set(
        old_digest,
        grace_seconds=10,
        config=shared_federation["config"],
    )

    class _RetryPastGrace(_AtomicDocument):
        def mutate_with_authoritative_time(
            self,
            callback,
            *,
            action,
            actor="system",
            max_retries=16,
        ):
            del action, actor, max_retries
            with self.lock:
                # The accepted first callback loses its CAS. The successful
                # retry is re-clocked after grace and must reject the old set.
                callback(copy.deepcopy(self.document), 7_009.0)
                return callback(copy.deepcopy(self.document), 7_011.0)

    retrying = _RetryPastGrace()
    retrying.document = copy.deepcopy(shared_federation["store"].document)
    monkeypatch.setattr(identity_federation, "_STORE", retrying)
    with pytest.raises(identity_federation.IdentityFederationError, match="does not match"):
        identity_federation.verify_scim_secret_set([old_secret])


@pytest.mark.parametrize(
    ("boundary", "expected_phase"),
    [
        ("after_deny", "deny_persisted"),
        ("after_sessions", "sessions_revoked"),
        ("after_tenant", "tenant_applied"),
    ],
)
@pytest.mark.parametrize("operation", ["suspend", "delete"])
def test_lifecycle_sagas_recover_from_every_external_boundary(
    shared_federation,
    monkeypatch,
    boundary,
    expected_phase,
    operation,
):
    from maverick import identity_federation
    from maverick.tenant import registry
    from maverick_dashboard import scim, subject_directory

    uid = _create_user("saga@example.com", "oid-saga")
    subject_directory.record_login("pairwise-saga", ["oid-saga"])

    def inactive(existing: dict, now: float) -> dict:
        return {**existing, "active": False, "updated_at": now}

    scim._shared_begin_lifecycle(uid, operation, inactive)
    original_revoke = scim._revoke_user_sessions
    original_suspend = registry.suspend_tenant
    original_delete = registry.delete_tenant
    original_finish = scim._shared_finish_lifecycle
    if boundary == "after_deny":
        monkeypatch.setattr(
            scim,
            "_revoke_user_sessions",
            lambda _rec: (_ for _ in ()).throw(RuntimeError("crash after deny")),
        )
    elif boundary == "after_sessions":
        target = "delete_tenant" if operation == "delete" else "suspend_tenant"
        monkeypatch.setattr(
            registry,
            target,
            lambda _uid: (_ for _ in ()).throw(RuntimeError("crash after sessions")),
        )
    else:
        monkeypatch.setattr(
            scim,
            "_shared_finish_lifecycle",
            lambda _uid: (_ for _ in ()).throw(RuntimeError("crash after tenant")),
        )

    with pytest.raises(RuntimeError, match="crash"):
        scim._shared_advance_lifecycle(uid)

    state = identity_federation.read_state()
    assert state["users"][uid]["active"] is False
    assert state["lifecycle_sagas"][uid]["phase"] == expected_phase
    assert subject_directory.is_retired("pairwise-saga")

    monkeypatch.setattr(scim, "_revoke_user_sessions", original_revoke)
    monkeypatch.setattr(registry, "suspend_tenant", original_suspend)
    monkeypatch.setattr(registry, "delete_tenant", original_delete)
    monkeypatch.setattr(scim, "_shared_finish_lifecycle", original_finish)
    scim.recover_pending_lifecycles()

    recovered = identity_federation.read_state()
    assert uid not in recovered["lifecycle_sagas"]
    if operation == "delete":
        assert uid not in recovered["users"]
        assert uid not in shared_federation["tenants"].values
    else:
        assert recovered["users"][uid]["active"] is False
        assert shared_federation["tenants"].values[uid] == "suspended"


def test_delete_removes_membership_but_retains_tombstone_until_reprovision(
    shared_federation,
):
    from maverick import identity_federation
    from maverick_dashboard import scim, subject_directory

    uid = _create_user("delete@example.com", "oid-delete")
    subject_directory.record_login("pairwise-delete", ["oid-delete"])
    gid = uuid.uuid4().hex
    scim._shared_create_group(
        {"displayName": "Delete", "members": [{"value": uid}]},
        gid,
    )

    scim._shared_delete_user(uid)
    state = identity_federation.read_state()
    assert uid not in state["users"]
    assert uid not in state["groups"][gid]["members"]
    assert subject_directory.is_retired("pairwise-delete")

    replacement = _create_user("delete@example.com", "oid-delete")
    assert replacement != uid
    assert not subject_directory.is_retired("pairwise-delete")


def test_activation_crash_after_tenant_resume_keeps_shared_identity_denied(
    shared_federation,
):
    from maverick import identity_federation
    from maverick_dashboard import scim, subject_directory

    uid = _create_user("activate@example.com", "oid-activate")
    subject_directory.record_login("pairwise-activate", ["oid-activate"])

    def inactive(existing: dict, now: float) -> dict:
        return {**existing, "active": False, "updated_at": now}

    scim._shared_begin_lifecycle(uid, "suspend", inactive)
    scim._shared_advance_lifecycle(uid)
    assert shared_federation["tenants"].values[uid] == "suspended"

    def active(existing: dict, now: float) -> dict:
        return {**existing, "active": True, "updated_at": now}

    shared_federation["store"].fail_mutate = True
    with pytest.raises(identity_federation.IdentityFederationError):
        scim._shared_activate_user(uid, active)
    # External resume happened, but the authoritative inactive row/tombstones
    # did not change, so authentication remains denied until a successful CAS.
    assert shared_federation["tenants"].values[uid] == "active"
    assert identity_federation.read_state()["users"][uid]["active"] is False
    assert subject_directory.is_retired("pairwise-activate")

    shared_federation["store"].fail_mutate = False
    scim._shared_activate_user(uid, active)
    assert identity_federation.read_state()["users"][uid]["active"] is True
    assert not subject_directory.is_retired("pairwise-activate")


def test_deprovision_and_pairwise_login_race_always_finishes_denied(
    shared_federation,
):
    from maverick import identity_federation
    from maverick_dashboard import scim, subject_directory

    uid = _create_user("race@example.com", "oid-race")
    barrier = threading.Barrier(2)

    def login() -> None:
        barrier.wait()
        subject_directory.record_login("pairwise-race", ["oid-race"])

    def deny() -> None:
        barrier.wait()

        def inactive(existing: dict, now: float) -> dict:
            return {**existing, "active": False, "updated_at": now}

        scim._shared_begin_lifecycle(uid, "suspend", inactive)

    threads = [threading.Thread(target=login), threading.Thread(target=deny)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert subject_directory.is_retired("pairwise-race")
    assert identity_federation.active_for_principal("user:pairwise-race") is False


@pytest.mark.parametrize(
    "binding_outage",
    [False, True],
    ids=["inactive-after-binding", "binding-outage"],
)
def test_pairwise_oidc_inactive_or_binding_outage_cannot_issue_session(
    shared_federation,
    monkeypatch,
    caplog,
    binding_outage,
):
    from fastapi import FastAPI
    from maverick.oidc import VerifiedPrincipal
    from maverick.web_session import verify_session
    from maverick_dashboard import oidc_login
    from starlette.testclient import TestClient

    _create_user("inactive@example.com", "oid-inactive", active=False)
    session_secret = "session-secret-for-binding-outage-test"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_OIDC_ENABLED", "1")
    monkeypatch.setenv("MAVERICK_OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("MAVERICK_OIDC_AUDIENCE", "maverick")
    monkeypatch.setenv("MAVERICK_OIDC_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("MAVERICK_OIDC_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv(
        "MAVERICK_OIDC_REDIRECT_URI",
        "http://testserver/auth/callback",
    )
    monkeypatch.setenv("MAVERICK_OIDC_SESSION_SECRET", session_secret)
    monkeypatch.setenv(
        "MAVERICK_OIDC_AUTHORIZATION_ENDPOINT",
        "https://idp.example.com/authorize",
    )
    monkeypatch.setenv(
        "MAVERICK_OIDC_TOKEN_ENDPOINT",
        "https://idp.example.com/token",
    )
    oidc_login._CONSUMED_TX_IDS.clear()
    released: list[str] = []
    monkeypatch.setattr(oidc_login, "_shared_release_tx", released.append)

    app = FastAPI()
    app.include_router(oidc_login.router)
    client = TestClient(app)
    login = client.get("/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    state = query["state"][0]
    nonce = query["nonce"][0]
    transaction = verify_session(
        client.cookies.get(oidc_login.TX_COOKIE),
        session_secret,
    )
    assert transaction is not None

    async def _exchange(*_args, **_kwargs):
        return {"id_token": "opaque-id-token"}

    monkeypatch.setattr(oidc_login, "_exchange_code_for_tokens", _exchange)
    monkeypatch.setattr(
        oidc_login,
        "verify_oidc_token",
        lambda _token: VerifiedPrincipal(
            sub="pairwise-inactive",
            issuer="https://idp.example.com",
            audience="maverick",
            claims={
                "sub": "pairwise-inactive",
                "oid": "oid-inactive",
                "email": "inactive@example.com",
                "nonce": nonce,
            },
        ),
    )
    shared_federation["store"].fail_mutate = binding_outage
    secret_marker = "must-not-appear-in-logs"  # pragma: allowlist secret
    response = client.get(
        f"/auth/callback?code={secret_marker}&state={state}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/auth/error"
    assert not client.cookies.get(oidc_login.SESSION_COOKIE)
    assert released == [transaction["jti"]]
    assert secret_marker not in caplog.text
    assert "inactive@example.com" not in caplog.text


@pytest.mark.parametrize(
    ("binding_outage", "expected_status"),
    [(False, 401), (True, 503)],
    ids=["inactive-after-binding", "binding-outage"],
)
def test_transient_saml_inactive_or_binding_outage_cannot_issue_session(
    shared_federation,
    monkeypatch,
    caplog,
    binding_outage,
    expected_status,
):
    from fastapi import FastAPI
    from maverick.web_session import sign_session
    from maverick_dashboard import saml, saml_replay
    from starlette.testclient import TestClient

    _create_user("inactive-saml@example.com", "oid-saml-inactive", active=False)
    session_secret = "session-secret-for-saml-binding-outage"  # pragma: allowlist secret
    monkeypatch.setattr(
        saml,
        "_auth_saml_cfg",
        lambda: {
            "sp_entity_id": "https://testserver/saml/metadata",
            "acs_url": "https://testserver/saml/acs",
            "idp_metadata_url": "https://idp.example.com/metadata",
        },
    )
    monkeypatch.setattr(saml, "_session_secret", lambda: session_secret)
    transient_name_id = "_transient-assertion-subject"
    monkeypatch.setattr(
        saml,
        "verify_acs_response",
        lambda _response, *, request_id: saml.SamlIdentity(
            name_id=transient_name_id,
            attributes={
                "email": ["inactive-saml@example.com"],
                "upn": ["oid-saml-inactive"],
            },
            assertion_id="assertion-inactive-saml",
            expires_at=time.time() + 300,
        ),
    )
    monkeypatch.setattr(saml_replay, "consume", lambda *_args, **_kwargs: True)

    app = FastAPI()
    app.include_router(saml.router)
    client = TestClient(
        app,
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
    )
    state = "saml-relay-state"
    transaction = sign_session(
        {
            "request_id": "saml-request-id",
            "state": state,
            "return_to": "/",
            "jti": "saml-transaction-id",
            "exp": int(time.time()) + saml._TX_TTL,
        },
        session_secret,
    )
    client.cookies.set(saml.TX_COOKIE, transaction, path="/saml")
    shared_federation["store"].fail_mutate = binding_outage
    secret_marker = "assertion-must-not-appear-in-logs"  # pragma: allowlist secret
    response = client.post(
        "/saml/acs",
        data={"SAMLResponse": secret_marker, "RelayState": state},
        follow_redirects=False,
    )

    assert response.status_code == expected_status
    assert not client.cookies.get("mvk_session")
    assert secret_marker not in caplog.text
    assert transient_name_id not in caplog.text
    assert "inactive-saml@example.com" not in caplog.text


def test_effective_roles_and_suites_use_shared_scim_explicit_and_pinned_policy(
    shared_federation,
):
    from maverick import identity_federation, suite_grants
    from maverick_dashboard import rbac, scim, scim_groups

    uid = _create_user("finance@example.com", "oid-finance")
    scim._shared_create_group(
        {"displayName": "Finance Team", "members": [{"value": uid}]},
        uuid.uuid4().hex,
    )

    assert scim_groups.role_for_principal("user:oid-finance") == "auditor"
    assert suite_grants.granted_suites("user:oid-finance") == frozenset({"finance"})
    suite_grants.set_suites("user:oid-finance", ["tax"])
    assert suite_grants.granted_suites("user:oid-finance") == frozenset({"tax"})
    assert suite_grants.admin_principals() == frozenset({"user:breakglass"})
    assert rbac.default_role() == "viewer"
    assert identity_federation.group_names_for_principal("user:oid-finance") == frozenset(
        {"Finance Team"}
    )


def test_shared_authority_is_deployment_global_not_ambient_tenant(
    shared_federation,
):
    from maverick import identity_federation
    from maverick.paths import reset_tenant, set_tenant

    alpha = set_tenant("alpha")
    try:
        uid = _create_user("global@example.com", "oid-global")
    finally:
        reset_tenant(alpha)
    beta = set_tenant("beta")
    try:
        assert uid in identity_federation.read_state()["users"]
    finally:
        reset_tenant(beta)


@pytest.mark.parametrize(
    "filename",
    [
        "scim_users.json",
        "scim_groups.json",
        "oidc-subjects.json",
        "oidc-retired-subjects.json",
        "dashboard-users.json",
        "dashboard-user-suites.json",
    ],
)
def test_shared_cutover_refuses_every_legacy_json_even_when_empty(
    shared_federation,
    filename,
):
    from maverick import identity_federation

    (shared_federation["root"] / filename).touch()
    with pytest.raises(identity_federation.IdentityFederationError, match="legacy"):
        identity_federation.read_state()


def test_shared_outage_and_backend_drift_have_no_local_fallback(
    shared_federation,
    monkeypatch,
):
    from maverick import identity_federation
    from maverick_dashboard import scim

    monkeypatch.setattr(
        scim,
        "_read_store",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shared SCIM attempted local JSON fallback")
        ),
    )
    shared_federation["store"].fail_read = True
    with pytest.raises(scim.ScimStoreError, match="shared"):
        scim._load()

    shared_federation["store"].fail_read = False
    shared_federation["store"].kind = "local"
    with pytest.raises(identity_federation.IdentityFederationError, match="Postgres"):
        identity_federation.read_state()


def test_shared_clock_outage_cannot_fall_back_to_node_lifecycle_time(
    shared_federation,
    monkeypatch,
):
    from maverick import identity_federation
    from maverick_dashboard import scim, subject_directory

    monkeypatch.setattr(
        scim.time,
        "time",
        lambda: (_ for _ in ()).throw(
            AssertionError("shared lifecycle attempted node-local time fallback")
        ),
    )
    shared_federation["store"].fail_clock = True
    # Shared login binding is an authorization prerequisite and must fail rather
    # than write a caller/node timestamp when the database clock is unavailable.
    with pytest.raises(subject_directory.SubjectDirectoryError, match="binding"):
        subject_directory.record_login("clock-outage", ["oid-clock-outage"])
    assert not identity_federation.read_state()["subject_bindings"]
    with pytest.raises(subject_directory.SubjectDirectoryError, match="retirement"):
        subject_directory.retire(["oid-clock-outage"])
    with pytest.raises(identity_federation.IdentityFederationError):
        scim._shared_create_group({"displayName": "Clock outage"}, uuid.uuid4().hex)


def test_shared_schema_rejects_unbounded_raw_subject(shared_federation):
    from maverick import identity_federation

    state = identity_federation.read_state()
    state["subject_bindings"]["0" * 64] = {
        "sub": "x" * (identity_federation.MAX_SUBJECT_CHARS + 1),
        "ts": 1.0,
    }
    with pytest.raises(identity_federation.IdentityFederationError, match="directory"):
        identity_federation.validate_document(state, initialize=False)


def test_identity_document_bound_fits_governed_record_wrapper(shared_federation):
    from maverick import governed_records, identity_federation

    assert identity_federation.MAX_DOCUMENT_BYTES <= (
        governed_records._MAX_RECORD_JSON_BYTES - 1024 * 1024
    )
    with pytest.raises(identity_federation.IdentityFederationError, match="byte bound"):
        identity_federation._bounded_json(
            {"padding": "x" * identity_federation.MAX_DOCUMENT_BYTES}
        )
