"""Auth routes: first-run setup, login, TOTP enrolment + verification, logout.

Hardening baked in: atomic first-owner bootstrap, timing-equalized login (no
email enumeration), per-identifier rate limiting on password + TOTP, single-use
TOTP (RFC 6238 §5.2), fail-closed Secure cookies, and epoch-bumped logout that
revokes live sessions server-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from . import audit, auth, ratelimit, security, store
from .models import Staff
from .render import render
from .util import client_ip, hash_token, secure_cookie

router = APIRouter()

_LOCKED = "Too many attempts. Wait 5 minutes, then try again."
_NO_MATCH = "That email and password don't match. Check them and try again."
_LINK_GONE = "This link has expired or was already used. Ask an owner for a new one."
MIN_PASSWORD = 10
_BAD_CODE = ("That code didn't work. Codes change every 30 seconds, so enter the newest "
             "one. If it still fails, check the time on your phone is set automatically.")

# A fixed hash so login spends PBKDF2 time even when the email is unknown/disabled
# — equalizes response timing so an attacker can't enumerate staff by latency.
_DUMMY_PW_HASH = security.hash_password("timing-equalizer")


@router.get("/setup")
def setup_form(request: Request):
    if store.count_staff(request.app.state.conn) > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", {})


@router.post("/setup")
def setup_submit(request: Request, email: str = Form(...), name: str = Form(""),
                 password: str = Form(...)):
    conn = request.app.state.conn
    if store.count_staff(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    if len(password) < MIN_PASSWORD:
        return render(request, "setup.html",
                      {"error": "Password must be at least 10 characters."})
    sid = store.create_first_owner(conn, email=email, name=name,
                                   pw_hash=security.hash_password(password))
    if sid is None:                        # lost the race — an owner already exists
        return RedirectResponse("/login", status_code=303)
    audit.record(conn, actor=email, action="staff.create", target=email,
                 detail={"role": "owner", "bootstrap": True})
    resp = RedirectResponse("/enroll-totp", status_code=303)
    auth.set_session(resp, staff_id=sid, stage="enroll", secure=secure_cookie(request))
    return resp


@router.get("/login")
def login_form(request: Request):
    conn = request.app.state.conn
    if store.count_staff(conn) == 0:
        return RedirectResponse("/setup", status_code=303)
    if auth.load_staff(request) is not None:
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html", {})


@router.post("/login")
def login_submit(request: Request, email: str = Form(...), password: str = Form(...)):
    conn = request.app.state.conn
    key = "login:" + email.strip().lower()
    if ratelimit.locked(key):
        return render(request, "login.html",
                      {"error": _LOCKED})
    row = store.get_staff_by_email(conn, email)
    # No password yet = a set-up link was never used: refuse exactly like an
    # unknown account (same message, same PBKDF2 time).
    if row is None or row["disabled"] or not row["pw_hash"]:
        security.verify_password(password, _DUMMY_PW_HASH)     # equalize timing
        ratelimit.record_failure(key)
        return render(request, "login.html", {"error": _NO_MATCH})
    if not security.verify_password(password, row["pw_hash"]):
        ratelimit.record_failure(key)
        return render(request, "login.html", {"error": _NO_MATCH})
    ratelimit.record_success(key)
    enrolled = bool(row["totp_enrolled"])
    audit.record(conn, actor=row["email"], action="staff.login.password",
                 target=row["email"], detail={"ip": client_ip(request)})
    resp = RedirectResponse("/totp" if enrolled else "/enroll-totp", status_code=303)
    auth.set_session(resp, staff_id=row["id"], stage="totp" if enrolled else "enroll",
                     secure=secure_cookie(request))
    return resp


@router.get("/totp")
def totp_form(request: Request):
    if auth.load_staff(request, stage="totp") is None:
        return RedirectResponse("/login", status_code=303)
    return render(request, "totp.html", {})


@router.post("/totp")
def totp_submit(request: Request, code: str = Form(...)):
    conn = request.app.state.conn
    st = auth.load_staff(request, stage="totp")
    if st is None:
        return RedirectResponse("/login", status_code=303)
    key = "totp:" + str(st.id)
    if ratelimit.locked(key):
        return render(request, "totp.html",
                      {"error": _LOCKED})
    row = store.get_staff_row(conn, st.id)
    step = security.totp_match_step(row["totp_secret"], code,
                                    after_step=row["totp_last_step"])
    if step is None:
        ratelimit.record_failure(key)
        return render(request, "totp.html", {"error": _BAD_CODE})
    store.set_totp_last_step(conn, st.id, step)                 # single-use
    ratelimit.record_success(key)
    audit.record(conn, actor=st.email, action="staff.login.totp", target=st.email,
                 detail={"ip": client_ip(request)})
    resp = RedirectResponse("/", status_code=303)
    auth.set_session(resp, staff_id=st.id, stage="full", role=st.role,
                     epoch=st.session_epoch, secure=secure_cookie(request))
    return resp


@router.get("/enroll-totp")
def enroll_form(request: Request):
    conn = request.app.state.conn
    st = auth.load_staff(request, stage="enroll")
    if st is None:
        return RedirectResponse("/login", status_code=303)
    row = store.get_staff_row(conn, st.id)
    secret = row["totp_secret"] or security.new_totp_secret()
    if not row["totp_secret"]:
        store.set_totp(conn, st.id, secret, enrolled=False)
    return render(request, "enroll_totp.html",
                  {"secret": secret, "uri": security.totp_uri(secret, st.email)})


@router.post("/enroll-totp")
def enroll_submit(request: Request, code: str = Form(...)):
    conn = request.app.state.conn
    st = auth.load_staff(request, stage="enroll")
    if st is None:
        return RedirectResponse("/login", status_code=303)
    key = "totp:" + str(st.id)
    if ratelimit.locked(key):
        return render(request, "enroll_totp.html",
                      {"error": _LOCKED})
    row = store.get_staff_row(conn, st.id)
    step = security.totp_match_step(row["totp_secret"], code,
                                    after_step=row["totp_last_step"])
    if step is None:
        ratelimit.record_failure(key)
        return render(request, "enroll_totp.html",
                      {"secret": row["totp_secret"],
                       "uri": security.totp_uri(row["totp_secret"], st.email),
                       "error": _BAD_CODE})
    store.set_totp(conn, st.id, row["totp_secret"], enrolled=True)
    store.set_totp_last_step(conn, st.id, step)                 # single-use
    ratelimit.record_success(key)
    audit.record(conn, actor=st.email, action="staff.totp.enroll", target=st.email)
    resp = RedirectResponse("/", status_code=303)
    auth.set_session(resp, staff_id=st.id, stage="full", role=st.role,
                     epoch=st.session_epoch, secure=secure_cookie(request))
    return resp


@router.post("/logout")
def logout(request: Request):
    st = auth.load_staff(request)
    if st is not None:
        store.bump_session_epoch(request.app.state.conn, st.id)   # revoke live sessions
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session(resp)
    return resp


# ---- set-up links (new team members choose their own password) ---------------

def _link_gone(request: Request):
    # One response for unknown, used and expired links, so a link can't be probed.
    resp = render(request, "link_gone.html", {"message": _LINK_GONE})
    resp.status_code = 404
    return resp


def _welcome_key(request: Request) -> str:
    return "welcome:" + client_ip(request)


@router.get("/welcome/{token}")
def welcome_form(request: Request, token: str):
    key = _welcome_key(request)
    if ratelimit.locked(key):
        return render(request, "link_gone.html", {"message": _LOCKED})
    member = store.invite_staff(request.app.state.conn, hash_token(token))
    if member is None:
        ratelimit.record_failure(key)
        return _link_gone(request)
    return render(request, "welcome.html", {"member": member, "token": token})


@router.post("/welcome/{token}")
def welcome_submit(request: Request, token: str, password: str = Form(...),
                   confirm: str = Form(...)):
    conn = request.app.state.conn
    key = _welcome_key(request)
    if ratelimit.locked(key):
        return render(request, "link_gone.html", {"message": _LOCKED})
    token_hash = hash_token(token)
    member = store.invite_staff(conn, token_hash)
    if member is None:
        ratelimit.record_failure(key)
        return _link_gone(request)
    problem = ""
    if len(password) < MIN_PASSWORD:
        problem = f"Choose a password of at least {MIN_PASSWORD} characters."
    elif password != confirm:
        problem = "The two passwords don't match. Type the same password twice."
    if problem:
        resp = render(request, "welcome.html", {"member": member, "token": token, "error": problem})
        resp.status_code = 400
        return resp
    member = store.consume_invite(conn, token_hash, security.hash_password(password))
    if member is None:                       # used or expired in the meantime
        ratelimit.record_failure(key)
        return _link_gone(request)
    ratelimit.record_success(key)
    audit.record(conn, actor=member.email, action="staff.invite.accept", target=member.email,
                 detail={"ip": client_ip(request)})
    # Straight into two-step set-up, exactly like the first owner after /setup.
    resp = RedirectResponse("/enroll-totp", status_code=303)
    auth.set_session(resp, staff_id=member.id, stage="enroll", secure=secure_cookie(request))
    return resp


# ---- change my password -------------------------------------------------------

@router.get("/account/password")
def password_form(request: Request, staff: Staff = Depends(auth.require_staff)):
    return render(request, "change_password.html", {"staff": staff, "section": "account"})


@router.post("/account/password")
def password_submit(request: Request, current: str = Form(...), password: str = Form(...),
                    confirm: str = Form(...), staff: Staff = Depends(auth.require_staff)):
    conn = request.app.state.conn
    key = "password:" + str(staff.id)

    def again(error: str, code: int = 400):
        resp = render(request, "change_password.html",
                      {"staff": staff, "section": "account", "error": error})
        resp.status_code = code
        return resp

    if ratelimit.locked(key):
        return again(_LOCKED, 429)
    row = store.get_staff_row(conn, staff.id)
    if not security.verify_password(current, row["pw_hash"]):
        ratelimit.record_failure(key)
        return again("Your current password isn't right. Check it and try again.")
    if len(password) < MIN_PASSWORD:
        return again(f"Choose a new password of at least {MIN_PASSWORD} characters.")
    if password != confirm:
        return again("The two new passwords don't match. Type the same password twice.")
    if password == current:
        return again("Choose a new password that is different from your current one.")
    ratelimit.record_success(key)
    epoch = store.set_password(conn, staff.id, security.hash_password(password))
    audit.record(conn, actor=staff.email, action="staff.password.change", target=staff.email,
                 detail={"ip": client_ip(request)})
    # Every other session ends (epoch bumped); this one continues on the new epoch.
    resp = render(request, "change_password.html",
                  {"staff": staff, "section": "account",
                   "notice": "Password changed. You've been signed out everywhere else."})
    auth.set_session(resp, staff_id=staff.id, stage="full", role=staff.role, epoch=epoch,
                     secure=secure_cookie(request))
    return resp
