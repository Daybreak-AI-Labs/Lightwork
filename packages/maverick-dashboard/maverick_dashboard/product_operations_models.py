"""Model evidence and paid tests use the same native authorization and receipts."""
from __future__ import annotations

from maverick import model_tests
from maverick.governed_operations import digest
from pydantic import Field

from . import model_tests_api as native
from .draft_api import context_for
from .plain_labels import money
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot, StrictInput


class TestInput(StrictInput):
    model: str = Field(min_length=1, max_length=200)
    kind: str = Field(pattern=r"^(generation|vision)$")


def snapshot(request, selection):
    native.authority(request)
    from maverick import model_discovery
    from maverick.llm import _allowed_model_specs
    from maverick.model_discovery import spec_for
    from maverick.model_guidance import describe
    evidence = model_discovery.snapshot()
    models = []
    for provider, row in evidence.items():
        for name in (row.get("models") or [])[:500]:
            spec = spec_for(provider, name)
            models.append({"model": spec, **describe(spec, provider=provider, capability=(row.get("capabilities") or {}).get(name),
                            fetched_at=row.get("fetched_at"), stale=row.get("stale", True))})
    details = {"models": models, "approved_models": sorted(_allowed_model_specs()),
               "scope": "Provider-reported evidence only. Tools remain untested; successful text generation does not establish tool support."}
    return Snapshot(digest(details), "Current approved-model policy and cached provider metadata", "Compare reported model capabilities and dated prices.", details=details)


def test_snapshot(request, selection):
    base = snapshot(request, selection)
    blocked = ""
    try:
        model_tests.policy()
    except (PermissionError, ValueError):
        blocked = "Enable bounded model tests in All settings before requesting a paid test."
    fields = [{"name": "model", "label": "Exact model name from your provider", "kind": "text", "value": ""},
              {"name": "kind", "label": "Single synthetic sample", "kind": "select", "value": "generation",
               "choices": [{"value": key, "label": value} for key, value in model_tests.KINDS.items()]}]
    return Snapshot(base.revision, base.source, "Review one bounded potentially paid call. No automatic retry or model failover.",
                    details=base.details, fields=fields, blocked=blocked)


def prepare_test(request, selection, arguments):
    row = native.call(model_tests.prepare, context_for(request), arguments["model"], arguments["kind"], native.guard_for(request, context_for(request)))
    caps = row["limits"]
    return Preview(f"Run one potentially paid synthetic sample on {row['model']}; at most {money(caps['max_dollars'])}, {caps['max_output_tokens']} output tokens and {caps['max_wall_seconds']} seconds of local waiting. Cancellation cannot retract an already sent request.",
        changes=[{"field": "Model test", "before": "Not dispatched", "after": model_tests.KINDS[row["kind"]]}], native={"check_id": row["id"]})


def execute_test(request, selection, arguments, review, operation_id):
    result, background = native.start(request, context_for(request), review["check_id"], True)
    return Outcome("queued" if background else "replayed", "Use the model test history to read this exact request's result. No uncertain call is retried.", result, background=background)


def recover_test(request, selection, arguments, review, operation_id):
    native.authority(request)
    row = model_tests.get(context_for(request), review["check_id"])
    if row is not None and row["state"] != "prepared":
        return Outcome("replayed", "Recovered the original test receipt without another paid request.", {"check": model_tests.public(row)})
    return None


def history_snapshot(request, selection):
    native.authority(request)
    rows = [model_tests.public(row) for row in model_tests.history(context_for(request))]
    return Snapshot(digest(rows), "Durable model test receipts in this account and workspace", "Review the original test results; unknown results were not replayed.", {"checks": rows})


def cancel_snapshot(request, selection):
    state = history_snapshot(request, selection)
    rows = [row for row in state.details["checks"] if row.get("may_still_be_running") or row["state"] in {"prepared", "queued", "executing"}]
    return Snapshot(state.revision, state.source, "Stop a pending test. A transmitted request may still be billable.", {"checks": rows},
        fields=[{"name": "check_id", "label": "Pending model test", "kind": "select", "value": rows[-1]["id"] if rows else "",
                 "choices": [{"value": row["id"], "label": row["model"] + " / " + model_tests.KINDS[row["kind"]]} for row in rows]}],
        blocked="No pending model tests in this account and workspace." if not rows else "")


class CancelInput(StrictInput):
    check_id: str = Field(pattern=r"^[a-f0-9]{32}$")


def prepare_cancel(request, selection, arguments):
    state = cancel_snapshot(request, selection)
    if not any(row["id"] == arguments["check_id"] for row in state.details["checks"]):
        raise ValueError("Choose a current owned model test.")
    return Preview("Stop this test; cancellation cannot undo a transmitted request.")


def execute_cancel(request, selection, arguments, review, operation_id):
    context = context_for(request)
    row = native.call(model_tests.cancel, context, arguments["check_id"], native.guard_for(request, context))
    return Outcome("confirmed", "The test's stop request was saved. No paid request was retried.", {"check": model_tests.public(row)})


def entries():
    pages = ("/settings", "/settings/all")
    return [Adapter("models.guidance", "Compare model capabilities", "models", "/settings", pages, EmptyInput, snapshot,
                    lambda request, selection, args: Preview("Current model evidence", native=snapshot(request, selection).details)),
            Adapter("models.test", "Run a bounded model test", "models", "/settings", pages, TestInput, test_snapshot, prepare_test, execute_test, recover_test),
            Adapter("models.test.history", "Check model test results", "models", "/settings", pages, EmptyInput, history_snapshot,
                    lambda request, selection, args: Preview("Original model test receipts", native=history_snapshot(request, selection).details)),
            Adapter("models.test.cancel", "Stop a model test", "models", "/settings", pages, CancelInput, cancel_snapshot, prepare_cancel, execute_cancel)]
