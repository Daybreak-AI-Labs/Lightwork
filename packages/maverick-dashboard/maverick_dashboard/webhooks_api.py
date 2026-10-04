"""Webhook subscriptions, the delivery log and deadline alerts over the dashboard.

* ``GET/POST /api/v1/webhooks/subscriptions`` and
  ``GET/PATCH /api/v1/webhooks/subscriptions/{id}`` -- saved subscriptions
  (address, event filter, signing secret, on/off, description). Workspace
  ``admin``, like the signing-secret panel. The secret is never returned.
* ``POST /api/v1/webhooks/subscriptions/{id}/test`` -- send a ``webhook.test``
  event now and return the delivery row.
* ``GET /api/v1/webhooks/deliveries`` and
  ``POST /api/v1/webhooks/deliveries/{id}/retry`` -- the delivery log
  (attempts, status, latency, response snippet, next retry) and "Retry now".
* ``GET /api/v1/webhooks/events`` -- the event catalogue for the page.
* ``GET /api/v1/deadline-alerts`` (``operate``) -- alerts sent about one
  record (``record_type`` + ``record_id``) or the latest across the
  workspace; ``POST /api/v1/deadline-alerts/run`` (``admin``) runs one
  evaluation cycle now.
* ``GET /settings/webhooks/manage`` -- the Settings > Webhooks page.

Every change is written to the activity log by the kernel store before it
happens; a refused audit write changes nothing.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from ._shared import _world
from .auth import (
    caller_principal,
    has_global_permission,
    require_global_permission,
    require_permission,
)

router = APIRouter(tags=["webhooks"])
PAGE_PATH = "/settings/webhooks/manage"


def _actor(request: Request) -> str:
    return caller_principal(request) or "local administrator"


def _refused(exc: Exception) -> HTTPException:
    from maverick.audit import AuditRefused
    from maverick.webhook_subscriptions import SubscriptionError

    if isinstance(exc, SubscriptionError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AuditRefused):
        return HTTPException(status_code=503, detail=(
            "This could not be added to the activity log, so nothing was changed."))
    return HTTPException(status_code=500, detail="The webhook store is unavailable.")


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)
    events: list[str] | str = "*"
    secret: str = Field(default="", max_length=4096)
    description: str = Field(default="", max_length=500)
    enabled: bool = True


class SubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    events: list[str] | str | None = None
    secret: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=500)
    enabled: bool | None = None


@router.get("/api/v1/webhooks/events")
async def webhook_events(request: Request) -> dict[str, Any]:
    require_permission(request, "view")
    from maverick.webhook_events import catalog

    return {"events": catalog()}


@router.get("/api/v1/webhooks/subscriptions")
async def list_subscriptions(request: Request) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    try:
        rows = await run_in_threadpool(lambda: subs.list_subscriptions(world=_world()))
    except Exception as exc:
        raise _refused(exc) from exc
    from maverick.webhooks import _load_config_outbound

    urls, secret = _load_config_outbound()
    return {"subscriptions": rows,
            "legacy": {"urls": urls, "signed": bool(secret),
                       "subscription_id": subs.LEGACY_SUBSCRIPTION_ID},
            "retry_schedule_seconds": list(subs.RETRY_DELAYS),
            "max_attempts": subs.MAX_ATTEMPTS}


@router.post("/api/v1/webhooks/subscriptions", status_code=201)
async def create_subscription(request: Request, body: SubscriptionIn) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    try:
        return await run_in_threadpool(lambda: subs.create_subscription(
            body.url, events=body.events, secret=body.secret, description=body.description,
            enabled=body.enabled, actor=_actor(request), world=_world()))
    except Exception as exc:
        raise _refused(exc) from exc


@router.get("/api/v1/webhooks/subscriptions/{sub_id}")
async def get_subscription(request: Request, sub_id: str) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    row = await run_in_threadpool(lambda: subs.get_subscription(sub_id, world=_world()))
    if row is None:
        raise HTTPException(status_code=404, detail="That subscription could not be found.")
    return row


@router.patch("/api/v1/webhooks/subscriptions/{sub_id}")
async def update_subscription(request: Request, sub_id: str, body: SubscriptionPatch) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    try:
        row = await run_in_threadpool(lambda: subs.update_subscription(
            sub_id, url=body.url, events=body.events, secret=body.secret,
            description=body.description, enabled=body.enabled,
            actor=_actor(request), world=_world()))
    except Exception as exc:
        raise _refused(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="That subscription could not be found.")
    return row


@router.post("/api/v1/webhooks/subscriptions/{sub_id}/test")
async def test_subscription(request: Request, sub_id: str) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    try:
        row = await run_in_threadpool(lambda: subs.send_test_event(
            sub_id, actor=_actor(request), world=_world()))
    except Exception as exc:
        raise _refused(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="That subscription could not be found.")
    return {"delivery": row}


@router.get("/api/v1/webhooks/deliveries")
async def list_deliveries(
    request: Request,
    subscription_id: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    if status is not None and status not in subs.STATUSES:
        raise HTTPException(status_code=400, detail="status must be pending, delivered or dead")
    rows = await run_in_threadpool(lambda: subs.list_deliveries(
        world=_world(), subscription_id=subscription_id, status=status, limit=limit))
    for row in rows:
        row.pop("body", None)  # the page shows the event, not the whole payload
    return {"deliveries": rows}


@router.get("/api/v1/webhooks/deliveries/{delivery_id}")
async def get_delivery(request: Request, delivery_id: str) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    row = await run_in_threadpool(lambda: subs.get_delivery(delivery_id, world=_world()))
    if row is None:
        raise HTTPException(status_code=404, detail="That delivery could not be found.")
    return row


@router.post("/api/v1/webhooks/deliveries/{delivery_id}/retry")
async def retry_delivery(request: Request, delivery_id: str) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick import webhook_subscriptions as subs

    try:
        row = await run_in_threadpool(lambda: subs.retry_now(
            delivery_id, actor=_actor(request), world=_world()))
    except Exception as exc:
        raise _refused(exc) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="That delivery could not be found.")
    return {"delivery": row}


# ---- deadline alerts -------------------------------------------------------

@router.get("/api/v1/deadline-alerts")
async def deadline_alerts(
    request: Request,
    record_type: str | None = Query(default=None, max_length=64),
    record_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    require_permission(request, "operate")
    from maverick import deadline_alerts as alerts

    if bool(record_type) != bool(record_id):
        raise HTTPException(status_code=400, detail="record_type and record_id go together")
    if record_type:
        rows = await run_in_threadpool(lambda: alerts.alerts_for(
            record_type, record_id, world=_world(), limit=limit))
    else:
        rows = await run_in_threadpool(lambda: alerts.recent_alerts(world=_world(), limit=limit))
    from .deadline_alerts_scheduler import last_cycle

    return {"alerts": rows, "settings": alerts.settings(), "last_cycle": last_cycle()}


@router.post("/api/v1/deadline-alerts/run")
async def run_deadline_alerts(request: Request) -> dict[str, Any]:
    require_global_permission(request, "admin")
    from maverick.audit import AuditRefused, audit_event

    from .deadline_alerts_scheduler import run_once

    try:
        audit_event("control_plane_action", agent="dashboard", action="deadline_alerts_run_now",
                    actor=_actor(request))
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail=(
            "This could not be added to the activity log, so nothing was run.")) from exc
    summary = await run_in_threadpool(run_once)
    return summary


def record_alerts(record_type: str, record_id: str) -> list[dict]:
    """The "Alerts" section of a record page; empty (never an error) when the
    ledger is unavailable."""
    from maverick import deadline_alerts as alerts

    try:
        return alerts.alerts_for(record_type, record_id, world=_world())
    except Exception:
        return []


def create_webhooks_page_router(templates: Jinja2Templates) -> APIRouter:
    if not isinstance(templates, Jinja2Templates):
        raise TypeError("webhooks page requires a Jinja template authority")
    page = APIRouter(tags=["webhooks"])

    @page.get(PAGE_PATH, response_class=HTMLResponse, include_in_schema=False)
    async def webhooks_page(request: Request) -> HTMLResponse:
        require_global_permission(request, "admin")
        from maverick.webhook_events import catalog

        return templates.TemplateResponse(request, "settings_webhooks.html", {
            "events": catalog(),
            "can_admin": has_global_permission(request, "admin"),
        })

    return page


__all__ = ["PAGE_PATH", "create_webhooks_page_router", "record_alerts", "router"]
