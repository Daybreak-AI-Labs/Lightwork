"""Push notifications for run events.

Wraps the most common consumer push services:
  - ntfy.sh (default, no account required; just a topic)
  - Pushover (PUSHOVER_USER_KEY + PUSHOVER_APP_TOKEN, else the sealed
    [notifications] user_key + app_token saved on the All settings page)
  - Discord webhook (DISCORD_NOTIFY_WEBHOOK_URL)
  - Slack webhook (SLACK_NOTIFY_WEBHOOK_URL)
  - Email over SMTP (the platform mail backend; also carries the daily
    summary from maverick.daily_digest)

Config (in ~/.maverick/config.toml):

    [notifications]
    backend = "ntfy"             # ntfy | pushover | discord | slack | email | none
    ntfy_topic = "${MAVERICK_NTFY_TOPIC}"
    ntfy_server = "https://ntfy.sh"

    [notifications.email]
    host = "smtp.example.com"
    port = 587
    user = "alerts@example.com"
    password = "${MAVERICK_SMTP_PASSWORD}"   # or the sealed All settings value
    from_address = "Lightwork <alerts@example.com>"
    tls = "starttls"             # starttls | ssl | none
    to = "ops@example.com"       # where run alerts go (the digest picks its own recipient)

Use:

    from maverick.notifications import notify
    notify("Run #42 finished", priority="default", category="run")

Failures are logged but never block the caller. Multiple backends can
be configured; each fires its own notification.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from collections.abc import Callable
from contextvars import ContextVar, copy_context
from typing import Any

log = logging.getLogger(__name__)


_outcome = ContextVar("notification_outcome", default=None)


def _response_accepted(status: int) -> bool:
    if 200 <= status < 300:
        _outcome.set(("delivered", "Accepted by the notification service. This does not mean the person saw it.", False))
        return True
    if 400 <= status < 500 and status != 408:
        _outcome.set(("failed", "The notification service refused this request. Check the saved destination and credentials.", True))
    else:
        _outcome.set(("unknown", "The service did not confirm delivery. Check the destination before sending again.", False))
    return False


def _network_unknown() -> None:
    _outcome.set(("unknown", "Delivery could not be confirmed. Check the destination before sending again.", False))


def mark_unconfirmed() -> None:
    """Flag that a send made outside this module (a Slack DM) may have gone
    out without a confirmation; :func:`send_checked` reports it."""
    _network_unknown()


def send_checked(send: Callable[[], Any]) -> tuple[Any, bool]:
    """Run ``send`` and also say whether a transport inside it could not
    confirm the result -- it may have been delivered, so never replay it
    blindly. An exception propagates; callers treat it as unconfirmed too."""
    token = _outcome.set(None)
    try:
        result = send()
        state = _outcome.get()
        return result, bool(state) and state[0] == "unknown"
    finally:
        _outcome.reset(token)


_executor = None  # type: ignore[var-annotated]
_executor_lock = threading.Lock()


def _get_executor():
    global _executor
    with _executor_lock:
        if _executor is None:
            from concurrent.futures import ThreadPoolExecutor
            _executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="mvk-notify",
            )
    return _executor


def _resolve_env_ref(value: str | None) -> str | None:
    """Resolve `${ENV_VAR}` references in config strings."""
    if not value:
        return value
    if value.startswith("${") and value.endswith("}"):
        env_name = value[2:-1]
        return os.environ.get(env_name)
    return value


def _load_config() -> dict:
    try:
        from .config import load_config
        return (load_config() or {}).get("notifications") or {}
    except Exception:
        return {}


def _send_ntfy(title: str, body: str, priority: str, server: str, topic: str) -> bool:
    try:
        import httpx
    except ImportError:
        return False
    if not topic:
        return False
    prio_map = {"low": "1", "default": "3", "high": "4", "max": "5"}
    headers = {
        # The title is agent-controlled (the notify tool lets the model set
        # it). Strip CR/LF so it can't inject extra ntfy control headers /
        # split the header block.
        "Title": title.replace("\r", " ").replace("\n", " "),
        "Priority": prio_map.get(priority, "3"),
    }
    try:
        # ntfy topics are [A-Za-z0-9_-]; reject anything else so a typo/hostile
        # topic with '/'/'..'/'?'/'#' can't post to a different path on the server.
        import re as _re
        if not _re.fullmatch(r"[A-Za-z0-9_-]+", topic or ""):
            log.warning("notify ntfy: invalid topic format")
            return False
        url = f"{server.rstrip('/')}/{topic}"
        resp = httpx.post(url, content=body.encode("utf-8"), headers=headers, timeout=10.0)
        return _response_accepted(resp.status_code)
    except Exception as e:
        _network_unknown()
        log.warning("notify ntfy failed: %s", type(e).__name__)
        return False


def _pushover_credentials(cfg: dict | None = None) -> tuple[str, str]:
    """``(user_key, app_token)``: ``PUSHOVER_USER_KEY`` / ``PUSHOVER_APP_TOKEN``
    win; otherwise ``[notifications] user_key`` / ``app_token`` -- the sealed
    values saved on the All settings page (opened by ``load_config``), or the
    installer's ``${PUSHOVER_...}`` references."""
    section = _load_config() if cfg is None else cfg
    values = []
    for env_name, key in (("PUSHOVER_USER_KEY", "user_key"), ("PUSHOVER_APP_TOKEN", "app_token")):
        value = os.environ.get(env_name, "").strip()
        if not value:
            saved = _resolve_env_ref(section.get(key)) if isinstance(section.get(key), str) else None
            value = (saved or "").strip()
        values.append(value)
    return values[0], values[1]


