"""One owner-bound journey across existing memory and data-rights stores."""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from maverick import personal_identity, personal_memory
from maverick.authority_effects import authority_effect_fence
from pydantic import BaseModel, ConfigDict, Field

from . import auth
from .draft_api import context_for

router = APIRouter(tags=["personal-data"])


def _app():
    from . import app
    return app


def _actor(request):
    auth.require_permission(request, "view")
    principal = auth.caller_principal(request)
    if not principal or not personal_identity.principal_current(principal):
        raise HTTPException(403, "Use a named account with current workspace membership for My Data.")
    if not personal_identity.enabled():
        raise HTTPException(403, "My Data controls are disabled. Ask your administrator to enable Personal data controls in Settings.")
    return principal


def _write_actor(request, body):
    _app()._require_same_origin(request)
    actor = _actor(request)
    if not hmac.compare_digest(body.context, context_for(request)):
        raise HTTPException(409, "Your account or workspace changed. Refresh before changing personal data.")
    return actor


def _call(operation):
    try:
        result = operation()
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"}) if isinstance(result, dict) else result
    except personal_identity.IdentityConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "Personal data could not be confirmed. Refresh its saved state before retrying; no success is claimed.") from exc


class ContextIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    context: str = Field(pattern=r"^[a-f0-9]{64}$")


class BeginIn(ContextIn):
    channel: str = Field(max_length=32)


class RevisionIn(ContextIn):
    expected_revision: int = Field(ge=0)


class ScopeIn(ContextIn):
    channel: str = Field(min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=256)


class MemoryIn(ScopeIn):
    expected_revision: int = Field(ge=0)
    recall: bool | None = None
    note: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    operation: Literal["delete", "suppress", "edit"] | None = None
    replacement: str | None = Field(default=None, max_length=160)


class ShareIn(RevisionIn):
    source: str | None = Field(default=None, max_length=64)


def _state(request):
    actor = _actor(request)
    from maverick.config import get_dreaming, load_config
    cfg = load_config()
    scopes = personal_memory.owned_scopes(actor)
    result = []
    for scope in scopes:
        record = personal_memory.controls(scope["channel"], scope["user_id"])
        notes = personal_memory.records(scope["channel"], scope["user_id"])
        result.append({**scope, "revision": record["revision"], "recall": record["recall"], "notes": notes})
    return {"context": context_for(request), "identity": personal_identity.state(actor),
            "scopes": result, "sharing": personal_memory.profile(actor),
            "policy": {"personal_recall": get_dreaming().get("user_notes") is True,
                       "organization_lesson_promotion": get_dreaming().get("promote_shared") is True,
                       "workflow_examples": "Existing workflow and task learning policy; exclude a task below to stop future local learning.",
                       "model_skill_learning": "Source-bound local collection, training ingestion, donation and skill distillation honor task exclusions. Prior models and external/manual datasets are not unlearned.",
                       "retention": cfg.get("retention", {}),
                       "companion": "Administrator sources, tools, memory and retention ceilings remain separate. Personal sharing grants no companion capability."}}


@router.get("/my-data")
def page(request: Request):
    state = _call(lambda: _state(request))
    return _app().templates.TemplateResponse(request, "personal_data.html", {"personal_data": state},
                                             headers={"Cache-Control": "private, no-store"})


@router.get("/api/v1/my-data")
def state(request: Request):
    return _call(lambda: _state(request))


@router.post("/api/v1/my-data/links")
def begin_link(request: Request, body: BeginIn):
    return _call(lambda: personal_identity.begin(_write_actor(request, body), body.channel))


@router.post("/api/v1/my-data/links/{identifier}/confirm")
def confirm_link(request: Request, identifier: str, body: RevisionIn):
    return _call(lambda: personal_identity.confirm(_write_actor(request, body), identifier,
                                                   expected_revision=body.expected_revision))


@router.post("/api/v1/my-data/links/{identifier}/unlink")
def unlink(request: Request, identifier: str, body: RevisionIn):
    return _call(lambda: personal_identity.unlink(_write_actor(request, body), identifier,
                                                  expected_revision=body.expected_revision))


@router.post("/api/v1/my-data/memory")
def memory(request: Request, body: MemoryIn):
    return _call(lambda: personal_memory.update(_write_actor(request, body), body.channel, body.user_id,
                 expected_revision=body.expected_revision, recall=body.recall, note=body.note,
                 operation=body.operation, replacement=body.replacement))


@router.post("/api/v1/my-data/sharing")
def sharing(request: Request, body: ShareIn):
    return _call(lambda: personal_memory.share(_write_actor(request, body), source=body.source,
                                               expected_revision=body.expected_revision))


@router.get("/api/v1/my-data/source/{conversation_id}/{turn_id}")
def source(request: Request, conversation_id: int, turn_id: int):
    def read():
        actor = _actor(request)
        scopes = {(s["channel"], s["user_id"]) for s in personal_memory.owned_scopes(actor)}
        world = _app()._world()
        conversation = next((c for c in world.list_conversations() if c.id == conversation_id
                             and (c.channel, c.user_id) in scopes), None)
        if conversation is None:
            raise HTTPException(404, "This source is no longer available to your account.")
        turn = next((t for t in world.recent_turns(conversation_id, limit=5000) if t.id == turn_id and t.role == "user"), None)
        if turn is None:
            raise HTTPException(404, "This source was removed or is outside the retained source window.")
        return {"conversation_id": conversation_id, "turn_id": turn_id, "text": turn.content,
                "date": turn.ts, "revision": hashlib.sha256(turn.content.encode()).hexdigest()}
    return _call(read)


