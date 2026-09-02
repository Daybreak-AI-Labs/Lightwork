"""Operator page for Cortex, the company brain (Observe -> Cortex).

One page, four panels: the lattice (the brain as a figure), what each memory
source holds with the encryption and isolation posture an auditor asks
about, a recall console driven by ``/api/v1/cortex``, and the last reads and
writes from the audit tail. The tail is the same one the ``/audit`` page
renders, so it stays behind the same ``audit`` permission; the rest of the
page is at the registry's ``view`` floor.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from maverick.cortex import SOURCES
from starlette.concurrency import run_in_threadpool

from .auth import has_permission, require_permission
from .cortex_api import DEFAULT_K, MAX_K, MAX_QUERY

PAGE_PATH = "/cortex"
API_BASE = "/api/v1/cortex"
ASSET_PATH = "/static/lightwork-cortex.js"
#: How far back the recent-activity panel looks on the day's tail, and how
#: many Cortex rows it keeps (newest first).
_TAIL_N = 1000
_RECENT_N = 25

_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Security-Policy": (
        "default-src 'none'; base-uri 'none'; connect-src 'self'; "
        "font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
        "img-src 'self'; manifest-src 'none'; media-src 'none'; "
        "object-src 'none'; script-src 'self'; style-src 'self'; worker-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        "serial=(), bluetooth=(), clipboard-read=(), clipboard-write=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}

#: What each source is, in the order Cortex consults them.
_SOURCE_COPY: dict[str, tuple[str, str]] = {
    "knowledge": ("Knowledge", "The tenant's documents and data, retrieved with citations."),
    "skills": ("Skills", "The recipes the workforce learned on this operation."),
    "lessons": ("Lessons", "Guidance mined from failures and proven on held-out work."),
    "record": ("Operating Record", "Every decision and sign-off: who decided, what, and when."),
    "fleet": ("Fleet memory",
              "What agents you didn't build deposited, roster-gated and Shield-scanned."),
}


def _status() -> dict[str, Any]:
    """``cortex.status()`` over the dashboard's cached world model."""
    from maverick import cortex

    from ._shared import _world

    try:
        world = _world()
    except Exception:  # the record source then opens, or reports, its own
        world = None
    return cortex.status(world=world)


def _safe_status() -> dict[str, Any]:
    """The kernel status, or a degraded envelope: a page that says why the
    brain is unreachable beats a 500 that says nothing."""
    try:
        return _status()
    except Exception as exc:
        return {"enabled": False, "tenant": None, "sources": {},
                "encryption": {}, "isolation": {}, "error": str(exc)}


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _card(name: str, info: dict[str, Any]) -> dict[str, Any]:
    """One "what it holds" card: label, blurb, availability and its counts."""
    label, blurb = _SOURCE_COPY.get(name, (name.title(), ""))
    error = info.get("error")
    counts: list[tuple[str, int]] = []
    collections: list[str] = []
    if name == "knowledge":
        available = bool(info.get("available"))
        collections = [str(c) for c in (info.get("collections") or [])]
        counts.append(("collections", len(collections)))
    elif name == "skills":
        available = True
        counts += [("skills", _int(info.get("count"))), ("verified", _int(info.get("verified")))]
    elif name == "lessons":
        available = bool(info.get("enabled"))
        counts += [("models", _int(info.get("models"))), ("lines", _int(info.get("lines")))]
    elif name == "record":
        available = True
        counts += [("entries", _int(info.get("entries"))), ("goals", _int(info.get("n_goals"))),
                   ("approvals", _int(info.get("n_approvals"))),
                   ("human decisions", _int(info.get("n_human_decisions")))]
    else:  # fleet
        available = bool(info.get("enabled"))
        ingested = info.get("ingested") or {}
        deposits = sum(_int(n) for by in ingested.values() if isinstance(by, dict)
                       for n in by.values())
        counts += [("agents", len(info.get("agents") or ())), ("deposits", deposits)]
    return {"name": name, "label": label, "blurb": blurb,
            "available": available and not error, "counts": counts,
            "collections": collections, "error": str(error) if error else ""}