def _send_pushover(title: str, body: str, priority: str, cfg: dict | None = None) -> bool:
    user, app = _pushover_credentials(cfg)
    if not user or not app:
        return False
    try:
        import httpx
    except ImportError:
        return False
    prio_map = {"low": "-1", "default": "0", "high": "1", "max": "2"}
    try:
        resp = httpx.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token": app, "user": user,
                "title": title, "message": body,
                "priority": prio_map.get(priority, "0"),
            },
            timeout=10.0,
        )
        if 200 <= resp.status_code < 300 and resp.json().get("status") != 1:
            _outcome.set(("failed", "The notification service refused this request. Check the saved destination and credentials.", True))
            return False
        return _response_accepted(resp.status_code)
    except Exception as e:
        _network_unknown()
        log.warning("notify pushover failed: %s", type(e).__name__)
        return False


def _send_discord(title: str, body: str, url: str) -> bool:
    if not url:
        return False
    try:
        import httpx
        resp = httpx.post(
            url,
            # allowed_mentions parse:[] so an agent-controlled title/body
            # containing @everyone/@here can't trigger a mass ping.
            json={
                "content": f"**{title}**\n{body}",
                "allowed_mentions": {"parse": []},
            },
            timeout=10.0,
        )
        return _response_accepted(resp.status_code)
    except Exception as e:
        # The webhook URL IS the secret here, and httpx embeds the request URL
        # in str(e); log the exception TYPE only so a failed POST can't write the
        # full secret URL to the logs.
        _network_unknown()
        log.warning("notify discord failed: %s", type(e).__name__)
        return False


def _send_slack(title: str, body: str, url: str) -> bool:
    if not url:
        return False
    try:
        import httpx
        resp = httpx.post(
            url,
            json={"text": f"*{title}*\n{body}"},
            timeout=10.0,
        )
        return _response_accepted(resp.status_code)
    except Exception as e:
        # The webhook URL IS the secret (see _send_discord); log type only.
        _network_unknown()
        log.warning("notify slack failed: %s", type(e).__name__)
        return False


_EMAIL_TLS_MODES = ("starttls", "ssl", "none")
_EMAIL_TIMEOUT = 15.0


