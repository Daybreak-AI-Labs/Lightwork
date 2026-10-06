"""Buttons for the Learned page's decision points that were CLI-only:
``refine list/show/propose/apply/revert`` and the ``self-harness`` read-outs
(``efficacy``, ``log``, ``preview``, ``conflicts``, ``corpus quality``,
``holdout verify``) plus ``self-harness run``.

Reads need ``view`` (the lessons are already on the page) or ``audit`` (the
learning activity log and the hold-out ledger check read the audit side).
Every change -- suggesting, applying or undoing an instruction change, and
starting a learning pass or a test-case check that spends evaluation budget --
needs a global administrator, the same rung as the other learning buttons.
Each call goes through the kernel function the CLI uses, so the kernel's own
guards (the learning stop, the approval card, snapshots, evaluation budget,
the signed learning audit) stay in force; dashboard-started runs are also
recorded with the person who started them.
"""
from __future__ import annotations

import re
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import caller_principal, require_global_permission, require_permission

router = APIRouter(prefix="/learning-loop", tags=["learning-loop"])

_MAX_ROWS = 100
_MAX_TEXT = 300
_PROPOSAL_ID = re.compile(r"[0-9a-f]{16}\Z")
_TARGET_LABELS = {
    "prompt": "Standing instructions",
    "skill": "A named procedure",
    "memory": "Something it remembers",
}
_APPROVAL_LABELS = {
    "pending": "Waiting for approval",
    "approved": "Approved",
    "denied": "Turned down",
    "rejected": "Turned down",
    "expired": "Approval expired",
}
_PHASE_LABELS = {
    "apply": "Learned a new lesson",
    "forget": "Removed a lesson",
    "retire": "Retired a lesson that was no longer confirmed",
    "relapse": "Put a lesson back on trial after recent failures",
}
_HALTED = "Learning is stopped for this installation right now, so nothing was changed."


class ProposeIn(BaseModel):
    target: Literal["prompt", "skill", "memory"] = "prompt"
    title: str = Field(min_length=1, max_length=90)
    failure: str = Field(min_length=1, max_length=2000)
    change: str = Field(min_length=1, max_length=2000)
    rationale: str = Field(min_length=1, max_length=2000)


class RunIn(BaseModel):
    # ``chosen`` runs for the model picked on the page (``--model``).
    scope: Literal["main", "all", "chosen"] = "main"
    model_id: str | None = Field(default=None, max_length=200)
    # Stage new lessons on trial (``--canary``) and retire stale ones
    # (``--retire``); ``instructions`` is the deployed system-prompt snapshot
    # careful (risk-limited) testing compares against (``--system-prompt-file``).
    trial: bool = False
    retire: bool = True
    instructions: str | None = Field(default=None, max_length=1024 * 1024 + 1)


class QualityIn(BaseModel):
    model_id: str | None = Field(default=None, max_length=200)
    remove_weak: bool = False
    confirm: bool = False


class WithdrawLessonIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=200)
    token: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=10, max_length=1000)
    confirm: Literal[True]


