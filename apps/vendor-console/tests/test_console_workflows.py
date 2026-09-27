"""Plain-language workflows: every console action is a button or selector,
destructive actions confirm first, and people never read developer wording.
Permission checks behind the new controls are pinned here too."""
from __future__ import annotations

import re

import pytest
from vendor_console import audit, releases, store, support

# Words staff should never have to read outside a collapsed "details" section.
_BANNED = [
    "tenant", "payload", "endpoint", "schema", "uuid", "sha-256", "sha256", "digest",
    "idempotency", "http ", "api_url", "maverick_", "correlation", "in_progress",
    "customer.create", "no such", "requires owner", "traceback", "posture", "yank",
    "à la carte", "2fa", " uri",
]


def _visible(html: str) -> str:
    """Text a person sees without opening any 'details' section."""
    body = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    while re.search(r"<details\b", body):
        body = re.sub(r"<details\b(?:(?!<details\b).)*?</details>", " ", body, flags=re.DOTALL)
    body = re.sub(r'\s(?:value|name|id|href|action|for|class|title|placeholder|aria-describedby|data-copy)="[^"]*"', " ", body)
    return re.sub(r"<[^>]+>", " ", body).lower()


def _assert_plain(html: str):
    text = _visible(html)
    found = [word for word in _BANNED if word in text]
    assert not found, f"developer wording on page: {found}"


def _as(client, make_staff, login_cookie, role, email=None):
    sid = make_staff(email=email or f"{role}@daybreak.co", role=role)
    client.cookies.update(login_cookie(sid, role))
    return sid


def _customer(admin, app, name="Cedar Valley Bank"):
    r = admin.post("/customers", data={"name": name, "contact_email": "c@cvb.example",
                                       "posture": "connected"})
    token = re.search(r"svc_[A-Za-z0-9_\-]+", r.text).group(0)
    return store.list_customers(app.state.conn, search=name)[0].id, token


# ---- every page reads plainly and offers its actions as buttons -------------

def test_pages_are_plain_and_offer_buttons(admin, app):
    conn = app.state.conn
    cid, _ = _customer(admin, app)
    admin.post(f"/customers/{cid}/licenses", data={"tier": "gold", "expires": "2030-01-01"})
    lic = store.list_licenses(conn, cid)[0]
    rel = releases.publish_release(conn, version="v0.1.7", channel="edge", min_from="",
                                   notes="n", migrations=[], artifacts=[], actor="t")
    store.record_checkin(conn, customer_id=cid, deployment_id="dep-1", version="v0.1.6",
                         license_status="release:edge")
    ticket = support.intake_bundle(conn, customer_id=cid, bundle={"correlation_id": "c-1"})
    pages = {
        "/customers": ["Add customer", "Search", "Open"],
        f"/customers/{cid}": ["Issue signed license", "Revoke", "Download license file",
                              "Save update channel", "Replace connection token",
                              "Save status", "Save details", "Copy key"],
        "/releases": ["Sign and publish", "Withdraw", "Download release record"],
        "/support": ["All", "In progress", "Waiting on customer"],
        f"/support/{ticket.id}": ["Save status", "Save assignment", "Add note"],
        "/fleet": ["Checked for Early access updates"],
        "/audit": ["Issued a license", "Log intact"],
        "/team": ["Add team member", "Owner"],
        f"/licenses/{lic.license_id}/revoke": ["Revoke license", "Cancel", "Reason"],
        f"/releases/{rel.id}/withdraw": ["Withdraw release", "Cancel", "Reason"],
        f"/customers/{cid}/token": ["Replace token", "Cancel"],
    }
    for path, expected in pages.items():
        r = admin.get(path)
        assert r.status_code == 200, path
        for text in expected:
            assert text in r.text, (path, text)
        _assert_plain(r.text)


def test_license_form_uses_pickers_not_typed_formats(admin, app):
    cid, _ = _customer(admin, app)
    html = admin.get(f"/customers/{cid}").text
    assert 'name="expires" type="date"' in html
    assert 'name="seats" type="number"' in html
    assert re.search(r'<select id="console-customer_detail-grace_days" name="grace_days"', html)
    assert "YYYY-MM-DD" not in html


def test_errors_read_plainly_with_raw_detail_tucked_away(admin):
    r = admin.get("/customers/9999")
    assert r.status_code == 404
    assert "We couldn" in r.text
    _assert_plain(r.text)
    assert "<summary>Technical details</summary>" in r.text


def test_viewer_sees_permission_message_not_role_codes(client, make_staff, login_cookie):
    _as(client, make_staff, login_cookie, "viewer")
    r = client.post("/customers", data={"name": "Nope"})
    assert r.status_code == 403
    assert "Only an owner or administrator can do this" in r.text
    _assert_plain(r.text)


