"""Customer-facing Agent Trust Auditor standalone application."""
from __future__ import annotations

from pathlib import Path

import backend
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from request_limits import (
    PayloadError,
    read_bounded_json,
    require_expected_revision,
    validate_analysis_payload,
)
from starlette.middleware.trustedhost import TrustedHostMiddleware

app = FastAPI(
    title="Agent Trust Auditor",
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
    """Keep the loopback UI out of hostile frames and external script supply chains."""
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


async def _body(request: Request) -> dict:
    try:
        return await read_bounded_json(request)
    except PayloadError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    state = backend.state()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"records": state["records"][-20:], "revision": state["revision"]},
    )


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return templates.TemplateResponse(
        request,
        "about.html",
        {"caps": backend.caps_summary()},
    )


@app.get("/api/findings")
def findings():
    return backend.state()


@app.post("/api/analyze")
async def analyze(request: Request):
    body = await _body(request)
    try:
        expected_revision = require_expected_revision(body)
        declared_assets, observations = validate_analysis_payload(body)
        rows = backend.analyze(
            declared_assets,
            observations,
            now=body.get("now"),
            stale_after_seconds=body.get(
                "stale_after_seconds",
                7 * 24 * 60 * 60,
            ),
        )
        saved = backend.save_derived(rows, expected_revision)
    except PayloadError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
    except (TypeError, ValueError) as exc:
        status = 409 if "revision changed" in str(exc) else 422
        return JSONResponse({"detail": str(exc)}, status_code=status)
    state = backend.state()
    return {
        "findings": saved,
        "revision": state["revision"],
        "declared_assets_persisted": False,
        "observations_persisted": False,
        "authority_mutated": False,
    }


@app.get("/healthz")
def healthz():
    return {"ok": True, "product": "agent-trust-auditor"}