class ReviewDecisionIn(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm: Literal[True]
    operation_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    context: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    review_expires_at: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class DecisionReceiptIn(BaseModel):
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    context: str = Field(pattern=r"^[0-9a-f]{64}$")
    undo: bool = False


class ReconciliationIn(DecisionReceiptIn):
    recovery_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reconciliation_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_expires_at: float = Field(gt=0, allow_inf_nan=False)
    confirm: Literal[True]


class SharedHoldIn(BaseModel):
    context: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class SharedAcceptanceIn(SharedHoldIn):
    recovery_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    reconciliation_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_expires_at: float = Field(gt=0, allow_inf_nan=False)
    confirm: Literal[True]


def _clip(value: Any, size: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _audit(request: Request, action: str, **payload: Any) -> None:
    from maverick.audit import AuditRefused, audit_event
    try:
        audit_event("learning_action", agent="dashboard", action=action,
                    actor=caller_principal(request) or "local", **payload)
    except AuditRefused as exc:
        raise HTTPException(status_code=503, detail="The activity log refused this change.") from exc


def _actor(request: Request) -> str:
    from .api import _request_actor
    return _request_actor(request)


def _slug(title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip()).strip("-._")
    return slug[:96].rstrip("-._")


def _refine_problem(exc: Exception) -> HTTPException:
    """A kernel refusal as one plain sentence (never the kernel's wording)."""
    text = str(exc).lower()
    if "stays marked applied" in text or "may be half-written" in text or "current state needs review" in text:
        return HTTPException(503, "The instruction change could not be fully confirmed. Refresh current instructions and ask an administrator to review recovery before starting work.")
    rules = (
        ("is off", f"Suggested instruction changes are turned off. Turn on {_refine_switch_place()} first."),
        ("injection screen", "Part of this text reads like an instruction to ignore Lightwork's rules, "
                             "so it was refused. Reword it and try again."),
        ("queue is full", "Too many suggestions are waiting for a decision. Decide some of them first."),
        ("refinement name", "Give the change a short title using letters and numbers."),
        ("ceiling", "Each box can hold up to 2,000 characters."),
        ("is required", "Fill in every box."),
        ("already applied a refinement", "The approval for this change was already used. "
                                         "Suggest it again to ask for a new approval."),
        ("needs an approved decision", "This change is still waiting for approval. "
                                       "An approver can decide it on the Approvals page."),
        ("needs the id of an approved", "This change has no approval yet. Suggest it again to ask for one."),
        ("no longer matches", "This change or its approval no longer matches. "
                              "Suggest it again and ask for a new approval."),
        ("actor", "Only the person who suggested this change can apply it after it is approved."),
        ("evaluator anchors", "Lightwork's quality checks are not in their released state, "
                              "so no change can be applied right now."),
        ("anchor guard", "Lightwork's quality checks could not run, so no change can be applied right now."),
        ("no longer retained", "The restore point for this change is no longer kept, "
                               "so it cannot be undone automatically."),
        ("snapshot", "Lightwork could not save or restore a restore point, so nothing was changed."),
        ("only a pending proposal", "Only a change that is still waiting can be applied."),
        ("original instruction decision is unconfirmed", "An earlier instruction decision is unconfirmed. Check its original result and review current instructions with an administrator before another change."),
        ("history is full", "The retained instruction decision history is full. No new change was started; ask an administrator to review retention."),
        ("unknown refinement proposal", "That suggestion no longer exists. Refresh the page."),
    )
    for needle, sentence in rules:
        if needle in text:
            return HTTPException(status_code=409, detail=sentence)
    return HTTPException(status_code=409, detail="Lightwork could not do that. Nothing was changed.")


def _approval_state(approval_id: Any) -> str:
    if approval_id in (None, ""):
        return ""
    try:
        from ._shared import _world
        approval = _world().get_approval(int(approval_id))
    except Exception:  # noqa: BLE001 -- the state line is advisory
        return ""
    if approval is None:
        return "Approval no longer on record"
    return _APPROVAL_LABELS.get(str(approval.status), str(approval.status).replace("_", " ").capitalize())


def _proposal_view(row: dict) -> dict:
    status = str(row.get("status") or "")
    approval = _approval_state(row.get("approval_id")) if status == "pending" else ""
    return {
        "id": str(row.get("id") or ""),
        "status": status,
        "target": _TARGET_LABELS.get(str(row.get("target") or ""), "Instructions"),
        "title": _clip(str(row.get("name") or "").replace("-", " ").replace("_", " "), 96),
        "failure": _clip(row.get("failure")),
        "change": _clip(row.get("change"), 600),
        "rationale": _clip(row.get("rationale")),
        "proposed_by": _clip(row.get("proposed_by"), 120),
        "proposed_at": row.get("proposed_at"),
        "applied_at": row.get("applied_at"),
        "reverted_at": row.get("reverted_at"),
        "approval": approval,
        "can_apply": status == "pending" and approval in ("", "Approved"),
        "can_revert": status == "applied",
        "technical": {
            "reference": str(row.get("id") or ""),
            "approval_number": row.get("approval_id"),
            "fingerprint": str(row.get("digest") or "")[:16],
        },
    }


def _refine_switch_place() -> str:
    from .learning_ops_api import setting_place
    return setting_place("harness_refine.enable")


def visible_instruction_rows(request, rows):
    from . import auth
    from ._shared import _world
    world = _world()
    return [row for row in rows if row.get("goal_id") is None or
            ((goal := world.get_goal(row["goal_id"])) is not None and auth.can_access_goal(request, goal))]


def _refinements_snapshot(request=None) -> dict:
    from maverick import harness_refine
    cfg = harness_refine._settings()
    proposals = harness_refine.list_proposals()
    in_force = harness_refine.refinements()
    if request is not None:
        proposals = visible_instruction_rows(request, proposals)
        visible = {row["id"] for row in proposals}
        in_force = [row for row in in_force if row.get("proposal_id") in visible]
    return {
        "enabled": bool(cfg.get("enable")),
        "require_approval": bool(cfg.get("require_approval", True)),
        "switch_place": _refine_switch_place(),
        "proposals": [_proposal_view(p) for p in proposals[:_MAX_ROWS]],
        "proposals_truncated": len(proposals) > _MAX_ROWS,
        "in_force": [
            {
                "target": _TARGET_LABELS.get(str(r.get("target") or ""), "Instructions"),
                "title": _clip(str(r.get("name") or "").replace("-", " ").replace("_", " "), 96),
                "change": _clip(r.get("change"), 600),
                "applied_at": r.get("applied_at"),
                "applied_by": _clip(r.get("applied_by"), 120),
            }
            for r in in_force[:_MAX_ROWS]
        ],
    }


@router.get("/refinements")
async def refinements(request: Request, response: Response) -> dict:
    require_permission(request, "view")
    from . import auth
    from .draft_api import context_for
    context = context_for(request)
    response.headers["Cache-Control"] = "private, no-store"
    try:
        result = await run_in_threadpool(_refinements_snapshot, request)
        if auth.has_global_permission(request, "admin"):
            from maverick import harness_refine
            history = await run_in_threadpool(_bound(request, lambda: harness_refine.decision_history(actor=_actor(request), context=context)))
            visible = {row["id"] for row in result["proposals"]}
            result["decisions"] = [{"proposal": row["proposal"], "operation_id": row["request"], "context": row["context"],
                                    "revision": row["revision"], "undo": row["action"] == "restore", "started_at": row["started_at"],
                                    "title": str(row["name"]).replace("-", " "), "unfinished": row["status"] == "reserved" and not row.get("resolved")}
                                   for row in history if row["proposal"] in visible]
            result["holds"] = await run_in_threadpool(_bound(request, lambda: harness_refine.shared_decision_holds(actor=_actor(request))))
        require_permission(request, "view")
        if context_for(request) != context:
            raise HTTPException(409, "Your account or workspace changed. Reload before reviewing instructions.")
        return {**result, "context": context, "can_manage": auth.has_global_permission(request, "admin")}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="Suggested changes are not available right now.") from exc


def _bound(request: Request, fn):
    """Run ``fn`` with the caller bound as the kernel's current identity."""
    from maverick import connections
    principal = caller_principal(request)

    def call():
        with connections.bind_principal(principal):
            return fn()
    return call


@router.post("/refinements")
async def propose_refinement(request: Request, body: ProposeIn) -> dict:
    require_global_permission(request, "admin")
    from maverick import harness_refine
    name = _slug(body.title)
    if not name:
        raise HTTPException(status_code=422, detail="Give the change a short title using letters and numbers.")
    actor = _actor(request)
    observation = {"failure": body.failure, "target": body.target, "name": name,
                   "change": body.change, "rationale": body.rationale}
    try:
        out = await run_in_threadpool(_bound(
            request, lambda: harness_refine.propose(observation, proposed_by=actor)))
    except harness_refine.RefineError as exc:
        raise _refine_problem(exc) from exc
    view = _proposal_view(out)
    return {"proposal": view, "needs_approval": bool(out.get("approval_id"))}


def _instruction_sources(request, after, proposals, *, learning=True):
    from maverick.governed_operations import digest
    from maverick.memory_ownership import source_allowed

    from . import auth
    from ._shared import _world
    world = _world()
    material = []
    for entry in after.values():
        record = proposals.get(entry.get("proposal_id"))
        if record is None:
            raise HTTPException(409, "An instruction's source is unavailable. Review current instructions before changing them.")
        identity = record.get("goal_id")
        if identity is None:
            material.append([record["id"], None])
            continue
        goal = world.get_goal(identity)
        if goal is None or not auth.can_access_goal(request, goal):
            raise HTTPException(403, "An instruction's original task is no longer available to your account.")
        owner = record.get("proposed_by") if goal.owner else ""
        if learning and not source_allowed(identity, owner, world=world):
            raise HTTPException(409, "An instruction's original task no longer permits learning. It cannot be applied or brought back.")
        material.append([record["id"], identity, goal.owner, goal.updated_at, goal.domain, goal.status])
    return digest(sorted(material))


def _instruction_authority(request, context):
    from .draft_api import context_for
    require_global_permission(request, "admin")
    if context is not None and context_for(request) != context:
        raise HTTPException(409, "Your account or workspace changed. Reload before reviewing instructions.")


def instruction_review(request, proposal_id, context=None):
    from maverick import harness_refine
    from maverick.authority_effects import authority_effect_fence

    from .draft_api import context_for
    from .learning_ops_api import _refine_preview
    with authority_effect_fence():
        _instruction_authority(request, context)
        bound = context_for(request)
        review = harness_refine.change_review(proposal_id)
        rows = {row["id"]: row for row in harness_refine.list_proposals()}
        readable = {**review["after"], "selected": {"proposal_id": proposal_id}}
        readable.update({"before-" + str(i): change["before"] for i, change in enumerate(review["changes"]) if change["before"]})
        _instruction_sources(request, readable, rows, learning=False)
        source_revision = _instruction_sources(request, review["after"], rows)
        plain = _refine_preview(proposal_id)
        if plain["revision"] != review["revision"]:
            raise HTTPException(409, "Instructions changed while reading the full review. Review them again.")
        _instruction_authority(request, bound)
        return {**plain, "context": bound, "source_revision": source_revision, "review_expires_at": time.time() + 300}


def original_instruction_result(request, proposal_id, revision, operation_id, context, *, undo):
    from maverick import harness_refine
    from maverick.authority_effects import authority_effect_fence

    def guard(record):
        _instruction_authority(request, context)
        _instruction_sources(request, {"selected": {"proposal_id": record["id"]}}, {record["id"]: record}, learning=False)

    with authority_effect_fence():
        _instruction_authority(request, context)
        try:
            value = _bound(request, lambda: harness_refine.decision_receipt(
                proposal_id, operation_id, actor=_actor(request), context=context,
                revision=revision, undo=undo, before_read=guard))()
        except harness_refine.RefineError as exc:
            raise HTTPException(409, "The original instruction result is unavailable or does not match this account and review. No change was repeated.") from exc
        _instruction_authority(request, context)
    if value is None:
        return None
    confirmed = value["status"] == "confirmed"
    message = ("Your original reviewed instructions were restored." if undo else
               "Your original approved instruction change was applied.") if confirmed else (
                   "The original decision started, but its result cannot be confirmed. Do not repeat it. Review current instructions with an administrator before another change.")
    if value.get("resolved"):
        message = "The original result remains unconfirmed. A separately recorded human acceptance of the reviewed current instructions closed its hold. The original decision was not repeated."
    if confirmed:
        if value["current_matches"] is False:
            message += " This is a historical receipt; current instructions have changed since that decision."
        elif value["current_matches"] is None:
            message += " This is the original receipt; current instructions could not be checked."
        message += " A recorded change does not prove better results or complete activity recording."
    return {"status": "confirmed" if confirmed else "unconfirmed", "message": message,
            "title": str(value["name"]).replace("-", " ").replace("_", " "),
            "recorded_at": value.get("completed_at"), "current_matches": value["current_matches"],
            "hold_closed": bool(value.get("resolved")), "reconciled_at": (value.get("resolved") or {}).get("recorded_at")}


@router.get("/refinements/{proposal_id}/review")
def review_instruction(request: Request, proposal_id: str, response: Response,
                       context: str = Query(pattern=r"^[0-9a-f]{64}$")):
    response.headers["Cache-Control"] = "private, no-store"
    return instruction_review(request, proposal_id, context)


@router.post("/refinements/{proposal_id}/receipt")
def read_instruction_result(request: Request, proposal_id: str, body: DecisionReceiptIn, response: Response):
    from .app import _require_same_origin
    _require_same_origin(request)
    response.headers["Cache-Control"] = "private, no-store"
    result = original_instruction_result(request, proposal_id, body.revision, body.operation_id,
                                         body.context, undo=body.undo)
    return {"receipt": result, "message": result["message"] if result else (
        "No original result can be confirmed yet. The request may still be processing. No change was repeated; check again before another decision.")}


@router.post("/refinements/{proposal_id}/reconciliation-review")
def review_reconciliation(request: Request, proposal_id: str, body: DecisionReceiptIn, response: Response):
    return _review_reconciliation(request, proposal_id, body, response)


def _review_reconciliation(request, proposal_id, body, response, *, successor=False):
    from maverick import harness_refine
    from maverick.authority_effects import authority_effect_fence

    from .app import _require_same_origin
    _require_same_origin(request)
    response.headers["Cache-Control"] = "private, no-store"
    with authority_effect_fence():
        _instruction_authority(request, body.context)
        try:
            review = _bound(request, lambda: harness_refine.reconciliation_review(
                body.operation_id, actor=_actor(request), context=body.context, revision=body.revision,
                proposal_id=proposal_id, undo=body.undo,
                before_read=lambda current, rows: _instruction_sources(request, current, rows, learning=False), successor=successor))()
        except harness_refine.RefineError as exc:
            raise HTTPException(409, "This original decision has no current-state hold to review. Check its original result.") from exc
        source_revision = _instruction_sources(request, review["current"], review["proposals"])
        _instruction_authority(request, body.context)
        return {"revision": review["revision"], "source_revision": source_revision, "review_expires_at": time.time() + 300,
                "instructions": [{"title": str(row["name"]).replace("-", " "), "text": row["change"]} for row in review["current"].values()],
                "message": "This is a new human acceptance of the full current instruction set. It closes the hold without confirming or repeating the original decision. No instruction text is changed; future instruction management becomes available."}


@router.post("/refinements/{proposal_id}/reconcile")
def reconcile_instruction(request: Request, proposal_id: str, body: ReconciliationIn, response: Response):
    return _reconcile_instruction(request, proposal_id, body, response)


def _reconcile_instruction(request, proposal_id, body, response, *, successor=False):
    from maverick import harness_refine
    from maverick.authority_effects import authority_effect_fence

    from .app import _require_same_origin
    _require_same_origin(request)
    response.headers["Cache-Control"] = "private, no-store"

    def guard(current, rows):
        _instruction_authority(request, body.context)
        if not time.time() < body.review_expires_at <= time.time() + 600:
            raise HTTPException(409, "This current-state review expired. Review current instructions again.")
        if _instruction_sources(request, current, rows) != body.source_revision:
            raise HTTPException(409, "An instruction source changed after review. Review current instructions again.")

    with authority_effect_fence():
        _instruction_authority(request, body.context)
        _audit(request, "instruction_current_state_acceptance_requested", proposal=proposal_id,
               original=body.operation_id, operation_id=body.recovery_id, review=body.reconciliation_revision)
        try:
            accepted = _bound(request, lambda: harness_refine.reconcile_decision(
                body.operation_id, actor=_actor(request), context=body.context, revision=body.revision, proposal_id=proposal_id,
                undo=body.undo, expected_revision=body.reconciliation_revision, recovery_id=body.recovery_id,
                before_write=guard, successor=successor))()
        except harness_refine.RefineError as exc:
            raise HTTPException(409, "The current instructions or original hold changed. Review again; no original decision was repeated.") from exc
        if not accepted.get("replayed"):
            _audit(request, "instruction_current_state_accepted", original=body.operation_id,
                   operation_id=accepted["request"], recorded_at=accepted["recorded_at"], successor=successor)
        return {"receipt": shared_instruction_result(request, body.operation_id, body.context, body.recovery_id) if successor else
                original_instruction_result(request, proposal_id, body.revision, body.operation_id, body.context, undo=body.undo)}


def shared_instruction_result(request, operation_id, context, recovery_id=None):
    from maverick import harness_refine
    from maverick.authority_effects import authority_effect_fence
    with authority_effect_fence():
        _instruction_authority(request, context)
        try:
            value = _bound(request, lambda: harness_refine.shared_hold_receipt(
                operation_id, recovery_id, actor=_actor(request), context=context))()
        except harness_refine.RefineError as exc:
            raise HTTPException(409, "This hold is unavailable or was closed by a different acceptance. Reload current instructions; no original action was repeated.") from exc
        _instruction_authority(request, context)
        return {"status": "confirmed" if value else "unconfirmed", "hold_closed": bool(value),
                "recorded_at": value.get("recorded_at") if value else None,
                "message": "Your separate acceptance of the reviewed current instructions closed the hold. The earlier original action remains unconfirmed and was not repeated." if value else
                "An earlier decision left a hold on shared instructions. You can separately review and accept the full current instructions with your own current authority; this does not confirm or repeat the earlier action."}


@router.post("/instruction-holds/{operation_id}/receipt")
def read_shared_hold(request: Request, operation_id: str, body: SharedHoldIn, response: Response):
    from .app import _require_same_origin
    _require_same_origin(request)
    response.headers["Cache-Control"] = "private, no-store"
    return {"receipt": shared_instruction_result(request, operation_id, body.context, body.recovery_id)}


@router.post("/instruction-holds/{operation_id}/review")
def review_shared_hold(request: Request, operation_id: str, body: SharedHoldIn, response: Response):
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise HTTPException(404, "This instruction hold is unavailable.")
    internal = DecisionReceiptIn(operation_id=operation_id, context=body.context, revision="0" * 64)
    return _review_reconciliation(request, "0" * 16, internal, response, successor=True)


@router.post("/instruction-holds/{operation_id}/accept")
def accept_shared_hold(request: Request, operation_id: str, body: SharedAcceptanceIn, response: Response):
    if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
        raise HTTPException(404, "This instruction hold is unavailable.")
    internal = ReconciliationIn(**body.model_dump(), operation_id=operation_id, revision="0" * 64)
    return _reconcile_instruction(request, "0" * 16, internal, response, successor=True)


def change_current_refinement(request: Request, proposal_id: str, revision: str, *, undo: bool,
                              operation_id=None, context=None, source_revision=None, review_expires_at=None) -> dict:
    """Shared current-state decision for the native review and Copilot."""
    require_global_permission(request, "admin")
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        raise HTTPException(status_code=404, detail="That suggestion no longer exists. Refresh the page.")
    from maverick import harness_refine
    from maverick.audit import AuditRefused
    from maverick.authority_effects import authority_effect_fence
    from maverick.learning_guard import Halted
    actor = _actor(request)
    if (operation_id is None) != (context is None):
        raise HTTPException(422, "Open the full instruction review before confirming this decision.")

    def guard(record, after, proposals):
        _instruction_authority(request, context)
        if review_expires_at is not None and not time.time() < review_expires_at <= time.time() + 600:
            raise HTTPException(409, "This instruction review expired. Open the full current review before deciding.")
        current = _instruction_sources(request, after, proposals)
        if source_revision is not None and source_revision != current:
            raise HTTPException(409, "An instruction's source changed after review. Review the complete change again.")

    def change():
        with authority_effect_fence():
            _instruction_authority(request, context)
            if operation_id is not None:
                receipt = original_instruction_result(request, proposal_id, revision, operation_id, context, undo=undo)
                if receipt is not None:
                    return {"receipt": receipt}
            _audit(request, "instruction_restore_requested" if undo else "instruction_apply_requested",
                   proposal=proposal_id, operation_id=operation_id or "", review=revision)
            if undo:
                result = harness_refine.revert(proposal_id, reverted_by=actor, expected_revision=revision,
                                               operation_id=operation_id, context=context or "", before_write=guard)
                if not result:
                    raise HTTPException(409, "There is nothing to undo for this change. Refresh the current instructions.")
                out = {"reverted": True}
            else:
                result = harness_refine.apply(proposal_id, applied_by=actor, expected_revision=revision,
                                              operation_id=operation_id, context=context or "", before_write=guard)
                out = {"proposal": _proposal_view(result)}
            if operation_id is not None:
                out["receipt"] = original_instruction_result(request, proposal_id, revision, operation_id, context, undo=undo)
            _instruction_authority(request, context)
            return out
    try:
        return _bound(request, change)()
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc
    except AuditRefused as exc:
        raise HTTPException(503, "The instruction result could not be fully recorded. Refresh current instructions before another decision.") from exc
    except harness_refine.RefineError as exc:
        if operation_id is not None:
            receipt = original_instruction_result(request, proposal_id, revision, operation_id, context, undo=undo)
            if receipt is not None:
                raise HTTPException(503, "The original instruction result needs checking. Use Check original result; do not apply or restore again.") from exc
        if "instruction review no longer matches" in str(exc):
            raise HTTPException(409, "The instructions changed after review. Refresh and review the full change again.") from exc
        raise _refine_problem(exc) from exc


async def _review_decision(request: Request, proposal_id: str, body: ReviewDecisionIn | None, *, undo: bool) -> dict:
    from .app import _require_same_origin
    _require_same_origin(request)
    require_global_permission(request, "admin")
    if not _PROPOSAL_ID.fullmatch(proposal_id):
        raise HTTPException(status_code=404, detail="That suggestion no longer exists. Refresh the page.")
    if body is None:
        raise HTTPException(422, "Open the current instruction review and confirm it before deciding.")
    return await run_in_threadpool(lambda: change_current_refinement(
        request, proposal_id, body.revision, undo=undo, operation_id=body.operation_id,
        context=body.context, source_revision=body.source_revision, review_expires_at=body.review_expires_at))


@router.post("/refinements/{proposal_id}/apply")
async def apply_refinement(request: Request, proposal_id: str, body: ReviewDecisionIn | None = None) -> dict:
    return await _review_decision(request, proposal_id, body, undo=False)


@router.post("/refinements/{proposal_id}/revert")
async def revert_refinement(request: Request, proposal_id: str, body: ReviewDecisionIn | None = None) -> dict:
    return await _review_decision(request, proposal_id, body, undo=True)


# ---- self-harness read-outs ---------------------------------------------

def _known_models() -> list[str]:
    from maverick import self_harness
    models = set(self_harness.list_learned().keys())
    try:
        from maverick.llm import model_for_role
        models.add(model_for_role("orchestrator"))
    except Exception:  # noqa: BLE001 -- no provider configured yet
        pass
    return sorted(m for m in models if m)[:50]


def _model_choice(model_id: str | None) -> str | None:
    """The chosen model, only when it is one the page offered."""
    if not model_id:
        return None
    if model_id not in _known_models():
        raise HTTPException(status_code=422, detail="Choose a model from the list.")
    return model_id


def _scope_label(scope: str) -> str:
    if not scope:
        return "All work"
    kind, _, name = scope.partition("=")
    return {"domain": "Department", "role": "Role", "tool": "Tool"}.get(kind, "Specific work") + (": " + name if name else "")


def _current_lesson(model_id: str, token: str) -> dict:
    from maverick import self_harness
    model = _model_choice(model_id)
    row = next((item for item in self_harness.line_efficacy(model)
                if item["token"] == token), None)
    if row is None:
        raise HTTPException(409, "That lesson changed or is no longer available. Refresh the lesson results before deciding.")
    return row


def withdraw_current_lesson(request: Request, model_id: str, token: str, reason: str) -> dict:
    """One guarded mutation shared by the native page and Copilot."""
    from maverick import self_harness
    require_global_permission(request, "admin")
    if not 10 <= len(reason.strip()) <= 1000:
        raise HTTPException(422, "Explain why this lesson should be removed in at least 10 characters.")
    row = _current_lesson(model_id, token)
    _audit(request, "withdraw_lesson_requested", model_id=model_id,
           lesson_token=token, reason=reason)
    try:
        removed = _bound(request, lambda: self_harness.forget_addendum(
            model_id, line=row["text"], scope=row["scope"], expected_token=token))()
    except ValueError as exc:
        raise HTTPException(409, "That lesson changed. Refresh the lesson results before deciding.") from exc
    if not removed:
        raise HTTPException(409, "That lesson is no longer available. Refresh the lesson results.")
    _audit(request, "withdraw_lesson_completed", model_id=model_id,
           lesson_token=token, reason=reason)
    return {"removed": True, "model_id": model_id, "token": token}


@router.get("/models")
async def models(request: Request) -> dict:
    require_permission(request, "view")
    from maverick import self_harness

    from .plain_labels import model_label
    names = await run_in_threadpool(_known_models)
    return {"enabled": bool(self_harness.enabled()),
            "models": [{"value": m, "label": model_label(m)} for m in names]}


@router.get("/efficacy")
async def efficacy(request: Request, model_id: str = Query(min_length=1, max_length=200)) -> dict:
    require_permission(request, "view")
    from maverick import self_harness
    model = await run_in_threadpool(_model_choice, model_id)
    rows = await run_in_threadpool(self_harness.line_efficacy, model)
    return {"lessons": [
        {"text": r["text"], "department": (r.get("domain") or ""),
         "scope_label": _scope_label(r["scope"]), "token": r["token"],
         "went_well": int(r.get("success") or 0), "did_not": int(r.get("failure") or 0),
         "rate": r.get("rate"), "recent_rate": r.get("recent_rate")}
        for r in rows[:200]
    ], "truncated": len(rows) > 200}


@router.post("/lessons/withdraw")
async def withdraw_lesson(request: Request, body: WithdrawLessonIn) -> dict:
    from .app import _require_same_origin
    _require_same_origin(request)
    return await run_in_threadpool(withdraw_current_lesson, request, body.model_id,
                                   body.token, body.reason.strip())


@router.get("/conflicts")
async def conflicts(request: Request, model_id: str | None = Query(default=None, max_length=200)) -> dict:
    require_permission(request, "view")
    from maverick import self_harness
    model = await run_in_threadpool(_model_choice, model_id)
    # This wording check is read-only. The separately reviewed native meaning
    # check on the Learned page uses the governed learning-job admission.
    pairs = await run_in_threadpool(self_harness.detect_store_conflicts, model)
    return {"pairs": [{"model": m, "first": _clip(a), "second": _clip(b)}
                      for m, a, b in pairs[:_MAX_ROWS]], "truncated": len(pairs) > _MAX_ROWS}


def _log_rows(limit: int) -> list[dict]:
    from maverick.audit import EventKind, default_audit_log
    rows: list[dict] = []
    for ev in default_audit_log().tail(2000):
        if ev.get("kind") == EventKind.LEARNING_UPDATE and ev.get("agent") == "self_harness":
            rows.append(ev)
    rows = rows[-limit:]
    rows.reverse()
    return [{"when": ev.get("ts") if isinstance(ev.get("ts"), (int, float)) else None,
             "what": _PHASE_LABELS.get(str(ev.get("phase") or ""), "Learning activity"),
             "model": "",
             "lesson": ""}
            for ev in rows]


@router.get("/log")
async def learning_log(request: Request, limit: int = Query(default=30, ge=1, le=_MAX_ROWS)) -> dict:
    require_permission(request, "audit")
    try:
        events = await run_in_threadpool(_log_rows, limit)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The learning activity is not available right now.") from exc
    return {"events": events, "privacy_notice": (
        "Activity shows metadata only. Lesson content is available only when its "
        "current shared-source ownership can be verified.")}


def _preview(model_id: str | None, min_support: int) -> dict:
    from maverick import reflexion, self_harness
    if not self_harness.enabled():
        from .learning_ops_api import switch_place
        return {"enabled": False, "weaknesses": [], "scanned": 0, "eligible": 0,
                "switch_place": switch_place("self_harness")}
    if not model_id:
        from maverick.llm import model_for_role
        model_id = model_for_role("orchestrator")
    records = [r.to_dict() for r in reflexion.list_recent(limit=500) if r.owner == ""]
    eligible = self_harness.count_eligible(records, model_id=model_id)
    sigs = self_harness.mine_failures(records, model_id=model_id, min_support=min_support)
    out = []
    for sig in sigs[:50]:
        proposal = self_harness.propose_addendum(sig)
        out.append({"pattern": _clip(sig.signature), "times": int(sig.support),
                    "kind": str(sig.failure_class or "").replace("_", " "),
                    "would_add": _clip(proposal.addendum_line) if proposal else ""})
    return {"enabled": True, "model": model_id, "weaknesses": out,
            "scanned": len(records), "eligible": eligible}


@router.get("/preview")
async def preview(request: Request, model_id: str | None = Query(default=None, max_length=200),
                  min_support: int = 3) -> dict:
    require_permission(request, "audit")
    if min_support not in (2, 3, 5):
        raise HTTPException(status_code=422, detail="Choose from the list.")
    model = await run_in_threadpool(_model_choice, model_id)
    try:
        return await run_in_threadpool(_preview, model, min_support)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The preview is not available right now.") from exc


def _holdout() -> dict:
    from maverick.config import get_self_harness
    configured = str(get_self_harness().get("holdout_ledger") or "").strip()
    if not configured:
        return {"configured": False}
    from pathlib import Path

    from maverick.self_harness_holdout import HoldoutLedgerError, HoldoutQueryLedger
    try:
        status = HoldoutQueryLedger(Path(configured)).verify()
    except (HoldoutLedgerError, OSError, ValueError):
        return {"configured": True, "verified": False}
    return {"configured": True, "verified": True, "events": int(status.ledger_events),
            "checks_used": int(status.queries),
            "technical": {"ledger": str(status.ledger_id),
                          "latest_fingerprint": str(status.ledger_tip_sha256 or "")[:16]}}


@router.get("/holdout")
async def holdout(request: Request) -> dict:
    require_permission(request, "audit")
    try:
        return await run_in_threadpool(_holdout)
    except Exception as exc:  # noqa: BLE001 -- plain message, no internals
        raise HTTPException(status_code=503, detail="The hold-out check is not available right now.") from exc


# ---- changes that spend evaluation budget ----------------------------------

def _gate_place() -> str:
    from .learning_ops_api import part_place
    return part_place("self_improvement")


def _report_view(report: Any, retired: int) -> dict:
    return {"model": str(getattr(report, "model_id", "") or ""),
            "patterns_found": int(report.mined), "ideas_tried": int(report.proposed),
            "passed_testing": int(report.validated), "adopted": int(report.promoted),
            "retired": int(retired or 0),
            "paused_for_calibration": bool(report.frozen),
            "promotion_gate_off": not bool(report.gate_enabled),
            "gate_place": _gate_place() if not report.gate_enabled else "",
            "new_lessons": [_clip(ln) for ln in list(report.applied_lines)[:20]],
            "back_on_trial": [_clip(ln) for ln in list(getattr(report, "relapsed", []) or [])[:20]]}


def _instructions(text: str | None) -> str | None:
    if not text:
        return None
    from maverick import self_improvement_runner as runner
    try:
        return runner.read_evaluation_system(text.encode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=(
            "That instructions file is empty, larger than 1 MB, or not plain text.")) from exc


@router.post("/run")
async def run_now(request: Request, body: RunIn) -> dict:
    """``self-harness run``: a background job the page polls. Every model
    (``--all-models``) can be stopped between models."""
    require_global_permission(request, "admin")
    from maverick import self_harness
    from maverick import self_improvement_runner as runner
    from maverick.learning_guard import Halted, check_learning_halt

    from .learning_ops_api import start_job
    if not self_harness.enabled():
        from .learning_ops_api import switch_place
        raise HTTPException(status_code=409, detail=(
            f"Self-coaching is off. Turn on {switch_place('self_harness')} first."))
    evaluation_system = _instructions(body.instructions)
    chosen = None
    if body.scope == "chosen":
        chosen = await run_in_threadpool(_model_choice, body.model_id)
        if not chosen:
            raise HTTPException(status_code=422, detail="Choose a model from the list.")
    try:
        check_learning_halt("self-harness", "dashboard-start")
    except Halted as exc:
        raise HTTPException(status_code=409, detail=_HALTED) from exc
    stage = True if body.trial else None
    _audit(request, "self_harness_run", scope=body.scope, model=chosen or "", trial=body.trial,
           retire=body.retire, with_instructions=evaluation_system is not None)

    def _run(job) -> dict:
        check_learning_halt("self-harness", "dashboard-start")
        if body.scope == "all":
            results = runner.run_self_harness_all_models(
                retire=body.retire, canary=stage, evaluation_system=evaluation_system,
                should_stop=lambda: job.cancelled)
            return {"reports": [_report_view(rep, ret) for _m, (rep, ret) in sorted(results.items())][:20]}
        pin = {"model_id": chosen} if chosen else {}
        report, retired = runner.run_self_harness_cycle(
            retire=body.retire, canary=stage, evaluation_system=evaluation_system, **pin)
        return {"reports": [_report_view(report, retired)]}
    return start_job(request, "self_harness_run", "Learning pass", _run, cancellable=body.scope == "all")


@router.post("/corpus-quality")
async def corpus_quality(request: Request, body: QualityIn) -> dict:
    """``self-harness corpus quality`` (with ``--retire`` when asked): a
    background job. Removing test cases needs an explicit confirmation."""
    require_global_permission(request, "admin")
    from maverick import self_harness
    from maverick import self_harness_eval as ev
    from maverick import self_improvement_runner as runner

    from .learning_ops_api import start_job
    cpath = self_harness.settings().get("eval_corpus")
    if not cpath:
        return {"configured": False, "cases": []}
    if body.remove_weak and not body.confirm:
        raise HTTPException(status_code=422, detail="Confirm removing the test cases first.")
    model = await run_in_threadpool(_model_choice, body.model_id)
    _audit(request, "self_harness_corpus_quality", model=model or "", retire=body.remove_weak)

    def _run(job) -> dict:
        rows = runner.run_corpus_quality(key=model, samples=2, raise_errors=True)
        removed = 0
        weak = [r["goal"] for r in rows if not r.get("discriminative")]
        if body.remove_weak and weak:
            from maverick.llm import model_for_role
            removed = ev.retire_corpus_cases(cpath, model or model_for_role("orchestrator"), weak)
        return {"configured": True, "removed": int(removed), "cases": [
            {"task": _clip(str(r.get("goal") or "").splitlines()[0] if r.get("goal") else ""),
             "useful": bool(r.get("discriminative")), "baseline_rate": r.get("baseline_rate"),
             "age_days": r.get("age_days")}
            for r in rows[:200]
        ]}
    return start_job(request, "self_harness_corpus_quality", "Checking the test cases", _run)


__all__ = ["router"]
