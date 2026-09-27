"""Set-up links for new team members and self-service password changes.

The owner never learns a team member's password: adding a person creates a
single-use, expiring link (stored only as a hash) where they choose their own
password, and every signed-in person can change their password themselves."""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient
from vendor_console import audit, ratelimit, security, store
from vendor_console.util import hash_token

NEW_PW = "chosen-by-them-1"  # pragma: allowlist secret
GONE = "This link has expired or was already used. Ask an owner for a new one."


def _add(admin, email="jane@daybreak.co", role="support", name="Jane"):
    r = admin.post("/team", data={"email": email, "name": name, "role": role})
    assert r.status_code == 200, r.text
    link = re.search(r"http://testserver/welcome/[A-Za-z0-9_\-]+", r.text).group(0)
    return link, link.rsplit("/", 1)[1], r


def _welcome(app, token, password=NEW_PW, confirm=None):
    return TestClient(app).post(f"/welcome/{token}", follow_redirects=False,
                                data={"password": password, "confirm": confirm or password})


def _login(app, email, password):
    return TestClient(app).post("/login", data={"email": email, "password": password},
                                follow_redirects=False)


def test_adding_a_person_takes_no_password_and_shows_the_link_once(admin, app):
    link, token, r = _add(admin)
    assert "It works once and expires in 3 days" in r.text
    assert 'name="password"' not in admin.get("/team").text
    row = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")
    assert row["pw_hash"] == ""
    stored = app.state.conn.execute("SELECT token_hash FROM staff_invites").fetchall()
    assert [s["token_hash"] for s in stored] == [hash_token(token)]      # hash only
    assert token not in admin.get("/team").text                          # never shown again
    assert len(token) >= 43


def test_login_is_refused_until_the_link_is_used(admin, app):
    _add(admin)
    for password in ("", "anything-at-all-1"):  # pragma: allowlist secret
        r = _login(app, "jane@daybreak.co", password or "x")
        assert r.status_code == 200 and "don&#39;t match" in r.text
    unknown = _login(app, "nobody@daybreak.co", "anything-at-all-1")  # pragma: allowlist secret
    assert unknown.text.count("don&#39;t match") == 1


def test_link_sets_own_password_then_requires_two_step(admin, app):
    _, token, _ = _add(admin)
    assert "Choose a password" in TestClient(app).get(f"/welcome/{token}").text
    r = _welcome(app, token)
    assert r.status_code == 303 and r.headers["location"] == "/enroll-totp"
    row = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")
    assert security.verify_password(NEW_PW, row["pw_hash"])
    assert not row["totp_enrolled"]
    login = _login(app, "jane@daybreak.co", NEW_PW)
    assert login.headers["location"] == "/enroll-totp"          # still must enrol two-step


def test_link_works_once(admin, app):
    _, token, _ = _add(admin)
    assert _welcome(app, token).status_code == 303
    again = _welcome(app, token, password="attacker-pass-1")  # pragma: allowlist secret
    assert again.status_code == 404 and GONE.replace("'", "&#39;") in again.text
    row = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")
    assert security.verify_password(NEW_PW, row["pw_hash"])


def test_unknown_used_and_expired_links_look_identical(admin, app):
    _, used, _ = _add(admin, email="a@daybreak.co")
    _welcome(app, used)
    _, expired, _ = _add(admin, email="b@daybreak.co")
    app.state.conn.execute("UPDATE staff_invites SET expires_at = ? WHERE token_hash = ?",
                           (time.time() - 1, hash_token(expired)))
    app.state.conn.commit()
    bodies = set()
    for token in (used, expired, "not-a-real-token"):
        ratelimit.reset_all()
        for response in (TestClient(app).get(f"/welcome/{token}"), _welcome(app, token)):
            assert response.status_code == 404
            bodies.add(response.text)
    assert len(bodies) == 1
    assert store.get_staff_by_email(app.state.conn, "b@daybreak.co")["pw_hash"] == ""


def test_link_guessing_is_rate_limited(app):
    client = TestClient(app)
    for _ in range(ratelimit.MAX_FAILS):
        assert client.get("/welcome/guess").status_code == 404
    assert "Too many attempts" in client.get("/welcome/guess").text


def test_new_link_retires_older_links_and_is_confirmed(admin, app):
    _, first, _ = _add(admin)
    member = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["id"]
    assert "Any earlier link" in admin.get(f"/team/{member}/link").text     # confirmation page
    r = admin.post(f"/team/{member}/link")
    second = re.search(r"/welcome/([A-Za-z0-9_\-]+)", r.text).group(1)
    assert second != first
    assert _welcome(app, first).status_code == 404
    assert _welcome(app, second).status_code == 303