def _email_config(cfg: dict | None = None) -> dict:
    """The ``[notifications.email]`` table with defaults and the password
    resolved through the secret provider (``MAVERICK_SMTP_PASSWORD`` wins over
    the sealed value saved on the All settings page)."""
    from .secret_provider import get_secret_or_saved

    section = _load_config() if cfg is None else cfg
    raw = section.get("email") if isinstance(section.get("email"), dict) else {}
    port = raw.get("port", 587)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = 587
    tls = str(raw.get("tls") or "starttls").strip().lower()
    if tls not in _EMAIL_TLS_MODES:
        tls = "starttls"
    saved_password = _resolve_env_ref(raw.get("password")) if isinstance(raw.get("password"), str) else None
    return {
        "host": str(_resolve_env_ref(raw.get("host")) or "").strip(),
        "port": port,
        "user": str(_resolve_env_ref(raw.get("user")) or "").strip(),
        "password": get_secret_or_saved("MAVERICK_SMTP_PASSWORD", saved_password) or "",
        "from_address": str(_resolve_env_ref(raw.get("from_address")) or "").strip(),
        "tls": tls,
        "to": str(_resolve_env_ref(raw.get("to")) or "").strip(),
    }


def email_configured(cfg: dict | None = None) -> bool:
    """A dedicated ``[notifications.email]`` server, or else the platform
    mailer's ``[email]`` account (the one invitations already use)."""
    mail = _email_config(cfg)
    if mail["host"] and mail["from_address"]:
        return True
    try:
        from .mailer import configured

        return configured()
    except Exception:  # noqa: BLE001 -- an unreadable mailer config means "not set up"
        return False


def _smtp_session(mail: dict):
    import smtplib
    import ssl

    if mail["tls"] == "ssl":
        return smtplib.SMTP_SSL(mail["host"], mail["port"], timeout=_EMAIL_TIMEOUT,
                                context=ssl.create_default_context())
    return smtplib.SMTP(mail["host"], mail["port"], timeout=_EMAIL_TIMEOUT)


