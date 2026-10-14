"""Shared identity-authority scope, contention, and fail-closed regressions."""
from __future__ import annotations

import copy
import hashlib
import threading

import pytest


class _AtomicDocument:
    """One atomic shared document, equivalent to the CAS adapter contract."""

    def __init__(self) -> None:
        self.kind = "postgres"
        self.document: dict = {}
        self.lock = threading.RLock()
        self.fail_read = False
        self.fail_mutate = False
        self.fail_clock = False
        self.clock_time = 1_000.0

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
        return self.mutate(
            lambda document: callback(document, self.authoritative_time()),
            action=action,
            actor=actor,
            max_retries=max_retries,
        )


class _TenantDocuments:
    """Atomic documents keyed by the exact active tenant context."""

    backend_kind = "postgres"

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.lock = threading.Lock()

    @staticmethod
    def _key() -> str:
        from maverick.paths import current_tenant_id_strict

        tenant = current_tenant_id_strict()
        assert tenant is not None
        return tenant

    def read(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.documents.get(self._key(), {}))

    def mutate(self, callback, *, action, actor="system", max_retries=16):
        del action, actor, max_retries
        with self.lock:
            key = self._key()
            document = copy.deepcopy(self.documents.get(key, {}))
            result = callback(document)
            self.documents[key] = copy.deepcopy(document)
            return result


@pytest.fixture
def shared_identity(monkeypatch, tmp_path):
    from maverick import identity_authority, suite_grants
    from maverick_dashboard import invites, rbac, session_revocation

    global_store = _AtomicDocument()
    tenant_store = _TenantDocuments()
    suite_store = _AtomicDocument()
    revocation_store = _AtomicDocument()

    for module in (suite_grants, invites, rbac, session_revocation):
        monkeypatch.setattr(module, "_configured_backend", lambda: "postgres")
    monkeypatch.setattr(identity_authority, "_GLOBAL_AUTHORITY", global_store)
    monkeypatch.setattr(rbac, "_TENANT_AUTHORITY", tenant_store)
    monkeypatch.setattr(suite_grants, "_SHARED_AUTHORITY", suite_store)
    monkeypatch.setattr(session_revocation, "_SHARED_AUTHORITY", revocation_store)

    monkeypatch.setattr(
        suite_grants, "store_path", lambda: tmp_path / "dashboard-user-suites.json"
    )
    monkeypatch.setattr(rbac, "store_path", lambda: tmp_path / "dashboard-users.json")
    monkeypatch.setattr(
        rbac,
        "tenant_store_path",
        lambda: tmp_path / "dashboard-tenant-roles.json",
    )
    monkeypatch.setattr(invites, "store_path", lambda: tmp_path / "dashboard-invites.json")
    monkeypatch.setattr(
        session_revocation,
        "_path",
        lambda: tmp_path / "session-revocations.json",
    )
    return {
        "global": global_store,
        "tenant": tenant_store,
        "suite": suite_store,
        "revocation": revocation_store,
        "root": tmp_path,
    }


def _raise_local_io(*_args, **_kwargs):
    raise AssertionError("shared identity authority attempted local record I/O")


def test_global_authorities_ignore_ambient_tenant_and_never_use_local_records(
    shared_identity,
    monkeypatch,
):
    from maverick import suite_grants
    from maverick.paths import reset_tenant, set_tenant
    from maverick_dashboard import invites, rbac, session_revocation

    monkeypatch.setattr(suite_grants, "_load_local", _raise_local_io)
    monkeypatch.setattr(rbac, "_load_local", _raise_local_io)
    monkeypatch.setattr(invites, "_load_local", _raise_local_io)
    monkeypatch.setattr(session_revocation, "_load_local", _raise_local_io)

    alpha = set_tenant("alpha")
    try:
        rbac.set_role("user:one", "viewer")
        suite_grants.set_suites("user:one", ["finance"])
        shared_identity["revocation"].clock_time = 123.0
        session_revocation.revoke_principal("user:one")
        _invite, token = invites.create_invite(
            "two@example.com", "auditor", created_by="user:admin"
        )
    finally:
        reset_tenant(alpha)

    beta = set_tenant("beta")
    try:
        assert rbac.get_stored_role("user:one") == "viewer"
        assert suite_grants.get_grant("user:one") == frozenset({"finance"})
        assert session_revocation.revocation_epoch("user:one") == 123.0
        assert invites.peek_invite(token) is not None
    finally:
        reset_tenant(beta)

    assert not (shared_identity["root"] / "dashboard-user-suites.json").exists()
    assert not (shared_identity["root"] / "dashboard-users.json").exists()
    assert not (shared_identity["root"] / "dashboard-invites.json").exists()
    assert not (shared_identity["root"] / "session-revocations.json").exists()


