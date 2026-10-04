"""Copilot entry to the native, release-pinned learned workflow run form."""
from __future__ import annotations

import math
from urllib.parse import quote

from fastapi import HTTPException, Response
from maverick.governed_operations import digest
from pydantic import BaseModel, ConfigDict

from . import api as native
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot


def _flow_id(selection):
    if (not isinstance(selection, dict) or not {"kind", "id"} <= set(selection)
            or set(selection) - {"kind", "id", "node_id"}
            or selection["kind"] != "flow" or not isinstance(selection["id"], str)
            or not 1 <= len(selection["id"]) <= 256
            or ("node_id" in selection and (not isinstance(selection["node_id"], str)
                or not 1 <= len(selection["node_id"]) <= 120))):
        raise HTTPException(422, "Open a saved workflow before asking Copilot to run it.")
    return selection["id"]


class HardeningBindings(BaseModel):
    """Names and values are checked against the current live action contract."""

    model_config = ConfigDict(extra="allow", strict=True)


def harden_snapshot(request, selection):
    flow_id = _flow_id(selection)
    node_id = selection.get("node_id")
    if not node_id:
        draft = native.get_flow_endpoint(request, flow_id)
        return Snapshot(digest([flow_id, draft["version"], draft["revision"]]),
                        "Current flow", "Select a suggested AI step to review its fixed action.",
                        blocked="Select a step marked with a suggestion in the flow designer.")
    try:
        review = native.flow_apply_review_endpoint(request, flow_id, node_id)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        draft = native.get_flow_endpoint(request, flow_id)
        return Snapshot(digest([flow_id, draft["version"], draft["revision"], node_id]),
                        "Current flow", "Review this step's current suggestions.",
                        blocked="This step has no current fixed-action suggestion. Choose a marked step.")
    revision = digest(review)
    if review["to_kind"] != "action":
        return Snapshot(revision, "Current flow", "This step has a different suggestion.",
                        blocked="Select an AI step suggested for a fixed action.")
    if not review["tool"]:
        return Snapshot(revision, "Current flow", "The action needs to be chosen by a person.",
                        blocked="Choose and review an action in the flow designer.")
    sources = review["sources"]
    fields = []
    for field in review["fields"]:
        name = field["name"]
        if field["type"] == "string" and sources:
            choices = [{"label": "Choose an input" if field["required"] else "Leave blank",
                        "value": ""}]
            choices.extend({"label": "Use " + source["label"] +
                            (" from the start form" if source["origin"] == "input" else " from an earlier step"),
                            "value": source["key"]}
                           for source in sources)
            fields.append({"name": name, "label": field["label"], "kind": "select",
                           "required": field["required"], "choices": choices})
        else:
            fields.append({"name": name, "label": field["label"],
                           "kind": "boolean" if field["type"] == "boolean" else
                                   "text",
                           "required": field["required"],
                           "max_length": 80 if field["type"] in {"number", "integer"} else 2000})
    summary = ("Replace “" + review["node_label"] + "” with the fixed action “" +
               review["tool"].replace("_", " ") + "” in a new draft version. " +
               "Review each input. Published runs will keep their current version until you publish the draft.")
    if review.get("approval_added"):
        summary += " This also adds an approval before the action runs."
    return Snapshot(revision, "Current flow and live action", summary,
                    {"flow": review["flow_name"], "step": review["node_label"],
                     "action": review["tool"], "published": review["published"]},
                    fields=fields)