def send_email(to: str, subject: str, text_body: str, *, html_body: str | None = None,
               cfg: dict | None = None, headers: dict[str, str] | None = None) -> bool:
    """Send one message through ``[notifications.email]``. False when mail is
    not set up or the server refuses; never raises. Logs the failure type only
    (an SMTP error can echo credentials)."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid

    from . import practice

    if practice.active():  # practice mode: recorded, never sent
        practice.record("email", "Email", {"to": to, "subject": subject})
        return False
    mail = _email_config(cfg)
    recipient = " ".join(str(to or "").split())
    if not recipient or "@" not in recipient:
        return False
    if not mail["host"] or not mail["from_address"]:
        # No dedicated alert server: the platform mailer's [email] account
        # (invitations use it) carries the plain-text version.
        try:
            from .mailer import MailerError, MailerNotSent, configured, send

            if not configured():
                return False
            send(recipient, str(subject or "Lightwork"), str(text_body or ""))
            return True
        except MailerNotSent as e:
            # The mailer's own "definitely not sent": it could not connect or
            # log in, the recipient was refused, or sending is off. Stays a
            # plain failure (safe to retry), never "unknown".
            log.warning("notify email failed: %s", type(e).__name__)
            return False
        except MailerError as e:
            _network_unknown()  # the message may already be on the wire
            log.warning("notify email failed: %s", type(e).__name__)
            return False
    message = EmailMessage()
    message["From"] = mail["from_address"]
    message["To"] = recipient
    message["Subject"] = " ".join(str(subject or "Lightwork").split())[:200]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="lightwork.local")
    # One-click unsubscribe (RFC 8058) and similar list headers; values are
    # links Lightwork made, never message content.
    for name, value in (headers or {}).items():
        if name in {"List-Unsubscribe", "List-Unsubscribe-Post"} and "\n" not in value and "\r" not in value:
            message[name] = value
    message.set_content(str(text_body or ""))
    if html_body:
        message.add_alternative(str(html_body), subtype="html")
    on_the_wire = False
    try:
        with _smtp_session(mail) as smtp:
            if mail["tls"] == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if mail["user"]:
                smtp.login(mail["user"], mail["password"])
            on_the_wire = True  # from here (MAIL FROM, RCPT TO, DATA) it may be delivered
            refused = smtp.send_message(message)
        return not refused
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        # A failure before the message is handed over (connection refused, TLS,
        # login) or an explicit refusal by the server sent nothing: a plain,
        # retryable failure. Only a lost or unanswered hand-over is unknown.
        refused_by_server = isinstance(e, (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                                           smtplib.SMTPDataError))
        if on_the_wire and not refused_by_server:
            _network_unknown()
        log.warning("notify email failed: %s", type(e).__name__)
        return False

def _legacy_url(section: dict, backend: str):
    # Older installers wrote ``webhook_url`` next to a single backend.
    return section.get("webhook_url") if section.get("backend") == backend else None

def _dispatch(backend: str, *, cfg: dict, title: str, body: str, priority: str) -> bool:
    if backend == "ntfy":
        # ``topic`` is what older installers wrote; keep honouring it.
        topic = _resolve_env_ref(cfg.get("ntfy_topic") or cfg.get("topic"))
        server = _resolve_env_ref(cfg.get("ntfy_server")) or "https://ntfy.sh"
        if not topic:
            topic = os.environ.get("MAVERICK_NTFY_TOPIC")
        if not topic:
            log.debug("notify ntfy: no topic configured")
            return False
        return _send_ntfy(title, body, priority, server, topic)
    if backend == "pushover":
        return _send_pushover(title, body, priority, cfg)
    if backend == "discord":
        url = _resolve_env_ref(cfg.get("discord_webhook") or _legacy_url(cfg, "discord")) or \
              os.environ.get("DISCORD_NOTIFY_WEBHOOK_URL")
        return _send_discord(title, body, url or "")
    if backend == "slack":
        url = _resolve_env_ref(cfg.get("slack_webhook") or _legacy_url(cfg, "slack")) or \
              os.environ.get("SLACK_NOTIFY_WEBHOOK_URL")
        return _send_slack(title, body, url or "")
    if backend == "email":
        mail = _email_config(cfg)
        if not mail["to"]:
            log.debug("notify email: no [notifications.email] to address configured")
            return False
        return send_email(mail["to"], title, body, cfg=cfg)
    log.warning("notify: unknown backend %r", backend)
    return False

def _admit(cfg, requested, event, delivery_kind, retry):
    from .notification_receipts import DeliveryReceipts
    receipts = DeliveryReceipts() if cfg.get("delivery_receipts", True) is not False else None
    if len(event) > 200:
        raise ValueError("Delivery reference is too long")
    if retry and (receipts is None or delivery_kind != "test"):
        return receipts, []
    admitted = []
    for backend in dict.fromkeys(requested):
        if receipts is None:
            admitted.append((backend, None))
        else:
            try:
                ident, acquired = receipts.claim(event, backend, kind=delivery_kind, retry=retry)
            except Exception as exc:
                log.warning("notify receipt admission unavailable: %s", type(exc).__name__)
                if delivery_kind == "test":
                    raise RuntimeError("Delivery history is unavailable; the test was not sent.") from None
                continue
            if acquired:
                admitted.append((backend, ident))

    return receipts, admitted


def _tracked(backend, ident, *, cfg, title, body, priority, receipts, authorize, states=None):
    from .authority_effects import authority_effect_fence
    token = _outcome.set(("failed", "Not sent. Check the channel configuration and installed backend dependencies.", True))
    started = False
    final = "failed"
    try:
        with authority_effect_fence():
            if authorize is not None:
                authorize()
            if cfg != _load_config():
                raise ValueError("Notification configuration changed")
            if receipts is not None:
                receipts.update(ident, "sending", "Contacting the notification service.")
            started = True
            result = _dispatch(backend, cfg=cfg, title=title, body=body, priority=priority)
            outcome = _outcome.get()
            if result and outcome[0] != "delivered":
                outcome = ("delivered", "Accepted by the notification service. This does not mean the person saw it.", False)
            final = outcome[0] if outcome[0] in ("delivered", "failed", "unknown") else ("delivered" if result else "unknown")
            if receipts is not None:
                receipts.update(ident, outcome[0], outcome[1], safe_to_retry=outcome[2])
            return result
    except Exception as exc:
        log.warning("notify dispatch unavailable: %s", type(exc).__name__)
        if final != "delivered":
            final = "unknown" if started else "failed"
        if receipts is not None:
            try:
                message = ("Delivery could not be confirmed. Check the destination before sending again." if started
                           else "Not sent. Current access, configuration or delivery storage could not be verified.")
                receipts.update(ident, "unknown" if started else "failed", message)
            except Exception:
                log.warning("notify receipt could not be saved")
        return False
    finally:
        _outcome.reset(token)
        if states is not None:
            states.append(final)


def notify(
    body: str,
    *,
    title: str = "Lightwork",
    priority: str = "default",
    category: str | None = None,
    backends: list[str] | None = None,
    async_dispatch: bool = True,
    batch: bool = True,
    delivery_key: str | None = None,
    delivery_kind: str = "event",
    retry: bool = False,
    authorize=None,
    outcomes: list[str] | None = None,
) -> int:
    """Queue a notification. Async returns accepted queue count, never proof of delivery.

    ``priority`` is one of: low / default / high / max.
    ``backends`` overrides config (e.g. ['ntfy']). None = use config.
    ``async_dispatch=False`` runs synchronously (mainly for tests).
    ``batch`` lets opt-in **notification batching** coalesce low/normal-priority
    pushes (``[notifications] batch_window_seconds``); high/urgent always go
    straight through, and with batching unconfigured this is a no-op. When an
    item is queued for a later batch, the call returns 0 (fired *now*).

    ``outcomes`` (synchronous sends only) receives one final state per
    dispatched backend: ``delivered`` (accepted), ``failed`` (definitely not
    sent) or ``unknown`` (it may have been sent; never replay it blindly).
    """
    from . import practice

    if practice.active():  # practice mode: recorded, never sent
        practice.record("alert", title, {"body": body, "priority": priority})
        return 0
    cfg = _load_config()
    # Normalize: backend names are user-typed (config/TOML or the `backends`
    # arg) and matched case-sensitively in _dispatch ("ntfy"/"discord"/...), so
    # "Discord" / "Ntfy" / "None" would otherwise dispatch to no handler or
    # skip the "none" filter -- a user who configured notifications silently
    # gets none. Lowercase + strip; drop empties and the "none" sentinel.
    requested = [
        b.strip().lower()
        for b in (backends or [cfg.get("backend", "ntfy")])
        if isinstance(b, str) and b.strip().lower() not in ("", "none")
    ]
    if not requested:
        return 0

    # Opt-in batching: coalesce low/normal-priority pushes into a windowed
    # digest. High/urgent bypass; sync sends (tests, and the batcher's own
    # delivery) bypass so there's no recursion. No-op unless configured.
    if batch and async_dispatch and delivery_key is None and priority not in ("high", "max", "urgent"):
        from .notification_batcher import shared as _shared_batcher
        batcher = _shared_batcher()
        if batcher is not None:
            return batcher.submit(body, title=title, priority=priority,
                                  category=category)

    event = delivery_key or uuid.uuid4().hex
    receipts, admitted = _admit(cfg, requested, event, delivery_kind, retry)

    def tracked(backend, ident, states=None):
        return _tracked(backend, ident, cfg=cfg, title=title, body=body,
                        priority=priority, receipts=receipts, authorize=authorize, states=states)

    if async_dispatch:
        exec_ = _get_executor()
        for backend, ident in admitted:
            context = copy_context()
            exec_.submit(context.run, tracked, backend, ident)
        return len(admitted)
    # Sync (for tests).
    fired = 0
    for backend, ident in admitted:
        if tracked(backend, ident, outcomes):
            fired += 1
    return fired


__all__ = ["email_configured", "mark_unconfirmed", "notify", "send_checked", "send_email"]
