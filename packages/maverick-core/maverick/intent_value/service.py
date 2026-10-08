"""Durable work identities, immutable estimate snapshots and explicit corrections."""
from __future__ import annotations

import copy
import time

from ..governed_operations import digest
from ..governed_records import GovernedRecordStore
from ..privacy_ops import RecordConflict
from . import calculation, sources
from .settings import policy, settings
from .taxonomy import definition

WORKS = GovernedRecordStore("intent_value_work", "IVW", "intent_value")
MAX_HISTORY = 500


def _label(intents):
    return "; ".join(definition(identifier)["label"] for identifier in intents) or "Unclassified work"


def _snapshot(row, *, reason, actor, refresh_assumptions=False):
    if len(row["estimates"]) >= MAX_HISTORY:
        raise ValueError("This work item needs estimate-history archival before another recalculation.")
    inputs = (calculation.assumptions(row["intents"], size=row["classification"]["size"], specialists=row["source"]["specialists"])
              if refresh_assumptions or not row["estimates"] else copy.deepcopy(row["estimates"][-1]["assumptions"]))
    value = calculation.estimate(row["source"], inputs)
    value.update(version=len(row["estimates"]) + 1, at=time.time(), reason=reason, actor=actor,
                 source_digest=row["source"]["source_digest"], intents=list(row["intents"]),
                 cost_snapshot=copy.deepcopy(row["source"]["costs"]))
    row["estimates"].append(value)


def refresh(world):
    if not policy()["enabled"]:
        raise PermissionError("Work value intelligence is turned off in this workspace.")
    observations = sources.collect(world)
    live = set()
    for source in observations:
        identifier = "IVW-" + digest([source["key"], source["owner"]])[:32]
        live.add(identifier)
        current = WORKS.get(identifier)
        if current is None:
            row = {"id": identifier, "owner": source["owner"], "source": source,
                   "intents": list(source["classification"]["intents"]),
                   "classification": source["classification"], "estimates": [], "corrections": [],
                   "confirmations": [], "deleted": False, "corrected": False}
            row["label"] = _label(row["intents"])
            _snapshot(row, reason="Original estimate", actor="system")
            try:
                WORKS.create(row, action="intent_value.observed", actor="system")
            except RecordConflict:
                continue  # Concurrent refresh already owns this identity.
        elif current["source"]["source_digest"] != source["source_digest"] or current.get("deleted"):
            def update(row, source=source):
                row.update(source=source, deleted=False)
                if (not row["corrected"] and source["classification"]["intents"]
                        and row["intents"] != source["classification"]["intents"]):
                    row.update(intents=source["classification"]["intents"], classification=source["classification"])
                    row["label"] = _label(row["intents"])
                    _snapshot(row, reason="Requested intent became identifiable or explicitly changed", actor="system", refresh_assumptions=True)
                else:
                    _snapshot(row, reason="Execution or observation evidence updated; assumptions retained", actor="system")
            try:
                WORKS.update(identifier, update, expected_revision=current["revision"], action="intent_value.evidence", actor="system")
            except RecordConflict:
                continue
    rows = WORKS.list(limit=sources.MAX_RECORDS + 1)
    if len(rows) > sources.MAX_RECORDS:
        raise ValueError("Work-value history exceeds the supported refresh population.")
    for current in rows:
        if current["id"] not in live and not current.get("deleted"):
            # No copied prompt or result survives source deletion. Historical
            # assumptions remain explainable, but deleted sources leave totals.
            def retire(row):
                row["deleted"] = True
                row["source"] = {**row["source"], "sources": [], "goal_ids": [], "flow_runs": [], "evidence": []}
            WORKS.update(current["id"], retire, expected_revision=current["revision"], action="intent_value.source_removed", actor="system")
    WORKS.retry_pending(limit=100)
    from .opportunities import conversions
    return conversions(WORKS.list(limit=sources.MAX_RECORDS + 1))


def correct(identifier, *, intents, size, expected_revision, reason, actor, guard):
    if len(intents) > 4 or len(set(intents)) != len(intents) or any(definition(value) is None for value in intents):
        raise ValueError("Choose up to four distinct business activities, or none if unknown.")
    if size not in {"standard", "large"} or not reason.strip():
        raise ValueError("Choose a size assumption and explain the correction.")
    def update(row):
        if row.get("deleted"):
            raise ValueError("The source work was removed.")
        row["corrections"].append({"at": time.time(), "actor": actor, "reason": reason[:1000],
                                   "before": row["intents"], "after": intents, "size": size})
        row.update(intents=list(intents), label=_label(intents), corrected=True,
                   classification={**row["classification"], "intents": list(intents), "confidence": "corrected", "size": size,
                                   "reason": "Authorized user correction"})
        _snapshot(row, reason="Intent correction: " + reason[:1000], actor=actor, refresh_assumptions=True)
    return WORKS.update(identifier, update, expected_revision=expected_revision, action="intent_value.corrected", actor=actor, effect_guard=guard)


def recalculate(identifier, *, expected_revision, actor, guard):
    return WORKS.update(identifier, lambda row: _snapshot(row, reason="Deliberate recalculation with current tenant assumptions", actor=actor, refresh_assumptions=True),
                        expected_revision=expected_revision, action="intent_value.recalculated", actor=actor, effect_guard=guard)


def confirm(identifier, *, expected_revision, actor, guard):
    if not settings()["completion_confirmation"]:
        raise ValueError("Optional completion confirmation is turned off.")
    def update(row):
        if not any(entry["actor"] == actor and entry["source_digest"] == row["source"]["source_digest"] for entry in row["confirmations"]):
            row["confirmations"].append({"at": time.time(), "actor": actor,
                "source_digest": row["source"]["source_digest"],
                "meaning": "User considers the present result complete; revisions remain possible. This is not execution evidence."})
    return WORKS.update(identifier, update, expected_revision=expected_revision,
                        action="intent_value.confirmed", actor=actor, effect_guard=guard)
