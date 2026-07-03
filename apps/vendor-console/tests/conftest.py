"""Test fixtures for the vendor console.

Pins a deterministic signing key + a scratch DAYBREAK_HOME so no real key file is
written, and puts maverick-core + the app package on the path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
for p in (Path(__file__).parent,                       # so tests can `import conftest`
          _ROOT / "apps" / "vendor-console", _ROOT / "packages" / "maverick-core"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

pytest.importorskip("fastapi")
pytest.importorskip("cryptography")

# A fixed publisher private key so signatures + public_key_hex are deterministic.
TEST_SIGNING_KEY = "1" * 64


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("VENDOR_CONSOLE_SIGNING_KEY", TEST_SIGNING_KEY)
    monkeypatch.setenv("VENDOR_CONSOLE_SECRET", "test-session-secret")
    monkeypatch.setenv("VENDOR_CONSOLE_INSECURE", "1")   # TestClient rides http
    monkeypatch.setenv("DAYBREAK_HOME", str(tmp_path / "daybreak"))
    from vendor_console import ratelimit
    ratelimit.reset_all()                                # no cross-test lockout leakage


@pytest.fixture
def conn(tmp_path):
    from vendor_console import db
    c = db.connect(str(tmp_path / "console.db"))
    db.init_db(c)
    return c


@pytest.fixture
def app(tmp_path):
    from vendor_console.app import create_app
    return create_app(str(tmp_path / "app.db"))


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def make_staff(app, *, email="owner@daybreak.co", role="owner"):
    """Create a staff row directly and return its id (bypasses the UI setup)."""
    from vendor_console import security, store
    conn = app.state.conn
    return store.create_staff(conn, email=email, name="O", role=role,
                              pw_hash=security.hash_password("password-1234"),
                              totp_secret=security.new_totp_secret())


def login_cookie(app, staff_id: int, role: str = "owner", epoch: int = 0) -> dict:
    """A full-session cookie for a staff id (skips the password+TOTP screens)."""
    from vendor_console import security
    from vendor_console.auth import SESSION_COOKIE
    token = security.sign_session(
        {"sid": staff_id, "stage": "full", "role": role, "ep": epoch},
        security.session_key())
    return {SESSION_COOKIE: token}


@pytest.fixture
def admin(app, client):
    """A TestClient authenticated as an owner (full session)."""
    sid = make_staff(app, email="owner@daybreak.co", role="owner")
    client.cookies.update(login_cookie(app, sid, "owner"))
    return client
