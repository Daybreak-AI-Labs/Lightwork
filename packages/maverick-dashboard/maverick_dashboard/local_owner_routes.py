"""Loopback-only owner bootstrap and passphrase login; no external identity calls."""
from __future__ import annotations

from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from . import local_owner

router = APIRouter()


def _form(request: Request, *, setup: bool, message="", status=200):
    title = "Set up the owner of this installation" if setup else "Sign in as the local owner"
    explanation = (
        "The owner manages this installation: they invite colleagues, set roles and change settings. "
        "Saving turns off anonymous administrator access and turns on named sign-in. "
        "Your email is only a label for this account; Lightwork does not verify it or send a message. "
        "Keep the passphrase somewhere safe: there is no reset by email."
        if setup else "Use the owner email and passphrase saved on this computer. Invited colleagues "
        "use their invitation links; this form does not grant new roles."
    )
    from .app import templates

    response = templates.TemplateResponse(
        request, "local_owner.html",
        {"title": title, "explanation": explanation, "message": message, "setup": setup},
        status_code=status)
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
        return _form(request, setup=True, message="Local first-owner setup is unavailable under this installation's current identity policy.", status=409)
    return _form(request, setup=True)


@router.get("/auth/local-login", response_class=HTMLResponse)
def login_form(request: Request):
    local_owner._local(request)
    local_owner._policy()
    if not local_owner.claimed():
        return RedirectResponse("/auth/local-owner", status_code=303)
    return _form(request, setup=False)


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
        data = parse_qs(bytes(body).decode("utf-8"), max_num_fields=5, keep_blank_values=True)
        if set(data) - {"email", "password", "password2", "confirm"} or any(len(v) != 1 for v in data.values()):
            raise HTTPException(422, "Submit one value for each field.")
        fields = {k: v[0] for k, v in data.items()}
        # A typo in a passphrase nobody can reset locks the owner out: the
        # setup form asks for it twice and refuses a mismatch.
        if setup and "password2" in fields and fields["password2"] != fields.get("password", ""):
            raise HTTPException(422, "The passphrases do not match. Type the same passphrase in both boxes.")
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
        return _form(request, setup=setup, message=str(exc.detail), status=exc.status_code)
    except (UnicodeError, ValueError):
        return _form(request, setup=setup, message="Check the submitted form values.", status=422)
    except Exception:
        return _form(request, setup=setup, message="Owner setup could not finish. If ownership was saved, sign in with the same email and passphrase to retry safely.", status=503)


@router.post("/auth/local-owner", response_class=HTMLResponse)
async def owner_submit(request: Request):
    return await _submit(request, setup=True)


@router.post("/auth/local-login", response_class=HTMLResponse)
async def login_submit(request: Request):
    return await _submit(request, setup=False)