# ---- destructive actions confirm first; the checks behind them stay ----------

def test_revoke_and_withdraw_are_links_to_confirmation_pages(admin, app):
    conn = app.state.conn
    cid, _ = _customer(admin, app)
    admin.post(f"/customers/{cid}/licenses", data={"tier": "basic"})
    lic = store.list_licenses(conn, cid)[0]
    html = admin.get(f"/customers/{cid}").text
    assert f'href="/licenses/{lic.license_id}/revoke"' in html
    assert "confirm(" not in html
    page = admin.get(f"/licenses/{lic.license_id}/revoke").text
    assert "can&#39;t be restored" in page
    assert not store.get_license(conn, lic.license_id).revoked      # GET changes nothing
    admin.post(f"/licenses/{lic.license_id}/revoke", data={"note": "contract ended"})
    assert store.get_license(conn, lic.license_id).revoked
    rel = releases.publish_release(conn, version="v0.2.0", channel="stable", min_from="",
                                   notes="", migrations=[], artifacts=[], actor="t")
    assert "confirm(" not in admin.get("/releases").text
    admin.get(f"/releases/{rel.id}/withdraw")
    assert not store.get_release(conn, rel.id).yanked
    admin.post(f"/releases/{rel.id}/yank", data={"reason": "bad build"})
    assert store.get_release(conn, rel.id).yanked
    assert any(e.action == "release.yank" for e in audit.recent(conn))


@pytest.mark.parametrize("path", ["/licenses/lic_x/revoke", "/releases/1/withdraw",
                                  "/customers/1/token", "/team/1/access"])
def test_confirmation_pages_keep_write_permission(client, make_staff, login_cookie, path):
    _as(client, make_staff, login_cookie, "viewer")
    assert client.get(path).status_code == 403


