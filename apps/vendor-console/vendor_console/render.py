"""Shared Jinja templates + the ``render`` helper (module-level so route modules
can import it without a per-app closure)."""
from __future__ import annotations

import datetime as _dt
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from . import labels

_HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _fmt_ts(value) -> str:
    """Jinja filter: epoch seconds → 'D Mon YYYY, HH:MM UTC' (readable)."""
    try:
        dt = _dt.datetime.fromtimestamp(float(value), _dt.timezone.utc)
        return f"{dt.day} {dt:%b %Y, %H:%M} UTC"   # no %-d: not portable to Windows
    except (TypeError, ValueError):
        return "—" if value in (None, "") else str(value)


def _fmt_date(value) -> str:
    """Jinja filter: an ISO date/time string → 'D Mon YYYY' ('—' when blank)."""
    if not value:
        return "—"
    try:
        dt = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return f"{dt.day} {dt:%b %Y}"
    except ValueError:
        return str(value)[:10]


templates.env.filters["ts"] = _fmt_ts
templates.env.filters["date"] = _fmt_date
templates.env.filters.update(labels.FILTERS)


def render(request: Request, name: str, ctx: dict) -> HTMLResponse:
    # Starlette's current signature: TemplateResponse(request, name, context);
    # it injects `request` into the context itself.
    return templates.TemplateResponse(request, name, ctx)
