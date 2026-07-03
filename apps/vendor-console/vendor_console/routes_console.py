"""Console routes: customers, license lifecycle, fleet health, audit log."""
from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from starlette.exceptions import HTTPException

from . import audit, licensing, store
from .auth import require_staff, require_write
from .models import Staff
from .render import render
from .util import hash_token

router = APIRouter()


@router.get("/")
def home(_staff: Staff = Depends(require_staff)):
    return RedirectResponse("/customers", status_code=303)


@router.get("/customers")
def customers_list(request: Request, q: str = "", staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    return render(request, "customers.html",
                  {"staff": staff, "customers": store.list_customers(conn, search=q),
                   "q": q, "section": "customers"})


@router.post("/customers")
def customers_create(request: Request, name: str = Form(...),
                     primary_contact: str = Form(""), contact_email: str = Form(""),
                     posture: str = Form("connected"),
                     staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    if not name.strip():
        return RedirectResponse("/customers", status_code=303)
    token = "svc_" + secrets.token_urlsafe(24)
    cid = store.create_customer(conn, name=name.strip(), primary_contact=primary_contact,
                                contact_email=contact_email, posture=posture,
                                serve_token_hash=hash_token(token))
    audit.record(conn, actor=staff.email, action="customer.create",
                 target=name.strip(), detail={"posture": posture})
    return render(request, "serve_token.html",
                  {"staff": staff, "customer": store.get_customer(conn, cid),
                   "token": token, "section": "customers"})


@router.get("/customers/{customer_id}")
def customer_detail(request: Request, customer_id: int,
                    staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    cust = store.get_customer(conn, customer_id)
    if cust is None:
        raise HTTPException(status_code=404, detail="no such customer")
    rows = [{"lic": lic, "status": licensing.display_status(lic)}
            for lic in store.list_licenses(conn, customer_id)]
    checkins = [c for c in store.latest_checkins(conn) if c["customer_id"] == customer_id]
    return render(request, "customer_detail.html",
                  {"staff": staff, "customer": cust, "licenses": rows,
                   "checkin": checkins[0] if checkins else None,
                   "pubkey": licensing.public_key_hex(), "section": "customers"})


@router.post("/customers/{customer_id}/licenses")
def issue_license(request: Request, customer_id: int, tier: str = Form(...),
                  suites: str = Form(""), features: str = Form(""),
                  seats: str = Form(""), expires: str = Form(""),
                  grace_days: int = Form(14), staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    try:
        licensing.issue_license(
            conn, customer_id=customer_id, tier=tier,
            suites=[s.strip() for s in suites.split(",") if s.strip()],
            features=[f.strip() for f in features.split(",") if f.strip()],
            seats=int(seats) if seats.strip() else None,
            expires=expires, grace_days=grace_days, actor=staff.email)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return RedirectResponse(f"/customers/{customer_id}", status_code=303)


@router.post("/licenses/{license_id}/revoke")
def revoke_license(request: Request, license_id: str, note: str = Form(""),
                   staff: Staff = Depends(require_write)):
    conn = request.app.state.conn
    lic = store.get_license(conn, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="no such license")
    licensing.revoke_license(conn, license_id, actor=staff.email, note=note)
    return RedirectResponse(f"/customers/{lic.customer_id}", status_code=303)


@router.get("/licenses/{license_id}/download")
def download_license(request: Request, license_id: str,
                     _staff: Staff = Depends(require_staff)):
    conn = request.app.state.conn
    lic = store.get_license(conn, license_id)
    if lic is None:
        raise HTTPException(status_code=404, detail="no such license")
    body = json.dumps(lic.doc, indent=2) + "\n"
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="license-{license_id}.json"'})


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
