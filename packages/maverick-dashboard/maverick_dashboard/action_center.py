"""An attention queue with private, version-bound read and snooze preferences."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from maverick.governed_records import GovernedRecordStore
from maverick.privacy_ops import RecordConflict

_PREFERENCES = GovernedRecordStore("action_center_preferences", "ACP", "attention_preferences")
SOURCE_LIMIT = 2000


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _item(kind, key, title, *, href, owner="", due_at=None, updated_at=0, version=None, reason=""):
    identity = _digest([kind, str(key)])[:32]
    value = {"id": identity, "kind": kind, "title": str(title)[:500], "href": href,
             "owner": owner or "", "due_at": due_at, "updated_at": updated_at or 0,
             "reason": reason}
    value["source_version"] = _digest([value, version])
    return value


def collect(world, *, principal: str | None, owner: str | None, specs: list[dict],
            assessments: Callable[[], list[dict]], assessment_allowed: Callable[[dict], bool],
            project_reviews: Callable[[], list[dict]] | None = None,
            now: float | None = None) -> dict:
    """Project only authorized content; report failed or bounded sources honestly."""
    now = time.time() if now is None else now
    items, coverage = [], []
    try:
        rows = world.pending_approvals()
        for row in rows[:SOURCE_LIMIT]:
            if row.claimed_by and row.claimed_by != principal:
                continue
            state = world.approval_state(row.id)
            if not state or state["status"] != "pending":
                continue
            if principal and (principal in state["approvers"] or row.requested_by == principal):
                continue
            items.append(_item("approval", row.id, str(row.action).replace("_", " "),
                href=f"/approvals#approval-{row.id}", owner=row.claimed_by,
                updated_at=row.requested_at, version=[row.detail, row.risk, state],
                reason="Assigned approval" if row.claimed_by else "Available for an independent review"))
        coverage.append({"kind": "approval", "status": "partial" if len(rows) > SOURCE_LIMIT else "current"})
    except Exception:
        coverage.append({"kind": "approval", "status": "unavailable"})
    try:
        rows = world.list_goals(status="done", owner=owner, limit=SOURCE_LIMIT + 1, order="desc")
        scoped_specs = {spec["domain"]: spec for spec in specs if spec.get("gate")}
        candidates = [goal for goal in rows[:SOURCE_LIMIT] if goal.domain in scoped_specs]
        signed = world.signoffs_for_goals([goal.id for goal in candidates])
        for goal in candidates:
            if signed.get(goal.id):
                continue
            spec = scoped_specs[goal.domain]
            items.append(_item("signoff", goal.id, spec["deliverable"],
                href=f"/chat/goal/{goal.id}", owner=getattr(goal, "owner", ""),
                updated_at=goal.updated_at, version=[goal.title, spec.get("gate")],
                reason="Completed work awaiting sign-off"))
        coverage.append({"kind": "signoff", "status": "partial" if len(rows) > SOURCE_LIMIT else "current"})
    except Exception:
        coverage.append({"kind": "signoff", "status": "unavailable"})
    try:
        rows = assessments()
        for row in rows:
            if not assessment_allowed(row):
                continue
            assigned = row.get("assignee") or ""
            if assigned and assigned != principal:
                continue
            dates = [float(row[key]) for key in ("next_review_at", "renewal_at", "acceptance_expires_at") if row.get(key)]
            due_at = min(dates) if dates else None
            if row.get("status") not in {"pending_review", "needs_more"} and not row.get("review_due"):
                continue
            items.append(_item("assessment", row["id"], row["subject"],
                href="#assessment", owner=assigned, due_at=due_at,
                updated_at=row.get("created_at"), version=row.get("revision"),
                reason=row.get("review_due_reason") or ("Assigned assessment" if assigned else "Unassigned assessment")))
            items[-1]["assessment_id"] = row["id"]
        coverage.append({"kind": "assessment", "status": "current"})
    except Exception:
        coverage.append({"kind": "assessment", "status": "unavailable"})
    if project_reviews is not None:
        try:
            reviews = project_reviews()
            for review in reviews:
                if review["reviewer"] != principal or review["status"] != "pending":
                    continue
                items.append(_item("project_review", review["id"], review["title"],
                    href="/project-reviews#packet=" + review["id"], owner=review["reviewer"],
                    due_at=review["due_at"], updated_at=review["updated_at"],
                    version=[review["revision"], review["source_version"], review["stale"]],
                    reason="Source changed; ask the owner for a fresh result" if review["stale"] else "Assigned project result review"))
            coverage.append({"kind": "project_review", "status": "current"})
        except Exception:
            coverage.append({"kind": "project_review", "status": "unavailable"})
    for row in items:
        row["overdue"] = bool(row["due_at"] and row["due_at"] <= now)
        row["assignment"] = "mine" if principal and row["owner"] == principal else "available"
    items.sort(key=lambda row: (not row["overdue"], row["due_at"] or float("inf"), -row["updated_at"], row["id"]))
    return {"items": items, "coverage": coverage, "generated_at": now}


def preferences(context: str) -> dict:
    record = _PREFERENCES.get("ACP-" + context[:59])
    if record is None:
        return {"revision": 0, "states": {}}
    if record.get("context") != context:
        raise ValueError("Attention preferences do not match this account.")
    return {"revision": record["revision"], "states": record["states"]}


def apply_preferences(model: dict, preference: dict, *, now: float) -> dict:
    for row in model["items"]:
        state = preference["states"].get(row["id"], {})
        current = state.get("source_version") == row["source_version"]
        row["read"] = bool(current and state.get("read"))
        row["snoozed_until"] = state.get("snoozed_until", 0) if current else 0
        row["snoozed"] = row["snoozed_until"] > now
    return {**model, "revision": preference["revision"], "context": model.get("context", "")}


def save_preference(context: str, item: dict, *, expected_revision: int, operation_id: str,
                    read: bool, snoozed_until: float, actor: str) -> dict:
    record_id = "ACP-" + context[:59]
    record = _PREFERENCES.get(record_id)
    state = {"source_version": item["source_version"], "read": read, "snoozed_until": snoozed_until}
    request = _digest([item["id"], state])
    if record and record.get("last_operation") == operation_id:
        if record.get("last_request") != request:
            raise RecordConflict("This retry reference was already used for a different change.")
        return {"revision": record["revision"]}
    if (record or {}).get("revision", 0) != expected_revision:
        raise RecordConflict("Attention preferences changed in another window. Refresh before trying again.")

    def mutate(value):
        if value.get("context") != context:
            raise RecordConflict("The account context changed.")
        states = dict(value.get("states") or {})
        if item["id"] not in states and len(states) >= 5000:
            # Discard the oldest preference, never a business record.
            states.pop(next(iter(states)))
        states[item["id"]] = state
        value.update(states=states, last_operation=operation_id, last_request=request)

    if record is None:
        value = {"id": record_id, "schema": "lightwork.attention-preferences.v1", "context": context, "states": {}}
        mutate(value)
        saved = _PREFERENCES.create(value, action="set_attention_preference", actor=actor)
    else:
        saved = _PREFERENCES.update(record_id, mutate, expected_revision=expected_revision,
                                     action="set_attention_preference", actor=actor)
    if saved is None:
        raise RecordConflict("Attention preferences disappeared. Refresh before trying again.")
    return {"revision": saved["revision"]}
