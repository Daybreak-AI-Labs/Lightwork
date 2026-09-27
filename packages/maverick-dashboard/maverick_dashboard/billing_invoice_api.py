"""Make a workspace invoice from the Billing page (``maverick billing invoice``).

An administrator picks a workspace and a date range, optionally a markup and a
minimum charge; the kernel's ``billing.generate_invoice`` rates that
workspace's recorded usage. The result comes back as a readable summary, or as
a spreadsheet file. Read-only: nothing is charged or stored. Global admin only.
"""
from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from .auth import require_global_permission

router = APIRouter(prefix="/api/v1/admin/billing")


def _invoice(tenant: str, since: date | None, until: date | None, markup: float, minimum: float):
    from maverick.billing import RateCard, generate_invoice
    from maverick.tenant.registry import get_tenant, list_tenants
    tenant = tenant.strip()
    if list_tenants() and get_tenant(tenant) is None:
        raise HTTPException(404, "Choose a workspace from the list.")
    if since and until and since > until:
        raise HTTPException(422, "The start date must be on or before the end date.")
    return generate_invoice(
        tenant or None, RateCard(markup_pct=markup, minimum_charge=minimum),
        since=since.isoformat() if since else None, until=until.isoformat() if until else None)


def _who(principal: str) -> str:
    from .identity_format import humanize_principal
    ident = humanize_principal(principal)
    return ident.name + (f" ({ident.detail})" if ident.detail and ident.detail != "Email" else "")


@router.get("/invoice")
async def invoice_summary(request: Request, tenant: str = Query(..., min_length=1, max_length=128),
                          since: date | None = Query(None), until: date | None = Query(None),
                          markup: float = Query(0.0, ge=0.0, le=1000.0),
                          minimum: float = Query(0.0, ge=0.0, le=1_000_000.0)) -> JSONResponse:
    require_global_permission(request, "admin")
    inv = await run_in_threadpool(_invoice, tenant, since, until, markup, minimum)
    return JSONResponse({
        "period_start": inv.period_start or "", "period_end": inv.period_end or "",
        "total": round(inv.total, 2), "subtotal": round(inv.subtotal, 2), "currency": inv.currency,
        "lines": [{"day": li.day, "person": _who(li.principal), "charge": round(li.charge, 2),
                   "usage": li.in_tokens + li.out_tokens} for li in inv.line_items],
        "closed_period": bool(inv.invoice_id),
    }, headers={"Cache-Control": "private, no-store"})


@router.get("/invoice.csv")
async def invoice_csv(request: Request, tenant: str = Query(..., min_length=1, max_length=128),
                      since: date | None = Query(None), until: date | None = Query(None),
                      markup: float = Query(0.0, ge=0.0, le=1000.0),
                      minimum: float = Query(0.0, ge=0.0, le=1_000_000.0)) -> Response:
    require_global_permission(request, "admin")
    inv = await run_in_threadpool(_invoice, tenant, since, until, markup, minimum)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Day", "Person", "Charge (" + inv.currency + ")", "AI usage (tokens)"])
    for li in inv.line_items:
        writer.writerow([li.day, _who(li.principal), f"{li.charge:.2f}", li.in_tokens + li.out_tokens])
    writer.writerow([])
    writer.writerow(["Total", "", f"{inv.total:.2f}", ""])
    if inv.invoice_id:
        writer.writerow(["Invoice number", inv.invoice_id, "", ""])
    safe = "".join(ch for ch in tenant if ch.isalnum() or ch in "-_")[:64] or "workspace"
    span = "-".join(p for p in (since.isoformat() if since else "", until.isoformat() if until else "") if p)
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="Lightwork-invoice-{safe}{("-" + span) if span else ""}.csv"',
                             "Cache-Control": "private, no-store"})
