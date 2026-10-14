"""Copilot handoff to the verified requester's native correction controls."""
from __future__ import annotations

from fastapi import HTTPException

from . import auth
from . import dsar_rectification_api as native
from .product_operations import Adapter, EmptyInput, Preview, Snapshot
from .product_operations_dsar_handoff import _id


def _state(request, selection):
    auth.require_permission(request, "operate")
    request_id = _id(selection)
    store = native._ops()
    record = store.get_dsar(request_id)
    if record is None or "status" not in record:
        raise HTTPException(404, "This privacy request is no longer available.")
    if record.get("kind") not in {"correction", "rectification"}:
        raise HTTPException(409, "Open a correction request to review these controls.")
    actor = native._actor(request)
    if record.get("status") != "open":
        return request_id, record, actor, "", "This request must be open before a correction can be reviewed."
    if (record.get("verification") or {}).get("status") != "verified":
        return request_id, record, actor, "", "Confirm the requester's identity before reviewing a correction."
    return request_id, record, actor, native.context_fingerprint(request_id, record, actor), ""


def snapshot(request, selection):
    request_id, record, actor, context, blocked = _state(request, selection)
    return Snapshot(
        context or native.context_fingerprint(request_id, record, actor),
        "The current verified correction request",
        "Open Lightwork's correction controls. Opening them does not change a record.",
        {
            "Request": request_id,
            "Correction available": not bool(blocked),
            "Scope": "The selected person's saved messages and personal facts in Lightwork only.",
        },
        blocked=blocked,
    )


def prepare(request, selection, _arguments):
    current = snapshot(request, selection)
    if current.blocked:
        raise HTTPException(409, current.blocked)
    return Preview(
        "Open the correction controls for this verified request. Nothing is changed until you review and confirm a specific correction.",
        native={"dsar_rectification_review": {"scope": "current_page", "context": current.revision}},
    )


def entries():
    return (Adapter(
        "artifacts.privacy_request_rectification",
        "Open the correction controls for this privacy request",
        "artifacts", "/privacy", ("/privacy/dsar/*",), EmptyInput,
        snapshot, prepare,
        aliases=("review a privacy correction", "correct this person's Lightwork records",
                 "open correction controls"),
    ),)
