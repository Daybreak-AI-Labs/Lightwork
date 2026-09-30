"""Owner-requested, separately reviewed deletion with per-store receipts.

Requests contain scope metadata, never source content. A dispatch receipt is
durable before deletion; an interrupted dispatch remains unknown and is never
automatically retried. Evidence and externally copied data are explicit retained
exceptions, not part of a misleading whole-person deletion certificate.
"""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from maverick import personal_memory
from maverick.audit.preservation import preservation_snapshot
from maverick.authority_effects import authority_effect_fence
from maverick.governed_records import GovernedRecordStore
from maverick.paths import current_tenant_id_strict

from .draft_store import DraftStore

STORE = GovernedRecordStore("personal_data_requests", "PDR", "personal_data_request")
KINDS = {"preference_notes", "conversation_history", "recovery_drafts",
         "companion_history", "experience_preferences", "model_memory", "notification_receipts", "diagnostic_reports", "activation_drafts", "project_defaults"}
DIAGNOSTICS = GovernedRecordStore("owned_task_diagnostics", "OTD", "owned_task_diagnostics")
EXCEPTIONS = [
    {"store": "audit_and_governance_evidence", "state": "retained", "reason": "Content-minimized activity, policy and deletion receipts follow their evidence retention policy."},
    {"store": "shared_packets_and_knowledge", "state": "external-unverified", "reason": "Shared outputs, indexed derivatives and purpose-bound knowledge need their own source rights request.", "href": "/knowledge-access"},
    {"store": "trained_models_and_external_copies", "state": "external-unverified", "reason": "Local deletion cannot unlearn prior models, remove manually copied data or certify provider deletion."},
    {"store": "source_linked_learning", "state": "retained-stop-use", "reason": "Deleted task sources become unavailable to automatic source-bound recall; retained derived files require custodian review and are not claimed purged."},
    {"store": "account_and_privacy_restrictions", "state": "retained", "reason": "Account identity, verified channel links and suppression/recall restrictions remain so deletion cannot silently re-enable collection."},
]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _draft_context(actor):
    return _digest_compact([current_tenant_id_strict(), "principal", actor])


