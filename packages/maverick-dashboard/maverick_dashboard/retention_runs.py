"""Durable, policy-bound reviews and receipts for dashboard retention runs.

Each step is one store in one workspace with a frozen cutoff. A receipt is
written before dispatch, so an interrupted step is unknown, never successful.
Retries revisit unresolved steps only and keep the original cutoff.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from pathlib import Path

from fastapi import HTTPException

STORES = {
    "audit_days": ("audit", "Activity record"),
    "episodes_days": ("episodes", "Task history"),
    "events_days": ("goal_events", "Task activity details"),
    "messages_days": ("messages", "Chat messages"),
    "usage_days": ("usage_ledger", "Spending records"),
}


def _directory() -> Path:
    from maverick.file_lock import ensure_private_directory
    from maverick.paths import maverick_home

    directory = maverick_home() / "retention-runs"
    ensure_private_directory(directory)
    return directory


def _path(run_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", run_id):
        raise HTTPException(404, "Retention review was not found.")
    return _directory() / f"{run_id}.json"


def _write(receipt: dict) -> None:
    from maverick.file_lock import atomic_write_text

    atomic_write_text(_path(receipt["id"]), json.dumps(receipt, sort_keys=True))


def read(run_id: str, actor: str) -> dict:
    from maverick.file_lock import atomic_read_text, ensure_private_file

    path = _path(run_id)
    try:
        ensure_private_file(path)
        receipt = json.loads(atomic_read_text(path))
    except FileNotFoundError as exc:
        raise HTTPException(404, "Retention review was not found.") from exc
    if receipt.get("actor") != actor:
        raise HTTPException(404, "Retention review was not found.")
    return receipt


def _policy() -> dict:
    from maverick.config import config_source_errors, load_config

    cfg = load_config()
    if config_source_errors():
        raise HTTPException(503, "Retention policy is unavailable. Nothing can be deleted.")
    section = cfg.get("retention") or {}
    if not isinstance(section, dict):
        raise HTTPException(503, "Retention policy is invalid. Nothing can be deleted.")
    policy = {}
    for key in STORES:
        value = section.get(key, 0)
        if value is None:
            value = 0
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise HTTPException(503, "Retention period is invalid. Nothing can be deleted.")
        policy[key] = value
    return policy


def _scope() -> tuple[str | None, list[dict]]:
    from maverick.audit.preservation import preservation_snapshot
    from maverick.paths import current_tenant_id, tenant_scope
    from maverick.tenant.registry import get_tenant, list_tenants

    selected = current_tenant_id()
    records = [get_tenant(selected)] if selected else list_tenants()
    if selected and not records[0]:
        raise HTTPException(409, "The selected workspace is unavailable. Reopen retention.")
    targets = []
    for record in records or [None]:
        tenant = record.id if record else None
        with tenant_scope(tenant=tenant):
            policy = _policy()
            try:
                preservation = preservation_snapshot()
            except Exception as exc:
                raise HTTPException(503, "Preservation holds could not be verified. Nothing can be deleted.") from exc
        targets.append({"tenant": tenant, "name": (record.display_name or record.id) if record else "Main workspace", "policy": policy, "preservation": preservation})
    return selected, targets


def _fingerprint(selected: str | None, targets: list[dict]) -> str:
    return hashlib.sha256(json.dumps([selected, targets], sort_keys=True).encode()).hexdigest()


def _count(report: dict) -> int:
    total = 0
    for key, value in report.items():
        if key.startswith(("deleted", "removed")):
            total += len(value) if isinstance(value, list) else value if isinstance(value, int) and not isinstance(value, bool) else 0
    return total


def preview(actor: str) -> dict:
    from maverick.audit.retention import enforce
    from maverick.paths import tenant_scope

    selected, targets = _scope()
    now = time.time()
    receipt = {"id": secrets.token_hex(16), "actor": actor, "created_at": now, "cutoff_time": now,
               "scope": selected, "policy_revision": _fingerprint(selected, targets),
               "targets": targets, "status": "review", "steps": [],
               "holds": "Recorded privacy-rights holds block broad workspace deletion. External holds require separate confirmation with your records owner.",
               "blocked_by_hold": any(target.get("preservation", {}).get("blocked") for target in targets),
               "dependencies": "Task-history deletion removes provenance links from retained facts. Completed deletions cannot be undone."}
    for target in targets:
        with tenant_scope(tenant=target["tenant"]):
            for key, days in target["policy"].items():
                if not days:
                    continue
                store, label = STORES[key]
                result = enforce(config={key: days}, dry_run=True, now=now, _fleet_child=True)
                report = result.get(store, {})
                receipt["steps"].append({"tenant": target["tenant"], "workspace": target["name"],
                                         "key": key, "store": store, "label": label, "days": days,
                                         "eligible": _count(report), "status": "pending"})
    _write(receipt)
    return receipt


def apply(run_id: str, actor: str, *, confirm_holds: bool, retry: bool, authorize) -> dict:
    from maverick.audit import audit_event
    from maverick.audit.retention import enforce
    from maverick.authority_effects import authority_effect_fence
    from maverick.file_lock import cross_process_lock
    from maverick.paths import tenant_scope

    if not confirm_holds:
        raise HTTPException(422, "Confirm with the records owner that no preservation hold applies before deleting.")
    with cross_process_lock(_path(run_id), strict=True):
        receipt = read(run_id, actor)
        selected, targets = _scope()
        if _fingerprint(selected, targets) != receipt["policy_revision"]:
            raise HTTPException(409, "Workspace, preservation holds or effective retention policy changed. Review a new preview.")
        if any(target.get("preservation", {}).get("blocked") for target in targets):
            raise HTTPException(409, "Recorded preservation holds block this deletion. The records owner must resolve the hold; confirmation cannot override it.")
        if receipt["status"] == "completed":
            return receipt
        if not retry and receipt["status"] != "review":
            raise HTTPException(409, "This run has already started. Read its receipt and retry only unresolved stores.")
        if not retry and time.time() - receipt["created_at"] > 600:
            raise HTTPException(409, "This review expired. Review a fresh preview before deleting.")
        authorize()
        acknowledged = audit_event("retention_review_accepted", agent="dashboard", _global=True,
                    actor=actor, review_id=run_id, policy_revision=receipt["policy_revision"],
                    holds_confirmed=True, retry=retry)
        if acknowledged is not True:
            raise HTTPException(503, "The review could not be recorded. No deletion was started.")
        receipt["status"] = "running"
        receipt["holds_confirmed_by"] = actor
        _write(receipt)
        for step in receipt["steps"]:
            if step["status"] == "completed":
                continue
            authorize()
            # Re-read policy before every independent irreversible step.
            current_scope, current_targets = _scope()
            if _fingerprint(current_scope, current_targets) != receipt["policy_revision"]:
                receipt["status"] = "partial"
                _write(receipt)
                raise HTTPException(409, "Policy changed during this run. Completed deletions remain in the receipt; review again.")
            prior = step.get("removed", 0)
            uncertain_prior = step.get("uncertain_prior", False) or step["status"] in {"unknown", "running"}
            audit_unconfirmed = step.get("audit_record") == "unconfirmed"
            step["status"] = "running"
            _write(receipt)
            try:
                with authority_effect_fence():
                    authorize()
                    current_scope, current_targets = _scope()
                    if _fingerprint(current_scope, current_targets) != receipt["policy_revision"]:
                        step["status"] = "blocked"
                        receipt["status"] = "partial"
                        _write(receipt)
                        raise HTTPException(409, "Policy or preservation holds changed before deletion. Review again.")
                    with tenant_scope(tenant=step["tenant"]):
                        report = enforce(config={step["key"]: step["days"]}, now=receipt["cutoff_time"], _fleet_child=True)
                outcome = report.get(step["store"], {})
                step["removed"] = prior + _count(outcome)
                step["skipped"] = len(outcome.get("skipped", []))
                step["uncertain_prior"] = uncertain_prior
                step["audit_record"] = "unconfirmed" if audit_unconfirmed else "recorded" if report.get("marker") else "not_needed" if not _count(outcome) else "unconfirmed"
                step["status"] = "partial" if uncertain_prior or step["skipped"] or outcome.get("purge_marker") == "failed" or step["audit_record"] == "unconfirmed" else "completed"
            except HTTPException:
                step["status"] = "blocked"
                receipt["status"] = "partial"
                _write(receipt)
                raise
            except Exception as exc:
                # A backend can fail after a partial write. Do not manufacture
                # a zero count or present it as an entirely failed deletion.
                step["status"] = "unknown"
                step["error"] = type(exc).__name__
            _write(receipt)
        receipt["status"] = "completed" if all(s["status"] == "completed" for s in receipt["steps"]) else "partial"
        _write(receipt)
        return receipt
