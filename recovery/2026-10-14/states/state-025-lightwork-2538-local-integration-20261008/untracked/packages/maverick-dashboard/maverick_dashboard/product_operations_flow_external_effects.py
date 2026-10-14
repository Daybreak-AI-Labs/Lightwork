"""Record a run owner's check of an uncertain connected-app action."""
from __future__ import annotations

import re
from typing import Literal

from fastapi import HTTPException, Request
from maverick.governed_operations import digest

from . import api, auth
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class OwnerCheckInput(StrictInput):
    decision: Literal["applied", "not_applied"]
    personally_checked: Literal[True]


def _run(request: Request, selection: dict):
    if (set(selection) != {"kind", "id", "flow_id"}
            or selection.get("kind") != "flow_run"
            or not isinstance(selection.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", selection["id"])
            or not isinstance(selection.get("flow_id"), str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", selection["flow_id"])):
        raise HTTPException(422, "Open the exact workflow run first.")
    auth.require_permission(request, "operate")
    api._require_flows()
    run = api._load_owned_run(request, selection["id"])
    if run.flow_id != selection["flow_id"]:
        raise HTTPException(404, "This workflow run is no longer available.")
    return run


def _step_label(run) -> str:
    """Use only the pinned step's human label; never expose its raw config."""
    if run.definition_digest:
        from maverick.flow import store

        try:
            definition = store.load_flow_snapshot(run.definition_digest)
        except (store.FlowSnapshotError, OSError, ValueError) as exc:
            raise HTTPException(503, "The saved workflow details are unavailable. Open the complete run to review it.") from exc
        if definition.id != run.flow_id or definition.owner != run.owner:
            raise HTTPException(503, "The saved workflow details could not be verified. Open the complete run to review it.")
        node = definition.node(run.cursor)
        if node is not None:
            for value in (node.label, node.brief, node.prompt):
                if isinstance(value, str) and value.strip():
                    return value.strip()[:180]
            return {"action": "Connected action", "agent": "AI task"}.get(node.kind, "Workflow step")
    return "The action that stopped"


def _view(request: Request, selection: dict):
    run = _run(request, selection)
    revision = digest({
        "run": run.run_id,
        "flow": run.flow_id,
        "owner": run.owner,
        "updated": run.updated,
        "status": run.status,
        "cursor": run.cursor,
        "definition_digest": run.definition_digest,
        "release_id": run.release_id,
    })
    link = f"/flows/{run.flow_id}/runs/{run.run_id}#fr-data-rows"
    if run.status != "indeterminate" or not run.cursor:
        return run, Snapshot(
            revision,
            "Current workflow run",
            "Open the complete run to review its latest status.",
            details={"open": {"label": "Open this complete workflow run", "href": link}},
            blocked="This run does not currently need an outside action check.",
        )
    step = _step_label(run)
    details = {
        "Current state": "Needs checking in the connected app",
        "Step to check": step,
        "What to do": (
            f"Open the connected app and look for the result of “{step}”. "
            "If you cannot tell whether it happened, leave the run unchanged. "
            "Lightwork will not repeat the action automatically."
        ),
        "Scope": "Your report is saved as an owner check. Lightwork does not read the connected app's record.",
        "open": {"label": "Open the complete workflow run", "href": link},
    }
    return run, Snapshot(
        revision,
        "The selected owned workflow run",
        "Check the connected app yourself, then record what you found. Nothing runs again from this check.",
        details=details,
        fields=[
            {"name": "decision", "label": "After checking the connected app, what did you find?",
             "kind": "select", "required": True,
             "choices": [
                 {"value": "", "label": "Choose what you found"},
                 {"value": "applied", "label": "I found the action"},
                 {"value": "not_applied", "label": "I did not find the action"},
             ]},
            {"name": "personally_checked", "label": "I personally checked the connected app. This records my report; it does not read that app or repeat the action.",
             "kind": "boolean", "required": True, "value": False},
        ],
    )


def snapshot(request: Request, selection: dict) -> Snapshot:
    return _view(request, selection)[1]


def prepare(request: Request, selection: dict, arguments: dict) -> Preview:
    run, state = _view(request, selection)
    if state.blocked or run.status != "indeterminate" or not run.cursor:
        raise HTTPException(409, "This run no longer needs an outside action check. Refresh it before continuing.")
    found = arguments["decision"] == "applied"
    reported = "You found the action in the connected app." if found else "You did not find the action in the connected app."
    next_step = (
        "This run will stop at the action you found. Later steps remain unfinished."
        if found else
        "This run will be available to resume from this step after a separate review. It will not retry automatically."
    )
    return Preview(
        "Review this owner-reported check. Lightwork will save what you found; it will not verify the connected app.",
        changes=[
            {"What you reported": reported},
            {"What happens next": next_step},
            {"Activity history": "Your report will be recorded with your account and this exact workflow step."},
        ],
        native={"expected_updated": float(run.updated), "expected_cursor": run.cursor,
                "decision": arguments["decision"]},
    )


def _outcome(request: Request, selection: dict, decision: str, native: dict, operation_id: str,
             *, dispatch: bool) -> Outcome | None:
    run = _run(request, selection)
    actor = api._request_actor(request)
    receipt = dict(run.reconciliation or {})
    if receipt.get("operation_id") == operation_id:
        if (receipt.get("decision") != decision or receipt.get("actor") != actor
                or receipt.get("node_id") != native.get("expected_cursor")):
            raise HTTPException(409, "This review was already recorded with different details. Refresh the run.")
        return Outcome(
            "replayed",
            "Your earlier check is recorded. Lightwork did not repeat the action.",
            {"Decision": "You reported that the action was " + ("found" if decision == "applied" else "not found") + ".",
             "Evidence source": "Owner report; not a direct read of the connected app.",
             "Workflow state": "Stopped at this step" if decision == "applied" else "Ready for a separate review to resume from this step"},
        )
    if not dispatch:
        return None

    from .api_schemas import FlowEffectReconcileIn

    result = api.reconcile_flow_external_effect_endpoint(
        request,
        run.run_id,
        FlowEffectReconcileIn(
            decision=decision,
            operation_id=operation_id,
            expected_updated=float(native["expected_updated"]),
            expected_cursor=native["expected_cursor"],
        ),
    )
    if result.get("status") != ("reconciled_applied" if decision == "applied" else "failed"):
        raise HTTPException(503, "The check result could not be confirmed. Open the complete run before trying again.")
    return Outcome(
        "confirmed",
        "Your check is recorded. " + (
            "This run stopped at the action you found; later steps did not run."
            if decision == "applied" else
            "This run is ready for a separate review to resume from this step."
        ) + " Lightwork did not check the connected app or repeat the action.",
        {"Decision": "You reported that the action was " + ("found" if decision == "applied" else "not found") + ".",
         "Evidence source": "Owner report; not a direct read of the connected app.",
         "Workflow state": "Stopped at this step" if decision == "applied" else "Ready for a separate review to resume from this step"},
    )


def execute(request: Request, selection: dict, arguments: dict, native: dict, operation_id: str) -> Outcome:
    return _outcome(request, selection, arguments["decision"], native, operation_id, dispatch=True)


def recover(request: Request, selection: dict, arguments: dict, native: dict, operation_id: str) -> Outcome | None:
    return _outcome(request, selection, arguments["decision"], native, operation_id, dispatch=False)


def entries():
    return (Adapter(
        "artifacts.workflow_external_effect_check",
        "Record what you found in the connected app",
        "artifacts",
        "/automations",
        ("/flows/*/runs/*",),
        OwnerCheckInput,
        snapshot,
        prepare,
        execute,
        recover,
        aliases=("check this workflow action", "record whether the action happened",
                 "I checked the connected app", "what do I do when a workflow action is unclear"),
        read_recovery=recover,
    ),)
