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

log = logging.getLogger(__name__)


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
            log.warning("notify ntfy: invalid topic %r (allowed: A-Za-z0-9_-)", topic)
            return False
        url = f"{server.rstrip('/')}/{topic}"
        resp = httpx.post(url, content=body.encode("utf-8"), headers=headers, timeout=10.0)
        return resp.status_code < 400
    except Exception as e:
        log.warning("notify ntfy failed: %s", e)
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
        return resp.status_code < 400
    except Exception as e:
        log.warning("notify pushover failed: %s", e)
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
        return resp.status_code < 400
    except Exception as e:
        # The webhook URL IS the secret here, and httpx embeds the request URL
        # in str(e); log the exception TYPE only so a failed POST can't write the
        # full secret URL to the logs.
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
        return resp.status_code < 400
    except Exception as e:
        # The webhook URL IS the secret (see _send_discord); log type only.
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
               cfg: dict | None = None) -> bool:
    """Send one message through ``[notifications.email]``. False when mail is
    not set up or the server refuses; never raises. Logs the failure type only
    (an SMTP error can echo credentials)."""
    import smtplib
    import ssl
    from email.message import EmailMessage
    from email.utils import formatdate, make_msgid

    mail = _email_config(cfg)
    recipient = " ".join(str(to or "").split())
    if not recipient or "@" not in recipient:
        return False
    if not mail["host"] or not mail["from_address"]:
        # No dedicated alert server: the platform mailer's [email] account
        # (invitations use it) carries the plain-text version.
        try:
            from .mailer import MailerError, configured, send

            if not configured():
                return False
            send(recipient, str(subject or "Lightwork"), str(text_body or ""))
            return True
        except MailerError as e:
            log.warning("notify email failed: %s", type(e).__name__)
            return False
    message = EmailMessage()
    message["From"] = mail["from_address"]
    message["To"] = recipient
    message["Subject"] = " ".join(str(subject or "Lightwork").split())[:200]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain="lightwork.local")
    message.set_content(str(text_body or ""))
    if html_body:
        message.add_alternative(str(html_body), subtype="html")
    try:
        with _smtp_session(mail) as smtp:
            if mail["tls"] == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if mail["user"]:
                smtp.login(mail["user"], mail["password"])
            refused = smtp.send_message(message)
        return not refused
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        log.warning("notify email failed: %s", type(e).__name__)
        return False


def notify(
    body: str,
    *,
    title: str = "Lightwork",
    priority: str = "default",
    category: str | None = None,
    backends: list[str] | None = None,
    async_dispatch: bool = True,
    batch: bool = True,
) -> int:
    """Send a notification. Returns the number of backends fired.

    ``priority`` is one of: low / default / high / max.
    ``backends`` overrides config (e.g. ['ntfy']). None = use config.
    ``async_dispatch=False`` runs synchronously (mainly for tests).
    ``batch`` lets opt-in **notification batching** coalesce low/normal-priority
    pushes (``[notifications] batch_window_seconds``); high/urgent always go
    straight through, and with batching unconfigured this is a no-op. When an
    item is queued for a later batch, the call returns 0 (fired *now*).
    """
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
    if batch and async_dispatch and priority not in ("high", "max", "urgent"):
        from .notification_batcher import shared as _shared_batcher
        batcher = _shared_batcher()
        if batcher is not None:
            return batcher.submit(body, title=title, priority=priority,
                                  category=category)

    def _legacy_url(section: dict, backend: str):
        # Older installers wrote ``webhook_url`` next to a single backend.
        return section.get("webhook_url") if section.get("backend") == backend else None

    def _dispatch(backend: str) -> bool:
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

    if async_dispatch:
        exec_ = _get_executor()
        for backend in requested:
            exec_.submit(_dispatch, backend)
        return len(requested)
    # Sync (for tests).
    fired = 0
    for backend in requested:
        if _dispatch(backend):
            fired += 1
    return fired


__all__ = ["email_configured", "notify", "send_email"]
