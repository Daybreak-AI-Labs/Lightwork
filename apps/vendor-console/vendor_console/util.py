"""Small request helpers shared across route modules."""
from __future__ import annotations

import hashlib
import os

from fastapi import Request


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def is_https(request: Request) -> bool:
    return (os.environ.get("VENDOR_CONSOLE_HTTPS", "").lower() in ("1", "true", "yes")
            or request.url.scheme == "https")