def harden_prepare(request, selection, arguments):
    state = harden_snapshot(request, selection)
    if state.blocked:
        raise HTTPException(409, state.blocked)
    flow_id, node_id = _flow_id(selection), selection["node_id"]
    review = native.flow_apply_review_endpoint(request, flow_id, node_id)
    if digest(review) != state.revision:
        raise HTTPException(409, "This suggestion changed. Refresh and review it again.")
    fields = review["fields"]
    if set(arguments) != {field["name"] for field in fields}:
        raise HTTPException(422, "Review every action input before saving the new version.")
    sources = {source["key"]: source for source in review["sources"]}
    params, changes = {}, []
    for field in fields:
        name, kind, value = field["name"], field["type"], arguments[field["name"]]
        if value in (None, ""):
            if field["required"]:
                raise HTTPException(422, "Choose a value for " + field["label"] + ".")
            continue
        if kind == "string" and sources:
            if not isinstance(value, str) or value not in sources:
                raise HTTPException(422, "Choose a current flow input for " + field["label"] + ".")
            params[name] = "{{" + value + "}}"
            shown = "From " + sources[value]["label"]
        elif kind == "string":
            if not isinstance(value, str) or len(value) > 2000 or "{{" in value or "}}" in value:
                raise HTTPException(422, "Enter a short fixed value for " + field["label"] + ".")
            params[name] = value
            shown = "Same value every run"
        elif kind == "boolean":
            if type(value) is not bool:
                raise HTTPException(422, "Choose yes or no for " + field["label"] + ".")
            params[name] = value
            shown = "Yes" if value else "No"
        else:
            try:
                number = float(value) if type(value) in (int, float, str) else math.nan
            except (OverflowError, ValueError):
                number = math.nan
            if (not math.isfinite(number) or (kind == "integer" and not number.is_integer())):
                raise HTTPException(422, "Enter a valid number for " + field["label"] + ".")
            params[name] = int(number) if kind == "integer" else number
            shown = str(params[name])
        changes.append({"item": field["label"], "effect": shown})
    from maverick.flow import Flow, evolve

    draft = Flow.from_dict(native.get_flow_endpoint(request, flow_id))
    revised = evolve.apply_proposal(draft, node_id, "action", tool=review["tool"], params=params)
    errors = revised.validate()
    if errors:
        raise HTTPException(409, "The new flow needs review in the designer before it can be saved.")
    changes.insert(0, {"item": review["node_label"], "effect": "Fixed action: " + review["tool"]})
    return Preview(state.summary, changes, native={"id": flow_id, "node_id": node_id,
                                             "tool": review["tool"], "params": params,
                                             "version": review["version"],
                                             "revision": review["revision"]})


def harden_execute(request, selection, _arguments, native_review, _operation_id):
    flow_id = _flow_id(selection)
    if native_review.get("id") != flow_id or native_review.get("node_id") != selection.get("node_id"):
        raise HTTPException(409, "The selected flow step changed. Review it again.")
    result = native.apply_flow_proposal_endpoint(request, flow_id, native.FlowApplyIn(
        node_id=native_review["node_id"], to_kind="action", tool=native_review["tool"],
        params=native_review["params"], version=native_review["version"],
        revision=native_review["revision"],
    ))
    if (result.get("id") != flow_id or result.get("node_id") != native_review["node_id"]
            or result.get("to_kind") != "action" or result.get("version", 0) <= native_review["version"]):
        return Outcome("unconfirmed", "The save result could not be confirmed. Open the flow version history.")
    return Outcome("confirmed", "A new draft version was saved. Review and publish it when ready.",
                   {"flow": flow_id, "version": result["version"], "published": False})


def snapshot(request, selection):
    flow_id = _flow_id(selection)
    # The same native endpoints own operate permission, flow enablement, exact
    # owner scope, publication integrity and the immutable release epoch.
    draft = native.get_flow_endpoint(request, flow_id)
    revision = digest([flow_id, draft.get("version"), draft.get("revision"), draft.get("published")])
    if not draft.get("published"):
        return Snapshot(revision, "Current workflow", "Run the published procedure after reviewing its inputs.",
                        blocked="Publish this workflow before starting real work.")
    try:
        published = native.get_published_flow_endpoint(request, flow_id)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        return Snapshot(revision, "Current workflow", "Run the published procedure after reviewing its inputs.",
                        blocked="The published workflow could not be confirmed. Reopen it and check its status.")
    flow = published["flow"]
    release_id = published["release_id"]
    title = str(flow.get("name") or "this workflow")[:200]
    blocked = "This workflow does not have a learned procedure. Use its normal Run control." if flow.get("learned_recipe") is None else ""
    return Snapshot(digest([flow_id, release_id, draft.get("revision")]), "Current published workflow",
                    "Run the published “" + title + "” using its labelled input form. Connected actions may start after their required approvals.",
                    {"flow_id": flow_id, "release_id": release_id, "name": title}, blocked=blocked)


