"""Customer routes for correcting one verified person's Lightwork-held data."""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

router = APIRouter(tags=["privacy-rectification"])


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: str = Field(min_length=1, max_length=64)
    incorrect_text: str = Field(min_length=1, max_length=200)


class ReviewIn(SearchIn):
    reference: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    replacement_text: str = Field(min_length=1, max_length=2_000)
    expected_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ApplyIn(ReviewIn):
    operation_id: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-f]{32}$")


def _case(request_id: str, *, open_only: bool) -> tuple[Any, dict]:
    ops = _ops()
    record = ops.get_dsar(request_id[:64])
    if record is None or "status" not in record:
        raise HTTPException(404, "No correction request is available.")
    if record.get("kind") not in {"correction", "rectification"}:
        raise HTTPException(409, "This request is not a data-correction request.")
    if open_only and record.get("status") != "open":
        raise HTTPException(409, "This request must be open before data can be corrected.")
    if (record.get("verification") or {}).get("status") != "verified":
        raise HTTPException(409, "Confirm the requester's identity before changing their records.")
    if not str(record.get("subject_id") or "").strip():
        raise HTTPException(409, "This request does not identify whose records can be corrected.")
    return ops, record


def _ops():
    from .public_rights_admin_api import _ops_or_403

    return _ops_or_403()


def _world():
    from maverick.dsar import _resolve_world

    try:
        return _resolve_world(None, strict=True)
    except Exception as exc:
        raise HTTPException(503, "Lightwork's personal records are unavailable. No correction was made.") from exc


def _operation(record: dict, operation_id: str) -> dict | None:
    rows = record.get("rectification_operations") or []
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if isinstance(row, dict) and row.get("operation_id") == operation_id), None)


