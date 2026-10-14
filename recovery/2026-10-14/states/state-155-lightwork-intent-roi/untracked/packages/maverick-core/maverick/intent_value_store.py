"""Tenant-scoped intent corrections, continuity and benchmark history.

One CAS document serializes links and edits, preventing two concurrent merges
from assigning the same source to different business tasks. No prompt, result,
or external credential is persisted here. The existing governed store provides
the durable audit outbox and shared-backend enforcement.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re

from .governed_records import GovernedRecordStore
from .intent_value_taxonomy import SIZE_FACTORS, catalog
from .privacy_ops import RecordConflict

SCHEMA = "lightwork.intent-value-ledger.v1"
RECORD_ID = "IVL-workspace"
MAX_CHANGES = 5000
_TASK = re.compile(r"(?:goal:[1-9][0-9]{0,18}|flow:[A-Za-z0-9][A-Za-z0-9_-]{0,127})\Z")


def task_key(value: str) -> str:
    if not isinstance(value, str) or not _TASK.fullmatch(value):
        raise ValueError("Choose a valid task or process run.")
    return value


def canonical_task(task_id: str, links: dict) -> str:
    seen = set()
    while task_id in links:
        if task_id in seen:
            raise ValueError("Task continuity contains a cycle.")
        seen.add(task_id)
        task_id = links[task_id]
    return task_id


def _text(value, label, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} is required and must be at most {limit} characters.")
    return value.strip()


def _number(value, label, maximum):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite non-negative number.")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite non-negative number.") from exc
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError(f"{label} is outside the supported range.")
    return result


def _intent(value):
    if value not in {"unknown", *(row["intent_id"] for row in catalog())}:
        raise ValueError("Choose an available work type.")
    return value


class IntentValueStore:
    def __init__(self, *, store=None):
        self.store = store or GovernedRecordStore("intent_value", "IVL", "intent_value")

    def snapshot(self) -> dict:
        row = self.store.get(RECORD_ID)
        if row is not None:
            if row.get("schema") != SCHEMA:
                raise ValueError("Intent value history needs recovery.")
            return copy.deepcopy(row)
        return {"id": RECORD_ID, "schema": SCHEMA, "revision": 0, "tasks": {},
                "links": {}, "benchmarks": {}, "settings": {}, "history": [], "operations": {}}

    def receipt_for(self, operation_id: str) -> dict | None:
        return next((row for row in self.snapshot()["history"]
                     if row["operation_id"] == operation_id), None)

    def _change(self, action, payload, *, actor, reason, expected_revision, operation_id):
        actor = _text(actor, "The person making this change", 4096)
        reason = _text(reason, "An explanation")
        operation_id = _text(operation_id, "The change reference", 128)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise ValueError("Refresh the report before making this change.")
        request = hashlib.sha256(json.dumps([action, payload, actor, reason],
                                sort_keys=True, allow_nan=False).encode()).hexdigest()
        current = self.snapshot()
        if operation_id in current["operations"]:
            if current["operations"][operation_id] != request:
                raise RecordConflict("This change reference was already used for another change.")
            return current
        if current["revision"] != expected_revision:
            raise RecordConflict("The value report changed. Refresh before saving.")
        timestamp = self.store.authoritative_time()

        def mutate(row):
            if len(row["history"]) >= MAX_CHANGES:
                raise ValueError("The intent history reached its retention limit.")
            before = _apply(row, action, payload)
            event = {"operation_id": operation_id, "action": action, "actor": actor,
                     "reason": reason, "at": timestamp, "before": before,
                     "after": copy.deepcopy(payload)}
            row["history"].append(event)
            row["operations"][operation_id] = request

        try:
            if expected_revision == 0:
                current.pop("revision")
                mutate(current)
                return self.store.create(current, actor=actor, action=action, max_records=1)
            saved = self.store.update(RECORD_ID, mutate, expected_revision=expected_revision,
                                      actor=actor, action=action)
            if saved is None:
                raise RecordConflict("The value report is unavailable. Refresh before saving.")
            return saved
        except RecordConflict:
            # Concurrent exact retries return the one committed change.
            latest = self.snapshot()
            if latest["operations"].get(operation_id) == request:
                return latest
            raise

    def correct_task(self, task_id, *, intent_id, size="standard", confirmed=None,
                     reason, actor, expected_revision, operation_id):
        if size not in SIZE_FACTORS:
            raise ValueError("Choose small, standard or large work size.")
        if confirmed is not None and type(confirmed) is not bool:
            raise ValueError("Completion confirmation must be yes or no.")
        return self._change("correct_intent_value", {"task_id": task_key(task_id),
            "intent_id": _intent(intent_id), "size": size, "confirmed": confirmed},
            reason=reason, actor=actor, expected_revision=expected_revision, operation_id=operation_id)

    def link_tasks(self, source_task_id, target_task_id, *, reason, actor,
                   expected_revision, operation_id):
        return self._change("link_intent_work", {"source_task_id": task_key(source_task_id),
            "target_task_id": task_key(target_task_id)}, reason=reason, actor=actor,
            expected_revision=expected_revision, operation_id=operation_id)

    def save_benchmark(self, intent_id, *, minutes_low, minutes, minutes_high, hourly_rate,
                       reason, actor, expected_revision, operation_id):
        intent_id = _intent(intent_id)
        if intent_id == "unknown":
            raise ValueError("Choose a known work type before setting its benchmark.")
        low = _number(minutes_low, "Lower effort", 525600)
        middle = _number(minutes, "Typical effort", 525600)
        high = _number(minutes_high, "Upper effort", 525600)
        if not low <= middle <= high:
            raise ValueError("Effort must increase from lower to typical to upper.")
        rate = _number(hourly_rate, "Hourly labor value", 100000)
        return self._change("change_intent_benchmark", {"intent_id": intent_id,
            "minutes_low": low, "minutes": middle, "minutes_high": high,
            "hourly_rate": rate, "currency": "USD", "basis": "company_assumption",
            "provenance": "Company-edited planning assumption; not measured effort",
            "version": f"company-{expected_revision + 1}"}, reason=reason, actor=actor,
            expected_revision=expected_revision, operation_id=operation_id)

    def save_settings(self, *, enable, confirmation_required, reason, actor,
                      expected_revision, operation_id):
        if type(enable) is not bool or type(confirmation_required) is not bool:
            raise ValueError("Intent value settings must be on or off.")
        return self._change("change_intent_settings", {"enable": enable,
            "confirmation_required": confirmation_required}, reason=reason, actor=actor,
            expected_revision=expected_revision, operation_id=operation_id)


def _apply(row, action, payload):
    if action == "correct_intent_value":
        key = canonical_task(payload["task_id"], row["links"])
        before = copy.deepcopy(row["tasks"].get(key))
        row["tasks"][key] = {**row["tasks"].get(key, {}), **payload, "task_id": key}
        return before
    if action == "link_intent_work":
        source = canonical_task(payload["source_task_id"], row["links"])
        target = canonical_task(payload["target_task_id"], row["links"])
        if source == target:
            raise ValueError("These records already describe the same business task.")
        row["links"][source] = target
        return {"source_task_id": source, "target_task_id": target, "linked": False}
    if action == "change_intent_benchmark":
        key = payload["intent_id"]
        before = copy.deepcopy(row["benchmarks"].get(key))
        row["benchmarks"][key] = copy.deepcopy(payload)
        return before
    before = copy.deepcopy(row["settings"])
    row["settings"] = copy.deepcopy(payload)
    return before
