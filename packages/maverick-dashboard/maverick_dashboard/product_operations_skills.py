"""Copilot reviews and controls the same learned-skill admission as Skills."""
from __future__ import annotations

import re

from fastapi import HTTPException
from maverick.governed_operations import digest
from pydantic import Field

from . import auth
from . import tools_ops_api as native
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot, StrictInput


class AdmitInput(StrictInput):
    candidate: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    name: str = Field(default="", max_length=96)


class WithdrawInput(StrictInput):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)


def _state(request):
    auth.require_permission(request, "admin")
    return native._learned_candidates()


def snapshot(request, selection, kind="review"):
    value = _state(request)
    suggestions = value["suggestions"]
    added = value["added"]
    fields = []
    blocked = ""
    if kind == "admit":
        choices = [{"value": row["name"], "label": row["name"].replace("-", " ")}
                   for row in suggestions if row["ok"]]
        fields = [
            {"name": "candidate", "label": "Suggestion", "kind": "select",
             "required": True, "choices": choices},
            {"name": "name", "label": "Save as (optional)", "kind": "text", "value": ""},
        ]
        blocked = value["blocked"] or ("No suggestion is ready for review." if not choices else "")
    elif kind == "withdraw":
        fields = [{"name": "name", "label": "Added skill", "kind": "select",
                   "required": True, "choices": [{"value": row["name"],
                                                     "label": row["name"].replace("-", " ") +
                                                     (" (paused)" if row.get("source_shared") is False else "")}
                                                  for row in added]}]
        blocked = "No added suggestion is available." if not added else ""
    return Snapshot(
        digest(value), "Suggestions from completed shared work",
        "Read all instructions and source tasks before adding a suggestion. Nothing is used automatically.",
        details=value, fields=fields, blocked=blocked,
    )


def review_prepare(request, selection, arguments):
    return Preview("These suggestions have not been added to the agents' skills.",
                   native=_state(request))


def admit_prepare(request, selection, arguments):
    value = _state(request)
    row = next((item for item in value["suggestions"]
                if item["name"] == arguments["candidate"] and item["ok"]), None)
    if row is None or not value["can_add"]:
        raise HTTPException(409, "This suggestion is unavailable. Refresh the current review.")
    name = arguments["name"] or row["name"]
    if len(name) > 96 or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise HTTPException(422, "Choose a short name using letters, numbers and dashes.")
    return Preview(
        "Add this exact suggestion to the agents' skills only after reviewing all instructions and linked tasks.",
        changes=[{"label": "Skill name", "before": "Not added", "after": name.replace("-", " ")},
                 {"label": "Full instructions", "before": "Not used", "after": row["instructions"]},
                 {"label": "Used when", "before": "Not used", "after": ", ".join(row["triggers"])},
                 {"label": "Source tasks", "before": "Not used", "after": ", ".join(str(n) for n in row["source_tasks"])}],
        native={"candidate": row["name"], "fingerprint": row["fingerprint"], "name": name},
    )


def admit_recover(request, selection, arguments, prepared, operation):
    value = _state(request)
    found = next((row for row in value["added"]
                  if row["name"] == prepared["name"] and
                  row.get("candidate_fingerprint") == prepared["fingerprint"]), None)
    return Outcome("replayed", "This exact suggestion is already added; it was not added again.", found) if found else None


def admit_execute(request, selection, arguments, prepared, operation):
    result = native.admit_checked_learned_skill(
        request, prepared["candidate"],
        native.LearnedAdmissionIn(fingerprint=prepared["fingerprint"],
                                  publish_name=prepared["name"], confirm=True),
    )
    return Outcome("confirmed", "The reviewed skill was added. Agents can use it on a matching new task.", result)


def withdraw_prepare(request, selection, arguments):
    value = _state(request)
    row = next((item for item in value["added"] if item["name"] == arguments["name"]), None)
    if row is None:
        raise HTTPException(409, "This saved skill is unavailable. Refresh the current review.")
    explanation = ("Its source task access is unavailable, so agents cannot use it now. "
                   "Removing it also prevents reuse if access returns. "
                   if row.get("source_shared") is False else "")
    return Preview(
        explanation + "Remove this reviewed skill from new tasks. Past task records remain.",
        changes=[{"label": "Agents' skill", "before": row["name"].replace("-", " "), "after": "Removed"}],
        native={"name": row["name"], "fingerprint": row["fingerprint"]},
    )


def withdraw_execute(request, selection, arguments, prepared, operation):
    result = native.withdraw_checked_learned_skill(
        request, prepared["name"],
        native.LearnedWithdrawalIn(fingerprint=prepared["fingerprint"], confirm=True),
    )
    return Outcome("confirmed", "The skill was removed. New tasks will not use it.", result)


def entries():
    shared = {"family": "learning", "direct_ui": "/skills", "pages": ("/skills", "/learned")}
    return (
        Adapter("skills.review_suggestions", "Review suggested skills", input_model=EmptyInput,
                snapshot=snapshot, prepare=review_prepare, **shared),
        Adapter("skills.admit_suggestion", "Add a reviewed skill", input_model=AdmitInput,
                snapshot=lambda r, s: snapshot(r, s, "admit"), prepare=admit_prepare,
                execute=admit_execute, recover=admit_recover, **shared),
        Adapter("skills.withdraw_suggestion", "Remove an added suggested skill", input_model=WithdrawInput,
                snapshot=lambda r, s: snapshot(r, s, "withdraw"), prepare=withdraw_prepare,
                execute=withdraw_execute, **shared),
    )
