"""Rendered console controls retain accessible names across auth and admin pages."""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser

import pytest
from vendor_console import auth, security, store, support


class _Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.labels: set[str] = set()
        self.ids: list[str] = []
        self.controls: list[dict] = []
        self.label_depth = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "label":
            self.label_depth += 1
            if values.get("for"):
                self.labels.add(values["for"])
        if tag in {"input", "select", "textarea"} and values.get("type") not in {
            "hidden", "submit", "button", "reset",
        }:
            self.controls.append({**values, "wrapped": self.label_depth > 0})

    def handle_endtag(self, tag):
        if tag == "label":
            self.label_depth -= 1


def _assert_named_controls(response):
    assert response.status_code == 200
    parsed = _Controls()
    parsed.feed(response.text)
    assert parsed.controls, "expected at least one form control"
    assert not [name for name, count in Counter(parsed.ids).items() if count > 1]
    assert parsed.labels <= set(parsed.ids)
    missing = [
        control.get("name", "unnamed") for control in parsed.controls
        if not (
            control["wrapped"] or control.get("id") in parsed.labels
            or control.get("aria-label") or control.get("aria-labelledby")
        )
    ]
    assert not missing, f"controls without accessible names: {missing}"


@pytest.mark.parametrize(
    ("path", "stage"),
    [("/setup", None), ("/login", None), ("/totp", "totp"), ("/enroll-totp", "enroll")],
)
def test_auth_form_controls_have_accessible_names(client, make_staff, path, stage):
    if path != "/setup":
        staff_id = make_staff()
        if stage:
            token = security.sign_session(
                {"sid": staff_id, "stage": stage}, security.session_key(),
            )
            client.cookies.set(auth.SESSION_COOKIE, token)
    _assert_named_controls(client.get(path))


@pytest.mark.parametrize("page", ["customers", "releases", "customer", "ticket"])
def test_admin_form_controls_have_accessible_names(admin, app, page):
    customer_id = store.create_customer(app.state.conn, name="UI review customer")
    if page == "customer":
        path = f"/customers/{customer_id}"
    elif page == "ticket":
        ticket = support.intake_bundle(
            app.state.conn, customer_id=customer_id,
            bundle={"correlation_id": "ui-review-ticket"},
        )
        path = f"/support/{ticket.id}"
    else:
        path = f"/{page}"
    _assert_named_controls(admin.get(path))
