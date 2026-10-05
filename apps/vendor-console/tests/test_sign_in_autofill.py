"""Console sign-in, set-up and two-step forms work with password managers and paste.

WCAG 2.2 SC 3.3.8: no step of signing in may depend on memory or transcription
without help. Every field names its purpose for password managers
(``username``, ``current-password``, ``new-password``, ``one-time-code``), and
nothing in the page or its script blocks pasting or asks for a puzzle.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from vendor_console import auth, security

_STATIC = Path(__file__).resolve().parents[1] / "vendor_console" / "static"
_BLOCKING_SCRIPT = re.compile(
    r"""addEventListener\(\s*['"](?:paste|copy|cut|drop|beforeinput)['"]"""
    r"""|\.on(?:paste|copy|cut|drop)\s*=|clipboardData\.(?:clearData|setData)""", re.IGNORECASE)
_BLOCKING_ATTRIBUTES = {"onpaste", "oncopy", "oncut", "ondrop", "oncontextmenu"}


class _Fields(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs: list[dict] = []
        self.blocking: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = {name: (value or "") for name, value in attrs}
        self.blocking += sorted(_BLOCKING_ATTRIBUTES & set(values))
        if tag == "input":
            self.inputs.append(values)
        if tag == "script" and values.get("src", "").startswith("/static/"):
            self.scripts.append(values["src"].split("?", 1)[0].rsplit("/", 1)[-1])


def _page(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:300]
    fields = _Fields()
    fields.feed(response.text)
    assert not fields.blocking
    for script in fields.scripts:
        assert not _BLOCKING_SCRIPT.search((_STATIC / script).read_text(encoding="utf-8")), script
    assert "captcha" not in response.text.lower()
    return {field.get("name"): field for field in fields.inputs if field.get("name")}


def _stage(client, make_staff, stage):
    staff_id = make_staff()
    client.cookies.set(auth.SESSION_COOKIE, security.sign_session(
        {"sid": staff_id, "stage": stage, "role": "owner", "ep": 0}, security.session_key()))


def test_setup_and_sign_in_name_the_account_and_passwords(client, make_staff):
    setup = _page(client, "/setup")
    assert setup["email"]["autocomplete"] == "username" and setup["email"]["type"] == "email"
    assert setup["password"]["autocomplete"] == "new-password"
    make_staff()
    login = _page(client, "/login")
    assert login["email"]["autocomplete"] == "username" and login["email"]["type"] == "email"
    assert login["password"]["autocomplete"] == "current-password"
    assert login["password"]["type"] == "password"


@pytest.mark.parametrize(("path", "stage"), [("/totp", "totp"), ("/enroll-totp", "enroll")])
def test_two_step_codes_are_one_time_codes(client, make_staff, path, stage):
    _stage(client, make_staff, stage)
    code = _page(client, path)["code"]
    assert code["autocomplete"] == "one-time-code"
    assert code["inputmode"] == "numeric"


def test_password_change_and_welcome_link_save_the_new_password(admin, app):
    change = _page(admin, "/account/password")
    assert change["username"]["autocomplete"] == "username"
    assert change["current"]["autocomplete"] == "current-password"
    assert change["password"]["autocomplete"] == change["confirm"]["autocomplete"] == "new-password"
    added = admin.post("/team", data={"email": "lee@daybreak.co", "name": "Lee", "role": "support"})
    token = re.search(r"/welcome/([A-Za-z0-9_\-]+)", added.text)[1]
    from fastapi.testclient import TestClient

    welcome = _page(TestClient(app), f"/welcome/{token}")
    assert welcome["username"]["autocomplete"] == "username"
    assert welcome["username"]["value"] == "lee@daybreak.co"
    assert welcome["password"]["autocomplete"] == welcome["confirm"]["autocomplete"] == "new-password"
