"""Console routes: customers, license lifecycle, fleet health, audit log."""
from __future__ import annotations

import datetime as _dt
import json
import re
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from . import audit, labels, licensing, releases, store, support
from .auth import require_staff, require_support, require_write
from .models import (
    CHANNELS,
    CUSTOMER_STATUSES,
    POSTURES,
    TICKET_PRIORITIES,
    TICKET_STATUSES,
    Staff,
)
from .render import render
from .util import hash_token

router = APIRouter()


def _customer(conn, customer_id: int):
    cust = store.get_customer(conn, customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="no such customer")
    return cust


def _today() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")


def _seats(raw: str) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit() or int(raw) < 1:
        raise HTTPException(status_code=400, detail="seats must be a whole number of 1 or more")
    return int(raw)


_SAVED = {
    "details": "Customer details saved.",
    "status": "Customer status saved.",
    "channel": "Update channel saved.",
    "license": "New signed license issued. Connected installations pick it up within about a minute.",
    "revoked": "License revoked.",
}


def _license_url(request: Request) -> str:
    """The full address a connected installation fetches its license from."""
    return str(request.base_url).rstrip("/") + "/api/v1/license"


def _parse_artifacts(raw: str) -> list[dict]:
    """Parse a textarea of ``name  sha256  size`` lines into artifact dicts.
    Blank lines and a leading header line are ignored; size defaults to 0."""
    out: list[dict] = []
    for line in (raw or "").splitlines():
        parts = line.replace(",", " ").split()
        if len(parts) < 2 or parts[0].lower() in ("name", "artifact"):
            continue
        size = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        out.append({"name": parts[0], "sha256": parts[1], "size": size})
    return out


def _csv(raw: str) -> list[str]:
    return [x.strip() for x in (raw or "").replace(",", "\n").splitlines() if x.strip()]


@router.get("/")
def home(_staff: Staff = Depends(require_staff)):
    return RedirectResponse("/customers", status_code=303)


