"""Auth routes: first-run setup, login, TOTP enrolment + verification, logout."""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from . import audit, auth, security, store
from .render import render
from .util import client_ip, is_https

router = APIRouter()


@router.get("/setup")
def setup_form(request: Request):
    conn = request.app.state.conn
    if store.count_staff(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    return render(request, "setup.html", {})


@router.post("/setup")
def setup_submit(request: Request, email: str = Form(...), name: str = Form(""),
                 password: str = Form(...)):
    conn = request.app.state.conn
    if store.count_staff(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    if len(password) < 10:
        return render(request, "setup.html",
                      {"error": "Password must be at least 10 characters."})
    sid = store.create_staff(conn, email=email, name=name, role="owner",
                             pw_hash=security.hash_password(password))
    audit.record(conn, actor=email, action="staff.create", target=email,
                 detail={"role": "owner", "bootstrap": True})
    resp = RedirectResponse("/enroll-totp", status_code=303)
    auth.set_session(resp, staff_id=sid, stage="enroll", https=is_https(request))
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
    row = store.get_staff_by_email(conn, email)
    if row is None or row["disabled"] or not security.verify_password(
            password, row["pw_hash"]):
        return render(request, "login.html", {"error": "Invalid credentials."})
    enrolled = bool(row["totp_enrolled"])
    audit.record(conn, actor=row["email"], action="staff.login.password",
                 target=row["email"], detail={"ip": client_ip(request)})
    resp = RedirectResponse("/totp" if enrolled else "/enroll-totp", status_code=303)
    auth.set_session(resp, staff_id=row["id"], stage="totp" if enrolled else "enroll",
                     https=is_https(request))
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
    row = store.get_staff_row(conn, st.id)
    if not security.totp_verify(row["totp_secret"], code):
        return render(request, "totp.html", {"error": "Wrong or expired code."})
    audit.record(conn, actor=st.email, action="staff.login.totp", target=st.email,
                 detail={"ip": client_ip(request)})
    resp = RedirectResponse("/", status_code=303)
    auth.set_session(resp, staff_id=st.id, stage="full", role=st.role,
                     https=is_https(request))
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
    row = store.get_staff_row(conn, st.id)
    if not security.totp_verify(row["totp_secret"], code):
        return render(request, "enroll_totp.html",
                      {"secret": row["totp_secret"],
                       "uri": security.totp_uri(row["totp_secret"], st.email),
                       "error": "Wrong code — check your authenticator and retry."})
    store.set_totp(conn, st.id, row["totp_secret"], enrolled=True)
    audit.record(conn, actor=st.email, action="staff.totp.enroll", target=st.email)
    resp = RedirectResponse("/", status_code=303)
    auth.set_session(resp, staff_id=st.id, stage="full", role=st.role,
                     https=is_https(request))
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session(resp)
    return resp
