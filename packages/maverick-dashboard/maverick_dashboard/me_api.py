"""The personal "My work" page (``/me``), its small API, the Slack HTTP
receivers and the admin controls for linking chat accounts.

``/me`` works for every signed-in person (view floor): their pending
approvals, questions an agent is waiting on, recent results, spending this
month against the limit, linked chat accounts and the daily summary
choices. It is the same data the daily summary sends
(``maverick.daily_digest``).

``/webhook/slack/interactions`` and ``/webhook/slack/commands`` are the
non-Socket-Mode receivers: each request must carry Slack's signature over
the raw body (``[channels.slack] signing_secret``), a fresh timestamp and a
signature not seen before (replay), and is then handled by
``maverick.slack_interactive`` exactly as the Socket Mode adapter does.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import auth

log = logging.getLogger(__name__)

router = APIRouter(tags=["me"])
api = APIRouter(prefix="/api/v1", tags=["me"])
_SLACK_REPLAY_WINDOW = 600
_LOCAL_PRINCIPAL = "local:dashboard"


def _app():
    from maverick_dashboard import app as app_module

    return app_module


def _principal(request: Request) -> str:
    """The signed-in person; the shared local identity when sign-in is off."""
    name = auth.caller_principal(request)
    if name:
        return name
    if auth.auth_genuinely_off() or auth.anonymous_local_access_allowed():
        return _LOCAL_PRINCIPAL
    raise HTTPException(status_code=401, detail="sign in to see your work")


def _world():
    from ._shared import _world as shared_world

    return shared_world()


def _my_work(request: Request, principal: str) -> dict:
    from maverick import channel_identities, daily_digest, user_preferences

    prefs = user_preferences.get_preferences(principal)
    can_decide = auth.has_permission(request, "operate")
    everything = auth.goal_owner_filter(request) is None
    digest = daily_digest.compose_digest(
        _world(), principal, can_decide=can_decide, sees_everything=everything,
        time_zone=prefs["time_zone"],
    )
    try:
        links = channel_identities.links_for(principal)
    except channel_identities.ChannelIdentityError:
        links = []
    return {"digest": digest, "preferences": prefs, "links": links,
            "can_decide": can_decide}


# ----------------------------------------------------------------------------
# The page and its JSON twin
# ----------------------------------------------------------------------------

@router.get("/me", response_class=HTMLResponse)
def me_page(request: Request, saved: str = "", code: str = "", channel: str = "") -> HTMLResponse:
    """My work: what is waiting for me, my spending, my chat accounts, my summary."""
    auth.require_permission(request, "view")
    from maverick import user_preferences
    from maverick.config import get_my_work
    from maverick.notifications import email_configured

    principal = _principal(request)
    model = _my_work(request, principal)
    try:
        digest_on = bool(get_my_work().get("digest_enable"))
    except Exception:  # noqa: BLE001 -- an unreadable config shows "off"
        digest_on = False
    saved_msg = {
        "preferences": "Your choices are saved.",
        "unlinked": "That chat account is no longer linked.",
        "sent": "Your summary was sent. Check your linked chat app or inbox.",
        "not_sent": "Your summary could not be sent. Link a chat account or add an email first.",
        "bad_preferences": "Those choices could not be saved. Check the values and try again.",
    }.get(saved, "")
    model.update({
        "principal": principal,
        "saved": saved_msg,
        "saved_failed": saved in {"not_sent", "bad_preferences"},
        "link_code": code if channel else "",
        "link_channel": channel,
        "time_zones": user_preferences.time_zone_choices(),
        "digest_scheduler_on": digest_on,
        "email_on": email_configured(),
        "hours": list(range(24)),
    })
    return _app().templates.TemplateResponse(request, "me.html", model)


@api.get("/me/work")
def me_work(request: Request) -> dict:
    """The My work page as data (the mobile companion reads this)."""
    auth.require_permission(request, "view")
    principal = _principal(request)
    model = _my_work(request, principal)
    return {"principal": principal, **model}


# ----------------------------------------------------------------------------
# Choices, chat accounts and "send it now"
# ----------------------------------------------------------------------------

@router.post("/me/preferences")
def me_preferences(
    request: Request,
    time_zone: str = Form("UTC"),
    digest_hour: int = Form(8),
    email: str = Form(""),
    digest_enabled: str = Form(""),
    send_when_empty: str = Form(""),
) -> RedirectResponse:
    auth.require_permission(request, "view")
    _app()._require_same_origin(request)
    from maverick import user_preferences

    principal = _principal(request)
    try:
        user_preferences.set_preferences(
            principal, time_zone=time_zone.strip(), digest_hour=int(digest_hour),
            email=email.strip(), digest_enabled=digest_enabled == "on",
            send_when_empty=send_when_empty == "on",
        )
    except (user_preferences.UserPreferenceError, ValueError):
        return RedirectResponse("/me?saved=bad_preferences", status_code=303)
    return RedirectResponse("/me?saved=preferences", status_code=303)


@router.post("/me/channels/link-code")
def me_link_code(request: Request, channel: str = Form(...)) -> RedirectResponse:
    """A one-time code the person sends from the chat app (``/lightwork link``)."""
    auth.require_permission(request, "view")
    _app()._require_same_origin(request)
    from maverick import channel_identities

    principal = _principal(request)
    try:
        issued = channel_identities.issue_link_code(principal, channel)
    except channel_identities.ChannelIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/me?channel={issued['channel']}&code={issued['code']}#chat-accounts", status_code=303)


@router.post("/me/channels/unlink")
def me_unlink(request: Request, channel: str = Form(...),
              external_id: str = Form(...)) -> RedirectResponse:
    auth.require_permission(request, "view")
    _app()._require_same_origin(request)
    from maverick import channel_identities

    principal = _principal(request)
    try:
        channel_identities.unlink(channel, external_id, actor=principal,
                                  expected_principal=principal)
    except channel_identities.ChannelIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/me?saved=unlinked#chat-accounts", status_code=303)


@router.post("/me/digest/send-now")
async def me_send_now(request: Request) -> RedirectResponse:
    """Send today's summary to me right away (a test of the linked channels)."""
    auth.require_permission(request, "view")
    _app()._require_same_origin(request)
    from maverick import channel_identities, daily_digest, user_preferences

    principal = _principal(request)
    prefs = user_preferences.get_preferences(principal)
    links = channel_identities.links_for(principal)
    if not links and not prefs.get("email"):
        return RedirectResponse("/me?saved=not_sent", status_code=303)
    can_decide = auth.has_permission(request, "operate")
    everything = auth.goal_owner_filter(request) is None
    digest = daily_digest.compose_digest(
        _world(), principal, can_decide=can_decide, sees_everything=everything,
        time_zone=prefs["time_zone"],
    )
    outcomes = await run_in_threadpool(
        daily_digest.send_digest, principal, digest, links=links, prefs=prefs)
    ok = any(o["ok"] for o in outcomes)
    return RedirectResponse(f"/me?saved={'sent' if ok else 'not_sent'}", status_code=303)