def context_fingerprint(request_id: str, record: dict, actor: str) -> str:
    """Bind Copilot's open-controls handoff to this actor and exact DSAR revision."""
    value = {
        "schema": "lightwork.dsar-rectification-context.v1",
        "request_id": request_id[:64],
        "revision": record.get("revision"),
        "subject_id": str(record.get("subject_id") or ""),
        "kind": record.get("kind"),
        "status": record.get("status"),
        "verification": (record.get("verification") or {}).get("status"),
        "actor": actor,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _update_operation(ops, request_id: str, actor: str, incoming: dict, *, state: str) -> dict | None:
    def mutate(record: dict) -> bool:
        if record.get("kind") not in {"correction", "rectification"}:
            raise ValueError("This request is not a data-correction request.")
        if state in {"pending", "applied"} and record.get("status") != "open":
            raise ValueError("This correction request is no longer open.")
        if (record.get("verification") or {}).get("status") != "verified":
            raise ValueError("The requester's identity is no longer confirmed.")
        rows = record.setdefault("rectification_operations", [])
        if not isinstance(rows, list):
            raise ValueError("Correction history is unavailable. Ask an administrator to investigate.")
        current = _operation(record, incoming["operation_id"])
        if current is None:
            if state != "pending":
                raise ValueError("The original correction review could not be found.")
            if len(rows) >= 500:
                raise ValueError("This request has reached its correction-history limit. Ask an administrator to review it.")
            rows.append(dict(incoming))
            return True
        if current.get("fingerprint") != incoming.get("fingerprint") or current.get("actor") != actor:
            raise ValueError("This correction reference belongs to different reviewed details.")
        if current.get("state") == state:
            return False
        if current.get("state") not in {"pending", "uncertain"}:
            raise ValueError("This correction already has a final result.")
        if current.get("state") == "uncertain" and state not in {"uncertain"}:
            raise ValueError("An uncertain correction needs an administrator's review.")
        if state not in {"applied", "not_applied", "reconciled_after", "uncertain"}:
            raise ValueError("The correction result is invalid.")
        current.update(
            state=state,
            applied_at=incoming.get("applied_at"),
            resolved_at=incoming.get("resolved_at"),
            resolved_sha256=incoming.get("resolved_sha256"),
            matches_changed=incoming.get("matches_changed", current.get("matches_changed", 0)),
        )
        if state != "pending":
            return True
        return False

    return ops.annotate_dsar(
        request_id[:64], mutate,
        action=("rectification_applied" if state == "applied" else
                "rectification_result_recovered" if state != "pending" else
                "rectification_reviewed"),
        actor=actor,
    )


def _fingerprint(request_id: str, body: ApplyIn) -> str:
    payload = {
        "schema": "lightwork.dsar-correction-request.v1",
        "request_id": request_id[:64],
        "channel": body.channel,
        "reference": body.reference,
        "incorrect_text": body.incorrect_text.strip(),
        "replacement_text": body.replacement_text,
        "expected_sha256": body.expected_sha256,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _service():
    from maverick import dsar_rectification

    return dsar_rectification


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


async def _retry_pending_audit(ops, request_id: str, actor: str) -> dict | None:
    """Flush a durable privacy audit outbox before a resumed correction write."""
    try:
        saved = await run_in_threadpool(
            ops.annotate_dsar, request_id[:64], lambda _record: False,
            action="rectification_reviewed", actor=actor,
        )
    except Exception as exc:
        raise HTTPException(503, "The earlier activity receipt is still being recorded. No correction was made.") from exc
    if saved is None:
        raise HTTPException(404, "The correction request is no longer available.")
    if saved.get("_audit_pending"):
        raise HTTPException(503, "The earlier activity receipt is still being recorded. No correction was made.")
    return saved


@router.post("/privacy/dsar/{request_id}/rectification/search")
async def search(request: Request, request_id: str, body: SearchIn, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    _, record = _case(request_id, open_only=True)
    service = _service()
    try:
        result = await run_in_threadpool(
            service.search_subject_records, _world(), str(record["subject_id"]),
            body.channel, body.incorrect_text,
        )
    except service.DsarRectificationError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        **result,
        "message": "Search is limited to this person's saved messages and personal details. Shared work and other people are not included.",
    }


@router.get("/privacy/dsar/{request_id}/rectification/context")
def rectification_context(request: Request, request_id: str, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    _ops, record = _case(request_id, open_only=True)
    return {"context": context_fingerprint(request_id, record, _actor(request)), "ready": True}


@router.get("/privacy/dsar/{request_id}/rectification/accounts")
async def accounts(request: Request, request_id: str, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    _, record = _case(request_id, open_only=True)
    service = _service()
    try:
        result = await run_in_threadpool(
            service.subject_channels, _world(), str(record["subject_id"]),
        )
    except service.DsarRectificationError as exc:
        raise HTTPException(503, str(exc)) from exc
    from .compliance_controls_api import _channel_label

    return {
        "accounts": [{"channel": channel, "label": _channel_label(channel)}
                     for channel in result["channels"]],
        "more_available": result["more_available"],
    }


@router.post("/privacy/dsar/{request_id}/rectification/review")
async def review(request: Request, request_id: str, body: ReviewIn, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    _, record = _case(request_id, open_only=True)
    service = _service()
    try:
        result = await run_in_threadpool(
            service.preview_subject_correction, _world(), str(record["subject_id"]),
            body.channel, reference=body.reference, incorrect_text=body.incorrect_text,
            replacement_text=body.replacement_text, expected_sha256=body.expected_sha256,
        )
    except service.DsarRectificationError as exc:
        raise HTTPException(409, str(exc)) from exc
    result["summary"] = (
        "Correct the selected " + result["label"].lower() + " in this person's Lightwork records? "
        "Only the reviewed field will change. Other systems and copies are not changed."
    )
    return result


@router.post("/privacy/dsar/{request_id}/rectification/apply")
async def apply(request: Request, request_id: str, body: ApplyIn, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    ops, record = _case(request_id, open_only=True)
    actor = _actor(request)
    service = _service()
    fingerprint = _fingerprint(request_id, body)
    if record.get("_audit_pending"):
        await _retry_pending_audit(ops, request_id, actor)
        _, record = _case(request_id, open_only=True)
    existing = _operation(record, body.operation_id)
    if existing is not None:
        if existing.get("fingerprint") != fingerprint or existing.get("actor") != actor:
            raise HTTPException(409, "This correction reference belongs to different reviewed details.")
        if existing.get("state") == "applied":
            return {
                "status": "applied", "operation_id": body.operation_id,
                "matches_changed": existing.get("matches_changed", 0),
                "message": "The reviewed correction is already saved.",
            }
        if existing.get("state") in {"not_applied", "reconciled_after", "uncertain"}:
            raise HTTPException(409, "The original result has already been resolved. Start a new review; Lightwork will not repeat it.")
        try:
            current_hash = await run_in_threadpool(
                service.current_subject_cell_sha256, _world(), str(record["subject_id"]),
                body.channel, body.reference,
            )
        except service.DsarRectificationError as exc:
            raise HTTPException(409, str(exc)) from exc
        if current_hash == existing.get("after_sha256"):
            completed = dict(existing, resolved_at=time.time(), resolved_sha256=current_hash)
            saved = await run_in_threadpool(_update_operation, ops, request_id, actor, completed, state="reconciled_after")
            if saved is None:
                raise HTTPException(404, "The original correction could not be confirmed.")
            return {
                "status": "reconciled_after", "operation_id": body.operation_id,
                "matches_changed": existing.get("matches_changed", 0),
                "audit_pending": bool(saved.get("_audit_pending")),
                "message": "The approved corrected text is present. Lightwork did not submit the correction again.",
            }
        if current_hash != existing.get("before_sha256"):
            raise HTTPException(409, "The original correction result is uncertain because this record changed. Do not repeat it; ask an administrator to review the activity record.")
    else:
        try:
            preview = await run_in_threadpool(
                service.preview_subject_correction, _world(), str(record["subject_id"]),
                body.channel, reference=body.reference, incorrect_text=body.incorrect_text,
                replacement_text=body.replacement_text, expected_sha256=body.expected_sha256,
            )
        except service.DsarRectificationError as exc:
            raise HTTPException(409, str(exc)) from exc
        existing = {
            "operation_id": body.operation_id,
            "fingerprint": fingerprint,
            "actor": actor,
            "state": "pending",
            "channel": body.channel,
            "reference": body.reference,
            "before_sha256": preview["before_sha256"],
            "after_sha256": preview["after_sha256"],
            "matches_changed": preview["matches_changed"],
            "created_at": time.time(),
        }
        try:
            saved = await run_in_threadpool(_update_operation, ops, request_id, actor, existing, state="pending")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if saved is None:
            raise HTTPException(404, "The correction request is no longer available.")
        if saved.get("_audit_pending"):
            raise HTTPException(503, "The correction review is saved, but its activity receipt is still being recorded. No data was changed. Read the original before continuing.")

    try:
        result = await run_in_threadpool(
            service.apply_subject_correction, _world(), str(record["subject_id"]),
            body.channel, reference=body.reference, incorrect_text=body.incorrect_text,
            replacement_text=body.replacement_text, expected_sha256=body.expected_sha256,
        )
    except service.DsarRectificationError as exc:
        raise HTTPException(409, str(exc)) from exc
    completed = dict(existing, applied_at=time.time(), matches_changed=result["matches_changed"])
    try:
        saved = await run_in_threadpool(_update_operation, ops, request_id, actor, completed, state="applied")
    except ValueError as exc:
        raise HTTPException(503, "The correction was applied, but its final activity receipt could not be confirmed. Read the original before continuing.") from exc
    if saved is None:
        raise HTTPException(503, "The correction was applied, but its final activity receipt could not be confirmed. Read the original before continuing.")
    return {
        "status": "applied", "operation_id": body.operation_id,
        "matches_changed": result["matches_changed"],
        "audit_pending": bool(saved.get("_audit_pending")),
        "message": "The correction is saved in Lightwork's records. It does not change other systems or copies.",
    }


def _actor(request: Request) -> str:
    from .public_rights_admin_api import _actor as actor_for_request

    return actor_for_request(request)


@router.post("/privacy/dsar/{request_id}/rectification/original/{operation_id}/resolve")
async def resolve_original(request: Request, request_id: str, operation_id: str, response: Response) -> dict:
    """Resolve an interrupted correction from its exact before/after digest.

    This records a durable result; it never reapplies the corrected text.
    """
    _no_store(response)
    require_permission(request, "operate")
    if re.fullmatch(r"[a-f0-9]{32}", operation_id) is None:
        raise HTTPException(404, "No saved correction result is available.")
    ops, record = _case(request_id, open_only=False)
    actor = _actor(request)
    existing = _operation(record, operation_id)
    if not existing or existing.get("actor") != actor:
        raise HTTPException(404, "No saved correction result is available.")
    state = str(existing.get("state") or "")
    if state in {"applied", "not_applied", "reconciled_after", "uncertain"}:
        message = {
            "applied": "The correction was saved in Lightwork.",
            "not_applied": "The correction did not change the record. Review it again to start a new correction.",
            "reconciled_after": "The approved corrected text is present. Lightwork did not submit the correction again.",
            "uncertain": "Lightwork cannot confirm the original result. Do not repeat it; ask an administrator to review the activity record.",
        }[state]
        return {"status": state, "operation_id": operation_id,
                "audit_pending": bool(record.get("_audit_pending")), "message": message}
    if state != "pending":
        raise HTTPException(409, "This correction result needs an administrator's review.")
    service = _service()
    try:
        current_hash = await run_in_threadpool(
            service.current_subject_cell_sha256, _world(), str(record["subject_id"]),
            str(existing.get("channel") or ""), str(existing.get("reference") or ""),
        )
    except service.DsarRectificationError as exc:
        raise HTTPException(409, str(exc)) from exc
    if current_hash == existing.get("after_sha256"):
        state = "reconciled_after"
        message = "The approved corrected text is present. Lightwork did not submit the correction again."
    elif current_hash == existing.get("before_sha256"):
        state = "not_applied"
        message = "The correction did not change the record. Review it again to start a new correction."
    else:
        state = "uncertain"
        message = "Lightwork cannot confirm the original result. Do not repeat it; ask an administrator to review the activity record."
    resolved = dict(existing, resolved_at=time.time(), resolved_sha256=current_hash or "")
    try:
        saved = await run_in_threadpool(
            _update_operation, ops, request_id, actor, resolved, state=state,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if saved is None:
        raise HTTPException(404, "No saved correction result is available.")
    return {"status": state, "operation_id": operation_id,
            "matches_changed": saved.get("matches_changed", 0),
            "audit_pending": bool(saved.get("_audit_pending")), "message": message}


@router.get("/privacy/dsar/{request_id}/rectification/original/{operation_id}")
async def original(request: Request, request_id: str, operation_id: str, response: Response) -> dict:
    _no_store(response)
    require_permission(request, "operate")
    if re.fullmatch(r"[a-f0-9]{32}", operation_id) is None:
        raise HTTPException(404, "No saved correction result is available.")
    _, record = _case(request_id, open_only=False)
    saved = _operation(record, operation_id)
    if not saved or saved.get("actor") != _actor(request):
        raise HTTPException(404, "No saved correction result is available.")
    status = saved.get("state")
    resolution_hint = None
    if status == "pending":
        service = _service()
        try:
            current_hash = await run_in_threadpool(
                service.current_subject_cell_sha256, _world(), str(record["subject_id"]),
                str(saved.get("channel") or ""), str(saved.get("reference") or ""),
            )
        except service.DsarRectificationError:
            current_hash = None
        if current_hash == saved.get("after_sha256"):
            resolution_hint = "reconciled_after"
        elif current_hash == saved.get("before_sha256"):
            resolution_hint = "not_applied"
        else:
            resolution_hint = "uncertain"
    messages = {
        "applied": "The original correction is saved.",
        "not_applied": "The original correction did not change the record. You can review it again.",
        "reconciled_after": "The approved corrected text is present. Lightwork did not submit the correction again.",
        "pending": "The original result needs confirmation before this request can be closed.",
        "uncertain": "The original correction result cannot be confirmed because this record changed. Do not repeat it; ask an administrator to review the activity record.",
    }
    return {
        "status": status,
        "resolution_hint": resolution_hint,
        "resolution_required": status == "pending",
        "operation_id": operation_id,
        "matches_changed": saved.get("matches_changed", 0) if status in {"applied", "reconciled_after"} else 0,
        "audit_pending": bool(record.get("_audit_pending")),
        "message": messages[status],
    }
