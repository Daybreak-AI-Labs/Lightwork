"""Copilot entry to the native, release-pinned learned workflow run form."""
from __future__ import annotations

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


def entries():
    return (Adapter("learning.workflow_run", "Run this published workflow", "learning", "/flows/designer",
                    ("/flows/designer/*",), EmptyInput, snapshot, prepare,
                    aliases=("run this workflow", "start this workflow", "run published workflow")),)