def _cards(status: dict[str, Any]) -> list[dict[str, Any]]:
    sources = status.get("sources") or {}
    fallback = str(status.get("error") or "not reported")
    out = []
    for name in SOURCES:
        info = sources.get(name)
        out.append(_card(name, info if isinstance(info, dict) else {"error": fallback}))
    return out


def _audit_tail(n: int) -> list[dict[str, Any]]:
    """The day's audit tail, exactly as the ``/audit`` page reads it."""
    from maverick.audit import default_audit_log

    return default_audit_log().tail(n)


def _when(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return "—"


def _row(event: dict[str, Any]) -> dict[str, Any]:
    from maverick.audit.events import EventKind

    per_source = event.get("per_source")
    denied = event.get("denied")
    return {
        "kind": str(event.get("kind") or ""),
        "verb": "read" if event.get("kind") == EventKind.CORTEX_RECALL else "write",
        "when": _when(event.get("ts")),
        "caller": str(event.get("caller") or event.get("principal") or "—"),
        "external": bool(event.get("external")),
        "vendor": str(event.get("vendor") or ""),
        "tenant": str(event.get("tenant") or "—"),
        "per_source": ({str(k): _int(v) for k, v in per_source.items()}
                       if isinstance(per_source, dict) else {}),
        "denied": ({str(k): str(v) for k, v in denied.items()}
                   if isinstance(denied, dict) else {}),
        "hits": event.get("hits"),
        "took_ms": event.get("took_ms"),
        "deposit_kind": str(event.get("deposit_kind") or ""),
        "ok": event.get("ok"),
        "reason": str(event.get("reason") or ""),
    }


def _recent(n: int = _RECENT_N) -> list[dict[str, Any]]:
    """The last ``n`` Cortex reads and writes on the tail, newest first. A
    tail that will not read renders as empty rather than failing the page."""
    from maverick.audit.events import EventKind

    kinds = {EventKind.CORTEX_RECALL, EventKind.CORTEX_DEPOSIT}
    try:
        events = _audit_tail(_TAIL_N)
    except Exception:
        return []
    rows = [e for e in events if isinstance(e, dict) and e.get("kind") in kinds]
    return [_row(e) for e in reversed(rows[-n:])]


def create_cortex_page_router(templates: Jinja2Templates) -> APIRouter:
    if not isinstance(templates, Jinja2Templates):
        raise TypeError("cortex page requires a Jinja template authority")
    router = APIRouter(tags=["cortex-console"])

    @router.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def console(request: Request) -> HTMLResponse:
        # The registry floor: every role may see what the brain holds and ask
        # it questions (each read is audited). The audit tail is a separate
        # grant, exactly like /audit and /api/v1/audit/tail.
        require_permission(request, "view")
        if any(key not in {"theme", "density", "font", "lang"} for key in request.query_params):
            raise HTTPException(status_code=404, detail="cortex unavailable")
        can_audit = has_permission(request, "audit")
        status = await run_in_threadpool(_safe_status)
        recent = await run_in_threadpool(_recent) if can_audit else []
        cards = _cards(status)
        nonce = secrets.token_urlsafe(18)
        response = templates.TemplateResponse(
            request,
            "cortex.html",
            {
                "api_base": API_BASE, "page_path": PAGE_PATH, "csp_style_nonce": nonce,
                "status": status, "cards": cards, "sources": list(SOURCES),
                "answering": sum(1 for c in cards if c["available"]), "total": len(cards),
                "can_audit": can_audit, "recent": recent, "recent_n": _RECENT_N,
                "max_query": MAX_QUERY, "max_k": MAX_K, "default_k": DEFAULT_K,
            },
        )
        for name, value in _HEADERS.items():
            if name == "Content-Security-Policy":
                value = value.replace("style-src 'self'", f"style-src 'self' 'nonce-{nonce}'")
            response.headers[name] = value
        return response

    return router


__all__ = ["API_BASE", "ASSET_PATH", "PAGE_PATH", "create_cortex_page_router"]
