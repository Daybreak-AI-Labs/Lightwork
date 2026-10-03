"""Copilot entry to the native, release-pinned learned workflow run form."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException
from maverick.governed_operations import digest

from . import api as native
from .product_operations import Adapter, EmptyInput, Preview, Snapshot


def _flow_id(selection):
    if (not isinstance(selection, dict) or set(selection) != {"kind", "id"}
            or selection["kind"] != "flow" or not isinstance(selection["id"], str)
            or not 1 <= len(selection["id"]) <= 256):
        raise HTTPException(422, "Open a saved workflow before asking Copilot to run it.")
    return selection["id"]


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
            Adapter("learning.examples_review", "Check recent learning examples", "learning", "/learning/examples",
                    ("/learning/examples",), EmptyInput, examples_snapshot, examples_prepare,
                    aliases=("which examples need review", "show learning examples")))
