"""Reviewed Copilot controls call the same work-value authorization and CAS APIs."""
from __future__ import annotations

from typing import Literal

from maverick.governed_operations import digest
from pydantic import Field

from . import intent_value_api as native
from .product_operations import Adapter, Outcome, Preview, Snapshot, StrictInput


class MetricInput(StrictInput):
    scope: Literal["own", "company"] = "own"
    start: str = ""
    end: str = ""


class WorkInput(StrictInput):
    identifier: str = Field(pattern=r"^IVW-[a-f0-9]{32}$")


class CorrectInput(WorkInput):
    intents: str = Field(max_length=300)
    size: Literal["standard", "large"] = "standard"
    reason: str = Field(min_length=1, max_length=1000)


class ProposalInput(StrictInput):
    identifier: str = Field(pattern=r"^IVR-[a-f0-9]{32}$")


class DecisionInput(ProposalInput):
    decision: Literal["defer", "decline", "reopen", "adjust"]
    keep_preparation_manual: bool = False


def metric_snapshot(request, _selection):
    result = native.report_endpoint(request)
    return Snapshot(digest(result["totals"]), "Shared authorized work-value service",
        result["notice"], fields=[{"name": "scope", "label": "Scope", "kind": "select", "required": True,
            "choices": [{"value": "own", "label": "My work"}, {"value": "company", "label": "Company totals"}]},
            {"name": "start", "label": "Start date (YYYY-MM-DD; empty means this month)", "kind": "text"},
            {"name": "end", "label": "End date (YYYY-MM-DD; empty means today)", "kind": "text"}])


def metric_prepare(request, _selection, arguments):
    result = native.report_endpoint(request, **arguments)
    return Preview("Audit-backed work and economic estimates for the selected UTC period.", native={
        "Metrics": result["totals"], "Scope": result["scope"], "Assumptions": result["notice"],
        "Evidence": {"href": "/work-value", "label": "Inspect contributing work and calculations"}})


def snapshot_for(kind):
    def snapshot(request, _selection):
        native.authority(request, write=True)
        if kind in {"correct", "recalculate"}:
            rows = native.records(request)
        else:
            rows = native.opportunities_endpoint(request, include_decided=True)["opportunities"]
        fields = [{"name": "identifier", "label": "Work item" if kind in {"correct", "recalculate"} else "Opportunity",
            "kind": "select", "required": True, "choices": [{"value": row["id"], "label": row["label"] + " · " + row["id"][-8:]} for row in rows]}]
        if kind == "correct":
            fields += [{"name": "intents", "label": "Activity identifiers, comma separated (e.g. rfp.review); empty means unknown", "kind": "text"},
                {"name": "size", "label": "Scope", "kind": "select", "required": True, "choices": [
                    {"value": "standard", "label": "Fits the benchmark component"}, {"value": "large", "label": "Larger/uncertain; leave unvalued"}]},
                {"name": "reason", "label": "Correction reason", "kind": "text", "required": True}]
        if kind == "decision":
            fields += [{"name": "decision", "label": "Your decision", "kind": "select", "required": True,
                "choices": [{"value": value, "label": label} for value, label in (("defer", "Defer"), ("decline", "Keep current process"), ("reopen", "Reopen"), ("adjust", "Adjust manual steps"))]},
                {"name": "keep_preparation_manual", "label": "Also retain preparation as a manual step", "kind": "checkbox"}]
        return Snapshot(digest([(row["id"], row["revision"]) for row in rows]), "Current owned work and opportunity records",
            "Inspect and confirm this change. Source evidence is preserved; drafts never activate themselves.",
            fields=fields, blocked="No authorized records are available." if not rows else "")
    return snapshot


def prepare_for(kind):
    def prepare(request, _selection, arguments):
        identifier = arguments["identifier"]
        row = (native.work(request, identifier, write=True) if kind in {"correct", "recalculate"}
               else native.recommendation(request, identifier, write=True))
        changes = [{"label": "Record", "before": row["label"], "after": kind}]
        if kind == "correct":
            changes.append({"label": "Business activities", "before": ", ".join(row["intents"]), "after": arguments["intents"] or "Unknown"})
        return Preview("Review the selected record and requested change. No execution will start.", changes=changes,
            native={"revision": row["revision"], "arguments": arguments})
    return prepare


def execute_for(kind):
    def execute(request, _selection, arguments, prepared, _operation):
        identifier, revision = arguments["identifier"], prepared["revision"]
        if kind == "correct":
            body = native.CorrectionIn(revision=revision, intents=[item.strip() for item in arguments["intents"].split(",") if item.strip()],
                                      size=arguments["size"], reason=arguments["reason"])
            result = native.correct_endpoint(request, identifier, body)
        elif kind == "recalculate":
            result = native.recalculate_endpoint(request, identifier, native.RevisionIn(revision=revision))
        elif kind == "decision":
            manual = ["inputs", "review"] + (["prepare"] if arguments["keep_preparation_manual"] else [])
            result = native.decision_endpoint(request, identifier, native.DecisionIn(revision=revision, decision=arguments["decision"], manual_steps=manual))
        elif kind == "use":
            result = native.use_endpoint(request, identifier, native.RevisionIn(revision=revision))
        else:
            result = native.build_endpoint(request, identifier, native.RevisionIn(revision=revision))
        pending = result.get("state") == "audit_pending"
        return Outcome("unconfirmed" if pending else "confirmed",
            "Audit delivery is pending. Refresh before continuing." if pending else result.get("message", "Change saved with history; source execution evidence is unchanged."),
            {"Inspect": {"href": result.get("href", "/work-value"), "label": "Inspect the result"}})
    return execute


def entries():
    common = {"family": "learning", "direct_ui": "/work-value", "pages": ("/work-value",)}
    rows = [Adapter("learning.work_value_read", "Read work value and costs", input_model=MetricInput,
        snapshot=metric_snapshot, prepare=metric_prepare, **common)]
    for kind, label, model in (("correct", "Correct detected business activity", CorrectInput),
            ("recalculate", "Recalculate a work estimate", WorkInput), ("decision", "Decide on a work opportunity", DecisionInput),
            ("use", "Use an existing capability for an opportunity", ProposalInput), ("build", "Create a reviewable workflow draft", ProposalInput)):
        rows.append(Adapter("learning.work_value_" + kind, label, input_model=model,
            snapshot=snapshot_for(kind), prepare=prepare_for(kind), execute=execute_for(kind), **common))
    return tuple(rows)
