"""Personal / service-account API keys: scopes, expiry, rotation, revocation.

Hermetic like the other dashboard tests: OIDC is simulated by mapping
``Bearer <name>`` to ``user:<name>`` (no PyJWT, no network); the key store,
RBAC roster and audit log live under a per-test ``MAVERICK_HOME``.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    for name in ("MAVERICK_DASHBOARD_TOKEN", "MAVERICK_TENANT", "MAVERICK_DASHBOARD_ADMINS",
                 "MAVERICK_DASHBOARD_REQUIRE_AUTH", "MAVERICK_DASHBOARD_API_KEYS",
                 "MAVERICK_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    from maverick import config, world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    import maverick.audit.writer as aw
    aw._default = None
    aw._defaults.clear()
    from maverick_dashboard import api_keys, auth
    api_keys._last_used_written.clear()
    auth._break_glass_last_audit[0] = None
    yield
    config.reset_config_cache()
    aw._default = None
    aw._defaults.clear()


def _client() -> TestClient:
    from maverick_dashboard.app import app
    return TestClient(app, headers=ORIGIN)


def _sign_in_map(monkeypatch):
    """``Bearer <name>`` -> ``user:<name>`` (the OIDC path, without PyJWT)."""
    from maverick.oidc import VerifiedPrincipal
    from maverick_dashboard import auth

    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)

    def _verify(token, **_kw):
        return VerifiedPrincipal(sub=token, issuer="https://issuer.example",
                                 audience="maverick", claims={"sub": token})

    monkeypatch.setattr(auth, "verify_oidc_token", _verify)


def _as(name: str) -> dict:
    return {"Authorization": f"Bearer {name}"}


def _key(secret: str) -> dict:
    return {"Authorization": f"Bearer {secret}"}


def _create(client, who, **body):
    payload = {"label": "nightly report", "scopes": ["tasks:read"]}
    payload.update(body)
    return client.post("/api/v1/api-keys", json=payload, headers=_as(who))


# ---- create / list / use ----------------------------------------------------

def test_create_shows_secret_once_and_stores_only_its_hash(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import api_keys, rbac
    rbac.set_role("user:alice", "operator")
    c = _client()

    r = _create(c, "alice", scopes=["tasks:read", "tasks:write"])
    assert r.status_code == 201, r.text
    body = r.json()
    secret = body["secret"]
    assert secret.startswith("lwk_") and len(secret) > 40
    assert "won't see it again" in body["notice"]
    assert body["key"]["prefix"] == secret[:12]
    assert body["key"]["scopes"] == ["tasks:read", "tasks:write"]
    assert body["key"]["owner"] == "user:alice"
    # Only the digest is on disk; never the secret, never the prefix beyond display.
    raw = api_keys.store_path().read_text()
    assert secret not in raw
    assert hashlib.sha256(secret.encode()).hexdigest() in raw

    listed = c.get("/api/v1/api-keys", headers=_as("alice")).json()
    assert [k["id"] for k in listed["keys"]] == [body["key"]["id"]]
    assert "secret" not in listed["keys"][0]
    assert listed["keys"][0]["status"] == "active"
    # ~90 days by default.
    assert abs(listed["keys"][0]["expires_at"] - (time.time() + 90 * 86400)) < 120


def test_key_authenticates_as_its_owner_and_records_last_use(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    secret = _create(c, "alice").json()["secret"]

    r = c.get("/api/v1/account/me", headers=_key(secret))
    assert r.status_code == 200, r.text
    assert r.json()["signed_in"] is True
    # A read-only key: view yes, operate no -- even though Alice may operate.
    allowed = {a["label"]: a["allowed"] for a in r.json()["abilities"]}
    assert allowed["See work and results"] and not allowed["Start and run tasks"]

    listed = c.get("/api/v1/api-keys", headers=_as("alice")).json()["keys"][0]
    assert listed["last_used_at"] is not None
    assert listed["last_used_ip"] == "testclient"


def test_last_used_write_is_rate_limited(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import api_keys, rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    secret = _create(c, "alice").json()["secret"]
    writes = []
    real_write = api_keys._write
    monkeypatch.setattr(api_keys, "_write", lambda data: (writes.append(1), real_write(data)))
    for _ in range(5):
        assert c.get("/api/v1/account/me", headers=_key(secret)).status_code == 200
    assert len(writes) == 1


def test_unknown_key_is_refused_and_never_falls_back_to_ambient_identity(monkeypatch):
    _sign_in_map(monkeypatch)
    c = _client()
    r = c.get("/api/v1/account/me", headers=_key("lwk_" + "x" * 43))
    assert r.status_code == 401
    assert r.json()["detail"] == "This API key is not recognised."


# ---- scopes -----------------------------------------------------------------

def test_read_scope_cannot_call_a_write_route_or_an_admin_route(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:alice")
    rbac.set_role("user:alice", "admin")
    c = _client()
    secret = _create(c, "alice", scopes=["tasks:read"]).json()["secret"]

    # Alice herself may operate ...
    assert c.post("/api/v1/graph/rebuild", headers=_as("alice")).status_code != 403
    # ... but her read-only key may not, and the message says why.
    r = c.post("/api/v1/graph/rebuild", headers=_key(secret))
    assert r.status_code == 403
    assert "does not have a scope" in r.json()["detail"]
    r = c.get("/api/v1/service-accounts", headers=_key(secret))
    assert r.status_code == 403
    # Reading still works.
    assert c.get("/api/v1/account/me", headers=_key(secret)).status_code == 200


def test_write_scope_allows_the_write_route(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    secret = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    assert c.post("/api/v1/graph/rebuild", headers=_key(secret)).status_code != 403


def test_task_and_approval_scopes_do_not_cross_authorize(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac

    rbac.set_role("user:alice", "operator")
    c = _client()
    task_key = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    approval_key = _create(c, "alice", scopes=["approvals:decide"]).json()["secret"]
    both_key = _create(c, "alice", scopes=["tasks:write", "approvals:decide"]).json()["secret"]

    assert c.post("/api/v1/graph/rebuild", headers=_key(task_key)).status_code != 403
    assert c.post("/api/v1/graph/rebuild", headers=_key(approval_key)).status_code == 403
    assert c.get("/api/v1/approvals", headers=_key(task_key)).status_code == 403
    assert c.get("/api/v1/approvals", headers=_key(approval_key)).status_code == 200
    assert c.get("/approvals", headers=_key(task_key)).status_code == 403
    assert c.get("/approvals", headers=_key(approval_key)).status_code == 200
    # Queue claim and vote are action gates, even without a matching approval.
    for suffix in ("claim", "approve"):
        path = f"/api/v1/approvals/424242/{suffix}"
        assert c.post(path, headers=_key(task_key)).status_code == 403
        assert c.post(path, headers=_key(approval_key)).status_code == 404
    assert c.get("/api/v1/approvals", headers=_key(both_key)).status_code == 200
    assert c.post("/api/v1/graph/rebuild", headers=_key(both_key)).status_code != 403


def test_flow_decision_authority_uses_approval_scope_off_queue(monkeypatch):
    _sign_in_map(monkeypatch)
    from fastapi import HTTPException, Request
    from maverick_dashboard import auth, rbac
    from maverick_dashboard.approval_authority import require_approval_authority

    rbac.set_role("user:alice", "operator")
    c = _client()
    task_key = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    approval_key = _create(c, "alice", scopes=["approvals:decide"]).json()["secret"]

    def request_for(secret):
        request = Request({"type": "http", "method": "POST",
                           "path": "/api/v1/flows/runs/run-1/resume", "headers": [],
                           "client": ("testclient", 1234)})
        request.state.principal = auth._authenticate_api_key(request, secret)
        return request

    with pytest.raises(HTTPException) as denied:
        require_approval_authority(
            request_for(task_key), action="flow.approval.decide", resource="run-1",
        )
    assert denied.value.status_code == 403
    authority = require_approval_authority(
        request_for(approval_key), action="flow.approval.decide", resource="run-1",
    )
    assert authority.action == "flow.approval.decide"


def test_task_key_cannot_read_approval_cards_through_other_pages(monkeypatch, tmp_path):
    _sign_in_map(monkeypatch)
    from fastapi import Request
    from maverick.world_model import WorldModel
    from maverick_dashboard import auth, rbac
    from maverick_dashboard.content_access import can_read_approval

    rbac.set_role("user:alice", "operator")
    c = _client()
    task_key = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    approval_key = _create(c, "alice", scopes=["approvals:decide"]).json()["secret"]
    world = WorldModel(tmp_path / "world.db")
    approval = world.get_approval(world.create_approval(
        "reviewed change", requested_by="user:alice", detail="private approval card",
    ))

    def request_for(secret):
        request = Request({"type": "http", "method": "GET", "path": "/oversight",
                           "headers": [], "client": ("testclient", 1234)})
        request.state.principal = auth._authenticate_api_key(request, secret)
        return request

    assert not can_read_approval(request_for(task_key), approval, world)
    assert can_read_approval(request_for(approval_key), approval, world)


def test_task_key_cannot_make_domain_review_decisions(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac

    rbac.set_role("user:alice", "operator")
    c = _client()
    task_key = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    decisions = (
        ("/api/v1/security/evidence/missing/decision",
         {"decision": "approved", "rationale": "Reviewed", "expected_revision": 1}),
        ("/api/v1/security/vendors/missing/decision",
         {"decision": "approved", "rationale": "Reviewed", "expected_revision": 1}),
        ("/api/v1/consent/decisions", {"profile_id": "profile", "purpose_id": "purpose"}),
        ("/api/v1/content-access/missing/decision",
         {"decision": "approve", "expected_revision": 1}),
    )
    for path, body in decisions:
        response = c.post(path, json=body, headers=_key(task_key))
        assert response.status_code == 403, (path, response.text)
        assert "does not have a scope" in response.json()["detail"]


def test_admin_scope_keeps_both_actions_and_owner_demotion_caps_approval(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac

    rbac.set_role("user:alice", "admin")
    rbac.set_role("user:bob", "operator")
    c = _client()
    admin_key = _create(c, "alice", scopes=["admin"]).json()["secret"]
    approval_key = _create(c, "bob", scopes=["approvals:decide"]).json()["secret"]

    assert c.get("/api/v1/approvals", headers=_key(admin_key)).status_code == 200
    assert c.post("/api/v1/graph/rebuild", headers=_key(admin_key)).status_code != 403
    assert c.get("/api/v1/approvals", headers=_key(approval_key)).status_code == 200
    rbac.set_role("user:bob", "viewer")
    assert c.get("/api/v1/approvals", headers=_key(approval_key)).status_code == 403


def test_key_cannot_exceed_owner_role(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:vic", "viewer")
    c = _client()
    r = _create(c, "vic", scopes=["tasks:read", "tasks:write"])
    assert r.status_code == 400
    assert "never do more than" in r.json()["detail"]
    # The page only offers the scopes the role allows.
    assert c.get("/api/v1/api-keys", headers=_as("vic")).json()["scopes_available"] == ["tasks:read"]


def test_demoting_the_owner_shrinks_the_key(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    secret = _create(c, "alice", scopes=["tasks:write"]).json()["secret"]
    assert c.post("/api/v1/graph/rebuild", headers=_key(secret)).status_code != 403
    rbac.set_role("user:alice", "viewer")
    assert c.post("/api/v1/graph/rebuild", headers=_key(secret)).status_code == 403


# ---- rotation / expiry / revocation ------------------------------------------

def _age(key_id: str, **fields):
    from maverick_dashboard import api_keys
    with api_keys._locked(api_keys.store_path()):
        data = api_keys._load()
        data["keys"][key_id].update(fields)
        api_keys._write(data)


def test_rotate_keeps_old_key_for_the_grace_period_then_stops_it(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    first = _create(c, "alice", scopes=["tasks:read"]).json()
    old_secret, old_id = first["secret"], first["key"]["id"]

    r = c.post(f"/api/v1/api-keys/{old_id}/rotate", headers=_as("alice"))
    assert r.status_code == 200, r.text
    new_secret, new_key = r.json()["secret"], r.json()["key"]
    assert new_secret != old_secret and new_key["rotated_from"] == old_id
    assert new_key["scopes"] == ["tasks:read"] and new_key["label"] == "nightly report"
    # Both work during the grace period.
    assert c.get("/api/v1/account/me", headers=_key(old_secret)).status_code == 200
    assert c.get("/api/v1/account/me", headers=_key(new_secret)).status_code == 200
    listed = {k["id"]: k for k in c.get("/api/v1/api-keys", headers=_as("alice")).json()["keys"]}
    assert listed[old_id]["status"] == "rotating"
    assert abs(listed[old_id]["grace_until"] - (time.time() + 24 * 3600)) < 120
    # Grace over: the old key stops with a plain message; the new one goes on.
    _age(old_id, grace_until=time.time() - 1)
    r = c.get("/api/v1/account/me", headers=_key(old_secret))
    assert r.status_code == 401
    assert "rotated" in r.json()["detail"]
    assert c.get("/api/v1/account/me", headers=_key(new_secret)).status_code == 200
    # A rotated-out key cannot be rotated again.
    assert c.post(f"/api/v1/api-keys/{old_id}/rotate", headers=_as("alice")).status_code == 400


def test_expired_key_fails_with_a_plain_message_and_the_page_warns_before(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    made = _create(c, "alice", expires_in_days=3).json()
    listed = c.get("/api/v1/api-keys", headers=_as("alice")).json()
    assert listed["expiring_soon"] == [made["key"]["id"]]
    assert listed["keys"][0]["status"] == "expiring"

    _age(made["key"]["id"], expires_at=time.time() - 1)
    r = c.get("/api/v1/account/me", headers=_key(made["secret"]))
    assert r.status_code == 401
    assert r.json()["detail"].startswith("This API key has expired.")


def test_expiry_is_capped_by_the_installation_maximum(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    r = _create(c, "alice", expires_in_days=400)
    assert r.status_code == 400 and "at most 365 days" in r.json()["detail"]
    assert _create(c, "alice", expires_in_days=0).status_code == 400


def test_revoke_stops_the_key_at_once_and_others_cannot_touch_it(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    rbac.set_role("user:bob", "operator")
    c = _client()
    made = _create(c, "alice").json()
    key_id = made["key"]["id"]
    # Bob cannot see, revoke or rotate Alice's key (404: no existence oracle).
    assert c.get("/api/v1/api-keys", headers=_as("bob")).json()["keys"] == []
    assert c.post(f"/api/v1/api-keys/{key_id}/revoke", headers=_as("bob")).status_code == 404
    assert c.post(f"/api/v1/api-keys/{key_id}/rotate", headers=_as("bob")).status_code == 404

    r = c.post(f"/api/v1/api-keys/{key_id}/revoke", headers=_as("alice"))
    assert r.status_code == 200 and r.json()["key"]["status"] == "revoked"
    r = c.get("/api/v1/account/me", headers=_key(made["secret"]))
    assert r.status_code == 401 and r.json()["detail"] == "This API key was revoked."


def test_a_key_can_list_its_owner_s_keys(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "operator")
    c = _client()
    secret = _create(c, "alice").json()["secret"]
    # The key acts as Alice on the keys API too (view scope).
    assert c.get("/api/v1/api-keys", headers=_key(secret)).status_code == 200


def test_api_key_cannot_mint_rotate_or_revoke_credentials(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac

    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:alice")
    rbac.set_role("user:alice", "admin")
    c = _client()
    read_key = _create(c, "alice", scopes=["tasks:read"]).json()
    admin_key = _create(c, "alice", scopes=["admin"]).json()
    account = c.post("/api/v1/service-accounts",
                     json={"name": "existing-sync", "role": "viewer"},
                     headers=_as("alice"))
    assert account.status_code == 201

    for bearer in (read_key["secret"], admin_key["secret"]):
        headers = _key(bearer)
        assert c.get("/api/v1/api-keys", headers=headers).status_code == 200
        assert c.get("/account/api-keys", headers=headers).status_code == 403
        assert c.post("/api/v1/api-keys",
                      json={"label": "elevated", "scopes": ["admin"]},
                      headers=headers).status_code == 403
        assert c.post(f"/api/v1/api-keys/{admin_key['key']['id']}/rotate",
                      headers=headers).status_code == 403
        assert c.post(f"/api/v1/api-keys/{admin_key['key']['id']}/revoke",
                      headers=headers).status_code == 403
        assert c.post("/api/v1/service-accounts", json={"name": "new-sync", "role": "viewer"},
                      headers=headers).status_code == 403
        assert c.post("/api/v1/service-accounts/existing-sync/disable",
                      headers=headers).status_code == 403

    # The signed-in owner still manages and rotates keys in the product.
    assert len(c.get("/api/v1/api-keys", headers=_as("alice")).json()["keys"]) == 2
    assert rbac.get_stored_role("user:svc:existing-sync") == "viewer"
    assert c.post(f"/api/v1/api-keys/{admin_key['key']['id']}/rotate",
                  headers=_as("alice")).status_code == 200


# ---- service accounts -------------------------------------------------------

def test_admin_creates_service_account_which_holds_keys_but_cannot_sign_in(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    monkeypatch.setenv("MAVERICK_DASHBOARD_ADMINS", "user:root")
    rbac.set_role("user:alice", "operator")
    c = _client()

    # Only administrators.
    assert c.post("/api/v1/service-accounts", json={"name": "billing-sync", "role": "operator"},
                  headers=_as("alice")).status_code == 403
    r = c.post("/api/v1/service-accounts",
               json={"name": "billing-sync", "role": "operator", "description": "Posts invoices"},
               headers=_as("root"))
    assert r.status_code == 201, r.text
    account = r.json()["service_account"]
    assert account["principal"] == "user:svc:billing-sync"
    assert rbac.get_stored_role("user:svc:billing-sync") == "operator"
    assert c.post("/api/v1/service-accounts", json={"name": "billing-sync", "role": "viewer"},
                  headers=_as("root")).status_code == 400

    # A key for it, minted by the admin, acts as the account within its role.
    r = _create(c, "root", owner="user:svc:billing-sync", scopes=["tasks:write"])
    assert r.status_code == 201, r.text
    secret = r.json()["secret"]
    me = c.get("/api/v1/account/me", headers=_key(secret))
    assert me.status_code == 200 and me.json()["signed_in"] is True
    assert c.get("/api/v1/api-keys", headers=_key(secret)).json()["you"] == "user:svc:billing-sync"
    # An admin scope is more than the account's operator role.
    assert _create(c, "root", owner="user:svc:billing-sync", scopes=["admin"]).status_code == 400
    # A non-admin cannot mint for it.
    assert _create(c, "alice", owner="user:svc:billing-sync").status_code == 403

    # The subject can never sign in interactively, even if an IdP issued it.
    assert c.get("/api/v1/account/me", headers=_as("svc:billing-sync")).status_code == 401

    # Admin overview lists the account and every key.
    overview = c.get("/api/v1/api-keys?all=1", headers=_as("root")).json()
    assert overview["showing_everyone"] and overview["service_accounts"][0]["slug"] == "billing-sync"
    assert overview["service_accounts"][0]["keys"][0]["scopes"] == ["tasks:write"]

    # Disabling revokes all of its keys and removes the role.
    r = c.post("/api/v1/service-accounts/billing-sync/disable", headers=_as("root"))
    assert r.status_code == 200 and r.json()["service_account"]["disabled_at"]
    assert c.get("/api/v1/account/me", headers=_key(secret)).status_code == 401
    assert rbac.get_stored_role("user:svc:billing-sync") is None
    assert _create(c, "root", owner="user:svc:billing-sync").status_code == 400


# ---- static token / break-glass ---------------------------------------------

def test_static_token_still_works_and_is_audited_as_break_glass(monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "static-break-glass-token-for-tests")  # pragma: allowlist secret
    rows = []
    import maverick.audit as audit_pkg
    monkeypatch.setattr(audit_pkg, "audit_event", lambda kind, **payload: rows.append((kind, payload)) or True)
    c = _client()
    tok = {"Authorization": "Bearer static-break-glass-token-for-tests"}
    assert c.get("/api/v1/account/me", headers=tok).status_code == 200
    assert c.get("/api/v1/account/me", headers=tok).status_code == 200
    assert c.post("/api/v1/api-keys", json={"label": "recovery", "scopes": ["tasks:read"]},
                  headers=tok).status_code == 201
    kinds = [getattr(k, "value", k) for k, _p in rows]
    assert kinds.count("break_glass_token_used") == 1  # rate-limited
    payload = next(p for k, p in rows if getattr(k, "value", k) == "break_glass_token_used")
    assert payload["path"] == "/api/v1/account/me"
    # A wrong bearer that is not a key is still refused.
    assert c.get("/api/v1/account/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_settings_page_warns_while_the_static_token_is_set(monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_TOKEN", "static-break-glass-token-for-tests")  # pragma: allowlist secret
    monkeypatch.setattr("maverick.audit.audit_event", lambda *a, **k: True)
    c = _client()
    page = c.get("/settings", headers={"Authorization": "Bearer static-break-glass-token-for-tests"})
    assert page.status_code == 200
    assert "shared server access token is set" in page.text
    assert 'href="/account/api-keys"' in page.text


def test_key_reaches_a_remote_caller_in_no_token_mode(monkeypatch):
    """Without a static token the dashboard is loopback-only; a key is an
    explicit credential the local operator minted, so it may come from
    another computer (and still fails closed when wrong)."""
    from maverick_dashboard import api_keys
    monkeypatch.setattr("maverick.audit.audit_event", lambda *a, **k: True)
    key, secret = api_keys.create_key(label="remote script", scopes=["tasks:read"],
                                      owner="user:local", created_by="user:local")
    from maverick_dashboard.app import app
    remote = TestClient(app, base_url="http://testserver", client=("203.0.113.9", 4321))
    assert remote.get("/api/v1/account/me").status_code == 401
    assert remote.get("/api/v1/account/me", headers=_key("lwk_" + "y" * 43)).status_code == 401
    r = remote.get("/api/v1/api-keys", headers=_key(secret))
    assert r.status_code == 200, r.text
    assert r.json()["you"] == "user:local"


# ---- hygiene ----------------------------------------------------------------

def test_secrets_scrubber_redacts_keys():
    from maverick.secrets import scrub
    text = "using lwk_" + "A1b2C3d4" * 6 + " for the report"
    assert "lwk_" not in scrub(text) and "REDACTED" in scrub(text)
    assert "lwk_" not in scrub("Authorization: Bearer lwk_" + "z" * 43)


def test_disabled_setting_turns_the_feature_off(monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_API_KEYS", "0")
    c = _client()
    assert c.get("/api/v1/api-keys").status_code == 404
    assert c.get("/account/api-keys").status_code == 404
    from maverick_dashboard import api_keys
    with pytest.raises(api_keys.ApiKeyError):
        api_keys.create_key(label="x", scopes=["tasks:read"], owner="user:local", created_by="")


def test_page_renders_for_a_signed_in_person(monkeypatch):
    _sign_in_map(monkeypatch)
    from maverick_dashboard import rbac
    rbac.set_role("user:alice", "viewer")
    page = _client().get("/account/api-keys", headers=_as("alice"))
    assert page.status_code == 200
    assert "Copy this key now. You won't see it again." in page.text


def test_corrupt_store_refuses_every_key(monkeypatch, tmp_path):
    from maverick_dashboard import api_keys
    api_keys.store_path().write_text("{not json")
    verified, reason = api_keys.verify("lwk_" + "q" * 43)
    assert verified is None and "unavailable" in reason
    data = json.dumps({"keys": {"k": {"id": "k", "label": "x"}}, "service_accounts": {}})
    api_keys.store_path().write_text(data)
    assert api_keys.verify("lwk_" + "q" * 43)[0] is None
