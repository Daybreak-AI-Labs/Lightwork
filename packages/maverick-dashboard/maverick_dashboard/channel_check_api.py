"""Chat-app checks from the Channels page: Test connection and Send a test message.

"Test connection" asks the chat service whether the saved credentials are
accepted (a read-only identity call per service). "Send a test message" sends
one fixed sentence to a destination the administrator chose (an email address,
a phone number or a Slack channel). Both record the outcome against the exact
saved configuration, so the page's status changes from "Not tested yet" to
"Working" or "Test failed". Only a pass/fail and a short caller-written summary
are kept; provider responses are never stored or shown.

Global admin only, same-origin checked, bounded timeouts, and outbound HTTP
goes through the egress-guarded client used by the provider checks.
"""
from __future__ import annotations

import base64
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission

router = APIRouter()

TEST_TEXT = "This is a test message from Lightwork. If you can read this, the connection works."
_TIMEOUT = 8.0
SENDABLE = {"email", "sms", "slack"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+[1-9][0-9]{6,14}$")
_SLACK = re.compile(r"^#?[a-z0-9][a-z0-9._-]{0,79}$")


def _admin(request: Request) -> None:
    from . import app as app_module
    app_module._require_same_origin(request)
    require_global_permission(request, "admin")


def _channel_config(name: str) -> dict[str, Any]:
    from .integration_readiness import require_readable_config
    raw = (require_readable_config().get("channels") or {}).get(name)
    if not isinstance(raw, dict) or not raw.get("enabled"):
        raise HTTPException(409, "Turn this chat app on and save it before testing.")
    return raw


def _get(url: str, headers: dict[str, str]):
    from maverick.tools import _ssrf
    return _ssrf.safe_get(url, headers=headers, timeout=_TIMEOUT)


def _post(url: str, *, headers: dict[str, str], data=None, json=None):
    from maverick.tools import _ssrf
    with _ssrf.safe_client(url, timeout=_TIMEOUT) as client:
        return client.post(url, headers=headers, data=data, json=json)


def _basic(user: str, secret: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{secret}".encode()).decode()


def _smtp(cfg: dict[str, Any]):
    host = str(cfg.get("smtp_host") or "").strip()
    port = int(cfg.get("smtp_port") or 465)
    context = ssl.create_default_context()
    if port == 587:
        smtp = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        smtp.starttls(context=context)
    else:
        smtp = smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT, context=context)
    smtp.login(str(cfg.get("smtp_user") or ""), str(cfg.get("smtp_password") or ""))
    return smtp


def check_credentials(name: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    """Ask the chat service whether the saved credentials work."""
    try:
        if name == "telegram":
            token = str(cfg.get("bot_token") or "")
            ok = _get(f"https://api.telegram.org/bot{quote(token, safe=':')}/getMe", {}).status_code == 200
        elif name == "discord":
            ok = _get("https://discord.com/api/v10/users/@me",
                      {"Authorization": "Bot " + str(cfg.get("bot_token") or "")}).status_code == 200
        elif name == "slack":
            response = _get("https://slack.com/api/auth.test",
                            {"Authorization": "Bearer " + str(cfg.get("bot_token") or "")})
            ok = response.status_code == 200 and bool((response.json() or {}).get("ok"))
        elif name == "whatsapp_cloud":
            phone = quote(str(cfg.get("phone_number_id") or ""), safe="")
            ok = _get(f"https://graph.facebook.com/v19.0/{phone}",
                      {"Authorization": "Bearer " + str(cfg.get("access_token") or "")}).status_code == 200
        elif name == "sms":
            sid = str(cfg.get("account_sid") or "")
            ok = _get(f"https://api.twilio.com/2010-04-01/Accounts/{quote(sid, safe='')}.json",
                      {"Authorization": _basic(sid, str(cfg.get("auth_token") or ""))}).status_code == 200
        elif name == "email":
            smtp = _smtp(cfg)
            smtp.quit()
            ok = True
        elif name == "teams":
            return _teams_token_check(cfg)
        else:
            return False, "this chat app has no connection test"
    except Exception as exc:  # noqa: BLE001 -- summarized, never echoed
        return False, f"the connection test could not reach the service ({type(exc).__name__})"
    return ok, ("the service accepted the saved details" if ok else "the service did not accept the saved details")


def _teams_token_check(cfg: dict[str, Any]) -> tuple[bool, str]:
    """Ask Microsoft's sign-in service for a Bot Framework token with the saved
    app id and secret (the same call the channel makes before every reply)."""
    from maverick_channels.teams import (
        TOKEN_SCOPE,
        channel_config,
        explain_token_failure,
        token_endpoint,
    )

    settings = channel_config(cfg)
    if not settings["app_id"] or not settings["app_password"]:
        return False, "enter the app ID and client secret first"
    response = _post(token_endpoint(settings["tenant_id"] or None),
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     data={"grant_type": "client_credentials", "client_id": settings["app_id"],
                           "client_secret": settings["app_password"], "scope": TOKEN_SCOPE})
    try:
        body = response.json() or {}
    except ValueError:
        body = {}
    if response.status_code == 200 and body.get("access_token"):
        return True, "Microsoft issued a Bot Framework token for the saved app id and secret"
    return False, explain_token_failure(response.status_code, body)


def send_test(name: str, cfg: dict[str, Any], to: str) -> tuple[bool, str]:
    """Send the fixed test sentence to ``to``."""
    try:
        if name == "email":
            smtp = _smtp(cfg)
            try:
                message = EmailMessage()
                message["From"] = str(cfg.get("smtp_user") or "")
                message["To"] = to
                message["Subject"] = "Lightwork test message"
                message.set_content(TEST_TEXT)
                refused = smtp.send_message(message)
            finally:
                smtp.quit()
            ok = not refused
        elif name == "sms":
            sid = str(cfg.get("account_sid") or "")
            response = _post(
                f"https://api.twilio.com/2010-04-01/Accounts/{quote(sid, safe='')}/Messages.json",
                headers={"Authorization": _basic(sid, str(cfg.get("auth_token") or ""))},
                data={"To": to, "From": str(cfg.get("from_number") or ""), "Body": TEST_TEXT})
            ok = 200 <= response.status_code < 300
        elif name == "slack":
            response = _post("https://slack.com/api/chat.postMessage",
                             headers={"Authorization": "Bearer " + str(cfg.get("bot_token") or "")},
                             json={"channel": to, "text": TEST_TEXT})
            ok = response.status_code == 200 and bool((response.json() or {}).get("ok"))
        else:
            return False, "this chat app can't send a test message from here"
    except Exception as exc:  # noqa: BLE001
        return False, f"the test message could not be sent ({type(exc).__name__})"
    return ok, ("the test message was accepted for delivery" if ok else "the service refused the test message")


def _valid_destination(name: str, to: str) -> str:
    to = (to or "").strip()
    ok = {"email": _EMAIL, "sms": _PHONE, "slack": _SLACK}[name].fullmatch(to.lower() if name == "slack" else to)
    if not ok:
        raise HTTPException(422, {"email": "Enter an email address.",
                                  "sms": "Enter a mobile number with its country code, like +15551234567.",
                                  "slack": "Enter a Slack channel name, like #general."}[name])
    return to


def _back(name: str, result: str) -> RedirectResponse:
    return RedirectResponse(f"/channels?saved={result}&channel={quote(name)}#channel-{quote(name)}",
                            status_code=303)


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "")


def _answer(request: Request, name: str, cfg: dict[str, Any], *, ok: bool, detail: str,
            result: str, sentence: str):
    """Redirect for a plain form post; a JSON answer (with the re-rendered
    status block) when the page asked for one, so the result shows inline."""
    if not _wants_json(request):
        return _back(name, result)
    from .app import templates
    from .integration_readiness import channel_readiness

    readiness = channel_readiness(name, cfg)
    module = templates.get_template("_integration_readiness.html").module
    return {"ok": ok, "detail": detail, "status": "Working" if ok else "Test failed",
            "message": sentence, "readiness_html": str(module.integration_readiness(readiness))}


@router.post("/channels/test", response_model=None)
async def channels_test(request: Request, channel: str = Form(...)):
    _admin(request)
    from .integration_readiness import record
    name = channel.strip()
    cfg = await run_in_threadpool(_channel_config, name)
    ok, detail = await run_in_threadpool(check_credentials, name, cfg)
    record("channel", name, cfg, verified=ok, detail=detail, evidence={"kind": "credential_check"})
    return _answer(request, name, cfg, ok=ok, detail=detail, result="tested" if ok else "test_failed",
                   sentence="Connection test passed: the service accepted the saved details." if ok
                   else "The connection test failed. Check the details, save, and test again.")


@router.post("/channels/send-test", response_model=None)
async def channels_send_test(request: Request, channel: str = Form(...), to: str = Form("")):
    _admin(request)
    from .integration_readiness import record
    name = channel.strip()
    if name not in SENDABLE:
        raise HTTPException(400, "This chat app can't send a test message from here.")
    destination = _valid_destination(name, to)
    cfg = await run_in_threadpool(_channel_config, name)
    ok, detail = await run_in_threadpool(send_test, name, cfg, destination)
    record("channel", name, cfg, verified=ok, detail=detail,
           evidence={"kind": "test_message", "external_delivery_verified": False})
    return _answer(request, name, cfg, ok=ok, detail=detail, result="sent" if ok else "send_failed",
                   sentence="Test message sent. Check that it arrived." if ok
                   else "The test message could not be sent. Check the details and try again.")