def prepare(request, selection, _arguments):
    state = snapshot(request, selection)
    if state.blocked:
        raise HTTPException(409, state.blocked)
    return Preview(state.summary, native=state.details)


def retirement_snapshot(request, selection, action):
    flow_id = _flow_id(selection)
    current = native.flow_retirement_preview_endpoint(request, flow_id)
    revision = digest(current)
    name = str(current.get("name") or "this flow")[:200]
    if action == "off":
        blocked = "This flow is already turned off." if not current["published"] else ""
        summary = (f"Turn off “{name}”. New scheduled and connected starts stop. "
                   "Already started or paused runs may continue. You can publish it again later.")
    else:
        blocked = ("Received form submissions are waiting. Review them in Automations before deleting this flow."
                   if current["pending_forms"] else "")
        summary = (f"Permanently delete “{name}” and its saved versions. "
                   "New starts stop; past run records remain. This cannot be undone.")
    details = {"name": name, "status": "Published" if current["published"] else "Draft",
               "scheduled": bool(current["scheduled"]),
               "connected_starts": int(current["connected_starts"]),
               "active_or_paused_runs": int(current["active_runs"]),
               "published_parent_flows": int(current["published_parent_flows"]),
               "pending_forms": int(current["pending_forms"])}
    return Snapshot(revision, "Current flow", summary, details, blocked=blocked)


def _retirement_prepare(request, selection, action):
    state = retirement_snapshot(request, selection, action)
    if state.blocked:
        raise HTTPException(409, state.blocked)
    current = native.flow_retirement_preview_endpoint(request, _flow_id(selection))
    if digest(current) != state.revision:
        raise HTTPException(409, "This flow changed while it was checked. Review it again.")
    changes = [{"item": "New scheduled and connected starts", "effect": "Stop"},
               {"item": "Existing runs", "effect": "May continue"}]
    if action == "delete":
        changes.append({"item": "Saved flow and versions", "effect": "Permanently deleted"})
    if current["published_parent_flows"]:
        changes.append({"item": "Other published flows containing this flow",
                        "effect": "Their saved copies can still run; turn them off separately"})
    return Preview(state.summary, changes, native=current)


def _retirement_execute(request, selection, native_review, action):
    flow_id = _flow_id(selection)
    if native_review.get("id") != flow_id:
        raise HTTPException(409, "The selected flow changed. Review it again.")
    if action == "off":
        result = native.unpublish_flow_endpoint(
            request, flow_id,
            native.FlowRetireIn(version=native_review["version"], revision=native_review["revision"],
                                expected_release_id=native_review["release_id"]),
        )
        if result.get("id") != flow_id or result.get("published") is not False:
            return Outcome("unconfirmed", "The result could not be confirmed. Open the flow to check its status.")
        return Outcome("confirmed", "Flow turned off. New automatic starts have stopped; existing runs may continue.",
                       {"flow": native_review["name"], "status": "Off"})
    response = Response()
    native.delete_flow_endpoint(
        request, flow_id, response, version=native_review["version"],
        revision=native_review["revision"], expected_release_id=native_review["release_id"],
    )
    recovery_copy = response.headers.get("X-Lightwork-Recovery-Copy")
    message = ("Flow deleted. Its private editor recovery copy needs administrator review."
               if recovery_copy == "needs-review" else "Flow deleted. Past run records remain available.")
    return Outcome("confirmed", message, {"flow": native_review["name"],
                                          "recovery_copy_needs_review": recovery_copy == "needs-review"})