# ----------------------------------------------------------------------------
# Administrators: map chat accounts on the Users page; test the mail backend
# ----------------------------------------------------------------------------

@router.post("/users/channels/link")
def users_channel_link(request: Request, principal: str = Form(...),
                       channel: str = Form(...), external_id: str = Form(...)) -> RedirectResponse:
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick import channel_identities

    try:
        channel_identities.link(channel, external_id, principal.strip(),
                                actor=auth.caller_principal(request) or "local", method="admin")
    except channel_identities.ChannelIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=channel_linked#chat-accounts", status_code=303)


@router.post("/users/channels/unlink")
def users_channel_unlink(request: Request, channel: str = Form(...),
                         external_id: str = Form(...)) -> RedirectResponse:
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick import channel_identities

    try:
        channel_identities.unlink(channel, external_id,
                                  actor=auth.caller_principal(request) or "local")
    except channel_identities.ChannelIdentityError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse("/users?saved=channel_unlinked#chat-accounts", status_code=303)


@router.post("/channels/test-email")
async def channels_test_email(request: Request, to: str = Form(...)) -> RedirectResponse:
    """The "Test email" button: one message through ``[notifications.email]``."""
    auth.require_global_permission(request, "admin")
    _app()._require_same_origin(request)
    from maverick.notifications import send_email

    ok = await run_in_threadpool(
        send_email, to.strip(), "Lightwork test email",
        "This is a test message from Lightwork. If you can read it, email alerts and "
        "the daily summary can reach this address.",
    )
    return RedirectResponse(f"/channels?saved={'email_sent' if ok else 'email_failed'}",
                            status_code=303)


