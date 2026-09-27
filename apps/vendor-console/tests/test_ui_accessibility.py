"""Rendered console controls retain accessible names across auth and admin pages."""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser

import pytest
from vendor_console import audit, auth, security, store, support


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


def test_audit_record_keeps_technical_detail_in_closed_disclosure(admin, app):
    audit.record(app.state.conn, actor="ui-review", action="customer.update",
                 target="preview", detail={"synthetic_internal_field": "review-only"})
    response = admin.get("/audit")
    assert response.status_code == 200
    assert "Updated customer details" in response.text
    assert '<details><summary>View details</summary>' in response.text
    assert "synthetic_internal_field" in response.text
    assert "<details open" not in response.text


def test_advanced_feature_controls_stay_in_the_license_form(admin, app):
    customer_id = store.create_customer(app.state.conn, name="UI review customer")
    response = admin.get(f"/customers/{customer_id}")
    assert response.status_code == 200
    form = response.text.split(f'action="/customers/{customer_id}/licenses"', 1)[1].split("</form>", 1)[0]
    assert "Advanced feature settings" in form
    assert 'name="features_extra"' in form
    assert "<details open" not in form


@pytest.mark.parametrize(("posture", "label"), [
    ("connected", "Connected to this console"),
    ("vpc", "Private cloud with controlled updates"),
    ("airgapped", "Offline with manual file delivery"),
])
def test_environment_labels_keep_original_form_values(admin, app, posture, label):
    customer_id = store.create_customer(
        app.state.conn, name="UI review customer", posture=posture,
    )
    listing = admin.get("/customers")
    detail = admin.get(f"/customers/{customer_id}")
    assert listing.status_code == detail.status_code == 200
    assert label in listing.text and label in detail.text
    assert f'<option value="{posture}">' in listing.text
    assert 'name="posture"' in listing.text


def test_feature_labels_preserve_license_checkbox_values(admin, app):
    customer_id = store.create_customer(app.state.conn, name="UI review customer")
    response = admin.get(f"/customers/{customer_id}")
    assert response.status_code == 200
    for name, label in [
        ("multi_tenant", "Multiple workspaces"),
        ("siem_export", "Security monitoring export"),
        ("fleet", "Connected workers"),
    ]:
        assert f'value="{name}"' in response.text
        assert f'title="{name}">{label}</span>' in response.text
