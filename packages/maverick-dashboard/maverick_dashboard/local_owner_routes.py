"""Loopback-only owner bootstrap and passphrase login; no external identity calls."""
from __future__ import annotations

import html
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import local_owner
from .invite_routes import _page

router = APIRouter()


def _form(*, setup: bool, message="", status=200):
    title = "Set up your local owner" if setup else "Sign in as the local owner"
    explanation = (
        "This first owner manages this installation. Saving turns off anonymous administrator access "
        "and enables named invite sign-in. Your email is a local account label; Lightwork does not "
        "verify ownership of that email address or send a message. Keep the passphrase securely. "
        "You can invite colleagues and assign workspace access from Users after signing in."
        if setup else "Use the owner email and passphrase saved on this computer. Invited colleagues "
        "use their invitation links; this form does not grant new roles."
    )
    body = f'<p>{explanation}</p>'
    if message:
        body += f'<p role="alert">{html.escape(message)}</p>'
    body += f'''<form method="post" data-draft-recovery="off">
<p><label>Email address<br><input type="email" name="email" maxlength="320" required autocomplete="username"></label></p>
<p><label>Passphrase (at least 14 characters)<br><input type="password" name="password" minlength="14" maxlength="1024" required autocomplete="{'new-password' if setup else 'current-password'}"></label></p>'''
    if setup:
        body += '<p><label><input type="checkbox" name="confirm" value="yes" required> I manage this computer and reviewed the change to named sign-in.</label></p>'
    body += f'<button type="submit">{"Save owner and sign in" if setup else "Sign in"}</button></form>'
    body += '<p><a href="/auth/local-login">Continue an existing owner setup or sign in</a></p>' if setup else '<p>For company sign-in, use your organization’s configured login method.</p>'
    response = _page(title, body, status)
    response.headers["Cache-Control"] = "no-store"
    # Native browser form submissions need their same-origin Origin header.
    # no-referrer makes Chromium send Origin: null and defeats the CSRF check.
    # External sites still receive no referrer under this policy.
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@router.get("/auth/local-owner", response_class=HTMLResponse)
def owner_form(request: Request):
    local_owner._local(request)
    if local_owner.claimed():
        return RedirectResponse("/auth/local-login", status_code=303)
    if not local_owner.available(request):
        return _form(setup=True, message="Local first-owner setup is unavailable under this installation's current identity policy.", status=409)
    return _form(setup=True)


@router.get("/auth/local-login", response_class=HTMLResponse)
def login_form(request: Request):
    local_owner._local(request)
    local_owner._policy()
    if not local_owner.claimed():
        return RedirectResponse("/auth/local-owner", status_code=303)
    return _form(setup=False)


async def _submit(request, *, setup):
    try:
        local_owner._local(request, mutate=True)
        if request.headers.get("content-type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
            raise HTTPException(415, "Submit the form from this page.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 16384:
                raise HTTPException(413, "The owner form exceeds its supported size.")
        data = parse_qs(bytes(body).decode("utf-8"), max_num_fields=4, keep_blank_values=True)
        if set(data) - {"email", "password", "confirm"} or any(len(v) != 1 for v in data.values()):
            raise HTTPException(422, "Submit one value for each field.")
        fields = {k: v[0] for k, v in data.items()}
        # Slow passphrase verification runs off the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        if setup:
            token = await run_in_threadpool(local_owner.enroll, request, email=fields.get("email", ""),
                                            password=fields.get("password", ""), confirm=fields.get("confirm") == "yes")
        else:
            token = await run_in_threadpool(local_owner.login, request, email=fields.get("email", ""), password=fields.get("password", ""))
        from .invites import session_ttl_seconds
        from .oidc_login import _cookie_secure, _set_cookie
        response = RedirectResponse("/start", status_code=303)
        _set_cookie(response, "mvk_session", token, max_age=session_ttl_seconds(), secure=_cookie_secure(request))
        response.headers["Cache-Control"] = "no-store"
        return response
    except HTTPException as exc:
        return _form(setup=setup, message=str(exc.detail), status=exc.status_code)
    except (UnicodeError, ValueError):
        return _form(setup=setup, message="Check the submitted form values.", status=422)
    except Exception:
        return _form(setup=setup, message="Owner setup could not finish. If ownership was saved, sign in with the same email and passphrase to retry safely.", status=503)


@router.post("/auth/local-owner", response_class=HTMLResponse)
async def owner_submit(request: Request):
    return await _submit(request, setup=True)


@router.post("/auth/local-login", response_class=HTMLResponse)
async def login_submit(request: Request):
    return await _submit(request, setup=False)