def _digest_compact(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def scope_owner(actor, channel, user_id):
    return actor if channel == "dashboard" else f"user:{channel}:{user_id}"


def _conversation_plan(world, actor, channel, user_id):
    conversations = [c.id for c in world.list_conversations(channel) if c.user_id == user_id]
    plan = world.plan_conversation_erasure(conversations)
    owner = scope_owner(actor, channel, user_id)
    for goal_id in plan["goal_ids"]:
        goal = world.get_goal(goal_id)
        if goal is None or goal.owner != owner:
            raise ValueError("Conversation closure contains work outside this verified owner. A separate governed review is required.")
        if goal.status in {"active", "pending"}:
            raise ValueError("Stop the scope's pending or active tasks before reviewing conversation deletion.")
    # Host attachment paths are execution details, not request metadata.
    return {key: plan[key] for key in ("conversation_ids", "goal_ids", "episode_ids")}


def _memory_files(owner):
    from maverick.tools.memory import memory_root_for
    root = memory_root_for(owner)
    files = []
    if root.exists():
        for path in root.rglob("*"):
            if path.is_symlink() or root not in path.resolve().parents:
                raise ValueError("Model memory contains an unsupported filesystem link; nothing can be deleted here.")
            if path.is_file():
                if len(files) >= 5000:
                    raise ValueError("Model memory exceeds the bounded review window.")
                files.append([path.relative_to(root).as_posix(), path.stat().st_size, path.stat().st_mtime_ns])
    return sorted(files)


def _diagnostic_records(actor):
    rows = DIAGNOSTICS.list(limit=10001)
    if len(rows) > 10000:
        raise ValueError("Diagnostic reports exceed the supported inventory window.")
    return [{"id": row["id"], "revision": row["revision"], "goal_id": row["goal_id"]}
            for row in rows if row.get("owner") == actor]


def preview(actor, channel, user_id, selected, world):
    personal_memory.require_scope(actor, channel, user_id)
    if not selected or len(selected) != len(set(selected)) or not set(selected) <= KINDS:
        raise ValueError("Choose the stores to request for deletion.")
    targets = []
    for kind in selected:
        target = {"store": kind, "state": "requested"}
        if kind == "conversation_history":
            target["scope"] = _conversation_plan(world, actor, channel, user_id)
            sources = []
            for conversation_id in target["scope"]["conversation_ids"]:
                turns = world.recent_turns(conversation_id, limit=100001)
                if len(turns) > 100000:
                    raise ValueError("Conversation exceeds the exact review window; ask the records owner to narrow its scope.")
                sources.extend([turn.id, turn.ts, turn.goal_id, _digest(turn.content)] for turn in turns)
            target["source_revision"] = _digest([sources, [vars(world.get_goal(gid)) for gid in target["scope"]["goal_ids"]]])
            target["use"] = "Selected conversation closure, associated tasks and attachments only; independently owned tasks remain outside this scope."
        elif kind == "preference_notes":
            target["revision"] = personal_memory.controls(channel, user_id)["revision"]
            target["count"] = len(personal_memory.records(channel, user_id))
        elif kind == "recovery_drafts":
            target["inventory"] = DraftStore(_draft_context(actor)).inventory()
        elif kind == "companion_history":
            from maverick.employee_companion import EmployeeCompanionStore
            target["history_revision"] = _digest(EmployeeCompanionStore().history(actor))
        elif kind == "experience_preferences":
            from maverick import experience_preferences
            directory = experience_preferences.data_dir("experience-preferences")
            prefix = experience_preferences._principal_key(actor)
            paths = [directory / (prefix + ".enc"), *directory.glob(prefix + "-thread-*.enc")]
            target["revision"] = _digest([[p.name, p.stat().st_size, p.stat().st_mtime_ns] for p in paths if p.is_file()])
        elif kind == "model_memory":
            files = _memory_files(scope_owner(actor, channel, user_id))
            target.update(count=len(files), revision=_digest(files))
        elif kind == "notification_receipts":
            from maverick.personal_notification_delivery import personal_history_inventory
            target["inventory"] = personal_history_inventory(actor)
            if target["inventory"].get("partial"):
                raise ValueError("Notification history exceeds the complete review window; ask the records owner to narrow it.")
        elif kind == "diagnostic_reports":
            target["records"] = _diagnostic_records(actor)
        elif kind == "activation_drafts":
            from .employee_activation import owned_activation_inventory
            target["records"] = owned_activation_inventory(actor, world=world)
            if any(record["blocked"] for record in target["records"]):
                raise ValueError("Some activation drafts have active or uncertain task work. Reconcile them before reviewing draft erasure.")
        elif kind == "project_defaults":
            from maverick.project_workspaces import owned_defaults_inventory
            target["records"] = owned_defaults_inventory(actor)
        targets.append(target)
    holds = preservation_snapshot()
    result = {"owner": actor, "channel": channel, "user_id": user_id, "targets": targets,
              "holds": holds, "exceptions": EXCEPTIONS,
              "notice": "Deletion needs a different current workspace administrator to confirm preservation obligations. This request does not grant content access."}
    result["snapshot"] = _digest(result)
    return result


def request_deletion(actor, channel, user_id, selected, snapshot, world):
    with authority_effect_fence():
        reviewed = preview(actor, channel, user_id, selected, world)
        if snapshot != reviewed["snapshot"]:
            raise HTTPException(409, "Data or preservation policy changed. Review a fresh deletion request.")
        identifier = "PDR-" + _digest([actor, snapshot])[:40]
        existing = STORE.get(identifier)
        if existing:
            return existing
        return STORE.create({"id": identifier, **reviewed, "status": "held" if reviewed["holds"]["blocked"] else "requested"},
                            action="deletion_requested", actor=actor, max_records=10000)


def list_requests(actor, *, reviewer=False):
    rows = STORE.list(limit=10001)
    if len(rows) > 10000:
        raise ValueError("Deletion requests exceed the supported inventory window.")
    return [row for row in rows if row["owner"] == actor or reviewer]


def _checkpoint(row, actor, mutate):
    result = STORE.update(row["id"], mutate, expected_revision=row["revision"], action="deletion_progress", actor=actor)
    if not result or result.get("_audit_pending"):
        raise RuntimeError("Deletion state was saved but its audit receipt is unconfirmed. Review the saved request before continuing.")
    return result


def _erase_conversations(world, row, target):
    from maverick.cli import _unlink_erasure_attachments
    from maverick.erasure_receipts import build_manifest, persist_receipt
    plan = target["scope"]
    # Never pass private host paths or unrelated planning fields to the proof.
    manifest = build_manifest(tenant_id=current_tenant_id_strict() or "shared", **plan)
    # Business records need an exact durable pre-delete proof. Failure prevents
    # this new reviewed route from dispatching an uncertifiable operation.
    receipt = persist_receipt(world, manifest)
    _, paths, _ = world.erase_conversations(plan["conversation_ids"],
        expected_conversation_ids=plan["conversation_ids"], expected_goal_ids=plan["goal_ids"], expected_episode_ids=plan["episode_ids"])
    removed, failures = _unlink_erasure_attachments(paths)
    residual = world.count_erasure_receipt_residuals(**plan)
    return {"state": "partial" if failures or any(residual.values()) else "purged",
            "receipt_id": receipt["receipt_id"], "stores": {key: {"state": "retained" if count else "purged", "remaining": count} for key, count in residual.items()},
            "attachment_files": {"state": "partial" if failures else "purged", "removed": removed, "failures": len(failures)},
            "retained": "Audit evidence, global caches, knowledge copies and derived-learning stores are separate exceptions, not covered by this database-closure proof."}


def _erase_target(world, row, target):
    kind, actor = target["store"], row["owner"]
    channel, user_id = row["channel"], row["user_id"]
    if kind == "conversation_history":
        return _erase_conversations(world, row, target)
    if kind == "recovery_drafts":
        return DraftStore(_draft_context(actor)).erase_all(expected_version=target["inventory"]["version"])
    if kind == "preference_notes":
        from maverick.file_lock import cross_process_lock
        from maverick.user_notes import erase_notes
        record = personal_memory.controls(channel, user_id)
        with cross_process_lock(personal_memory._path(record["key"]), strict=True):
            record["recall"] = False
            record["edits"] = []
            record["suppressed"] = [personal_memory.suppression(rule["note"], topic=rule.get("topic", False))
                                    if "note" in rule else rule for rule in record["suppressed"]]
            personal_memory._write(record, actor, "notes_erased_recall_stopped")
            removed = erase_notes(channel, user_id, strict=True)
        return {"state": "purged", "removed": removed, "retained": "Personal recall is off. Existing suppression rules remain so erased notes cannot silently regenerate."}
    if kind == "companion_history":
        from maverick.employee_companion import EmployeeCompanionStore
        EmployeeCompanionStore().clear_history(actor, actor=actor)
        return {"state": "purged", "retained": "Administrator companion policy and content-free audit evidence remain."}
    if kind == "experience_preferences":
        from maverick.experience_preferences import erase_personal
        return {"state": "purged", "removed": erase_personal(actor)}
    if kind == "notification_receipts":
        from maverick.personal_notification_delivery import remove_personal_history
        return {"state": "purged", "result": remove_personal_history(actor), "retained": "Notification preferences, provider records and recipient enrollment are separate."}
    if kind == "diagnostic_reports":
        for item in target["records"]:
            if not DIAGNOSTICS.delete(item["id"], expected_revision=item["revision"]):
                raise ValueError("A diagnostic report changed during deletion.")
        return {"state": "purged", "removed": len(target["records"]), "retained": "Download audit and any manually shared support copies are separate."}
    if kind == "activation_drafts":
        from .employee_activation import erase_owned_activation
        for item in target["records"]:
            result = erase_owned_activation(actor, item["id"], expected_revision=item["revision"],
                actor=row["reviewer"], operation_id=_digest([row["id"], kind, item["id"]])[:32], world=world)
            if result["status"] == "unconfirmed":
                raise ValueError("Activation erasure receipt is not confirmed.")
        return {"state": "purged", "removed": len(target["records"]), "retained": "Content-free erasure tombstones, task/admission history, audit evidence and separately created owned workflows remain."}
    if kind == "project_defaults":
        from maverick.project_workspaces import erase_owned_defaults
        for item in target["records"]:
            result = erase_owned_defaults(actor, record_id=item["id"], expected_revision=item["revision"],
                actor=row["reviewer"], operation_id=_digest([row["id"], kind, item["id"]])[:32])
            if result["status"] not in {"confirmed", "replayed"}:
                raise ValueError("Project-default erasure receipt is not confirmed.")
        return {"state": "purged", "removed": len(target["records"]), "retained": "Revision tombstones, erasure receipts and existing shared project plans remain."}
    if kind == "model_memory":
        from maverick.tools.memory import memory_root_for
        owner = scope_owner(actor, channel, user_id)
        files = _memory_files(owner)
        root = memory_root_for(owner)
        for relative, _, _ in files:
            (root / relative).unlink()
        return {"state": "purged", "removed": len(files), "retained": "Legacy memory with unknown ownership is retained for governed review."}
    raise ValueError("Unsupported deletion target")


def apply(identifier, revision, reviewer, authorize, world, *, external_holds_clear):
    with authority_effect_fence():
        authorize()
        row = STORE.get(identifier)
        if not row:
            raise HTTPException(404, "Deletion request was not found.")
        if row["revision"] != revision:
            raise HTTPException(409, "Deletion review changed. Refresh its saved status.")
        if row["owner"] == reviewer:
            raise HTTPException(403, "A different workspace administrator must review this request.")
        if row["status"] not in {"requested", "held"}:
            raise HTTPException(409, "This request was already dispatched. Inspect per-store outcomes; interrupted deletion is never automatically retried.")
        if preservation_snapshot()["blocked"]:
            return _checkpoint(row, reviewer, lambda current: current.update(status="held"))
        if not external_holds_clear:
            raise ValueError("Confirm external preservation obligations with the records owner before deletion.")
        current = preview(row["owner"], row["channel"], row["user_id"], [t["store"] for t in row["targets"]], world)
        # Released holds may legitimately change, but source scope never expands.
        if current["targets"] != row["targets"]:
            raise HTTPException(409, "The source data changed. Its owner must review a new exact scope.")
        row = _checkpoint(row, reviewer, lambda record: record.update(status="processing", reviewer=reviewer, external_holds_clear=True))
        for index, target in enumerate(row["targets"]):
            authorize()
            personal_memory.require_scope(row["owner"], row["channel"], row["user_id"])
            if preservation_snapshot()["blocked"]:
                row = _checkpoint(row, reviewer, lambda record: record.update(status="partial-held"))
                break
            row = _checkpoint(row, reviewer, lambda record, index=index: record["targets"][index].update(state="dispatching"))
            try:
                outcome = _erase_target(world, row, target)
            except Exception:
                outcome = {"state": "unconfirmed", "message": "This store may be partly changed. Inspect it before preparing a new request; no successful deletion is claimed."}
            row = _checkpoint(row, reviewer, lambda record, index=index, outcome=outcome: record["targets"][index].update(outcome))
        if row["status"] == "processing":
            status = "purged-with-retained-exceptions" if all(t["state"] == "purged" for t in row["targets"]) else "partial"
            row = _checkpoint(row, reviewer, lambda record: record.update(status=status))
        return row