# ----------------------------------------------------------------------------
# Slack over HTTP (the non-Socket deployment)
# ----------------------------------------------------------------------------

def _slack_signing_secret() -> str:
    from maverick.config import load_config
    from maverick.notifications import _resolve_env_ref
    from maverick.secret_provider import get_secret_or_saved

    try:
        section = (load_config().get("channels") or {}).get("slack") or {}
    except Exception:  # noqa: BLE001 -- an unreadable config means "not set up"
        section = {}
    saved = section.get("signing_secret") if isinstance(section, dict) else None
    saved = _resolve_env_ref(saved) if isinstance(saved, str) else None
    return get_secret_or_saved("SLACK_SIGNING_SECRET", saved) or ""


async def _verified_slack_body(request: Request) -> bytes:
    """Refuse unless the request is signed by Slack, fresh, and not a replay."""
    from maverick.slack_interactive import verify_slack_signature

    secret = await run_in_threadpool(_slack_signing_secret)
    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Slack buttons over HTTP aren't set up yet. An administrator must save the "
                   "Slack signing secret on the Channels page.")
    body = await _app()._read_limited_webhook_body(request)
    timestamp = request.headers.get("X-Slack-Request-Timestamp") or ""
    signature = request.headers.get("X-Slack-Signature") or ""
    if not verify_slack_signature(secret, timestamp=timestamp, signature=signature, body=body):
        raise HTTPException(status_code=401, detail="bad Slack signature")
    if _app()._issue_webhook_replay_seen(f"slack:{signature}", _SLACK_REPLAY_WINDOW):
        raise HTTPException(status_code=409, detail="that Slack request was already handled")
    return body


def _slack_principal_for(user_id: str):
    from maverick.channel_identities import principal_for

    return principal_for("slack", user_id)


@router.post("/webhook/slack/interactions")
async def webhook_slack_interactions(request: Request) -> JSONResponse:
    """Button presses from Slack (signed; ``payload`` form field)."""
    from maverick.slack_interactive import handle_block_action, parse_interaction_body, respond

    body = await _verified_slack_body(request)
    payload = parse_interaction_body(body)
    if payload.get("type") != "block_actions":
        return JSONResponse({})
    result = await run_in_threadpool(
        handle_block_action, payload, world=_world(), principal_for=_slack_principal_for)
    if result is None:
        return JSONResponse({})
    # Update the card in place through the payload's response_url; answering the
    # POST itself is only the acknowledgement Slack wants within 3 seconds.
    await run_in_threadpool(respond, str(payload.get("response_url") or ""), result)
    return JSONResponse({})


@router.post("/webhook/slack/commands")
async def webhook_slack_commands(request: Request) -> JSONResponse:
    """``/lightwork ...`` slash commands from Slack (signed form POST).

    ``/lightwork ask`` needs the running chat bot (``maverick serve`` with the
    Slack channel); over HTTP it answers with that pointer.
    """
    from maverick.slack_interactive import handle_slash_command, parse_form

    body = await _verified_slack_body(request)
    form = parse_form(body)
    result = await run_in_threadpool(
        handle_slash_command, form, world=_world(), principal_for=_slack_principal_for, ask=None)
    return JSONResponse(result)



__all__ = ["api", "router"]