def test_replacing_the_connection_token_retires_the_old_one(admin, app):
    cid, old = _customer(admin, app)
    admin.post(f"/customers/{cid}/licenses", data={"tier": "gold"})
    r = admin.post(f"/customers/{cid}/token")
    assert r.status_code == 200 and "The old token no longer works" in r.text
    new = re.search(r"svc_[A-Za-z0-9_\-]+", r.text).group(0)
    assert new != old
    assert admin.get("/api/v1/license", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert admin.get("/api/v1/license", headers={"Authorization": f"Bearer {new}"}).status_code == 200
    assert any(e.action == "customer.token.rotate" for e in audit.recent(app.state.conn))


def test_viewer_cannot_replace_token_or_edit_customer(client, make_staff, login_cookie, app):
    cid = store.create_customer(app.state.conn, name="Read only")
    _as(client, make_staff, login_cookie, "viewer")
    assert client.post(f"/customers/{cid}/token").status_code == 403
    assert client.post(f"/customers/{cid}/status", data={"status": "active"}).status_code == 403
    assert client.post(f"/customers/{cid}/details", data={"posture": "vpc"}).status_code == 403
    assert store.get_customer(app.state.conn, cid).status == "trial"


# ---- customer details and status ---------------------------------------------

def test_admin_edits_customer_status_and_details(admin, app):
    conn = app.state.conn
    cid, _ = _customer(admin, app)
    r = admin.post(f"/customers/{cid}/status", data={"status": "suspended"})
    assert "Customer status saved." in r.text
    admin.post(f"/customers/{cid}/details", data={
        "primary_contact": "Dana", "contact_email": "dana@cvb.example",
        "posture": "airgapped", "notes": "Renewal in May"})
    cust = store.get_customer(conn, cid)
    assert (cust.status, cust.primary_contact, cust.posture, cust.notes) == (
        "suspended", "Dana", "airgapped", "Renewal in May")
    actions = [e.action for e in audit.recent(conn)]
    assert "customer.status" in actions and "customer.details" in actions
    assert admin.post(f"/customers/{cid}/status", data={"status": "deleted"}).status_code == 400
    assert admin.post(f"/customers/{cid}/details", data={"posture": "moon"}).status_code == 400
    listing = admin.get("/customers", params={"status": "suspended"}).text
    assert "Cedar Valley Bank" in listing
    assert "Cedar Valley Bank" not in admin.get("/customers", params={"status": "active"}).text.split("<table>")[1]


def test_seats_must_be_a_whole_number(admin, app):
    cid, _ = _customer(admin, app)
    r = admin.post(f"/customers/{cid}/licenses", data={"tier": "gold", "seats": "ten"})
    assert r.status_code == 400
    assert "whole number" in r.text
    assert store.list_licenses(app.state.conn, cid) == []


# ---- releases ----------------------------------------------------------------

def test_failed_publish_keeps_the_form_and_explains_plainly(admin, app):
    r = admin.post("/releases", data={"version": "v9.9.9", "channel": "beta",
                                      "notes": "keep me", "artifacts": ""})
    assert r.status_code == 400
    assert "The release wasn" in r.text and "keep me" in r.text
    _assert_plain(r.text)


def test_oldest_version_is_chosen_from_published_releases(admin, app):
    releases.publish_release(app.state.conn, version="v0.1.5", channel="stable", min_from="",
                             notes="", migrations=[], artifacts=[], actor="t")
    html = admin.get("/releases").text
    assert re.search(r'<select id="console-releases-min_from" name="min_from">\s*<option value="">Any version</option>\s*<option value="v0.1.5"', html)


# ---- support desk ------------------------------------------------------------

def test_ticket_assignee_is_picked_from_the_team(admin, app, make_staff):
    conn = app.state.conn
    make_staff(email="helper@daybreak.co", role="support")
    make_staff(email="watcher@daybreak.co", role="viewer")
    cid = store.create_customer(conn, name="Ticket customer")
    ticket = support.intake_bundle(conn, customer_id=cid, bundle={"correlation_id": "t-1"})
    html = admin.get(f"/support/{ticket.id}").text
    assert '<option value="helper@daybreak.co"' in html
    assert '<option value="watcher@daybreak.co"' not in html        # viewers can't work tickets
    admin.post(f"/support/{ticket.id}/assign", data={"assignee": "helper@daybreak.co",
                                                     "priority": "high"})
    assert store.get_ticket(conn, ticket.id).assignee == "helper@daybreak.co"
    assert admin.post(f"/support/{ticket.id}/assign",
                      data={"assignee": "", "priority": "whenever"}).status_code == 400


# ---- team --------------------------------------------------------------------

@pytest.mark.parametrize("role", ["admin", "support", "viewer"])
def test_only_owners_change_the_team(client, make_staff, login_cookie, app, role):
    target = make_staff(email="target@daybreak.co", role="viewer")
    _as(client, make_staff, login_cookie, role)
    assert client.post("/team", data={"email": "x@daybreak.co", "role": "owner"}).status_code == 403
    assert client.post(f"/team/{target}/link").status_code == 403
    assert client.get(f"/team/{target}/link").status_code == 403
    assert client.post(f"/team/{target}/role", data={"role": "owner"}).status_code == 403
    assert client.post(f"/team/{target}/access", data={"disabled": "1"}).status_code == 403
    assert store.get_staff(app.state.conn, target).role == "viewer"
    assert client.get("/team").status_code == (200 if role == "admin" else 403)


def test_turning_access_off_signs_the_person_out(admin, app, make_staff, login_cookie):
    from fastapi.testclient import TestClient
    other_id = make_staff(email="leaver@daybreak.co", role="admin")
    other = TestClient(app)
    other.cookies.update(login_cookie(other_id, "admin"))
    assert other.get("/customers").status_code == 200
    admin.post(f"/team/{other_id}/access", data={"disabled": "1"})
    assert store.get_staff(app.state.conn, other_id).disabled
    assert other.get("/customers", follow_redirects=False).status_code == 303   # to sign-in
    admin.post(f"/team/{other_id}/access", data={"disabled": "0"})
    assert not store.get_staff(app.state.conn, other_id).disabled
    actions = [e.action for e in audit.recent(app.state.conn)]
    assert "staff.disable" in actions and "staff.enable" in actions


def test_role_change_ends_sessions_and_is_audited(admin, app, make_staff, login_cookie):
    from fastapi.testclient import TestClient
    other_id = make_staff(email="promoted@daybreak.co", role="viewer")
    other = TestClient(app)
    other.cookies.update(login_cookie(other_id, "viewer"))
    admin.post(f"/team/{other_id}/role", data={"role": "admin"})
    assert store.get_staff(app.state.conn, other_id).role == "admin"
    assert other.get("/customers", follow_redirects=False).status_code == 303
    assert any(e.action == "staff.role" for e in audit.recent(app.state.conn))


def test_owner_cannot_lock_themselves_out_or_remove_the_last_owner(admin, app):
    me = store.get_staff_by_email(app.state.conn, "owner@daybreak.co")["id"]
    r = admin.post(f"/team/{me}/access", data={"disabled": "1"})
    assert r.status_code == 400 and "your own" in r.text
    assert admin.post(f"/team/{me}/role", data={"role": "viewer"}).status_code == 400
    assert not store.get_staff(app.state.conn, me).disabled

