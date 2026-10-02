"""Read-only Copilot check for one owner-selected improvement pilot release.

The pilot page owns creation, observation, review, and recovery. This adapter
uses the same account/workspace gate and live deployment read as its native
release-status route; it never turns a frozen plan into a claim of customer lift.
"""
from __future__ import annotations

import re

from fastapi import HTTPException
from maverick.governed_operations import digest

from . import outcome_pilot_releases, outcome_pilots, outcome_pilots_api
from .product_operations import Adapter, EmptyInput, Preview, Snapshot

_PILOT_ID = re.compile(r"OPL-[a-f0-9]{48}\Z")


def _id(selection):
    if (not isinstance(selection, dict) or set(selection) != {"kind", "id"}
            or selection["kind"] != "outcome_pilot"
            or not isinstance(selection["id"], str)
            or _PILOT_ID.fullmatch(selection["id"]) is None):
        raise HTTPException(404, "Choose an improvement pilot on its page first.")
    return selection["id"]


def snapshot(request, selection):
    pilot_id = _id(selection)
    context = outcome_pilots_api._context(request)
    try:
        record = outcome_pilots.get(pilot_id, context)
    except LookupError as exc:
        raise HTTPException(404, "This pilot is no longer available to your account and workspace.") from exc
    binding_data = record.get("release_binding")
    if binding_data is None:
        return Snapshot(digest([context, pilot_id, record["plan_version"], "no_release"]),
                        "Selected improvement pilot", "Check the pilot's measured work on its page.",
                        blocked="This pilot has no candidate release to check.")
    try:
        binding = outcome_pilot_releases.ReleaseBinding(**binding_data)
        current = outcome_pilot_releases.status(request, binding)
    except (TypeError, ValueError, PermissionError) as exc:
        raise HTTPException(503, "The release check is unavailable. Open the pilot to refresh its status.") from exc
    details = {
        "pilot": record["title"],
        "promotion_state": current["promotion_state"],
        "deployment_state": current["deployment_state"],
        "release_evidence_current": current["release_evidence_current"],
        "case_exposure_verified": current["case_exposure_verified"],
        "causal_lift_qualified": current["causal_lift_qualified"],
        "checked_at": current.get("checked_at"),
        "release_decisions": {"href": "/research#research-promotions",
                              "label": "Open release decisions"},
    }
    # The observation time changes on every healthy read. It is useful to show,
    # but cannot be part of a revision that Copilot rechecks several times.
    revision_details = {key: value for key, value in details.items() if key != "checked_at"}
    return Snapshot(digest([context, pilot_id, record["plan_version"], revision_details]),
                    "Current release check",
                    "Check which release the installation currently reports. This does not prove which release each case saw or that the change improved results.",
                    details=details)


def prepare(request, selection, _arguments):
    current = snapshot(request, selection)
    if current.blocked:
        raise HTTPException(409, current.blocked)
    return Preview(current.summary, native=current.details)


def entries():
    return (Adapter("learning.pilot_release_status", "Check this pilot's release", "learning",
                    "/outcome-pilots", ("/outcome-pilots",), EmptyInput,
                    snapshot, prepare,
                    aliases=("check pilot release", "check this pilot's release")),)
