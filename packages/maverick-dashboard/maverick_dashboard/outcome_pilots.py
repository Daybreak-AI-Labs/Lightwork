"""Frozen paired pilot plans and explicit, source-bound observations.

Measurements are attributed reports, never inferred time savings. Current
business-record acceptance is resolved independently on every report read.
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from decimal import Decimal

from maverick.governed_records import GovernedRecordStore
from maverick.privacy_ops import RecordConflict

STORE = GovernedRecordStore("outcome_pilots", "OPL", "outcome_pilots")
LIMIT = 1000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _owned(record, context):
    if record is None or record.get("context") != context:
        raise LookupError("This pilot is unavailable in your account and workspace.")
    return record


def get(pilot_id, context):
    return _owned(STORE.get(pilot_id), context)


def listing(context):
    rows = STORE.list(limit=LIMIT + 1)
    visible = [r for r in rows[:LIMIT] if r.get("context") == context]
    return {"items": [{"id": r["id"], "title": r["title"], "revision": r["revision"],
                       "created_at": r["created_at"], "cases": len(r["cases"])} for r in visible],
            "partial": len(rows) > LIMIT}


def create(body, *, context, actor, resolve):
    mode = body.get("learning_mode", "observational")
    if mode not in {"observational", "exclude_local_learning"}:
        raise ValueError("Choose an available pilot learning-data treatment.")
    if mode == "exclude_local_learning":
        if not actor.startswith("user:"):
            raise ValueError("Sign in with a named account before freezing a protected pilot.")
        if any(case["kind"] != "goal" for case in body["cases"]):
            raise ValueError("Local learning exclusion currently supports tasks only. Vendor cases can use an observational pilot.")
        if len(str(body.get("prior_exposure_statement", "")).strip()) < 20:
            raise ValueError("Record the prior-exposure checks and any remaining limitations before freezing this pilot.")
    request = digest(body)
    identifier = "OPL-" + digest([context, body["operation_id"]])[:48]
    existing = STORE.get(identifier)
    if existing:
        _owned(existing, context)
        if existing["creation_request"] != request:
            raise RecordConflict("This operation reference already belongs to another pilot.")
        return _finish_protected_plan(existing, resolve) if existing.get("state") == "preparing" else existing
    sources, cases = set(), []
    for index, case in enumerate(body["cases"]):
        key = (case["kind"], case["reference"])
        if key in sources:
            raise ValueError("Each pilot case must use a different task or vendor case.")
        sources.add(key)
        source = resolve(*key)
        if not source["unstarted"]:
            raise ValueError("Plan cases before their AI work starts. Completed work cannot enter a new pilot.")
        cases.append({**case, "id": str(index + 1), "input_version": source["input_version"],
                      "allocation": "development", "measurement": None})
    holdout = set(secrets.SystemRandom().sample(range(len(cases)), max(1, len(cases) // 2)))
    for index in holdout:
        cases[index]["allocation"] = "holdout"
    record = {"id": identifier, "schema": "lightwork.outcome-pilot.v1", "context": context,
              "title": body["title"], "customer": body["customer"], "workflow": body["workflow"],
              "criteria": body["criteria"], "cases": cases, "actor": actor,
              "learning_mode": mode, "prior_exposure_statement": body.get("prior_exposure_statement", ""),
              "state": "preparing" if mode == "exclude_local_learning" else "frozen",
              "creation_request": request, "frozen_at": time.time(), "operations": {}}
    record["plan_version"] = digest({k: record[k] for k in ("title", "customer", "workflow", "criteria", "cases", "frozen_at", "learning_mode", "prior_exposure_statement")})
    # Recheck after assembling the whole plan. This detects concurrent starts;
    # sources remain separate stores, so report eligibility also checks times.
    for case in cases:
        source = resolve(case["kind"], case["reference"])
        if not source["unstarted"] or source["input_version"] != case["input_version"]:
            raise RecordConflict("A case changed while the plan was being prepared.")
    try:
        saved = STORE.create(record, action="prepare_outcome_pilot" if mode == "exclude_local_learning" else "freeze_outcome_pilot", actor=actor, max_records=LIMIT)
    except RecordConflict:
        concurrent = get(identifier, context)
        if concurrent["creation_request"] != request:
            raise
        saved = concurrent
    return _finish_protected_plan(saved, resolve) if saved.get("state") == "preparing" else saved


def _finish_protected_plan(record, resolve):
    from maverick.pilot_learning import protect
    for case in record["cases"]:
        source = resolve(case["kind"], case["reference"])
        if not source["unstarted"] or source["input_version"] != case["input_version"]:
            raise RecordConflict("Work started or inputs changed before the protected plan could freeze. Its learning exclusions are retained; prepare new unstarted cases.")
    for case in record["cases"]:
        if case["allocation"] == "holdout":
            protect(int(case["reference"]), pilot_id=record["id"], plan_version=record["plan_version"],
                    input_version=case["input_version"], actor=record["actor"])
    for case in record["cases"]:
        source = resolve(case["kind"], case["reference"])
        if not source["unstarted"] or source["input_version"] != case["input_version"]:
            raise RecordConflict("A task started while exclusions were being installed. The plan is not frozen; prepare new unstarted cases.")
    return STORE.update(record["id"], lambda current: current.update(state="frozen"), expected_revision=record["revision"],
                        action="freeze_protected_outcome_pilot", actor=record["actor"])


def observe(pilot_id, case_id, body, *, context, actor, resolve):
    record = get(pilot_id, context)
    if record.get("state", "frozen") != "frozen":
        raise RecordConflict("This plan has not finished freezing its learning policy. No observation was accepted.")
    request = digest([case_id, {k: v for k, v in body.items() if k != "expected_revision"}])
    prior = record["operations"].get(body["operation_id"])
    if prior is not None:
        if prior != request:
            raise RecordConflict("This operation reference was already used for a different observation.")
        return record
    case = next((c for c in record["cases"] if c["id"] == case_id), None)
    if case is None:
        raise LookupError("This pilot case does not exist.")
    source = resolve(case["kind"], case["reference"])
    if source["version"] != body["source_version"]:
        raise RecordConflict("The source changed. Review its current result before recording an observation.")
    if source["input_version"] != case["input_version"]:
        raise RecordConflict("The task inputs changed after this plan was frozen. This case is not a valid pair.")
    measurement = {k: v for k, v in body.items() if k not in {"expected_revision", "operation_id"}}
    measurement.update(actor=actor, observed_at=time.time())

    def mutate(current):
        _owned(current, context)
        if len(current["operations"]) >= 5000:
            raise ValueError("This pilot reached its observation history limit. Export its record before further work.")
        rows = copy.deepcopy(current["cases"])
        selected = next(c for c in rows if c["id"] == case_id)
        history = selected.setdefault("history", [])
        if selected["measurement"] is not None:
            history.append(selected["measurement"])
        selected["measurement"] = measurement
        current["cases"] = rows
        current["operations"] = {**current["operations"], body["operation_id"]: request}

    saved = STORE.update(pilot_id, mutate, expected_revision=body["expected_revision"],
                         action="record_pilot_observation", actor=actor)
    if saved is None:
        raise RecordConflict("The pilot disappeared. Refresh before continuing.")
    return saved


def _pair(case, source, frozen_at):
    measurement = case["measurement"]
    if source.get("sample"):
        return "sample"
    if source["input_version"] != case["input_version"]:
        return "inputs_changed"
    if source.get("first_started_at") is not None and source["first_started_at"] <= frozen_at:
        return "started_before_plan"
    if measurement is None:
        return "unmeasured"
    if measurement["source_version"] != source["version"]:
        return "stale"
    if not source["finished"]:
        return "unfinished"
    if any(measurement.get(field) is None for field in
           ("baseline_minutes", "ai_hands_on_minutes", "review_minutes", "rework_minutes", "corrections")):
        return "partial_measurement"
    return "paired"


def report(record, *, resolve):
    rows = []
    for case in record["cases"]:
        row = {**case}
        try:
            source = resolve(case["kind"], case["reference"])
            row.update(source=source, status=_pair(case, source, record["frozen_at"]))
            if record.get("state", "frozen") != "frozen":
                row["status"] = "plan_not_frozen"
            if record.get("learning_mode") == "exclude_local_learning" and case["allocation"] == "holdout":
                from maverick.pilot_learning import evidence
                row["learning_exclusion"] = evidence(int(case["reference"]), pilot_id=record["id"],
                    plan_version=record["plan_version"], input_version=case["input_version"])
                if source["input_version"] != case["input_version"]:
                    row["learning_exclusion"]["enforced"] = False
        except Exception:
            # A previously permitted source cannot leak its title or observations
            # once source permissions have been revoked.
            row = {"id": case["id"], "allocation": case["allocation"], "status": "unavailable"}
        rows.append(row)
    groups = {}
    for allocation in ("all", "development", "holdout"):
        selected = [r for r in rows if allocation == "all" or r["allocation"] == allocation]
        paired = [r for r in selected if r["status"] == "paired"]
        accepted = [r for r in selected if r.get("source", {}).get("accepted") is True]
        minutes = sum(r["measurement"]["baseline_minutes"] - sum(r["measurement"][f] for f in
                     ("ai_hands_on_minutes", "review_minutes", "rework_minutes")) for r in paired)
        complete_cost = bool(paired) and all(r["measurement"].get("baseline_cost") is not None
            and r["measurement"].get("ai_total_cost") is not None for r in paired)
        currencies = {r["measurement"]["currency"] for r in paired}
        cost_difference = None
        if complete_cost and len(currencies) == 1:
            cost_difference = str(sum((Decimal(r["measurement"]["baseline_cost"]) -
                Decimal(r["measurement"]["ai_total_cost"]) for r in paired), Decimal(0)))
        groups[allocation] = {"planned": len(selected), "paired": len(paired), "accepted": len(accepted),
            "unavailable": sum(r["status"] == "unavailable" for r in selected),
            "acceptance_rate": len(accepted) / len(selected) if selected else None,
            "reported_minutes_difference": minutes if paired else None,
            "reported_cost_difference": cost_difference,
            "reported_ai_cost_per_accepted": str(sum((Decimal(r["measurement"]["ai_total_cost"]) for r in paired), Decimal(0)) / len(accepted))
                if complete_cost and len(currencies) == 1 and len(paired) == len(selected) and accepted else None,
            "criteria_met": sum(r["measurement"].get("criteria_met") is True and r["source"]["accepted"] for r in paired),
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "corrections": sum(r["measurement"]["corrections"] for r in paired) if paired else None}
    holdouts = [row for row in rows if row["allocation"] == "holdout"]
    learning = {"mode": record.get("learning_mode", "observational"),
                "automatic_local_exclusion_enforced": record.get("state", "frozen") == "frozen" and bool(holdouts) and all(row.get("learning_exclusion", {}).get("enforced") for row in holdouts),
                "prior_exposure_statement": record.get("prior_exposure_statement", ""),
                "prior_exposure_actor": record["actor"], "provider_training_verified": False,
                "manual_imports_or_relabelled_copies_covered": False}
    learning["evidence_version"] = digest([record["plan_version"], learning,
        [{"id": row["id"], "source_version": row.get("source", {}).get("version"),
          "exclusion": row.get("learning_exclusion")} for row in rows]])
    reviewed = record.get("independent_review")
    learning["independent_review"] = {**reviewed, "current": reviewed["evidence_version"] == learning["evidence_version"]} if reviewed else None
    learning["review_history"] = record.get("independent_review_history", [])
    return {"id": record["id"], "title": record["title"], "customer": record["customer"],
            "workflow": record["workflow"], "criteria": record["criteria"], "revision": record["revision"],
            "plan_version": record["plan_version"], "frozen_at": record["frozen_at"], "cases": rows,
            "state": record.get("state", "frozen"),
            "groups": groups, "measurement_basis": "operator_reported",
            "learning_policy": learning,
            "provider_invoice_verified": False, "holdout_exposure_verified": False,
            "limitations": ["Observations and evidence references are attributed to their recorder; they are not independently verified.",
              "Time fields are non-overlapping hands-on effort. Wall time and costs remain separate observations.",
              "All planned cases remain in the acceptance denominator, including missing and rejected cases.",
              "Paired comparisons include current complete observations, including rejected results. Missing pairs can bias comparisons.",
              "The selected local-learning mode covers automatic goal-bound capture, replay and ingestion only. It does not prove prior exposure, provider training exclusion or manually relabelled imports.",
              "Recorded runtime prices are not provider invoices. Reported total cost must include review, rework, and failed attempts."]}


def independent_review(pilot_id, body, *, actor, resolve):
    if body.get("confirm") is not True:
        raise ValueError("Explicitly confirm the exact learning-policy evidence you reviewed.")
    record = STORE.get(pilot_id)
    if record is None:
        raise LookupError("Pilot unavailable.")
    if not actor.startswith("user:") or actor == record["actor"]:
        raise PermissionError("A different named administrator must perform the independent review.")
    # The API enforces global administrator access; resolve reauthorizes every
    # underlying source before disclosing or accepting the review.
    for case in record["cases"]:
        resolve(case["kind"], case["reference"])
    snapshot = report(record, resolve=resolve)["learning_policy"]
    if snapshot["evidence_version"] != body["evidence_version"]:
        raise RecordConflict("The plan, source or exclusion evidence changed. Review it again.")
    if body["decision"] == "accepted_local_scope" and not snapshot["automatic_local_exclusion_enforced"]:
        raise ValueError("Local exclusion is not established for every holdout task. Request more evidence instead.")
    request = digest([actor, {key: value for key, value in body.items() if key != "expected_revision"}])
    previous = record.setdefault("review_operations", {}).get(body["operation_id"])
    if previous is not None:
        if previous != request:
            raise RecordConflict("This review reference was already used differently.")
        return record
    def update(current):
        if len(current.get("review_operations", {})) >= 100:
            raise ValueError("This pilot has reached its independent review history limit.")
        if current.get("independent_review"):
            current.setdefault("independent_review_history", []).append(current["independent_review"])
        current["independent_review"] = {"actor": actor, "decision": body["decision"], "notes": body["notes"],
                                         "evidence_version": body["evidence_version"], "reviewed_at": time.time()}
        current.setdefault("review_operations", {})[body["operation_id"]] = request
    return STORE.update(pilot_id, update, expected_revision=body["expected_revision"], action="independent_learning_review", actor=actor)
