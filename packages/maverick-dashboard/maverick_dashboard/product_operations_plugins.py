"""Copilot adapters reuse the native administrator plugin workbench."""
from __future__ import annotations

import json
from typing import Literal

from fastapi import HTTPException
from maverick import plugin_workbench as workbench
from maverick.governed_operations import digest
from pydantic import Field

from . import plugin_workbench_api as native
from .draft_api import context_for
from .product_operations import Adapter, EmptyInput, Outcome, Preview, Snapshot, StrictInput


def state(request):
    native._authority(request)
    context = context_for(request)
    return {"context": context, "plugins": workbench.snapshot(), **workbench.history(context)}


def snapshot(request, selection, kind="status"):
    value = state(request)
    choices = [{"value": row["fingerprint"], "label": row["name"] + " / " + row["version"]}
               for row in value["plugins"] if row["eligible"]]
    fields = []
    if kind in {"check", "publish", "migrate"}:
        fields.append({"name": "fingerprint", "label": "Exact plugin version", "kind": "select", "choices": choices, "required": True})
    if kind == "check":
        fields.extend([
            {"name": "mode", "label": "Check", "kind": "select", "choices": [{"value": key, "label": label} for key, label in
             (("sample", "Validate and execute sample"), ("timeout", "Observe bounded timeout"), ("cancellation", "Observe cancellation"))], "required": True},
            {"name": "sample_json", "label": "Synthetic input JSON (no private data)", "kind": "textarea", "value": "{}", "required": True},
            {"name": "expected_json", "label": "Exact expected result JSON", "kind": "textarea", "value": "null", "required": True}])
    if kind == "publish":
        fields.append({"name": "rollback", "label": "Affected-work review and rollback plan", "kind": "textarea", "required": True})
    if kind == "cancel":
        fields.append({"name": "check_id", "label": "Recorded check", "kind": "select", "required": True,
            "choices": [{"value": row["id"], "label": row["plugin"] + " / " + row["state"]} for row in value["checks"]]})
    if kind == "migrate":
        value["pins"] = migration_pins(request)
        fields.append({"name": "pin", "label": "Stopped task or workflow pin", "kind": "select", "required": True,
            "choices": [{"value": item["id"], "label": item["label"]} for item in value["pins"]]})
    return Snapshot(digest(value), "Plugin qualification ledger", "Readiness is restricted-container evidence; no live capability is accepted.",
        details=value, fields=fields, blocked="No eligible installed plugin is available." if kind in {"check", "publish", "migrate"} and not choices else "")


class CheckInput(StrictInput):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["sample", "timeout", "cancellation"]
    sample_json: str = Field(max_length=16384)
    expected_json: str = Field(max_length=16384)


class PublishInput(StrictInput):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    rollback: str = Field(min_length=10, max_length=2000)


class CancelInput(StrictInput):
    check_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class MigrateInput(StrictInput):
    pin: str = Field(pattern=r"^[a-f0-9]{64}$")
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


def migration_pins(request):
    dependencies = native._dependencies(request)
    result = []
    for kind, rows in (("task", dependencies["tasks"]), ("flow", dependencies.get("runs", []))):
        for row in rows:
            identity = {"kind": kind, "record": row["goal_id"] if kind == "task" else row["run_id"],
                        "distribution": row["distribution"], "name": row["name"], "previous": row["fingerprint"]}
            result.append({**identity, "id": digest(identity), "state": row["state"],
                           "label": kind.capitalize() + " " + str(identity["record"]) + " / " + row["name"] + " / " + row["state"]})
    return result


