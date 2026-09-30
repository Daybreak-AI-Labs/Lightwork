"""Version-bound plugin checks; no imports or external effects on the host.

A check may run only in a locally reviewed immutable Linux image through the
strict research runner. The image must contain the exact installed distribution.
A completed sample is scoped sandbox evidence, never live-service acceptance.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from contextlib import contextmanager
from pathlib import Path

from .file_lock import atomic_read_text, atomic_write_text, cross_process_lock, ensure_private_directory
from .paths import data_dir


def config():
    from .config import config_source_errors, load_config

    if config_source_errors(include_tenant=True):
        raise PermissionError("Plugin qualification policy is unavailable. Repair Settings.")
    value = (load_config().get("plugins") or {})
    if value.get("workbench", False) is not True:
        raise PermissionError("Enable the plugin qualification workbench in Settings.")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def snapshot():
    from dataclasses import asdict

    from . import plugins
    from .plugin_lock import _dist_content_hash

    policy = config()
    harness = hashlib.sha256((Path(__file__).parent / "sandbox" / "plugin_probe.py").read_bytes()).hexdigest()
    rows = []
    allow, (granted, enforce) = plugins._allowed_plugin_names(), plugins._permission_policy()
    for group in plugins._PLUGIN_GROUPS:
        entries = list(plugins._entry_points(group))
        for ep in entries:
            manifest = plugins._find_manifest(ep)
            dist = plugins._ep_dist_name(ep)
            row = {"group": group, "name": ep.name, "distribution": dist or "", "entry": ep.value,
                   "version": str(getattr(getattr(ep, "dist", None), "version", "") or ""),
                   "content_sha256": _dist_content_hash(dist) if dist else None,
                   "manifest": asdict(manifest) if manifest else None,
                   "missing_permissions": plugins._permission_violations(manifest, granted),
                   "permission_enforcement": enforce, "qualification_image": policy.get("workbench_image", ""),
                   "harness_sha256": harness}
            try:
                row["policy_allowed"] = plugins._gate(ep, group, allow, plugins._name_dist_map(entries), granted, enforce)
                # Negative control: the same runtime gate rejects a denied entry
                # before ep.load. This is host admission evidence, not a claim
                # that arbitrary plugin code obeys its declared permissions.
                row["permission_denial_checked"] = not plugins._gate(ep, group, set(), plugins._name_dist_map(entries), set(), True)
            except Exception:
                row["policy_allowed"] = row["permission_denial_checked"] = False
            row["eligible"] = bool(group == "maverick.tools" and row["policy_allowed"] and manifest
                and manifest.is_compatible() and not manifest.warnings and not row["missing_permissions"]
                and row["content_sha256"] and row["version"])
            row["fingerprint"] = _digest(row)
            row["scope"] = "Python tool sandbox qualification" if group == "maverick.tools" else "Metadata review only; this workbench has no executable contract for this plugin kind"
            rows.append(row)
    return rows


def selected(fingerprint):
    item = next((row for row in snapshot() if row["fingerprint"] == fingerprint), None)
    if item is None:
        raise ValueError("Plugin bytes, version or policy changed. Review the current plugin.")
    return item


def _path(context):
    if not re.fullmatch(r"[a-f0-9]{64}", context):
        raise ValueError("invalid account context")
    return data_dir() / "plugin-workbench" / (context + ".json")


def _read(path):
    if not path.exists():
        return {"schema": 1, "checks": {}, "publications": []}
    value = json.loads(atomic_read_text(path, encoding="utf-8"))
    if value.get("schema") != 1 or not isinstance(value.get("checks"), dict):
        raise ValueError("Qualification history is unavailable; no action was started.")
    return value


def _edit(context, mutate):
    path = _path(context)
    ensure_private_directory(path.parent)
    with cross_process_lock(path, strict=True):
        state = _read(path)
        result = mutate(state)
        atomic_write_text(path, json.dumps(state, sort_keys=True))
        return result


def history(context):
    state = _read(_path(context))
    fingerprints = {row["fingerprint"] for row in snapshot()}
    return {"checks": [{**row, "current": row["fingerprint"] in fingerprints}
                        for row in state["checks"].values()],
            "publications": [{**row, "current": row["fingerprint"] in fingerprints}
                             for row in state["publications"]]}


def cancel(context, operation):
    def mutate(state):
        row = state["checks"].get(operation)
        if row is None:
            raise LookupError("This check is not recorded. Refresh before another request.")
        if row["state"] == "running_or_interrupted":
            row["cancel_requested"] = True
        return row
    return _edit(context, mutate)


def _cancelled(context, operation):
    return bool(_read(_path(context))["checks"][operation].get("cancel_requested"))


def run_check(context, operation, fingerprint, sample, mode, *, authorize, expected=None):
    from .budget import Budget
    from .sandbox.research import ResearchContainerRunner, ResearchLimits

    authorize()
    policy = config()
    row = selected(fingerprint)
    if not row["eligible"]:
        raise ValueError("This plugin needs a valid compatible manifest, current permission approval and inspectable installed bytes before a sandbox check.")
    if not re.fullmatch(r"[a-f0-9]{32}", operation) or mode not in {"sample", "timeout", "cancellation"}:
        raise ValueError("Choose a supported check and a new operation identifier.")
    if type(sample) is not dict or len(json.dumps(sample, allow_nan=False).encode()) > 16384:
        raise ValueError("Use an object sample smaller than 16 KiB; do not include credentials or private production data.")
    if len(json.dumps(expected, allow_nan=False).encode()) > 16384:
        raise ValueError("Expected sample result must be smaller than 16 KiB.")
    expected_result = hashlib.sha256(json.dumps(expected, sort_keys=True, allow_nan=False).encode()).hexdigest()
    image = str(policy.get("workbench_image") or "")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise ValueError("Select a locally installed, reviewed immutable Linux image in Settings. No image is downloaded and host execution is forbidden.")
    initial = {"id": operation, "fingerprint": fingerprint, "plugin": row["name"], "distribution": row["distribution"],
               "version": row["version"], "mode": mode, "started_at": time.time(), "state": "running_or_interrupted",
               "sample_sha256": _digest(sample), "expected_result_sha256": expected_result, "attempts": 0, "replay": "never", "external_outcome": "unknown"}
    def reserve(state):
        if operation in state["checks"]:
            raise ValueError("This operation was already recorded. Refresh its result; it will not be replayed.")
        if len(state["checks"]) >= 1000:
            raise ValueError("This account's qualification ledger reached its 1000-check limit. Retain/export its evidence before an administrator reviews retention.")
        state["checks"][operation] = initial
    _edit(context, reserve)
    started = time.monotonic()
    def interrupted():
        return _cancelled(context, operation) or (mode == "cancellation" and time.monotonic() - started >= 5)
    @contextmanager
    def guard():
        authorize()
        selected(fingerprint)
        if _cancelled(context, operation):
            raise PermissionError("Check was cancelled before dispatch.")
        yield
    try:
        payload = {"entry": row["entry"], "distribution": row["distribution"], "version": row["version"],
                   "content_sha256": row["content_sha256"], "operation": operation, "sample": sample}
        source = (Path(__file__).parent / "sandbox" / "plugin_probe.py").read_bytes()
        result = ResearchContainerRunner(image, limits=ResearchLimits(timeout_seconds=0.5 if mode == "timeout" else 15)).run(
            {"main.py": source, "sample.json": json.dumps(payload, allow_nan=False).encode()},
            budget=Budget(max_wall_seconds=45, max_tool_calls=1, max_dollars=0), effect_guard=guard, cancelled=interrupted)
        authorize()
        selected(fingerprint)
        observed, dispatched = {}, False
        for line in result.stdout.splitlines():
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if not isinstance(frame, dict) or frame.get("operation") != operation:
                continue
            if frame.get("phase") == "dispatch":
                dispatched = True
            elif frame.get("phase") == "result" and result.reason == "completed" and result.exit_code == 0:
                observed = frame
        passed = bool(result.cleanup_verified and ((mode == "sample" and observed.get("schema_valid") is True
            and observed.get("sample_valid") is True and observed.get("completed") is True
            and observed.get("content_sha256") == row["content_sha256"]
            and observed.get("result_sha256") == expected_result)
            or (mode == "timeout" and result.reason == "timeout" and dispatched)
            or (mode == "cancellation" and result.reason == "cancelled" and dispatched)))
        final = {"state": "passed_scoped_check" if passed else "not_qualified", "finished_at": time.time(),
                 "reason": result.reason, "attempts": 1, "plugin_dispatch_observed": dispatched, "cleanup_verified": result.cleanup_verified,
                 "image_id": result.image_id, "policy_sha256": result.policy_sha256,
                 "permission_denial_checked": row["permission_denial_checked"],
                 "schema_valid": observed.get("schema_valid") is True,
                 "result_sha256": observed.get("result_sha256") if mode == "sample" else None,
                 "external_outcome": "not attempted: no network or credentials" if result.reason == "completed" else "unknown; no replay",
                 "scope": "Restricted-container behavior only. No live service or write capability was accepted."}
    except Exception:
        final = {"state": "not_qualified", "finished_at": time.time(), "reason": "check unavailable or authority changed",
                 "external_outcome": "unknown; no replay", "scope": "Review current plugin policy, local container readiness and immutable image. Do not retry an uncertain operation."}
    def finish(state):
        state["checks"][operation].update(final)
        return state["checks"][operation]
    return _edit(context, finish)


def publish(context, fingerprint, rollback, *, authorize):
    authorize()
    selected(fingerprint)
    if not isinstance(rollback, str) or not 10 <= len(rollback.strip()) <= 2000:
        raise ValueError("Record a concrete rollback and affected-work review plan (10-2000 characters).")
    def mutate(state):
        checks = [row for row in state["checks"].values() if row["fingerprint"] == fingerprint
                  and row["state"] == "passed_scoped_check" and row.get("cleanup_verified")
                  and row.get("permission_denial_checked")]
        if {row["mode"] for row in checks} != {"sample", "timeout", "cancellation"}:
            raise ValueError("A current sample, timeout and cancellation check must pass before publishing scoped readiness.")
        receipt = {"fingerprint": fingerprint, "created_at": time.time(), "rollback": rollback,
                   "checks": [row["id"] for row in checks], "scope": "sandbox-only; external read/write acceptance unverified"}
        state["publications"].append(receipt)
        return receipt
    return _edit(context, mutate)


def pinned_factory(name, distribution, factory):
    """Keep each already constructed task tool bound to its installed bytes.

    Goal pins survive a restart. Drift refuses dispatch; this never swaps in a
    newly installed implementation or replays the old operation automatically.
    """
    from .plugin_lock import _dist_content_hash

    fingerprint = _dist_content_hash(distribution)
    if not fingerprint:
        raise ValueError("Plugin bytes cannot be pinned for this task.")
    def construct():
        if _dist_content_hash(distribution) != fingerprint:
            raise PermissionError("Plugin changed before tool construction. Review its current version.")
        tool = factory()
        original = tool.fn
        def check():
            from .audit.writer import _current_goal
            from .connections import current_principal

            config()
            from . import plugins

            entries = list(plugins._entry_points("maverick.tools"))
            entry = next((ep for ep in entries if ep.name == name and plugins._ep_dist_name(ep) == distribution), None)
            grants, enforce = plugins._permission_policy()
            if entry is None or not plugins._gate(entry, "maverick.tools", plugins._allowed_plugin_names(), plugins._name_dist_map(entries), grants, enforce):
                raise PermissionError("Current plugin policy no longer permits this task tool.")
            if _dist_content_hash(distribution) != fingerprint:
                raise PermissionError("Plugin version changed. Restore its pinned version or explicitly review migration before resuming this task.")
            goal = _current_goal.get()
            if goal is not None:
                path = data_dir() / "plugin-task-pins" / (str(int(goal)) + ".json")
                ensure_private_directory(path.parent)
                with cross_process_lock(path, strict=True):
                    pins = json.loads(atomic_read_text(path, encoding="utf-8")) if path.exists() else {}
                    key = distribution + ":" + name
                    existing = pins.get(key)
                    if existing and (existing["fingerprint"] != fingerprint or existing["owner"] != (current_principal() or "")):
                        raise PermissionError("This task retains an earlier plugin version. Review migration; no action was dispatched.")
                    pins[key] = {"fingerprint": fingerprint, "distribution": distribution, "name": name,
                                 "goal_id": goal, "owner": current_principal() or "", "updated_at": time.time()}
                    atomic_write_text(path, json.dumps(pins, sort_keys=True))
        if inspect.iscoroutinefunction(original):
            async def invoke(args):
                check()
                return await original(args)
        else:
            def invoke(args):
                check()
                return original(args)
        tool.fn = invoke
        return tool
    return construct
