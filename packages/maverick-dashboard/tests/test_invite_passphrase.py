"""Invited colleagues can sign back in.

Accepting an invitation used to create a session but no passphrase, so a
colleague who signed out was locked out: the link says "already used" and the
sign-in page rejected their email. Now, when invitations sign people in
locally (no SSO), accepting sets a passphrase with the owner's rules (14 to
1,024 characters), hashing (scrypt with a per-account salt) and lockout (five
wrong tries, then a one-minute wait); the colleague signs in again from any
computer; the owner's own sign-in still works only on the computer running
Lightwork. With SSO the company account signs people in and no passphrase is
asked for.
"""
from __future__ import annotations

import os
import re
import stat

import pytest
from fastapi.testclient import TestClient

LOCAL = "http://127.0.0.1:8765"
REMOTE = "https://lightwork.example"  # remote access runs over TLS (Secure cookies)
OWNER = "owner@example.test"
OWNER_PASS = "Synthetic owner passphrase for invites"  # pragma: allowlist secret
COLLEAGUE = "sam@example.test"
GOOD = "harbor violet tram ledger"  # pragma: allowlist secret - synthetic fixture passphrase
OTHER = "a different synthetic passphrase"  # pragma: allowlist secret - synthetic fixture passphrase


@pytest.fixture
def workspace(monkeypatch, tmp_path):
    from maverick import client as client_binding
    from maverick import config, world_model

    for name in tuple(os.environ):
        if name.startswith(("MAVERICK_", "LIGHTWORK_")):
            monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text('[client]\nid="invite-passphrase"\n', encoding="utf-8")
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(config_path))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    config.reset_config_cache()
    client_binding.reset_client_cache()
    from maverick_dashboard.app import app

    owner = TestClient(app, base_url=LOCAL, client=("127.0.0.1", 50001),
                       headers={"Origin": LOCAL}, follow_redirects=False)
    claimed = owner.post("/auth/local-owner", data={
        "email": OWNER, "password": OWNER_PASS, "password2": OWNER_PASS, "confirm": "yes"})
    assert claimed.status_code == 303, claimed.text
    yield owner
    owner.close()
    config.reset_config_cache()
    client_binding.reset_client_cache()


def _remote():
    """A colleague's browser on another computer."""
    from maverick_dashboard.app import app

    return TestClient(app, base_url=REMOTE, client=("192.0.2.40", 50002),
                      headers={"Origin": REMOTE}, follow_redirects=False)


def _invite(owner, email=COLLEAGUE, role="operator") -> str:
    made = owner.post("/users/invite", data={"email": email, "role": role})
    assert made.status_code == 200, made.text
    return re.search(r"/auth/invite/(inv_[A-Za-z0-9_-]+)", made.text)[0]


def _accept(browser, link, password=GOOD, password2=None):
    return browser.post(link, data={"password": password,
                                    "password2": password if password2 is None else password2})


def _sign_in(browser, email=COLLEAGUE, password=GOOD):
    return browser.post("/auth/local-login", data={"email": email, "password": password})


def test_invite_page_is_branded_names_the_role_plainly_and_asks_for_a_passphrase(workspace):
    link = _invite(workspace)
    page = _remote().get(link)
    assert page.status_code == 200
    html = page.text
    assert "/static/lightwork.css" in html and "daybreak-logo" in html
    assert "You&#39;re invited" in html or "You're invited" in html
    assert "Operator" in html and "run work and approve actions" in html
    assert "<code>operator</code>" not in html
    assert re.search(r'<input[^>]*name="password"[^>]*autocomplete="new-password"', html)
    assert re.search(r'<input[^>]*name="password2"', html)
    # Password managers save the right account name with the new passphrase.
    assert re.search(rf'<input[^>]*autocomplete="username"[^>]*value="{re.escape(COLLEAGUE)}"', html)
    assert "Accept invitation" in html


def test_accept_set_passphrase_sign_out_and_sign_back_in_from_another_computer(workspace, tmp_path):
    from maverick_dashboard import invites, rbac

    link = _invite(workspace)
    colleague = _remote()
    accepted = _accept(colleague, link)
    assert accepted.status_code == 303, accepted.text
    assert "mvk_session" in colleague.cookies
    assert rbac.get_stored_role("user:" + COLLEAGUE) == "operator"
    assert invites.peek_invite(link.rsplit("/", 1)[1]) is None
    store = tmp_path / "dashboard-passphrases.json"
    assert store.exists() and GOOD not in store.read_text(encoding="utf-8")
    assert stat.S_IMODE(store.stat().st_mode) == 0o600
    assert colleague.get("/api/v1/features/modules").status_code == 200
    # Sign out, then come back the next day.
    assert colleague.get("/auth/logout").status_code == 303
    assert "mvk_session" not in colleague.cookies
    denied = colleague.get("/goals", headers={"Accept": "text/html"})
    assert denied.status_code in {302, 303} and denied.headers["location"].startswith("/auth/local-login")
    form = colleague.get("/auth/local-login")
    assert form.status_code == 200 and 'name="email"' in form.text
    signed_in = _sign_in(colleague)
    assert signed_in.status_code == 303, signed_in.text
    assert "mvk_session" in colleague.cookies
    assert colleague.get("/api/v1/features/modules").status_code == 200