def prepare(request, selection, arguments):
    value = state(request)
    if "fingerprint" in arguments:
        workbench.selected(arguments["fingerprint"])
    if "sample_json" in arguments:
        try:
            sample, expected = json.loads(arguments["sample_json"]), json.loads(arguments["expected_json"])
            if type(sample) is not dict:
                raise ValueError
            json.dumps([sample, expected], allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Use valid finite JSON and an object input. No check started.") from exc
    prepared = {"context": value["context"]}
    if "pin" in arguments:
        pin = next((item for item in migration_pins(request) if item["id"] == arguments["pin"]), None)
        selected = workbench.selected(arguments["fingerprint"])
        if pin is None or pin["distribution"] != selected["distribution"] or pin["name"] != selected.get("pin_name", selected["name"]):
            raise HTTPException(409, "Select an owned pin for this exact plugin before reviewing migration.")
        prepared["pin"] = pin
        prepared["new_content"] = selected["content_sha256"]
    return Preview("Review this exact plugin operation. Network, credentials and host writes remain unavailable; nothing installs, resumes or replays work.",
                   native=prepared)


def recover_check(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    row = next((item for item in workbench.history(prepared["context"])["checks"] if item["id"] == operation), None)
    if row is not None:
        return Outcome("replayed", "Existing check receipt recovered; execution was not repeated.", row)
    return None


def check(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    recovered = recover_check(request, selection, arguments, prepared, operation)
    if recovered:
        return recovered
    row = workbench.run_check(prepared["context"], operation, arguments["fingerprint"],
        json.loads(arguments["sample_json"]), arguments["mode"],
        expected=json.loads(arguments["expected_json"]), authorize=lambda: native._authority(request, prepared["context"]))
    return Outcome("confirmed" if row["state"] == "passed_scoped_check" else "unconfirmed", "Check recorded. Review the observed completion/interruption and cleanup evidence.", row)


def recover_publish(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    row = next((item for item in workbench.history(prepared["context"])["publications"] if item.get("operation_id") == operation), None)
    return Outcome("replayed", "Existing scoped publication recovered without another change.", row) if row else None


def publish(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    row = workbench.publish(prepared["context"], arguments["fingerprint"], arguments["rollback"], operation=operation,
        authorize=lambda: native._authority(request, prepared["context"]))
    return Outcome("confirmed", "Scoped readiness published; no plugin was installed and no task migrated.", row)


def cancel(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    row = workbench.cancel(prepared["context"], arguments["check_id"])
    return Outcome("confirmed", "Cancellation request recorded. Check the ledger for verified cleanup and the final outcome.", row)


def recover_migration(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    # Inventory performs the same exact task/run ownership checks as the page.
    dependencies = native._dependencies(request)
    reviewed = prepared["pin"]
    rows = dependencies["tasks"] if reviewed["kind"] == "task" else dependencies.get("runs", [])
    for row in rows:
        record = row.get("goal_id") if reviewed["kind"] == "task" else row.get("run_id")
        if (record == reviewed["record"] and row["distribution"] == reviewed["distribution"] and row["name"] == reviewed["name"]
                and row.get("migration_operations", {}).get(operation) == {"previous": reviewed["previous"], "current": prepared["new_content"]}):
            return Outcome("replayed", "This exact version migration was already recorded. No task was resumed or historical action replayed.", {"migrated": True})
    return None


def migrate(request, selection, arguments, prepared, operation):
    native._authority(request, prepared["context"])
    recovered = recover_migration(request, selection, arguments, prepared, operation)
    if recovered:
        return recovered
    pin = prepared["pin"]
    options = {"context": prepared["context"], "fingerprint": arguments["fingerprint"],
               "previous": pin["previous"], "operation_id": operation, "confirm": True}
    if pin["kind"] == "task":
        result = native.migrate_plugin_pin(request, native.MigrateIn(goal_id=pin["record"], **options))
    else:
        result = native.migrate_flow_plugin_pin(request, native.MigrateFlowIn(run_id=pin["record"], **options))
    return Outcome("confirmed", "Owned stopped-work pin migrated. No work was resumed and no previous effect replayed.", result)


def entries():
    shared = {"family": "plugins", "direct_ui": "/plugins", "pages": ("/plugins",)}
    return (
        Adapter("plugins.status", "Review plugin qualification", input_model=EmptyInput, snapshot=snapshot, prepare=prepare, **shared),
        Adapter("plugins.check", "Run a sandbox plugin check", input_model=CheckInput,
                snapshot=lambda r, s: snapshot(r, s, "check"), prepare=prepare, execute=check, recover=recover_check, **shared),
        Adapter("plugins.publish", "Publish scoped plugin readiness", input_model=PublishInput,
                snapshot=lambda r, s: snapshot(r, s, "publish"), prepare=prepare, execute=publish, recover=recover_publish, **shared),
        Adapter("plugins.migrate", "Migrate a stopped-work plugin pin", input_model=MigrateInput,
                snapshot=lambda r, s: snapshot(r, s, "migrate"), prepare=prepare, execute=migrate, recover=recover_migration, **shared),
        Adapter("plugins.cancel", "Cancel a recorded plugin check", input_model=CancelInput,
                snapshot=lambda r, s: snapshot(r, s, "cancel"), prepare=prepare, execute=cancel, **shared),
    )
