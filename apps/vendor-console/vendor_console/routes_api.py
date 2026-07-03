"""Machine routes: health + the license serve API a connected deployment polls
(``refresh_from_server()`` consumes this)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException

from . import audit, licensing, store
from .util import client_ip, hash_token

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request) -> PlainTextResponse:
    ok, broken = audit.verify_chain(request.app.state.conn)
    return PlainTextResponse("ok" if ok else f"audit-chain-broken:{broken}",
                             status_code=200 if ok else 500)


@router.get("/api/v1/license")
def serve_license(request: Request):
    conn = request.app.state.conn
    # Header-only: never accept the serve token in the query string, where it
    # would leak into proxy/access logs, browser history, and Referer.
    authz = request.headers.get("authorization", "")
    token = authz[7:].strip() if authz.lower().startswith("bearer ") else ""
    if not token:
        raise HTTPException(status_code=401, detail="missing serve token")
    cust = store.customer_by_serve_token_hash(conn, hash_token(token))
    if cust is None:
        raise HTTPException(status_code=401, detail="invalid serve token")
    doc = licensing.active_license_doc(conn, cust.id)
    store.record_checkin(
        conn, customer_id=cust.id,
        deployment_id=request.query_params.get("deployment", ""),
        version=request.query_params.get("version", ""),
        license_status="served" if doc else "none", ip=client_ip(request))
    if doc is None:
        raise HTTPException(status_code=404, detail="no active license")
    return JSONResponse(doc)