def off_snapshot(request, selection):
    return retirement_snapshot(request, selection, "off")


def delete_snapshot(request, selection):
    return retirement_snapshot(request, selection, "delete")


def off_prepare(request, selection, _arguments):
    return _retirement_prepare(request, selection, "off")


def delete_prepare(request, selection, _arguments):
    return _retirement_prepare(request, selection, "delete")


def off_execute(request, selection, _arguments, native_review, _operation_id):
    return _retirement_execute(request, selection, native_review, "off")


def delete_execute(request, selection, _arguments, native_review, _operation_id):
    return _retirement_execute(request, selection, native_review, "delete")


def examples_snapshot(request, selection):
    if selection:
        raise HTTPException(422, "Open Learning examples to check your recent examples.")
    from .flow_teach_ops_api import learning_examples_inbox

    inbox = learning_examples_inbox(request)
    rows = inbox["examples"]
    needs_review = sum(row["status"] == "awaiting_review" for row in rows)
    needs_permission = sum(row["status"] == "accepted" and not row["training_available"] for row in rows)
    ready = sum(row["status"] == "accepted" and row["training_available"] for row in rows)
    summary = (f"Among recent examples, {needs_review} need review, {needs_permission} need separate "
               f"learning permission, and {ready} are ready to teach from. Open an example to check its current status.")
    details = {"needs_review": needs_review, "needs_learning_permission": needs_permission,
               "ready_to_teach": ready,
               "examples": [{"label": row["flow_name"] + " · " +
                             ("Needs review" if row["status"] == "awaiting_review" else
                              "Ready to teach" if row["training_available"] else "Check example"),
                             "href": "/flows/" + quote(row["flow_id"], safe="")
                             + "/runs/" + quote(row["run_id"], safe="")}
                            for row in rows[:10]],
               "recent_only": inbox["recent_only"]}
    revision = digest([[row["flow_id"], row["run_id"], row["status"], row["training_available"],
                        row["audit_status"]] for row in rows])
    return Snapshot(revision, "Your recent learning examples", summary, details,
                    blocked="Example collection is off. Ask an administrator to turn it on in Settings."
                    if not inbox["available"] else "")


def examples_prepare(request, selection, _arguments):
    state = examples_snapshot(request, selection)
    return Preview(state.summary, native=state.details)


def entries():
    return (Adapter("learning.workflow_run", "Run this published workflow", "learning", "/flows/designer",
                    ("/flows/designer/*",), EmptyInput, snapshot, prepare,
                    aliases=("run this workflow", "start this workflow", "run published workflow")),
            Adapter("learning.workflow_harden", "Review fixed action for this step", "learning", "/workflows",
                    ("/flows/designer/*",), HardeningBindings, harden_snapshot, harden_prepare, harden_execute,
                    aliases=("harden this step", "make this step a fixed action", "apply flow improvement")),
            Adapter("learning.workflow_turn_off", "Turn off this flow", "learning", "/workflows",
                    ("/flows/designer/*",), EmptyInput, off_snapshot, off_prepare, off_execute,
                    aliases=("unpublish this flow", "stop this flow", "turn off this flow")),
            Adapter("learning.workflow_delete", "Delete this flow", "learning", "/workflows",
                    ("/flows/designer/*",), EmptyInput, delete_snapshot, delete_prepare, delete_execute,
                    aliases=("delete this flow", "remove this flow")),
            Adapter("learning.examples_review", "Check recent learning examples", "learning", "/learning/examples",
                    ("/learning/examples",), EmptyInput, examples_snapshot, examples_prepare,
                    aliases=("which examples need review", "show learning examples")))