def test_a_bad_passphrase_at_acceptance_keeps_the_invitation_usable(workspace):
    from maverick_dashboard import invites

    link = _invite(workspace)
    token = link.rsplit("/", 1)[1]
    colleague = _remote()
    for password, password2 in (("", ""), ("too short 13c", "too short 13c"), (GOOD, GOOD + " typo")):
        refused = _accept(colleague, link, password, password2)
        assert refused.status_code == 422, (password, refused.text)
        assert "mvk_session" not in colleague.cookies
        assert invites.peek_invite(token) is not None
    assert "14" in _accept(colleague, link, "short", "short").text
    assert "do not match" in _accept(colleague, link, GOOD, OTHER).text
    assert _accept(colleague, link).status_code == 303


def test_a_used_link_is_refused_and_cannot_replace_the_passphrase(workspace):
    link = _invite(workspace)
    assert _accept(_remote(), link).status_code == 303
    again = _accept(_remote(), link, OTHER)
    assert again.status_code == 410
    assert "Ask your administrator to send a new one" in again.text
    assert _sign_in(_remote(), password=OTHER).status_code == 401
    assert _sign_in(_remote(), password=GOOD).status_code == 303


def test_wrong_passphrases_lock_the_account_like_the_owner_and_never_reveal_who_exists(workspace):
    link = _invite(workspace)
    assert _accept(_remote(), link).status_code == 303
    browser = _remote()
    unknown = _sign_in(browser, email="nobody@example.test", password=OTHER)
    wrong = _sign_in(browser, password=OTHER)
    assert unknown.status_code == wrong.status_code == 401
    text = lambda page: re.sub(r"\s+", " ", page.text)  # noqa: E731
    assert "The email or passphrase was not accepted." in text(wrong)
    assert "ask your administrator for a new invite" in text(wrong)
    assert text(unknown).count("not accepted") == text(wrong).count("not accepted")
    for _ in range(4):
        assert _sign_in(browser, password=OTHER).status_code == 401
    locked = _sign_in(browser, password=GOOD)
    assert locked.status_code == 429
    assert "mvk_session" not in browser.cookies


def test_a_removed_colleague_cannot_sign_back_in(workspace):
    from maverick_dashboard import rbac

    link = _invite(workspace)
    assert _accept(_remote(), link).status_code == 303
    rbac.remove_user("user:" + COLLEAGUE)
    refused = _sign_in(_remote())
    assert refused.status_code == 403
    assert "new invite" in refused.text


def test_workspace_invitation_keeps_its_workspace_role_for_sign_in(workspace):
    from maverick_dashboard import invites, rbac

    _inv, token = invites.create_invite("client-admin@example.test", "admin",
                                        created_by="user:" + OWNER, tenant="client-a")
    assert _accept(_remote(), f"/auth/invite/{token}").status_code == 303
    assert rbac.get_stored_role("user:client-admin@example.test") is None
    assert rbac.get_tenant_role("client-a", "user:client-admin@example.test") == "admin"
    assert _sign_in(_remote(), email="client-admin@example.test").status_code == 303


def test_the_owner_still_signs_in_only_on_this_computer(workspace):
    assert _sign_in(_remote(), email=OWNER, password=OWNER_PASS).status_code == 401
    local = workspace
    local.cookies.clear()
    assert _sign_in(local, email=OWNER, password=OWNER_PASS).status_code == 303


def test_sign_in_post_needs_this_sites_origin(workspace):
    from maverick_dashboard.app import app

    link = _invite(workspace)
    assert _accept(_remote(), link).status_code == 303
    for headers in ({}, {"Origin": "https://attacker.example"}):
        hostile = TestClient(app, base_url=REMOTE, client=("192.0.2.41", 50003),
                             headers=headers, follow_redirects=False)
        response = _sign_in(hostile)
        assert response.status_code in {400, 403}, headers
        assert "mvk_session" not in hostile.cookies


def test_sign_in_page_offers_a_new_invite_to_people_without_a_passphrase(workspace):
    page = re.sub(r"\s+", " ", _remote().get("/auth/local-login").text)
    assert "Ask your administrator for a new invite" in page
    assert "/static/lightwork.css" in page


def test_a_corrupt_passphrase_store_fails_closed(workspace, tmp_path):
    link = _invite(workspace)
    assert _accept(_remote(), link).status_code == 303
    store = tmp_path / "dashboard-passphrases.json"
    store.write_text("not json", encoding="utf-8")
    response = _sign_in(_remote())
    assert response.status_code == 503
    # Nothing is reset or overwritten: the file is left for an administrator.
    assert store.read_text(encoding="utf-8") == "not json"


def test_with_sso_the_invitation_signs_in_with_the_company_account_and_asks_no_passphrase(
        workspace, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from maverick_dashboard import invite_routes, invites, rbac

    link = _invite(workspace, email="sso.person@example.test")
    monkeypatch.setattr("maverick.oidc.login_enabled", lambda: True)
    assert not invites.local_login_mode()
    page = _remote().get(link)
    assert page.status_code == 200
    assert "company account" in page.text and 'name="password"' not in page.text
    assert "no Lightwork passphrase" in page.text
    signed_in = SimpleNamespace(sub="sso.person@example.test", principal="user:sso.person@example.test")
    monkeypatch.setattr(invite_routes, "_session_principal", lambda request: signed_in)
    accepted = _remote().post(link)
    assert accepted.status_code == 303, accepted.text
    assert rbac.get_stored_role("user:sso.person@example.test") == "operator"
    assert not (tmp_path / "dashboard-passphrases.json").exists()