@router.get("/customers")
def customers_list(request: Request, q: str = "", status: str = "",
                   staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    from .models import CUSTOMER_STATUSES
    rows = store.list_customers(conn, search=q)
    if status in CUSTOMER_STATUSES:
        rows = [c for c in rows if c.status == status]
    else:
        status = ""
    return render(request, "customers.html",
                  {"staff": staff, "customers": rows, "q": q, "status": status,
                   "statuses": CUSTOMER_STATUSES, "section": "customers"})


@router.post("/customers")
def customers_create(request: Request, name: str = Form(...),
                     primary_contact: str = Form(""), contact_email: str = Form(""),
                     posture: str = Form("connected"),
                     staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    if not name.strip():
        return RedirectResponse("/customers", status_code=303)
    if posture not in POSTURES:
        raise HTTPException(status_code=400, detail="invalid environment")
    token = "svc_" + secrets.token_urlsafe(24)
    cid = store.create_customer(conn, name=name.strip(), primary_contact=primary_contact,
                                contact_email=contact_email, posture=posture,
                                serve_token_hash=hash_token(token))
    audit.record(conn, actor=staff.email, action="customer.create",
                 target=name.strip(), detail={"posture": posture})
    return render(request, "serve_token.html",
                  {"staff": staff, "customer": store.get_customer(conn, cid),
                   "token": token, "section": "customers",
                   "license_url": _license_url(request)})


@router.get("/customers/{customer_id}")
def customer_detail(request: Request, customer_id: int, saved: str = "",
                    staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    cust = store.get_customer(conn, customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="no such customer")
    rows = [{"lic": lic, "status": licensing.display_status(lic)}
            for lic in store.list_licenses(conn, customer_id)]
    return render(request, "customer_detail.html",
                  {"staff": staff, "customer": cust, "licenses": rows,
                   "matrix": licensing.feature_matrix(
                       licensing.active_license_doc(conn, customer_id)),
                   "checkin": store.latest_checkin_for(conn, customer_id),
                   "statuses": CUSTOMER_STATUSES, "postures": POSTURES,
                   "notice": _SAVED.get(saved, ""),
                   "channels": CHANNELS, "today": _today(),
                   "pubkey": licensing.public_key_hex(), "section": "customers"})


@router.post("/customers/{customer_id}/details")
def set_customer_details(request: Request, customer_id: int,
                         primary_contact: str = Form(""), contact_email: str = Form(""),
                         posture: str = Form(...), notes: str = Form(""),
                         staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    cust = _customer(conn, customer_id)
    if posture not in POSTURES:
        raise HTTPException(status_code=400, detail="invalid environment")
    store.update_customer(conn, customer_id, primary_contact=primary_contact.strip(),
                          contact_email=contact_email.strip(), posture=posture,
                          notes=notes.strip())
    audit.record(conn, actor=staff.email, action="customer.details", target=cust.name,
                 detail={"posture": posture})
    return RedirectResponse(f"/customers/{customer_id}?saved=details", status_code=303)


@router.post("/customers/{customer_id}/status")
def set_customer_status(request: Request, customer_id: int, status: str = Form(...),
                        staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    cust = _customer(conn, customer_id)
    if status not in CUSTOMER_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    if status != cust.status:
        store.update_customer(conn, customer_id, status=status)
        audit.record(conn, actor=staff.email, action="customer.status", target=cust.name,
                     detail={"from": cust.status, "to": status})
    return RedirectResponse(f"/customers/{customer_id}?saved=status", status_code=303)


@router.get("/customers/{customer_id}/token")
def rotate_token_confirm(request: Request, customer_id: int,
                         staff: Staff = Depends(require_write)):
    cust = _customer(request.app.state.conn, customer_id)
    return render(request, "confirm.html", {
        "staff": staff, "section": "customers",
        "title": f"Replace {cust.name}'s connection token?",
        "consequence": ("The current token stops working right away. The customer's "
                        "installation won't receive license or update changes until its "
                        "administrator enters the new token, which is shown once on the next page."),
        "action": f"/customers/{customer_id}/token", "hidden": {},
        "button": "Replace token", "danger": True,
        "cancel": f"/customers/{customer_id}"})


@router.post("/customers/{customer_id}/token")
def rotate_token(request: Request, customer_id: int, staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    cust = _customer(conn, customer_id)
    token = "svc_" + secrets.token_urlsafe(24)
    store.update_customer(conn, customer_id, serve_token_hash=hash_token(token))
    audit.record(conn, actor=staff.email, action="customer.token.rotate", target=cust.name)
    return render(request, "serve_token.html",
                  {"staff": staff, "customer": store.get_customer(conn, customer_id),
                   "token": token, "rotated": True, "section": "customers",
                   "license_url": _license_url(request)})


@router.post("/customers/{customer_id}/channel")
def set_customer_channel(request: Request, customer_id: int, channel: str = Form(...),
                         staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    if channel in CHANNELS:
        store.update_customer(conn, customer_id, channel=channel)
        audit.record(conn, actor=staff.email, action="customer.channel",
                     target=str(customer_id), detail={"channel": channel})
    return RedirectResponse(f"/customers/{customer_id}?saved=channel", status_code=303)


@router.post("/customers/{customer_id}/licenses")
def issue_license(request: Request, customer_id: int, tier: str = Form(...),
                  suites_on: list[str] = Form([]), features_on: list[str] = Form([]),
                  features_extra: str = Form(""),
                  seats: str = Form(""), expires: str = Form(""),
                  grace_days: int = Form(14), staff: Staff = Depends(require_write)):
    """Issue/upsell from the feature-access checkboxes: ``features_on`` are the
    checked registry features (grants below tier / denials for unchecked
    in-tier ones are derived), ``suites_on`` the checked add-on suites, and
    ``features_extra`` a comma-separated escape hatch for custom grants."""
    conn = request.app.state.conn
    from maverick.entitlements import GATED_SUITES
    extra = [f.strip() for f in features_extra.replace(",", "\n").splitlines()
             if f.strip()]
    features, denied = licensing.grants_from_checkboxes(tier, features_on, extra)
    try:
        licensing.issue_license(
            conn, customer_id=customer_id, tier=tier,
            suites=[s for s in suites_on if s in GATED_SUITES],
            features=features, features_denied=denied,
            seats=_seats(seats),
            expires=expires, grace_days=grace_days, actor=staff.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RedirectResponse(f"/customers/{customer_id}?saved=license", status_code=303)


@router.get("/licenses/{license_id}/revoke")
def revoke_license_confirm(request: Request, license_id: str,
                           staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    lic = store.get_license(conn, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="no such license")
    cust = _customer(conn, lic.customer_id)
    return render(request, "confirm.html", {
        "staff": staff, "section": "customers",
        "title": f"Revoke this {labels.tier(lic.tier)} license for {cust.name}?",
        "consequence": ("A revoked license can't be restored. Connected installations "
                        "stop receiving it on their next check-in, and if it is the only "
                        "license the customer loses access to licensed features. To change "
                        "what they can use instead, issue a new license from their page."),
        "action": f"/licenses/{license_id}/revoke", "hidden": {},
        "reason": {"name": "note", "label": "Reason (kept in the audit log)"},
        "button": "Revoke license", "danger": True,
        "cancel": f"/customers/{lic.customer_id}"})


@router.post("/licenses/{license_id}/revoke")
def revoke_license(request: Request, license_id: str, note: str = Form(""),
                   staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    lic = store.get_license(conn, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="no such license")
    licensing.revoke_license(conn, license_id, actor=staff.email, note=note)
    return RedirectResponse(f"/customers/{lic.customer_id}?saved=revoked", status_code=303)


@router.get("/licenses/{license_id}/download")
def download_license(request: Request, license_id: str,
                     _staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    lic = store.get_license(conn, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="no such license")
    body = json.dumps(lic.doc, indent=2) + "\n"
    # license_id is server-generated (lic_<hex>), but sanitize before it lands in
    # a response header regardless — never let a lookup key shape headers.
    safe = re.sub(r"[^A-Za-z0-9_-]", "", license_id)[:40]
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="license-{safe}.json"'})


@router.get("/fleet")
def fleet(request: Request, staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    return render(request, "fleet.html",
                  {"staff": staff, "rows": store.latest_checkins(conn),
                   "section": "fleet"})


@router.get("/audit")
def audit_view(request: Request, staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    ok, broken = audit.verify_chain(conn)
    return render(request, "audit.html",
                  {"staff": staff, "events": audit.recent(conn),
                   "chain_ok": ok, "broken": broken, "section": "audit"})


# ---- releases --------------------------------------------------------------

@router.get("/releases")
def releases_list(request: Request, staff: Staff = Depends(require_staff)):
    return _releases_page(request, staff)


def _releases_page(request: Request, staff: Staff, *, error: str = "", detail: str = "",
                   form: dict | None = None, status_code: int = 200):
    conn = request.app.state.conn
    rows = store.list_releases(conn)
    resp = render(request, "releases.html",
                  {"staff": staff, "releases": rows, "channels": CHANNELS,
                   "versions": sorted({r.version for r in rows if not r.yanked}, reverse=True),
                   "error": error, "detail": detail, "form": form or {},
                   "section": "releases"})
    resp.status_code = status_code
    return resp


@router.post("/releases")
def releases_publish(request: Request, version: str = Form(...),
                     channel: str = Form("stable"), min_from: str = Form(""),
                     notes: str = Form(""), migrations: str = Form(""),
                     artifacts: str = Form(""), staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    try:
        releases.publish_release(
            conn, version=version.strip(), channel=channel, min_from=min_from.strip(),
            notes=notes, migrations=_csv(migrations),
            artifacts=_parse_artifacts(artifacts), actor=staff.email)
    except Exception as e:  # noqa: BLE001 - bad version/sha/etc → surface a 400
        return _releases_page(
            request, staff, status_code=400, detail=f"publish failed: {e}",
            form={"version": version, "channel": channel, "min_from": min_from,
                  "notes": notes, "migrations": migrations, "artifacts": artifacts},
            error=("The release wasn't published. Check the version (for example v0.1.7) and "
                   "that each file line has a name, a 64-character checksum and a size."))
    return RedirectResponse("/releases", status_code=303)


@router.get("/releases/{release_id}/withdraw")
def releases_withdraw_confirm(request: Request, release_id: int,
                              staff: Staff = Depends(require_write)):
    rel = store.get_release(request.app.state.conn, release_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="no such release")
    return render(request, "confirm.html", {
        "staff": staff, "section": "releases",
        "title": f"Withdraw {rel.version} from {labels.channel(rel.channel)}?",
        "consequence": ("Customer installations will no longer be offered this release. "
                        "Installations that already updated keep running it. The previous "
                        "release on this channel is offered again."),
        "action": f"/releases/{release_id}/yank", "hidden": {},
        "reason": {"name": "reason", "label": "Reason (kept in the audit log)"},
        "button": "Withdraw release", "danger": True, "cancel": "/releases"})


@router.post("/releases/{release_id}/yank")
def releases_yank(request: Request, release_id: int, reason: str = Form(""),
                  staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    releases.yank_release(conn, release_id, actor=staff.email, reason=reason)
    return RedirectResponse("/releases", status_code=303)


@router.get("/releases/{release_id}/manifest")
def releases_manifest(request: Request, release_id: int,
                      _staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    rel = store.get_release(conn, release_id)
    if rel is None:
        raise HTTPException(status_code=404, detail="no such release")
    body = json.dumps(rel.manifest, indent=2) + "\n"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", rel.version)[:40]
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="manifest-{safe}.json"'})


# ---- support desk ----------------------------------------------------------

@router.get("/support")
def tickets_list(request: Request, status: str = "",
                 staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    return render(request, "tickets.html",
                  {"staff": staff, "tickets": store.list_tickets(conn, status=status),
                   "counts": store.ticket_status_counts(conn), "status": status,
                   "statuses": TICKET_STATUSES,
                   "intake_conflicts": store.intake_conflicts(conn),
                   "customer_names": {c.id: c.name for c in store.list_customers(conn)},
                   "section": "support"})


@router.get("/support/{ticket_id}")
def ticket_detail(request: Request, ticket_id: int,
                  staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    tkt = store.get_ticket(conn, ticket_id)
    if tkt is None:
        raise HTTPException(status_code=404, detail="no such ticket")
    cust = store.get_customer(conn, tkt.customer_id) if tkt.customer_id else None
    return render(request, "ticket_detail.html",
                  {"staff": staff, "ticket": tkt, "customer": cust,
                   "comments": store.list_comments(conn, ticket_id),
                   "statuses": TICKET_STATUSES, "priorities": TICKET_PRIORITIES,
                   "assignees": [m for m in store.list_staff(conn)
                                 if m.can_support and not m.disabled],
                   "section": "support"})


@router.post("/support/{ticket_id}/comment")
def ticket_comment(request: Request, ticket_id: int, body: str = Form(...),
                   staff: Staff = Depends(require_support)):
    conn = request.app.state.conn
    if body.strip():
        store.add_comment(conn, ticket_id=ticket_id, author=staff.email,
                          body=body.strip())
    return RedirectResponse(f"/support/{ticket_id}", status_code=303)


@router.post("/support/{ticket_id}/status")
def ticket_set_status(request: Request, ticket_id: int, status: str = Form(...),
                      note: str = Form(""), staff: Staff = Depends(require_support)):
    conn = request.app.state.conn
    if status not in TICKET_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    try:
        support.set_status(conn, ticket_id, status, actor=staff.email, note=note)
    except store.SupportIntakeConflict as exc:
        raise HTTPException(status_code=409, detail="another active ticket owns this intake identity") from exc
    return RedirectResponse(f"/support/{ticket_id}", status_code=303)


@router.post("/support/{ticket_id}/assign")
def ticket_assign(request: Request, ticket_id: int, assignee: str = Form(""),
                  priority: str = Form(""), staff: Staff = Depends(require_support)):
    conn = request.app.state.conn
    fields: dict = {"assignee": assignee.strip()}   # blank clears the assignee
    if priority.strip():
        if priority.strip() not in TICKET_PRIORITIES:
            raise HTTPException(status_code=400, detail="invalid priority")
        fields["priority"] = priority.strip()
    store.update_ticket(conn, ticket_id, **fields)
    audit.record(conn, actor=staff.email, action="ticket.assign", target=str(ticket_id),
                 detail=fields)
    return RedirectResponse(f"/support/{ticket_id}", status_code=303)
