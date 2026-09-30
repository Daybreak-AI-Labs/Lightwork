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


# ---- every sign-in stage ends when the account is reset -------------------------

def _enroll_client(app, token):
    """A browser that used ``token`` and now holds the two-step set-up session."""
    c = TestClient(app)
    r = c.post(f"/welcome/{token}", data={"password": NEW_PW, "confirm": NEW_PW},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/enroll-totp"
    return c


def _member_id(app, email="jane@daybreak.co"):
    return store.get_staff_by_email(app.state.conn, email)["id"]


def test_new_link_ends_an_unfinished_set_up_session(admin, app):
    _, token, _ = _add(admin, role="admin")
    leaked = _enroll_client(app, token)
    sid = _member_id(app)
    assert admin.post(f"/team/{sid}/link").status_code == 200
    page = leaked.get("/enroll-totp", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    assert store.get_staff_row(app.state.conn, sid)["totp_secret"] == ""
    # Even with a guessed secret the old session can't finish two-step set-up.
    store.set_totp(app.state.conn, sid, security.new_totp_secret(), enrolled=False)
    code = security.totp_now(store.get_staff_row(app.state.conn, sid)["totp_secret"])
    r = leaked.post("/enroll-totp", data={"code": code}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert not store.get_staff_row(app.state.conn, sid)["totp_enrolled"]
    assert leaked.get("/customers", follow_redirects=False).status_code == 303


def test_new_link_ends_a_set_up_session_started_from_sign_in(admin, app):
    _, token, _ = _add(admin)
    _welcome(app, token)
    leaked = TestClient(app)
    r = leaked.post("/login", data={"email": "jane@daybreak.co", "password": NEW_PW},
                    follow_redirects=False)
    assert r.headers["location"] == "/enroll-totp"
    admin.post(f"/team/{_member_id(app)}/link")
    assert leaked.get("/enroll-totp", follow_redirects=False).headers["location"] == "/login"


def test_turning_access_off_and_on_ends_a_set_up_session(admin, app):
    _, token, _ = _add(admin)
    leaked = _enroll_client(app, token)
    sid = _member_id(app)
    store.set_staff_disabled(app.state.conn, sid, True)
    store.set_staff_disabled(app.state.conn, sid, False)
    assert leaked.get("/enroll-totp", follow_redirects=False).headers["location"] == "/login"


def test_password_change_ends_a_half_finished_sign_in(app, make_staff):
    sid = make_staff(email="me@daybreak.co", role="support")
    row = store.get_staff_row(app.state.conn, sid)
    store.set_totp(app.state.conn, sid, row["totp_secret"], enrolled=True)
    c = TestClient(app)
    r = c.post("/login", data={"email": "me@daybreak.co", "password": "password-1234"},  # pragma: allowlist secret
               follow_redirects=False)
    assert r.headers["location"] == "/totp"
    assert c.get("/totp", follow_redirects=False).status_code == 200
    store.set_password(app.state.conn, sid, security.hash_password(NEW_PW))
    assert c.get("/totp", follow_redirects=False).headers["location"] == "/login"
    code = security.totp_now(row["totp_secret"])
    assert c.post("/totp", data={"code": code}, follow_redirects=False).headers["location"] == "/login"


def test_a_link_cannot_reset_an_account_that_finished_setting_up(app, make_staff):
    sid = make_staff(email="done@daybreak.co", role="viewer")
    row = store.get_staff_row(app.state.conn, sid)
    store.set_totp(app.state.conn, sid, row["totp_secret"], enrolled=True)
    store.create_invite(app.state.conn, staff_id=sid, token_hash=hash_token("left-open"),
                        expires_at=time.time() + 3600, created_by="owner@daybreak.co")
    assert store.consume_invite(app.state.conn, hash_token("left-open"),
                                security.hash_password(NEW_PW)) is None
    assert _welcome(app, "left-open").status_code == 404
    after = store.get_staff_row(app.state.conn, sid)
    assert after["pw_hash"] == row["pw_hash"]


def test_finishing_two_step_set_up_retires_open_links(admin, app):
    _, token, _ = _add(admin)
    jane = _enroll_client(app, token)
    sid = _member_id(app)
    store.create_invite(app.state.conn, staff_id=sid, token_hash=hash_token("still-open"),
                        expires_at=time.time() + 3600, created_by="owner@daybreak.co")
    jane.get("/enroll-totp")
    code = security.totp_now(store.get_staff_row(app.state.conn, sid)["totp_secret"])
    r = jane.post("/enroll-totp", data={"code": code}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert jane.get("/customers").status_code == 200
    open_links = app.state.conn.execute(
        "SELECT COUNT(*) FROM staff_invites WHERE staff_id = ? AND used_at IS NULL",
        (sid,)).fetchone()[0]
    assert open_links == 0


# ---- one-time secrets are never cached -------------------------------------------

def _assert_no_store(response):
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("pragma") == "no-cache"


def test_set_up_link_pages_are_not_cached(admin, app):
    _, _, first = _add(admin)
    _assert_no_store(first)
    _assert_no_store(admin.post(f"/team/{_member_id(app)}/link"))


# ---- the set-up page limit doesn't lock out a shared network ----------------------

def test_stale_link_clicks_do_not_lock_out_a_new_link_on_the_same_network(admin, app):
    _, token, _ = _add(admin)
    office = TestClient(app)
    for _ in range(ratelimit.MAX_FAILS + 1):
        assert office.get("/welcome/an-old-used-link").status_code in (200, 404)
    assert "Too many attempts" in office.get("/welcome/an-old-used-link").text
    fresh = office.get(f"/welcome/{token}")
    assert fresh.status_code == 200 and "Choose a password" in fresh.text
    assert _welcome(app, token).status_code == 303


def test_guessing_many_links_from_one_address_is_still_limited(admin, app):
    from vendor_console import routes_auth
    _, token, _ = _add(admin)
    guesser = TestClient(app)
    for i in range(routes_auth.WELCOME_IP_MAX_FAILS):
        assert guesser.get(f"/welcome/guess-{i}").status_code == 404
    assert "Too many attempts" in guesser.get("/welcome/guess-new").text
    assert "Too many attempts" in guesser.get(f"/welcome/{token}").text
    r = guesser.post(f"/welcome/{token}", data={"password": NEW_PW, "confirm": NEW_PW},
                     follow_redirects=False)
    assert "Too many attempts" in r.text
    assert store.get_staff_by_email(app.state.conn, "jane@daybreak.co")["pw_hash"] == ""