def test_new_link_clears_a_password_chosen_with_an_older_link(admin, app):
    _, first, _ = _add(admin)
    _welcome(app, first)
    member = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["id"]
    admin.post(f"/team/{member}/link")
    assert store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["pw_hash"] == ""
    assert "don&#39;t match" in _login(app, "jane@daybreak.co", NEW_PW).text


def test_no_new_link_for_finished_accounts_or_yourself(admin, app, make_staff):
    done = make_staff(email="done@daybreak.co", role="viewer")
    store.set_totp(app.state.conn, done, security.new_totp_secret(), enrolled=True)
    assert admin.post(f"/team/{done}/link").status_code == 400
    me = store.get_staff_by_email(app.state.conn, "owner@daybreak.co")["id"]
    assert admin.post(f"/team/{me}/link").status_code == 400


def test_disabled_person_cannot_use_their_link(admin, app):
    _, token, _ = _add(admin)
    member = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["id"]
    store.set_staff_disabled(app.state.conn, member, True)
    assert _welcome(app, token).status_code == 404


def test_link_creation_and_use_are_audited_without_the_token(admin, app):
    _, token, _ = _add(admin)
    member = store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["id"]
    r = admin.post(f"/team/{member}/link")
    second = re.search(r"/welcome/([A-Za-z0-9_\-]+)", r.text).group(1)
    _welcome(app, second)
    events = audit.recent(app.state.conn)
    actions = [e.action for e in events]
    assert actions.count("staff.invite") == 2 and "staff.invite.accept" in actions
    rows = app.state.conn.execute("SELECT action, target, detail_json FROM audit").fetchall()
    dump = " ".join(" ".join(map(str, row)) for row in rows)
    assert token not in dump and second not in dump
    assert hash_token(token) not in dump and hash_token(second) not in dump
    assert audit.verify_chain(app.state.conn)[0]


def test_welcome_post_keeps_same_origin_check(admin, app):
    _, token, _ = _add(admin)
    r = TestClient(app).post(f"/welcome/{token}", data={"password": NEW_PW, "confirm": NEW_PW},
                             headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["pw_hash"] == ""
    assert admin.post("/team", data={"email": "x@daybreak.co", "role": "viewer"},
                      headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize(("password", "confirm", "message"), [
    ("short", "short", "at least 10"),
    ("long-enough-1", "long-enough-2", "don&#39;t match"),  # pragma: allowlist secret
])
def test_weak_or_mismatched_password_keeps_the_link_usable(admin, app, password, confirm, message):
    _, token, _ = _add(admin)
    r = _welcome(app, token, password=password, confirm=confirm)
    assert r.status_code == 400 and message in r.text
    assert _welcome(app, token).status_code == 303


# ---- change my password ------------------------------------------------------

def _staff_client(app, make_staff, login_cookie, email="me@daybreak.co", role="support"):
    sid = make_staff(email=email, role=role)
    c = TestClient(app)
    c.cookies.update(login_cookie(sid, role))
    return c, sid


def test_change_password_requires_the_current_one(app, make_staff, login_cookie):
    c, sid = _staff_client(app, make_staff, login_cookie)
    r = c.post("/account/password", data={"current": "wrong-password-1",  # pragma: allowlist secret
                                          "password": NEW_PW, "confirm": NEW_PW})
    assert r.status_code == 400 and "current password isn" in r.text
    assert security.verify_password("password-1234", store.get_staff_row(app.state.conn, sid)["pw_hash"])


def test_change_password_ends_other_sessions_and_keeps_this_one(app, make_staff, login_cookie):
    c, sid = _staff_client(app, make_staff, login_cookie)
    other = TestClient(app)
    other.cookies.update(login_cookie(sid, "support"))
    before = store.get_staff(app.state.conn, sid).session_epoch
    r = c.post("/account/password", data={"current": "password-1234",  # pragma: allowlist secret
                                          "password": NEW_PW, "confirm": NEW_PW})
    assert r.status_code == 200 and "Password changed" in r.text
    assert store.get_staff(app.state.conn, sid).session_epoch == before + 1
    assert security.verify_password(NEW_PW, store.get_staff_row(app.state.conn, sid)["pw_hash"])
    assert c.get("/customers").status_code == 200                       # this session continues
    assert other.get("/customers", follow_redirects=False).status_code == 303
    assert any(e.action == "staff.password.change" for e in audit.recent(app.state.conn))


def test_change_password_needs_sign_in_and_same_origin(app, make_staff, login_cookie):
    assert TestClient(app).get("/account/password", follow_redirects=False).status_code == 303
    c, _ = _staff_client(app, make_staff, login_cookie)
    r = c.post("/account/password", headers={"Origin": "https://evil.example"},
               data={"current": "password-1234", "password": NEW_PW,  # pragma: allowlist secret
                     "confirm": NEW_PW})
    assert r.status_code == 403
