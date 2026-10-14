"""Narrow, self-authenticating readiness handshake for the desktop shell."""

from __future__ import annotations

import hmac
import os
import re
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

DESKTOP_READY_PATH = "/desktop/ready"
DESKTOP_CHALLENGE_HEADER = "X-Lightwork-Desktop-Challenge"
_ALLOWED_ORIGINS = frozenset({"tauri://localhost", "http://tauri.localhost"})
_INSTANCE_ID = secrets.token_urlsafe(18)
_CHALLENGE = re.compile(r"[0-9a-f]{32,128}\Z")

router = APIRouter(tags=["desktop"])


def _origin(request: Request) -> str:
    origin = request.headers.get("origin", "")
    if origin not in _ALLOWED_ORIGINS:
        # A browser outside the desktop trust boundary cannot use this route as
        # a Lightwork installation oracle.
        raise HTTPException(status_code=404, detail="not found")
    return origin


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Cache-Control": "no-store, private, max-age=0",
        "Pragma": "no-cache",
        "Vary": "Origin",
        "X-Content-Type-Options": "nosniff",
    }


@router.options(DESKTOP_READY_PATH, include_in_schema=False)
async def desktop_ready_preflight(request: Request) -> Response:
    """Permit only the desktop shell's one credentialed readiness request."""

    origin = _origin(request)
    requested_method = request.headers.get("access-control-request-method", "")
    requested_headers = {
        item.strip().lower()
        for item in request.headers.get("access-control-request-headers", "").split(",")
        if item.strip()
    }
    if requested_method != "GET" or requested_headers != {
        DESKTOP_CHALLENGE_HEADER.lower()
    }:
        raise HTTPException(status_code=404, detail="not found")
    return Response(
        status_code=204,
        headers={
            **_cors_headers(origin),
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": DESKTOP_CHALLENGE_HEADER,
            "Access-Control-Max-Age": "300",
        },
    )


@router.get(DESKTOP_READY_PATH, include_in_schema=False)
async def desktop_ready(request: Request) -> JSONResponse:
    """Prove possession of the desktop secret without transmitting the secret."""

    origin = _origin(request)
    expected = os.environ.get("MAVERICK_DESKTOP_TOKEN", "")
    challenge = request.headers.get(DESKTOP_CHALLENGE_HEADER, "")
    if not expected or not _CHALLENGE.fullmatch(challenge):
        raise HTTPException(status_code=404, detail="not found")

    proof = hmac.new(
        expected.encode("utf-8"),
        challenge.encode("ascii"),
        "sha256",
    ).hexdigest()

    from maverick import __version__

    return JSONResponse(
        {
            "schema": "lightwork.desktop-ready.v1",
            "product": "lightwork",
            "version": str(__version__),
            "instance": _INSTANCE_ID,
            "proof": proof,
        },
        headers=_cors_headers(origin),
    )


__all__ = ["DESKTOP_CHALLENGE_HEADER", "DESKTOP_READY_PATH", "router"]
