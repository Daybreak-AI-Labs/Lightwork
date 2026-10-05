"""Reviewed instruction activation and exact whole-restore control for Copilot.

The native Learned page and Copilot use the same current-state decision. The
model selects a control only: people choose a proposal, review every affected
instruction and confirm. Recovery reads the actual overlay without applying a
second time. An instruction change is not evidence of improved results.
"""
from __future__ import annotations

from fastapi import HTTPException
from maverick import harness_refine
from maverick.governed_operations import digest
from pydantic import Field

from . import auth, learning_ops_api
from . import learning_loop_api as native
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot, StrictInput


class DecisionInput(StrictInput):
    proposal: str = Field(pattern=r"^[0-9a-f]{16}$")


def _state(request):
    auth.require_global_permission(request, "admin")
    rows = harness_refine.list_proposals()
    current = harness_refine.refinements()
    settings = harness_refine._settings()
    return rows, current, settings


def review_snapshot(request, _selection):
    rows, current, settings = _state(request)
    proposals = [native._proposal_view(row) for row in rows[:100]]
    details = {"instruction_changes": [{"title": row["title"], "where_used": row["target"],
                                       "instructions": row["change"], "why": row["rationale"],
                                       "decision": row["approval"] or {"pending": "Waiting to apply",
                                                                 "applied": "Applied", "reverted": "Restored"}.get(row["status"], "Needs review")}
                                      for row in proposals],
               "changes_in_use": [{"title": str(row["name"]).replace("-", " "),
                                   "instructions": row["change"]} for row in current[:100]],
               "more_changes": len(rows) > 100 or len(current) > 100,
               "open_learning": {"href": "/learned", "label": "Open instruction decisions"}}
    return Snapshot(digest([rows, current, settings]), "Current instruction decisions and instructions in use",
                    "Review suggestions, approvals and the instructions currently used. Applying instructions does not establish that results improved.",
                    details=details)


def review_prepare(request, selection, _arguments):
    state = review_snapshot(request, selection)
    return Preview(state.summary, native=state.details)


def decision_snapshot(request, _selection, *, undo):
    rows, current, settings = _state(request)
    candidates = [row for row in rows if row.get("status") == ("applied" if undo else "pending")]
    if not undo:
        actor = native._actor(request)
        candidates = [row for row in candidates if row.get("proposed_by") == actor
                      and (not settings["require_approval"] or native._approval_state(row.get("approval_id")) == "Approved")]
    choices = [{"value": row["id"], "label": str(row["name"]).replace("-", " ")}
               for row in candidates[:100]]
    approval_states = [(row["id"], native._approval_state(row.get("approval_id"))) for row in candidates]
    blocked = ("No applied instruction change has a restore point to review." if undo else
               "No suggestion from your account is ready to apply. Review approvals on the Learned page.") if not choices else ""
    if not undo and not settings["enable"]:
        blocked = "Suggested instruction changes are turned off. Open Learning settings before applying."
    return Snapshot(digest([rows, current, settings, approval_states]),
                    "Current instruction proposals, approvals and instructions in use",
                    "Restore the complete earlier instruction set. The review names every instruction removed or brought back." if undo else
                    "Apply one exact approved suggestion from your account. A restore point is saved first.",
                    fields=[{"name": "proposal", "label": "Instruction change", "kind": "select",
                             "required": True, "choices": choices}], blocked=blocked)


def decision_prepare(request, selection, arguments, *, undo):
    offered = decision_snapshot(request, selection, undo=undo)
    if offered.blocked:
        raise HTTPException(409, offered.blocked)
    if arguments["proposal"] not in {choice["value"] for choice in offered.fields[0]["choices"]}:
        raise HTTPException(409, "This change is no longer available to decide. Refresh current instructions.")
    review = harness_refine.change_review(arguments["proposal"])
    plain = learning_ops_api._refine_preview(arguments["proposal"])
    if plain["revision"] != review["revision"]:
        raise HTTPException(409, "The instructions changed while preparing this review. Refresh and review again.")
    summary = ("Restore these exact reviewed instructions. Every listed removal and return is part of this decision." if undo else
               "Apply this exact reviewed instruction change. Approval is checked again, and agents use the change on new tasks.")
    return Preview(summary, changes=[{"label": change["title"],
                                     "before": change["before"] or "No instructions for this change",
                                     "after": change["after"] or "Removed"} for change in plain["changes"]],
                   native={"proposal": arguments["proposal"], "revision": review["revision"],
                           "change": review["proposal"]["digest"], "after": digest(review["after"])})


def decision_execute(request, selection, arguments, prepared, _operation, *, undo):
    if prepared.get("proposal") != arguments["proposal"]:
        raise HTTPException(409, "The selected instruction change changed. Review it again.")
    result = native.change_current_refinement(request, arguments["proposal"], prepared["revision"], undo=undo)
    return Outcome("confirmed", "Reviewed instructions restored. Current decisions were refreshed." if undo else
                   "The approved instruction change is in use for new work. Review its actual results separately.",
                   {"result": result, "current_instructions": {"href": "/learned", "label": "Review current instructions"}})


def decision_recover(request, _selection, arguments, prepared, _operation, *, undo):
    rows, current, _settings = _state(request)
    if prepared.get("proposal") != arguments["proposal"]:
        raise HTTPException(409, "The selected instruction change changed. Review it again.")
    row = next((row for row in rows if row["id"] == arguments["proposal"]), None)
    if row is None or row.get("digest") != prepared.get("change"):
        return None
    if undo:
        confirmed = row.get("status") == "reverted" and digest(harness_refine._load_refinements()) == prepared.get("after")
    else:
        confirmed = row.get("status") == "applied" and any(
            item.get("proposal_id") == row["id"] and item.get("digest") == row["digest"] for item in current)
    if not confirmed:
        return None
    return Outcome("replayed", "The reviewed instruction result is already present. No change was repeated.",
                   {"current_instructions": {"href": "/learned", "label": "Review current instructions"}})


def entries():
    common = {"family": "learning", "direct_ui": "/learned", "pages": ("/learned",)}
    result = [Adapter("learning.instruction_decisions", "Review instruction changes", input_model=EmptyInput,
                      snapshot=review_snapshot, prepare=review_prepare,
                      aliases=("show instruction changes", "review improvement decisions"), **common)]
    for undo, identifier, label in ((False, "apply", "Apply an approved instruction change"),
                                    (True, "restore", "Restore earlier instructions")):
        result.append(Adapter("learning.instructions_" + identifier, label, input_model=DecisionInput,
                              snapshot=lambda request, selection, undo=undo: decision_snapshot(request, selection, undo=undo),
                              prepare=lambda request, selection, arguments, undo=undo: decision_prepare(request, selection, arguments, undo=undo),
                              execute=lambda request, selection, arguments, prepared, operation, undo=undo: decision_execute(request, selection, arguments, prepared, operation, undo=undo),
                              recover=lambda request, selection, arguments, prepared, operation, undo=undo: decision_recover(request, selection, arguments, prepared, operation, undo=undo),
                              **common))
    return tuple(result)
