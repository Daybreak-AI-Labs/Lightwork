"""Local customer-facing CRA Product Security Officer web app."""
from __future__ import annotations

from pathlib import Path

import backend
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from request_limits import (
    PayloadError,
    read_bounded_json,
    reject_sensitive_fields,
    require_expected_revision,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI(
    title="Lightwork CRA Product Security Officer",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]"],
)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@app.middleware("http")
async def browser_security_headers(request: Request, call_next):
    """Protect local human-decision forms from framing and remote scripts."""
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; form-action 'self'; "
        "base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


async def _body(request: Request, allowed: set[str]) -> tuple[dict, int]:
    try:
        body = await read_bounded_json(request)
        reject_sensitive_fields(body)
        revision = require_expected_revision(body)
    except PayloadError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    unexpected = set(body).difference(allowed | {"expected_revision"})
    if unexpected:
        raise HTTPException(422, f"unsupported fields: {sorted(unexpected)}")
    return body, revision


def _result(call):
    try:
        return call()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        status = 409 if "revision changed" in str(exc) else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    records = backend.list_records()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "caps": backend.caps_summary(),
            "records": records[-30:],
            "revision": backend.current_revision(),
            "products": [row for row in records if row.get("type") == "product"],
            "releases": [row for row in records if row.get("type") == "release"],
            "cases": [row for row in records if row.get("type") == "cra_case"],
        },
    )


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(
        request, "about.html", {"caps": backend.caps_summary()}
    )


@app.get("/api/records")
def records():
    return {
        "revision": backend.current_revision(),
        "records": backend.list_records(),
        "authority": "unsigned-local",
    }


@app.post("/api/products")
async def create_product(request: Request):
    body, revision = await _body(
        request, {"name", "manufacturer", "support_end_date"}
    )
    return _result(
        lambda: backend.create_product(
            body.get("name"),
            body.get("manufacturer"),
            body.get("support_end_date"),
            revision,
        )
    )


@app.post("/api/products/{product_id}/applicability")
async def decide_product(product_id: str, request: Request):
    body, revision = await _body(request, {"decision", "reviewer", "rationale"})
    return _result(
        lambda: backend.decide_product(
            product_id,
            body.get("decision"),
            body.get("reviewer"),
            body.get("rationale"),
            revision,
        )
    )


@app.post("/api/releases")
async def create_release(request: Request):
    body, revision = await _body(
        request, {"product_id", "version", "released_at", "sbom"}
    )
    return _result(
        lambda: backend.create_release(
            body.get("product_id"),
            body.get("version"),
            body.get("released_at"),
            body.get("sbom"),
            revision,
        )
    )


@app.post("/api/cases")
async def open_case(request: Request):
    body, revision = await _body(
        request,
        {"product_id", "release_id", "case_type", "awareness_at", "title"},
    )
    return _result(
        lambda: backend.open_case(
            body.get("product_id"),
            body.get("release_id"),
            body.get("case_type"),
            body.get("awareness_at"),
            body.get("title"),
            revision,
        )
    )


@app.post("/api/cases/{case_id}/reportability")
async def decide_case(case_id: str, request: Request):
    body, revision = await _body(request, {"decision", "reviewer", "rationale"})
    return _result(
        lambda: backend.decide_case(
            case_id,
            body.get("decision"),
            body.get("reviewer"),
            body.get("rationale"),
            revision,
        )
    )


@app.post("/api/cases/{case_id}/anchors")
async def record_anchor(case_id: str, request: Request):
    body, revision = await _body(request, {"anchor", "occurred_at", "actor"})
    return _result(
        lambda: backend.record_case_anchor(
            case_id,
            body.get("anchor"),
            body.get("occurred_at"),
            body.get("actor"),
            revision,
        )
    )


@app.post("/api/vex")
async def create_vex(request: Request):
    body, revision = await _body(
        request,
        {
            "product_id",
            "release_id",
            "vulnerability_id",
            "status",
            "justification",
            "author",
        },
    )
    return _result(
        lambda: backend.create_vex(
            body.get("product_id"),
            body.get("release_id"),
            body.get("vulnerability_id"),
            body.get("status"),
            body.get("justification"),
            body.get("author"),
            revision,
        )
    )


@app.post("/api/reports")
async def create_report(request: Request):
    body, revision = await _body(
        request, {"case_id", "stage", "summary", "facts", "author"}
    )
    return _result(
        lambda: backend.create_report(
            body.get("case_id"),
            body.get("stage"),
            body.get("summary"),
            body.get("facts", {}),
            body.get("author"),
            revision,
        )
    )


@app.post("/api/reports/{report_id}/decision")
async def decide_report(report_id: str, request: Request):
    body, revision = await _body(
        request, {"decision", "reviewer", "rationale", "packet_sha256"}
    )
    return _result(
        lambda: backend.decide_report(
            report_id,
            body.get("decision"),
            body.get("reviewer"),
            body.get("rationale"),
            body.get("packet_sha256"),
            revision,
        )
    )


@app.post("/api/reports/{report_id}/receipts")
async def record_receipt(report_id: str, request: Request):
    body, revision = await _body(
        request,
        {"packet_sha256", "receipt_reference", "submitted_at", "recorded_by"},
    )
    return _result(
        lambda: backend.record_receipt(
            report_id,
            body.get("packet_sha256"),
            body.get("receipt_reference"),
            body.get("submitted_at"),
            body.get("recorded_by"),
            revision,
        )
    )


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "product": "cra-product-security-officer",
        "authority": "unsigned-local",
        "automatic_legal_decisions": False,
        "network_regulator_filing": False,
    }
