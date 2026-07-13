"""Email invite links: mint/peek/consume store semantics, the fail-closed
feature gate, the scanner-proof GET/POST accept flow, local (no-IdP) session
sign-in, and the RBAC binding. See maverick_dashboard/invites.py."""
from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Scratch HOME (invite/rbac/session stores) + world DB, invites OFF."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MAVERICK_DASHBOARD_INVITES", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    monkeypatch.delenv("MAVERICK_DASHBOARD_REQUIRE_AUTH", raising=False)
    monkeypatch.setattr("maverick.config.load_config", lambda *a, **k: {})
    from maverick import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")


@pytest.fixture
def client():
    from maverick_dashboard.app import app
    return TestClient(app, headers={"Origin": "http://testserver"})


# ---------- store + gate (no HTTP) ----------

def test_disabled_by_default():
    from maverick_dashboard import invites
    assert invites.invites_enabled() is False


def test_create_peek_consume_roundtrip(monkeypatch):
    from maverick_dashboard import invites
    inv, token = invites.create_invite("Bob@Corp.com", "operator", created_by="me")
    assert inv.email == "bob@corp.com" and inv.pending
    # the raw token never lands on disk — only its hash
    stored = json.loads(invites.store_path().read_text())
    assert token not in json.dumps(stored)
    assert invites.peek_invite(token).id == inv.id       # peek does NOT consume
    assert invites.peek_invite(token) is not None
    used = invites.consume_invite(token)
    assert used.used_by == "user:bob@corp.com"
    assert invites.consume_invite(token) is None          # single-use
    assert invites.peek_invite(token) is None


def test_expired_invite_is_dead():
    from maverick_dashboard import invites
    _inv, token = invites.create_invite("a@b.co", "viewer", created_by="me",
                                        ttl_hours=-1)
    assert invites.peek_invite(token) is None
    assert invites.consume_invite(token) is None


def test_revoke_kills_the_link():
    from maverick_dashboard import invites
    inv, token = invites.create_invite("a@b.co", "viewer", created_by="me")
    assert invites.revoke_invite(inv.id) is True
    assert invites.peek_invite(token) is None


def test_validation():
    from maverick_dashboard import invites
    with pytest.raises(ValueError):
        invites.create_invite("not-an-email", "viewer", created_by="me")
    with pytest.raises(ValueError):
        invites.create_invite("a@b.co", "superuser", created_by="me")


def test_local_session_secret_is_persistent_and_private(tmp_path):
    from maverick_dashboard import invites
    s1 = invites.local_session_secret()
    s2 = invites.local_session_secret()
    assert s1 == s2 and len(s1) >= 32
    key = invites._session_secret_path()
    assert key.exists() and (key.stat().st_mode & 0o777) == 0o600


# ---------- HTTP: fail-closed while disabled ----------

def test_routes_404_when_disabled(client):
    assert client.get("/auth/invite/inv_whatever").status_code == 404
    assert client.post("/auth/invite/inv_whatever").status_code == 404
    # admin mint form is also closed
    r = client.post("/users/invite", data={"email": "a@b.co", "role": "viewer"})
    assert r.status_code == 404


# ---------- HTTP: local (no-IdP) accept flow ----------

def test_local_accept_flow_signs_in_and_binds_role(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    from maverick_dashboard import invites, rbac
    inv, token = invites.create_invite("bob@corp.com", "viewer", created_by="me")
    # GET renders the confirmation page and must NOT consume (scanner prefetch)
    r = client.get(f"/auth/invite/{token}")
    assert r.status_code == 200 and "bob@corp.com" in r.text
    assert invites.peek_invite(token) is not None
    # POST consumes: signs the browser in + binds the role
    r = client.post(f"/auth/invite/{token}", follow_redirects=False)
    assert r.status_code == 303
    assert "mvk_session=" in r.headers.get("set-cookie", "")
    assert rbac.get_stored_role("user:bob@corp.com") == "viewer"
    # single-use: replaying the link is dead
    assert client.post(f"/auth/invite/{token}").status_code == 410
    # the cookie now establishes a principal whose role gates admin pages
    assert client.get("/users").status_code == 403      # viewer ≠ admin
    client.cookies.clear()
    assert client.get("/users").status_code == 200      # anonymous loopback = legacy


def test_accept_post_requires_same_origin(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    from maverick_dashboard import invites
    _inv, token = invites.create_invite("bob@corp.com", "viewer", created_by="me")
    from maverick_dashboard.app import app
    hostile = TestClient(app)                            # no Origin/Referer at all
    assert hostile.post(f"/auth/invite/{token}").status_code == 400
    evil = TestClient(app, headers={"Origin": "http://evil.example"})
    assert evil.post(f"/auth/invite/{token}").status_code == 400
    assert invites.peek_invite(token) is not None        # nothing was consumed


def test_tampered_session_cookie_is_ignored(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    from maverick.web_session import sign_session
    from maverick_dashboard import invites
    forged = sign_session({"sub": "eve@corp.com", "iat": 0, "exp": 2**33},
                          "not-the-real-secret")
    client.cookies.set("mvk_session", forged)

    class _Req:
        cookies = {"mvk_session": forged}
    assert invites.local_session_principal(_Req()) is None


# ---------- HTTP: admin mint + revoke on /users ----------

def test_admin_mints_and_revokes_links(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    from maverick_dashboard import invites
    r = client.post("/users/invite", data={"email": "new@corp.com",
                                           "role": "operator"})
    assert r.status_code == 200
    assert "/auth/invite/inv_" in r.text                 # link shown once
    pending = [i for i in invites.list_invites() if i.pending]
    assert [i.email for i in pending] == ["new@corp.com"]
    r = client.post("/users/invite/revoke", data={"invite_id": pending[0].id},
                    follow_redirects=False)
    assert r.status_code == 303
    assert not [i for i in invites.list_invites() if i.pending]


def test_mint_rejects_bad_input(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    r = client.post("/users/invite", data={"email": "nope", "role": "viewer"})
    assert r.status_code == 400


# ---------- require_auth integration ----------

def test_invites_satisfy_require_auth_boot_guard(monkeypatch):
    import maverick_dashboard.app as app_mod
    monkeypatch.setenv("MAVERICK_DASHBOARD_REQUIRE_AUTH", "1")
    monkeypatch.setattr("maverick.oidc.oidc_enabled", lambda: False)
    monkeypatch.setattr("maverick.proxy_auth.proxy_auth_enabled", lambda: False)
    with pytest.raises(RuntimeError):
        app_mod._assert_dashboard_auth_configured()
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    app_mod._assert_dashboard_auth_configured()          # invites count


def test_require_auth_fails_closed_without_session(client, monkeypatch):
    monkeypatch.setenv("MAVERICK_DASHBOARD_INVITES", "1")
    monkeypatch.setenv("MAVERICK_DASHBOARD_REQUIRE_AUTH", "1")
    from maverick_dashboard import invites
    _inv, token = invites.create_invite("bob@corp.com", "admin", created_by="me")
    assert client.get("/goals").status_code == 401       # anonymous → closed
    assert client.get(f"/auth/invite/{token}").status_code == 200  # link exempt
    r = client.post(f"/auth/invite/{token}", follow_redirects=False)
    assert r.status_code == 303                          # accept works
    assert client.get("/users").status_code == 200       # admin session opens it
