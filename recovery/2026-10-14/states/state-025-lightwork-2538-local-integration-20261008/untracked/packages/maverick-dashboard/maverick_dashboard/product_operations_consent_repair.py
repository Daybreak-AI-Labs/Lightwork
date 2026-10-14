"""The exact Privacy-page consent repair is available through reviewed Copilot."""
from __future__ import annotations

from fastapi import HTTPException
from maverick.governed_operations import digest
from pydantic import Field

from . import auth
from .draft_api import context_for
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput

PAGE = "/privacy"


class RepairInput(StrictInput):
    subject_ref: str = Field(min_length=1, max_length=512)
    purpose: str = Field(min_length=1, max_length=128)


def _purpose(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def _field(name: str, label: str, *, placeholder: str = "") -> dict:
    return {"name": name, "label": label, "kind": "text", "required": True,
            "max_length": 512 if name == "subject_ref" else 128,
            **({"placeholder": placeholder} if placeholder else {})}


def _registry(request):
    auth.require_permission(request, "operate")
    from maverick import consent_registry
    if not consent_registry.enabled():
        raise HTTPException(403, "Consent records are turned off. Open Privacy to review setup.")
    return consent_registry


def snapshot(request, selection):
    if selection:
        raise HTTPException(422, "Open the Privacy page to choose the person and purpose.")
    registry = _registry(request)
    posture = registry.consent_posture()
    fields = [
        _field("subject_ref", "Person's email or ID", placeholder="Enter the same email or ID used for the saved choice"),
        _field("purpose", "What they were asked about", placeholder="For example: marketing emails"),
    ]
    return Snapshot(
        digest([context_for(request), posture]),
        "Consent records in this workspace",
        "Review one saved choice. Lightwork will keep every original receipt and only repair a summary that disagrees with valid history.",
        {"records_needing_review": posture["repair_needed"],
         "excluded_history_events": posture["excluded_history_events"]},
        fields,
    )


def prepare(request, selection, arguments):
    if selection:
        raise HTTPException(422, "Open the Privacy page to choose the person and purpose.")
    registry = _registry(request)
    subject_ref = arguments["subject_ref"].strip()
    purpose = _purpose(arguments["purpose"])
    try:
        preferences = registry.subject_preferences(subject_ref)
        details = preferences["purposes"].get(purpose)
    except ValueError as exc:
        raise HTTPException(422, "Enter the same person identifier used for the saved choice.") from exc
    if details is None:
        raise HTTPException(404, "No saved consent choice matches that person and purpose.")
    if not details["needs_repair"]:
        raise HTTPException(409, "This saved summary already matches its valid receipt history. Nothing changed.")
    return Preview(
        "Rebuild the saved consent summary from valid receipt history. Original receipt history, including entries with invalid dates, stays unchanged.",
        changes=[
            {"label": "Purpose", "after": purpose.replace("_", " ")},
            {"label": "Current choice from valid history", "after": details["status"]},
            {"label": "Original receipts", "after": "Kept unchanged"},
        ],
        native={"revision": details["revision"], "subject_digest": preferences["subject_digest"]},
    )


def _details(result):
    return {"purpose": result["purpose"], "status": result["status"],
            "policy_version": result["policy_version"],
            "excluded_history_count": result.get("excluded_history_count", 0),
            "history_preserved": bool(result.get("history_preserved")),
            "revision": result.get("revision")}


def _outcome(result, *, replay=False):
    pending = bool(result.get("audit_pending"))
    status = "unconfirmed" if pending else "replayed" if replay else "confirmed"
    message = (
        "The summary was repaired, but its activity record is still being saved. Check Activity before relying on the repair."
        if pending else
        "The saved summary now matches valid receipt history. Every original receipt is still kept."
    )
    return Outcome(status, message, _details(result))


def execute(request, _selection, arguments, prepared, operation_id):
    registry = _registry(request)
    from .api import _request_actor
    try:
        result = registry.repair_consent_projection(
            arguments["subject_ref"],
            purpose=arguments["purpose"],
            expected_revision=prepared["revision"],
            actor=_request_actor(request),
            operation_ref=operation_id,
        )
    except registry.ConsentRecordNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except registry.ConsentRegistryError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not result["repaired"]:
        return Outcome("confirmed", "The saved summary already matches valid receipt history. No record was changed.", _details(result))
    return _outcome(result)


def recover(request, _selection, arguments, prepared, operation_id):
    registry = _registry(request)
    try:
        receipt = registry.consent_repair_receipt(
            arguments["subject_ref"], purpose=arguments["purpose"],
            operation_ref=operation_id,
        )
        current = registry.subject_preferences(arguments["subject_ref"])["purposes"].get(_purpose(arguments["purpose"]))
    except ValueError as exc:
        raise HTTPException(422, "The selected consent record is unavailable. Open Privacy and look it up again.") from exc
    if receipt is not None:
        return _outcome(receipt, replay=True)
    if current is None:
        return Outcome("unconfirmed", "The selected consent record is no longer available. Check Privacy before preparing another change.")
    if current["revision"] != prepared["revision"]:
        return Outcome("unconfirmed", "The consent record changed after review. Check its current choice and Activity before deciding what to do next.", {
            "purpose": _purpose(arguments["purpose"]), "status": current["status"],
            "policy_version": current["policy_version"],
            "excluded_history_count": current["excluded_history_count"],
            "history_preserved": True, "revision": current["revision"],
        })
    return None


def entries():
    return (Adapter(
        "artifacts.consent_summary_repair",
        "Review and repair a saved consent summary",
        "artifacts",
        PAGE,
        (PAGE,),
        RepairInput,
        snapshot,
        prepare,
        execute,
        recover,
        aliases=("repair an incorrect consent summary", "fix a future-dated consent record",
                 "rebuild a consent choice from its history"),
    ),)