def _inventory(request):
    from maverick.audit.preservation import preservation_snapshot
    from maverick.erasure_receipts import AUXILIARY_ERASURE_STORES, RECEIPT_STORES
    from maverick.pilot_learning import excluded
    from .draft_store import DraftStore

    actor = _actor(request)
    world = _app()._world()
    scopes = personal_memory.owned_scopes(actor)
    exact = {(s["channel"], s["user_id"]) for s in scopes}
    conversations = [{"id": c.id, "channel": c.channel, "user_id": c.user_id, "last_seen": c.last_seen}
                     for c in world.list_conversations() if (c.channel, c.user_id) in exact]
    goals = world.list_goals(limit=5001, order="desc", owner=actor)
    tasks = [{"id": g.id, "title": g.title, "status": g.status, "local_learning_excluded": excluded(g.id)} for g in goals[:5000]]
    holds = preservation_snapshot()
    stores = [
        {"name": "Personal preference notes", "state": "available", "action": "Edit, suppress or turn off recall above", "use": "Personal recall only", "expiry": "Until source rebuild, correction, erasure or policy restriction"},
        {"name": "Conversations, task details, attachments and subject facts", "state": "available", "action": "Export one verified scope below; request governed erasure in Compliance", "use": "Task execution and retained business history", "stores": list(RECEIPT_STORES)},
        {"name": "Erasure dependencies", "state": "review-required", "action": "Signed erasure closure reports each auxiliary store", "stores": list(AUXILIARY_ERASURE_STORES), "use": "Evidence of completed erasure; retained audit proof is distinct from content"},
        {"name": "Shared packets, knowledge derivatives and previously trained models", "state": "external-unverified", "action": "Review Knowledge Access source lineage and rights request; copied/external data needs its custodian", "use": "Each source purpose and consent apply independently; local deletion is not model unlearning", "href": "/knowledge-access"},
    ]
    try:
        draft_inventory = DraftStore(context_for(request)).inventory()
        stores.append({"name": "Private editor recovery copies", "state": "available", "inventory": draft_inventory,
                       "use": "Recover your drafts; not automatic memory", "action": "Open the original editor to restore/discard; retire discarded copies in recovery storage"})
    except Exception:
        stores.append({"name": "Private editor recovery copies", "state": "unavailable", "action": "Retry recovery storage; no zero count is claimed"})
    try:
        from maverick.employee_companion import EmployeeCompanionStore
        companion = EmployeeCompanionStore()
        policy = companion.policy(actor)
        history = companion.history(actor)
        stores.append({"name": "Employee companion history", "state": "available", "count": len(history),
                       "expiry": str(policy["retention_days"]) + " days under administrator policy",
                       "use": "Private owner history; audit metadata only for administrators",
                       "action": "Clear your history in Employee companion", "href": "/employee-companion"})
    except Exception:
        stores.append({"name": "Employee companion history", "state": "unavailable", "action": "Open Employee companion to review its current policy; no zero count is claimed"})
    return {"context": context_for(request), "holds": holds, "scopes": scopes, "conversations": conversations,
            "tasks": tasks, "tasks_truncated": len(goals) > 5000, "stores": stores,
            "limits": "Links authorize this personal-data view, not task mutation. Copies, shared packets, external systems and prior trained derivatives are separate; nothing here claims they are deleted."}


@router.get("/api/v1/my-data/inventory")
def inventory(request: Request):
    return _call(lambda: _inventory(request))


@router.post("/api/v1/my-data/export")
def export_scope(request: Request, body: ScopeIn):
    def operation():
        from maverick.audit import audit_event
        from maverick.dsar import export_subject_data
        actor = _write_actor(request, body)
        with authority_effect_fence():
            personal_memory.require_scope(actor, body.channel, body.user_id)
            if audit_event("personal_data_exported", actor=actor, channel=body.channel) is not True:
                raise RuntimeError("The export could not be recorded.")
            bundle = export_subject_data(body.user_id, channel=body.channel, strict=True, world=_app()._world())
            bundle["personal_notes"] = personal_memory.records(body.channel, body.user_id)
            bundle["memory_controls"] = personal_memory.controls(body.channel, body.user_id)
            bundle["limits"] = "This export covers the selected verified conversation scope. Drafts, companion history, shared packets, derivatives and external copies have separate inventory and controls."
            return Response(json.dumps(bundle, indent=2, default=str), media_type="application/json",
                            headers={"Content-Disposition": 'attachment; filename="lightwork-my-data.json"', "Cache-Control": "private, no-store"})
    return _call(operation)


@router.post("/api/v1/my-data/tasks/{goal_id}/exclude-learning")
def exclude_learning(request: Request, goal_id: int, body: ContextIn):
    def operation():
        from maverick import pilot_learning
        actor = _write_actor(request, body)
        with authority_effect_fence():
            _actor(request)
            goal = _app()._world().get_goal(goal_id)
            if goal is None or goal.owner != actor:
                raise HTTPException(403, "Only the task owner can narrow this task's learning use.")
            if not pilot_learning.excluded(goal_id):
                record = pilot_learning.protect(goal_id, pilot_id="personal-" + hashlib.sha256(actor.encode()).hexdigest()[:24],
                                                plan_version=1, input_version=1, actor=actor)
                if record.get("_audit_pending"):
                    return {"state": "excluded-audit-pending", "message": "Future local learning is blocked, but its activity record remains unconfirmed."}
            return {"state": "excluded", "message": "Future automatic local reuse and learning are blocked for this task and descendants. Business history is retained; prior models, copied examples and external/manual datasets are not unlearned."}
    return _call(operation)