def test_tenant_roles_bind_function_argument_not_ambient_context(shared_identity):
    from maverick.paths import current_tenant_id_strict, reset_tenant, set_tenant
    from maverick_dashboard import rbac

    ambient = set_tenant("ambient-wrong")
    try:
        rbac.set_tenant_role("customer-a", "user:one", "admin")
        rbac.set_tenant_role("customer-b", "user:one", "viewer")
        assert current_tenant_id_strict() == "ambient-wrong"
        assert rbac.get_tenant_role("customer-a", "user:one") == "admin"
        assert rbac.get_tenant_role("customer-b", "user:one") == "viewer"
        assert current_tenant_id_strict() == "ambient-wrong"
    finally:
        reset_tenant(ambient)

    assert set(shared_identity["tenant"].documents) == {"customer-a", "customer-b"}
    assert shared_identity["tenant"].documents["customer-a"]["roles"] == {
        "user:one": "admin"
    }
    assert shared_identity["tenant"].documents["customer-b"]["roles"] == {
        "user:one": "viewer"
    }


def test_concurrent_invite_redemption_is_single_use_and_role_atomic(
    shared_identity,
):
    from maverick_dashboard import invites, rbac

    _invite, token = invites.create_invite(
        "winner@example.com", "operator", created_by="user:admin"
    )
    barrier = threading.Barrier(2)
    results: list[tuple[str, object]] = []
    result_lock = threading.Lock()

    def redeem(principal: str) -> None:
        barrier.wait()
        result = invites.consume_invite(token, used_by=principal)
        with result_lock:
            results.append((principal, result))

    threads = [
        threading.Thread(target=redeem, args=("user:first",)),
        threading.Thread(target=redeem, args=("user:second",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [principal for principal, result in results if result is not None]
    assert len(winners) == 1
    assert rbac.list_users() == {winners[0]: "operator"}
    stored = next(iter(shared_identity["global"].document["invitations"].values()))
    assert stored["used_by"] == winners[0]
    assert stored["used_at"] is not None


def test_concurrent_shared_updates_preserve_grants_roles_and_monotonic_revocation(
    shared_identity,
):
    from maverick import suite_grants
    from maverick_dashboard import rbac, session_revocation

    barrier = threading.Barrier(6)

    def role(index: int) -> None:
        barrier.wait()
        rbac.set_role(f"user:r{index}", "viewer")

    def grant(index: int) -> None:
        barrier.wait()
        suite_grants.set_suites(f"user:g{index}", ["finance"])

    def revoke() -> None:
        barrier.wait()
        session_revocation.revoke_principal("user:revoked")

    threads = [
        threading.Thread(target=role, args=(1,)),
        threading.Thread(target=role, args=(2,)),
        threading.Thread(target=grant, args=(1,)),
        threading.Thread(target=grant, args=(2,)),
        threading.Thread(target=revoke),
        threading.Thread(target=revoke),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert rbac.list_users() == {"user:r1": "viewer", "user:r2": "viewer"}
    assert suite_grants.list_grants() == {
        "user:g1": ["finance"],
        "user:g2": ["finance"],
    }
    assert session_revocation.revocation_epoch("user:revoked") == 1_000.0


def test_shared_invite_lifecycle_uses_database_time_not_fast_or_slow_node(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import invites

    # A fast node must not prematurely expire an invitation minted and judged
    # against the deployment authority's clock.
    monkeypatch.setattr(invites.time, "time", lambda: 9_999_999_999.0)
    shared_identity["global"].clock_time = 1_000.0
    active, active_token = invites.create_invite(
        "active@example.com",
        "viewer",
        created_by="user:admin",
        ttl_hours=1,
    )
    assert active.created_at == 1_000.0
    assert active.expires_at == 4_600.0
    assert active.pending

    shared_identity["global"].clock_time = 2_000.0
    assert invites.peek_invite(active_token) is not None
    consumed = invites.consume_invite(active_token, used_by="user:active")
    assert consumed is not None
    assert consumed.used_at == 2_000.0

    # A slow node must not accept an invite the deployment clock has expired.
    monkeypatch.setattr(invites.time, "time", lambda: 1.0)
    shared_identity["global"].clock_time = 3_000.0
    expired, expired_token = invites.create_invite(
        "expired@example.com",
        "auditor",
        created_by="user:admin",
        ttl_hours=1,
    )
    shared_identity["global"].clock_time = 7_000.0
    listed = {item.id: item for item in invites.list_invites()}
    assert not listed[expired.id].pending
    assert invites.peek_invite(expired_token) is None
    assert invites.consume_invite(expired_token, used_by="user:expired") is None


def test_invite_cannot_cross_expiry_during_cas_retry(
    shared_identity,
    monkeypatch,
):
    from maverick import identity_authority
    from maverick_dashboard import invites

    shared_identity["global"].clock_time = 1_000.0
    invite, token = invites.create_invite(
        "retry@example.com",
        "operator",
        created_by="user:admin",
        ttl_hours=1,
    )

    class _RetryAfterExpiry(_AtomicDocument):
        def mutate(self, callback, *, action, actor="system", max_retries=16):
            del action, actor, max_retries
            with self.lock:
                # Simulate a callback attempt whose CAS commit loses just
                # before expiry. Its document/result are intentionally dropped.
                self.clock_time = invite.expires_at - 1
                callback(copy.deepcopy(self.document))

                # The successful retry starts after expiry and must re-read
                # database time instead of reusing the first attempt's value.
                self.clock_time = invite.expires_at + 1
                document = copy.deepcopy(self.document)
                result = callback(document)
                self.document = copy.deepcopy(document)
                return result

    retrying = _RetryAfterExpiry()
    retrying.document = copy.deepcopy(shared_identity["global"].document)
    monkeypatch.setattr(identity_authority, "_GLOBAL_AUTHORITY", retrying)

    assert invites.consume_invite(token, used_by="user:retry") is None
    stored = retrying.document["invitations"][invite.id]
    assert stored["used_at"] is None
    assert "user:retry" not in retrying.document["roles"]


@pytest.mark.parametrize("node_now", [1.0, 9_999_999_999.0])
def test_shared_revocation_uses_database_time_and_rejects_caller_epoch(
    shared_identity,
    monkeypatch,
    node_now,
):
    from maverick_dashboard import session_revocation

    monkeypatch.setattr(session_revocation.time, "time", lambda: node_now)
    shared_identity["revocation"].clock_time = 4_321.0
    assert session_revocation.session_issued_at() == 4_321
    session_revocation.revoke_principal("user:clocked")
    assert session_revocation.revocation_epoch("user:clocked") == 4_321.0

    with pytest.raises(ValueError, match="caller-supplied"):
        session_revocation.revoke_principal("user:forged", at=8_765.0)
    assert session_revocation.revocation_epoch("user:forged") == 0.0


def test_shared_authority_clock_outage_has_no_local_time_fallback(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import invites, session_revocation

    monkeypatch.setattr(invites.time, "time", _raise_local_io)
    monkeypatch.setattr(session_revocation.time, "time", _raise_local_io)
    shared_identity["global"].fail_clock = True
    shared_identity["revocation"].fail_clock = True

    with pytest.raises(invites.InviteStoreError, match="clock"):
        invites.create_invite(
            "clock@example.com", "viewer", created_by="user:admin"
        )
    with pytest.raises(invites.InviteStoreError, match="clock"):
        invites.mint_local_session("clock@example.com")
    with pytest.raises(session_revocation.RevocationStoreError, match="clock"):
        session_revocation.revoke_principal("user:clock")
    with pytest.raises(session_revocation.RevocationStoreError, match="clock"):
        session_revocation.session_issued_at()


def test_shared_session_issuance_uses_same_clock_as_later_revocation(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import session_revocation

    # Replica wall clocks disagree wildly, but both the signed-session iat and
    # revocation epoch are ordered by the shared authority clock.
    monkeypatch.setattr(session_revocation.time, "time", lambda: 9_999_999_999.0)
    shared_identity["revocation"].clock_time = 1_000.75
    issued_at = session_revocation.session_issued_at()
    assert issued_at == 1_000

    shared_identity["revocation"].clock_time = 1_001.25
    session_revocation.revoke_principal("user:federated")
    assert session_revocation.is_revoked("user:federated", issued_at) is True


def test_fast_shared_session_issuer_cannot_bypass_later_db_revocation(
    shared_identity,
    monkeypatch,
):
    from maverick.web_session import verify_session
    from maverick_dashboard import invites, session_revocation

    secret = "shared-dashboard-session-key-0123456789"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", secret)
    monkeypatch.setattr(invites, "local_login_mode", lambda: True)
    monkeypatch.setattr(invites, "session_ttl_seconds", lambda: 60)
    monkeypatch.setattr(invites.time, "time", lambda: 9_999_999_999.0)
    shared_identity["global"].clock_time = 1_000.75

    cookie = invites.mint_local_session("fast@example.com")
    payload = verify_session(cookie, secret, now=1_000.75)
    assert payload is not None
    assert payload["iat"] == 1_000
    assert payload["exp"] == 1_060

    # The DB revocation occurs after issuance. A future-skewed node timestamp
    # must not be present in iat and therefore cannot evade this epoch.
    shared_identity["revocation"].clock_time = 1_001.25
    session_revocation.revoke_principal("fast@example.com")
    shared_identity["global"].clock_time = 1_002.0
    monkeypatch.setattr(invites.time, "time", lambda: 1.0)

    request = type("Request", (), {"cookies": {"mvk_session": cookie}})()
    assert invites.local_session_principal(request) is None


def test_shared_session_expiry_ignores_slow_issuer_and_fast_or_slow_verifier(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import invites

    secret = "shared-dashboard-session-key-0123456789"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", secret)
    monkeypatch.setattr(invites, "local_login_mode", lambda: True)
    monkeypatch.setattr(invites, "session_ttl_seconds", lambda: 60)
    monkeypatch.setattr(invites.time, "time", lambda: 1.0)
    shared_identity["global"].clock_time = 3_000.75
    cookie = invites.mint_local_session("expiry@example.com")
    request = type("Request", (), {"cookies": {"mvk_session": cookie}})()

    # A fast verifier still accepts while the DB clock is before expiry.
    monkeypatch.setattr(invites.time, "time", lambda: 9_999_999_999.0)
    shared_identity["global"].clock_time = 3_010.0
    principal = invites.local_session_principal(request)
    assert principal is not None
    assert principal.sub == "expiry@example.com"

    # A slow verifier rejects once the DB clock crosses the signed deadline.
    monkeypatch.setattr(invites.time, "time", lambda: 1.0)
    shared_identity["global"].clock_time = 3_061.0
    assert invites.local_session_principal(request) is None


def test_shared_session_auth_fails_closed_when_signer_binding_is_unavailable(
    shared_identity,
    monkeypatch,
):
    from maverick.web_session import sign_session
    from maverick_dashboard import invites

    secret = "shared-dashboard-session-key-0123456789"  # pragma: allowlist secret
    cookie = sign_session(
        {"sub": "binding@example.com", "iat": 1_000, "exp": 2_000},
        secret,
    )
    request = type("Request", (), {"cookies": {"mvk_session": cookie}})()
    shared_identity["global"].clock_time = 1_100.0
    monkeypatch.setattr(invites, "local_login_mode", lambda: True)
    monkeypatch.setattr(
        invites,
        "local_session_secret",
        lambda: (_ for _ in ()).throw(
            invites.InviteStoreError("shared signer binding unavailable")
        ),
    )

    assert invites.local_session_principal(request) is None


def test_shared_outage_and_backend_drift_fail_closed_without_local_fallback(
    shared_identity,
    monkeypatch,
):
    from maverick import suite_grants
    from maverick_dashboard import rbac

    monkeypatch.setattr(suite_grants, "_load_local", _raise_local_io)
    monkeypatch.setattr(suite_grants, "_write", _raise_local_io)
    shared_identity["suite"].fail_mutate = True
    with pytest.raises(suite_grants.SuiteGrantStoreError):
        suite_grants.set_suites("user:one", ["finance"])

    class _DriftAfterRead(_AtomicDocument):
        def read(self):
            value = super().read()
            self.kind = "local"
            return value

    drifted = _DriftAfterRead()
    monkeypatch.setattr(rbac, "_load_local", _raise_local_io)
    monkeypatch.setattr("maverick.identity_authority._GLOBAL_AUTHORITY", drifted)
    with pytest.raises(rbac.RbacStoreError, match="governed Postgres"):
        rbac.list_users()


def test_shared_global_identity_refuses_partial_legacy_cutover(
    shared_identity,
):
    from maverick_dashboard import invites, rbac

    legacy_invites = shared_identity["root"] / "dashboard-invites.json"
    legacy_invites.write_text("{}", encoding="utf-8")
    with pytest.raises(rbac.RbacStoreError, match="legacy invitation state exists"):
        rbac.set_role("user:one", "viewer")
    assert shared_identity["global"].document == {}

    legacy_invites.unlink()
    legacy_roles = shared_identity["root"] / "dashboard-users.json"
    legacy_roles.write_text("{}", encoding="utf-8")
    with pytest.raises(invites.InviteStoreError, match="legacy global RBAC state exists"):
        invites.create_invite("one@example.com", "viewer", created_by="admin")
    assert shared_identity["global"].document == {}


def test_shared_invite_session_signer_digest_rejects_replica_key_skew(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import invites

    monkeypatch.setattr(
        invites,
        "_session_secret_path",
        lambda: (_ for _ in ()).throw(
            AssertionError("shared signer attempted node-local key I/O")
        ),
    )
    first_key = "fleet-key-one-0123456789-abcdefgh"
    second_key = "fleet-key-two-0123456789-abcdefgh"
    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", first_key)
    assert invites.local_session_secret() == first_key
    assert shared_identity["global"].document["session_signing_sha256"] == (
        hashlib.sha256(first_key.encode("utf-8")).hexdigest()
    )

    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", second_key)
    with pytest.raises(invites.InviteStoreError, match="does not match"):
        invites.local_session_secret()


def test_shared_invite_sessions_refuse_missing_external_signer(
    shared_identity,
    monkeypatch,
):
    from maverick_dashboard import invites

    monkeypatch.delenv("MAVERICK_DASHBOARD_SESSION_SECRET", raising=False)
    monkeypatch.setattr("maverick.config.load_config", lambda: {"dashboard": {}})
    monkeypatch.setattr(invites, "_session_secret_path", _raise_local_io)

    with pytest.raises(invites.InviteStoreError, match="deployment-wide"):
        invites.local_session_secret()


@pytest.mark.parametrize("secret", ["short", "\ud800"], ids=["short", "invalid-unicode"])
def test_shared_invite_sessions_refuse_malformed_configured_signer(
    shared_identity,
    monkeypatch,
    secret,
):
    from maverick_dashboard import invites

    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", secret)
    with pytest.raises(invites.InviteStoreError, match="does not match"):
        invites.local_session_secret()
    assert shared_identity["global"].document == {}


def test_readiness_binds_shared_invite_signer_and_rejects_key_skew(
    shared_identity,
    monkeypatch,
):
    from maverick import (
        agent_trust,
        authority_state,
        client,
        governed_records,
        shield_policy,
    )
    from maverick_dashboard import app, invites

    monkeypatch.setattr(client, "client_binding_enforced", lambda: False)
    monkeypatch.setattr(client, "client_id", lambda: None)
    monkeypatch.setattr(shield_policy, "shield_required", lambda: False)
    monkeypatch.setattr(shield_policy, "shield_available", lambda: True)
    monkeypatch.setattr(agent_trust, "load_trust_state", lambda: (False, {}))
    monkeypatch.setattr(
        authority_state,
        "deployment_guarantee",
        lambda: (True, "test"),
    )
    monkeypatch.setattr(
        governed_records,
        "configured_governed_records_backend",
        lambda: "postgres",
    )
    monkeypatch.setattr(invites, "invites_enabled", lambda: True)
    monkeypatch.setattr(invites, "local_login_mode", lambda: True)

    first_key = "readiness-key-one-0123456789-abcdef"
    second_key = "readiness-key-two-0123456789-abcdef"  # pragma: allowlist secret
    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", first_key)
    ok, checks = app._readiness_deep_checks()
    assert ok
    assert checks["invite_session_signing"] == "ok"
    assert shared_identity["global"].document["session_signing_sha256"] == (
        hashlib.sha256(first_key.encode("utf-8")).hexdigest()
    )

    monkeypatch.setenv("MAVERICK_DASHBOARD_SESSION_SECRET", second_key)
    ok, checks = app._readiness_deep_checks()
    assert not ok
    assert checks["invite_session_signing"].startswith("fail:")
