"""Exact-source employee feedback never grants independent review or apply rights."""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException, Response
from maverick.authority_effects import authority_effect_fence
from maverick.governed_operations import digest
from pydantic import Field, ValidationError

from . import knowledge_access_api as native
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot, StrictInput


class Feedback(StrictInput):
    request_type: Literal["correction", "exclusion", "deletion"]
    reason: str = Field(min_length=1, max_length=2000)
    correction: str = Field(default="", max_length=8192)


def _scope(request):
    tenant, actor, context = native._binding(request, Response())
    return tenant, actor, context, native.call_native(native._service, request)


def _selected(selection, context):
    if set(selection) != {"kind", "reference", "purpose"} or selection.get("kind") != "knowledge_source":
        raise HTTPException(422, "Open a currently permitted source detail in Knowledge first.")
    try:
        return native.DetailIn(context=context, reference=selection["reference"], purpose=selection["purpose"])
    except ValidationError as exc:
        raise HTTPException(422, "Choose the exact source and permitted use from Knowledge.") from exc


def feedback_snapshot(request, selection):
    tenant, actor, context, service = _scope(request)
    if not selection:
        return Snapshot(digest([context, "no-source"]), "Exact knowledge feedback", "Select the source you can currently read.",
                        blocked="Open a source's details in Knowledge before reporting a correction or exclusion.")
    selected = _selected(selection, context)
    native.call_native(service.detail, tenant, actor, selected.reference.exact(), purpose=selected.purpose)
    return Snapshot(digest([context, selection]), "Currently authorized exact source", "Submitting records a request for independent review. It does not edit shared knowledge, approve deletion or grant training rights.", fields=[
        {"name": "request_type", "label": "Requested review", "kind": "select", "required": True, "choices": [
            {"value": kind, "label": label} for kind, label in (("correction", "Correct information"), ("exclusion", "Stop future use"), ("deletion", "Review source deletion"))]},
        {"name": "reason", "label": "Business reason", "kind": "textarea", "required": True, "max_length": 2000},
        {"name": "correction", "label": "Proposed corrected information (required for correction)", "kind": "textarea", "max_length": 8192}])


def feedback_prepare(request, selection, arguments):
    _, _, context, _ = _scope(request)
    feedback_snapshot(request, selection)
    reason, correction = arguments["reason"].strip(), arguments["correction"].strip()
    if not reason or arguments["request_type"] == "correction" and not correction:
        raise HTTPException(422, "Give a business reason and the proposed information for a correction.")
    return Preview("Send an exact-source request to independent administrators. Existing evidence, legal holds and separate training rights remain unchanged.",
                   changes=[{"label": "Requested review", "before": "No request", "after": arguments["request_type"]},
                            {"label": "Reason", "before": "", "after": reason},
                            {"label": "Proposed information", "before": "", "after": correction}],
                   native={"context": context, "selection": selection, "request_type": arguments["request_type"],
                           "reason": reason, "proposed_correction": correction or None})


def feedback_execute(request, selection, arguments, prepared, operation):
    with authority_effect_fence():
        tenant, actor, context, service = _scope(request)
        if context != prepared["context"] or selection != prepared["selection"]:
            raise HTTPException(409, "Account, workspace or source selection changed. Review again.")
        selected = _selected(selection, context)
        payload = native.RightsRequestIn(context=context, confirm=True, reference=selected.reference,
                                        purpose=selected.purpose, request_key=operation, request_type=prepared["request_type"],
                                        reason=prepared["reason"], proposed_correction=prepared["proposed_correction"])
        native._binding(request, Response(), payload, administrator=False)
        # Always use the employee intake even for an administrator. This action
        # cannot inherit the separate administrator review/apply interface.
        item = native.call_native(service.submit_feedback, tenant, actor, selected.reference.exact(),
                                  purpose=selected.purpose, request_type=payload.request_type, reason=payload.reason,
                                  proposed_correction=payload.proposed_correction, request_key=operation)
        receipt = service.request_store.get(item["id"])
        if (not receipt or receipt.get("tenant") != tenant or receipt.get("requested_by") != actor
                or receipt.get("_audit_pending")):
            return Outcome("unconfirmed", "The request returned, but its durable audit receipt is not confirmed. Refresh your requests before another submission.")
        return Outcome("confirmed", "Your request is recorded for independent review. No source, deletion or training permission was applied.", item)


def status_snapshot(request, selection):
    tenant, actor, context, service = _scope(request)
    items = native.call_native(service.feedback_requests, tenant, actor, limit=100)
    return Snapshot(digest([context, items]), "Your knowledge feedback", "The latest 100 requests from your current named account and workspace; independent administrative evidence is withheld.")


def status_prepare(request, selection, arguments):
    tenant, actor, _, service = _scope(request)
    items = native.call_native(service.feedback_requests, tenant, actor, limit=100)
    return Preview("Your request status, including separately retained requests when source use changes.", native={"items": items, "limit": 100})


def feedback_entries():
    return (Adapter("knowledge.feedback", "Report an exact source correction or concern", "learning", "/knowledge-access", ("/knowledge-access",),
                    Feedback, feedback_snapshot, feedback_prepare, feedback_execute),
            Adapter("knowledge.my_feedback", "Review my knowledge requests", "learning", "/knowledge-access", ("/knowledge-access",),
                    EmptyInput, status_snapshot, status_prepare))
