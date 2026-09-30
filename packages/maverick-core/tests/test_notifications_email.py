"""The email notification backend: SMTP through a fake server, the secret
provider for the password, the platform-mailer fallback, and notify()."""
from __future__ import annotations

import pytest


class FakeSMTP:
    """Records what a real smtplib.SMTP would have been told."""

    instances: list = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.started_tls = False
        self.login_args = None
        self.sent = []
        self.refuse = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent.append(message)
        return {"x@example.com": (550, b"no")} if self.refuse else {}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import smtplib

    from maverick import config

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.delenv("MAVERICK_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("EMAIL_USER", raising=False)
    monkeypatch.delenv("EMAIL_APP_PASSWORD", raising=False)
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.instances = []
    config.reset_config_cache()
    yield
    config.reset_config_cache()


CFG = {"email": {"host": "smtp.example.test", "port": 2525, "user": "alerts",
                 "password": "saved-pw",  # pragma: allowlist secret
                 "from_address": "Lightwork <alerts@example.test>", "tls": "starttls",
                 "to": "ops@example.test"}}


def test_send_email_uses_starttls_login_and_html_alternative():
    from maverick.notifications import email_configured, send_email

    assert email_configured(CFG) is True
    ok = send_email("Bob <bob@example.test>", "Subject\nline", "plain text",
                    html_body="<p>hi</p>", cfg=CFG)
    assert ok is True
    smtp = FakeSMTP.instances[-1]
    assert (smtp.host, smtp.port) == ("smtp.example.test", 2525)
    assert smtp.started_tls is True and smtp.login_args == ("alerts", "saved-pw")
    message = smtp.sent[0]
    assert message["To"] == "Bob <bob@example.test>"
    assert message["Subject"] == "Subject line"  # header injection stripped
    assert message["From"] == "Lightwork <alerts@example.test>"
    parts = [p.get_content_type() for p in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]


def test_password_from_secret_provider_wins_and_ssl_mode(monkeypatch):
    from maverick.notifications import send_email

    monkeypatch.setenv("MAVERICK_SMTP_PASSWORD", "env-pw")
    cfg = {"email": {**CFG["email"], "tls": "ssl", "port": 465}}
    assert send_email("bob@example.test", "s", "b", cfg=cfg) is True
    smtp = FakeSMTP.instances[-1]
    assert smtp.login_args == ("alerts", "env-pw") and smtp.started_tls is False


def test_refusals_bad_recipients_and_failures_never_raise(monkeypatch):
    import smtplib

    from maverick.notifications import send_email

    assert send_email("not-an-address", "s", "b", cfg=CFG) is False
    assert send_email("", "s", "b", cfg=CFG) is False

    class Refusing(FakeSMTP):
        def send_message(self, message):
            return {"bob@example.test": (550, b"no")}

    monkeypatch.setattr(smtplib, "SMTP", Refusing)
    assert send_email("bob@example.test", "s", "b", cfg=CFG) is False

    class Broken(FakeSMTP):
        def __init__(self, *a, **kw):
            raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", Broken)
    assert send_email("bob@example.test", "s", "b", cfg=CFG) is False


def test_falls_back_to_the_platform_mailer_when_no_alert_server(monkeypatch):
    from maverick import mailer
    from maverick.notifications import email_configured, send_email

    assert email_configured({}) is False
    assert send_email("bob@example.test", "s", "b", cfg={}) is False
    sent = []
    monkeypatch.setattr(mailer, "configured", lambda: True)
    monkeypatch.setattr(mailer, "send", lambda to, subject, body, transport=None: sent.append((to, subject, body)))
    assert email_configured({}) is True
    assert send_email("bob@example.test", "s", "b", html_body="<p>x</p>", cfg={}) is True
    assert sent == [("bob@example.test", "s", "b")]
    monkeypatch.setattr(mailer, "send", lambda *a, **k: (_ for _ in ()).throw(mailer.MailerError("x")))
    assert send_email("bob@example.test", "s", "b", cfg={}) is False


def test_notify_email_backend_uses_the_default_recipient(monkeypatch):
    from maverick import notifications

    monkeypatch.setattr(notifications, "_load_config", lambda: {"backend": "email", **CFG})
    assert notifications.notify("Run 42 finished", title="Lightwork", async_dispatch=False) == 1
    message = FakeSMTP.instances[-1].sent[0]
    assert message["To"] == "ops@example.test" and message["Subject"] == "Lightwork"
    monkeypatch.setattr(notifications, "_load_config",
                        lambda: {"backend": "email", "email": {**CFG["email"], "to": ""}})
    assert notifications.notify("Run 43 finished", async_dispatch=False) == 0
