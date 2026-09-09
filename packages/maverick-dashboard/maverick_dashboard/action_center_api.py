"""Authorized action discovery and private presentation preferences."""
from __future__ import annotations

import hmac
import re
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from maverick import config
from maverick.privacy_ops import RecordConflict
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import action_center
from .auth import (
    caller_principal,
    goal_owner_filter,
    require_permission,
    scope_to_suites,
    suite_allowed,
)
from .draft_api import context_for

router = APIRouter(prefix="/api/v1/action-center", tags=["action-center"])
_QUEUE_CURSOR = re.compile(r"[A-Za-z0-9_-]{1,768}\Z")


class PreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(..., ge=0)
    source_version: str = Field(..., pattern=r"^[a-f0-9]{64}$")
    operation_id: str = Field(..., min_length=16, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    read: bool
    snoozed_until: float = Field(0, ge=0)


def require_enabled(request):
    require_permission(request, "operate")
    if config.config_source_errors(include_tenant=True) or config.get_features().get("action_center") is not True:
        raise HTTPException(status_code=403, detail="Action Center is disabled in Settings or configuration is unavailable.")


def _vendor_reconciliation_rows(request) -> dict:
    """Read a bounded local queue through the current request-bound authority."""
    from .vendor_onboarding_api import _binding

    binding = _binding(request)
    rows, cursor, complete = [], "", False
    seen = set()
    max_pages = max(1, (action_center.SOURCE_LIMIT + 99) // 100)
    for _page in range(max_pages):
        if len(rows) >= action_center.SOURCE_LIMIT:
            break
        requested_limit = min(100, action_center.SOURCE_LIMIT - len(rows))
        page = binding.service.list_page(
            binding.context,
            limit=requested_limit,
            cursor=cursor,
            view="unresolved",
        )
        expected_keys = {
            "schema", "cases", "view", "limit", "has_more", "next_cursor",
            "record_reads", "scan_complete", "order",
        }
        if (not isinstance(page, dict) or set(page) != expected_keys
                or page.get("schema") != "lightwork.vendor-onboarding.case-page.v1"
                or page.get("view") != "unresolved"
                or page.get("limit") != requested_limit
                or page.get("order") != "case_id_ascending"
                or not isinstance(page.get("cases"), list)
                or len(page["cases"]) > requested_limit
                or type(page.get("record_reads")) is not int
                or not 0 <= page["record_reads"] <= max(requested_limit * 4, requested_limit + 1)
                or type(page.get("has_more")) is not bool
                or type(page.get("scan_complete")) is not bool
                or page["scan_complete"] is page["has_more"]):
            raise ValueError("vendor reconciliation source is unavailable")
        next_cursor = page["next_cursor"]
        if (page["has_more"] and (
                not isinstance(next_cursor, str) or not _QUEUE_CURSOR.fullmatch(next_cursor)
        )
                or not page["has_more"] and next_cursor is not None):
            raise ValueError("vendor reconciliation source is unavailable")
        rows.extend(page["cases"])
        if not page["has_more"]:
            complete = True
            break
        if next_cursor in seen:
            raise ValueError("vendor reconciliation cursor did not advance")
        seen.add(next_cursor)
        cursor = next_cursor
    return {"items": rows, "complete": complete}


def _model(request):
    from maverick.assessment import custom_template_records, list_saved

    from .app import (
        FINANCE_ASSESSMENT_TYPES,
        PRIVACY_ASSESSMENT_TYPES,
        SECURITY_ASSESSMENT_TYPES,
        _deliverable_specs,
        _world,
    )

    principal = caller_principal(request)
    specs = scope_to_suites(request, _deliverable_specs(), suite_of=lambda spec: spec.get("suite"))
    mapping = {kind: suite for suite, kinds in (("finance", FINANCE_ASSESSMENT_TYPES),
        ("privacy", PRIVACY_ASSESSMENT_TYPES), ("security", SECURITY_ASSESSMENT_TYPES)) for kind in kinds}
    try:
        mapping.update({kind: record.get("department", "privacy") for kind, record in custom_template_records().items()})
        def allowed(row):
            return row.get("type") in mapping and suite_allowed(request, mapping[row["type"]])
    except Exception:
        def allowed(row):
            raise ValueError("Assessment department permissions are unavailable.")
    project_reviews = None
    if config.get_features().get("project_reviews") is True:
        from .project_reviews_api import _service

        def project_reviews():
            return _service(request).inbox()
    vendor_reconciliations = None
    if config.get_features().get("vendor_onboarding") is True:
        def vendor_reconciliations():
            return _vendor_reconciliation_rows(request)
    return action_center.collect(_world(), principal=principal, owner=goal_owner_filter(request),
        specs=specs, assessments=list_saved, assessment_allowed=allowed,
        project_reviews=project_reviews, vendor_reconciliations=vendor_reconciliations)


async def _snapshot(request):
    require_enabled(request)
    context = context_for(request)
    requested_context = request.headers.get("X-Lightwork-Action-Context")
    if requested_context is not None and not hmac.compare_digest(requested_context, context):
        raise HTTPException(status_code=409, detail="The account or workspace changed. Reload Action Center.")
    model = await run_in_threadpool(_model, request)
    try:
        preference = await run_in_threadpool(action_center.preferences, context)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Attention preferences are unavailable; no read or snooze state was changed.") from exc
    return action_center.apply_preferences({**model, "context": context}, preference, now=time.time())


@router.get("")
async def inbox(request: Request, response: Response,
                kind: Literal[
                    "all", "approval", "signoff", "assessment", "project_review",
                    "vendor_reconciliation",
                ] = "all",
                view: Literal["active", "unread", "snoozed", "overdue", "all"] = "active",
                assignment: Literal["all", "mine", "available"] = "all",
                offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    response.headers["Cache-Control"] = "private, no-store"
    model = await _snapshot(request)
    rows = model["items"]
    counts = {"total": len(rows), "unread": sum(not row["read"] for row in rows),
              "overdue": sum(row["overdue"] for row in rows), "snoozed": sum(row["snoozed"] for row in rows)}
    selected = [row for row in rows if (kind == "all" or row["kind"] == kind)
                and (assignment == "all" or row["assignment"] == assignment)
                and (view == "all" or (view == "active" and (not row["snoozed"] or row["overdue"]))
                     or (view == "unread" and not row["read"]) or (view == "snoozed" and row["snoozed"])
                     or (view == "overdue" and row["overdue"]))]
    return {**model, "items": selected[offset:offset + limit], "counts": counts,
            "total": len(selected), "offset": offset, "limit": limit, "has_more": offset + limit < len(selected)}


@router.put("/{item_id}/preference")
async def set_preference(request: Request, response: Response, item_id: str, body: PreferenceIn):
    response.headers["Cache-Control"] = "private, no-store"
    model = await _snapshot(request)
    if not hmac.compare_digest(request.headers.get("X-Lightwork-Action-Context", ""), model["context"]):
        raise HTTPException(status_code=409, detail="The account or workspace changed. Reload Action Center.")
    item = next((row for row in model["items"] if row["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="This action is complete, unavailable, or no longer accessible. Refresh the list.")
    if item["source_version"] != body.source_version:
        raise HTTPException(status_code=409, detail="The action changed. Review its current version before updating your preferences.")
    if body.snoozed_until and not time.time() < body.snoozed_until <= time.time() + 7 * 86400:
        raise HTTPException(status_code=422, detail="Choose a future reminder within seven days.")
    try:
        return await run_in_threadpool(action_center.save_preference, model["context"], item,
            expected_revision=body.expected_revision, operation_id=body.operation_id,
            read=body.read, snoozed_until=body.snoozed_until,
            actor=caller_principal(request) or "local:dashboard")
    except RecordConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Your preference could not be saved. Refresh before retrying.") from exc
