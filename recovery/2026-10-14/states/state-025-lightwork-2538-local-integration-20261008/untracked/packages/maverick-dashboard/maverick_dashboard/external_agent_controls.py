"""Reviewed, recoverable outside-agent controls for the native product UI."""
from __future__ import annotations

import re
import time

from fastapi import HTTPException, Request
from maverick import agent_trust, config
from maverick.authority_effects import authority_effect_fence
from maverick.governed_operations import digest
from maverick.governed_records import GovernedRecordStore

from . import auth, tools_ops_api
from .draft_api import context_for

PAGE = "/external-agents"
TTL = 600
NOTICE = ("Pausing access stops this agent's next request to Lightwork. It does not turn off or remove "
          "the agent on its own platform.")


def _store() -> GovernedRecordStore:
    return GovernedRecordStore("outside-agent-access-changes", "EAC", "outside_agent_access_change")


def _authority(request: Request, expected_context: str | None = None, *, write: bool = False) -> str:
    auth.require_permission(request, "admin")
    current = context_for(request)
    if expected_context is not None and current != expected_context:
        raise HTTPException(409, "Your account or workspace changed. Reopen Outside agents before continuing.")
    if write:
        tools_ops_api.same_origin(request)
        if config.config_source_errors(include_tenant=True):
            raise HTTPException(503, "Current settings could not be checked. Nothing was changed.")
    return current


def _selected_id(selection: dict) -> str:
    if (set(selection) != {"kind", "id"} or selection.get("kind") != "external_agent"
            or not isinstance(selection.get("id"), str)
            or not 1 <= len(selection["id"]) <= 64):
        raise HTTPException(404, "Open one outside agent before reviewing this change.")
    return selection["id"]


def _managed_entry(agent_id: str) -> dict:
    try:
        entries = agent_trust._load_managed()
    except (OSError, ValueError, agent_trust.AgentTrustError) as exc:
        raise HTTPException(503, "Saved outside-agent access could not be checked. Nothing was changed.") from exc
    entry = next((dict(row) for row in entries if str(row.get("id") or "") == agent_id), None)
    if entry is None:
        raise HTTPException(409, "This agent is controlled by installation settings. Add it on Outside agents before changing its access here.")
    return entry


def _current_access(agent_id: str) -> tuple[str, bool, agent_trust.TrustedAgent]:
    entry = _managed_entry(agent_id)
    try:
        registry = agent_trust.load_registry(strict=True)
        agent = registry.get(agent_id)
    except (OSError, ValueError, agent_trust.AgentTrustError) as exc:
        raise HTTPException(503, "Saved outside-agent access could not be checked. Nothing was changed.") from exc
    if agent is None:
        raise HTTPException(409, "This outside agent is no longer available. Refresh the page before continuing.")
    active, rule = agent.is_active()
    label = ("Paused" if entry.get("revoked") is True else "Active" if active else
             "Expired" if rule == "expired" else "Starts later" if rule == "not_yet_valid" else "Unavailable")
    return label, entry.get("revoked") is True, agent


def snapshot(request: Request, selection: dict, *, desired_paused: bool = True):
    from .product_operations import Snapshot

    context = _authority(request)
    agent_id = _selected_id(selection)
    entry = _managed_entry(agent_id)
    state, revoked, agent = _current_access(agent_id)
    return Snapshot(digest([context, entry]), "Current outside-agent access and its saved limits",
        NOTICE if desired_paused else "Restoring access allows the agent to contact Lightwork again under its saved limits.",
        {"agent": agent_id, "access": state, "highest_allowed_risk": agent.max_risk,
                 "monthly_or_total_limit": agent.max_dollars,
                 "time_limit_seconds": agent.max_wall_seconds},
        blocked=("This agent's access is already paused." if desired_paused else
                 "This agent's access is already active.") if revoked == desired_paused else "")


def prepare(request: Request, selection: dict, _arguments: dict, *, desired_paused: bool = True) -> dict:
    context = _authority(request, write=True)
    agent_id = _selected_id(selection)
    entry = _managed_entry(agent_id)
    current_paused = bool(entry.get("revoked", False))
    if current_paused == desired_paused:
        state = "paused" if desired_paused else "not paused"
        raise HTTPException(409, f"This agent's access is already {state}. Refresh its current status.")
    if not desired_paused:
        _current, _revoked, agent = _current_access(agent_id)
        now = time.time()
        if agent.expires_at is not None and now >= agent.expires_at:
            raise HTTPException(409, "This agent's access key has expired. Create a current key on Outside agents before restoring access.")
        if agent.not_before is not None and now < agent.not_before:
            raise HTTPException(409, "This agent's access key has not started yet. Check its settings on Outside agents before restoring access.")
    bound = {"agent_id": agent_id, "entry": entry, "context": context,
             "actor": tools_ops_api.actor(request), "expires": time.time() + TTL,
             "desired_paused": desired_paused}
    _authority(request, context, write=True)
    action = "Pause" if desired_paused else "Restore"
    return {"summary": f"{action} this exact outside agent's access to Lightwork. Its settings, history and outside platform stay unchanged.",
            "changes": [{"field": "Agent", "after": agent_id},
                        {"field": "Effect", "after": NOTICE if desired_paused else
                            "Restore its access to Lightwork under its existing limits. This does not turn on or change its outside platform."}],
            "native": bound}


def _owned(request: Request, operation: str, prepared: dict | None = None):
    context = _authority(request)
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise HTTPException(404, "This original access change is unavailable.")
    row = _store().get("EAC-" + operation)
    actor = tools_ops_api.actor(request)
    if row and (row["actor"] != actor or row["context"] != context):
        raise HTTPException(404, "This original access change is unavailable to this account and workspace.")
    if row and prepared is not None and row["binding"] != digest([operation, prepared]):
        raise HTTPException(409, "This original request belongs to another reviewed access change.")
    if row and row.get("_audit_pending"):
        row = _store()._flush(row)
    _authority(request, context)
    return row


def original(request: Request, operation: str, prepared: dict | None = None) -> dict:
    context = _authority(request)
    row = _owned(request, operation, prepared)
    desired_paused = prepared["desired_paused"] if prepared else (row or {}).get("desired_paused", True)
    action = "pause" if desired_paused else "restore"
    expected_access = "Paused" if desired_paused else "Active"
    value = {"agent": prepared["agent_id"] if prepared else (row or {}).get("agent_id", "outside agent"),
             "requested_access": expected_access, "confirmed": False,
             "message": f"The original access {action} is not confirmed. Check Outside agents before deciding what to do next; nothing was repeated."}
    if row and row.get("state") == "confirmed" and not row.get("_audit_pending"):
        value.update(confirmed=True, recorded_at=row["updated_at"],
                     message=f"The original reviewed access {action} is confirmed. Nothing was repeated.")
    try:
        if row or prepared:
            agent_id = prepared["agent_id"] if prepared else row["agent_id"]
            value["current_access"], value["current_revoked"], _agent = _current_access(agent_id)
    except HTTPException:
        value["current_access"] = "Unavailable; check Outside agents."
    _authority(request, context)
    return value


def apply(request: Request, prepared: dict, operation: str):
    from .product_operations import Outcome

    context = _authority(request, prepared["context"], write=True)
    actor = tools_ops_api.actor(request)
    expected_revoked = prepared["desired_paused"]
    with authority_effect_fence():
        if _owned(request, operation, prepared):
            result = original(request, operation, prepared)
            return Outcome("replayed" if result["confirmed"] else "unconfirmed", result["message"], result)

        def guard() -> None:
            _authority(request, context, write=True)
            expires = prepared.get("expires")
            if (prepared.get("actor") != actor or type(expires) not in {float, int}
                    or not time.time() < expires <= time.time() + TTL):
                raise HTTPException(409, "This review expired or access changed. Review the current agent again; nothing was changed.")
            _authority(request, context, write=True)

        action = "pause" if prepared["desired_paused"] else "restore"
        claim = _store().create({"id": "EAC-" + operation, "actor": actor, "context": context,
            "agent_id": prepared["agent_id"], "desired_paused": prepared["desired_paused"],
            "binding": digest([operation, prepared]), "state": "started"},
            action=f"external_agent_access.{action}_started", actor=actor, max_records=10000)
        if claim.get("_audit_pending"):
            raise HTTPException(503, "The activity record could not confirm this request. No access was changed.")
        try:
            def change_access() -> bool:
                return agent_trust.set_revoked(prepared["agent_id"], prepared["desired_paused"],
                    expected_entry=prepared["entry"], effect_guard=guard)

            if prepared["desired_paused"]:
                changed = change_access()
            else:
                from maverick.revocation import ProtectedRevocationError, shared
                try:
                    changed = shared().run_if_unprotected(f"agent:{prepared['agent_id']}", change_access)
                except ProtectedRevocationError as exc:
                    raise HTTPException(409, "This agent is blocked for a security review. Use its release controls on Outside agents.") from exc
            if not changed:
                raise HTTPException(409, "This outside agent is no longer managed here. Nothing was changed.")
            _store().update(claim["id"], lambda value: value.update(state="confirmed"),
                            expected_revision=claim["revision"],
                            action=f"external_agent_access.{action}d", actor=actor)
        except Exception as exc:
            result = original(request, operation, prepared)
            if isinstance(exc, HTTPException) and result.get("current_revoked") is not expected_revoked:
                raise
            return Outcome("confirmed" if result["confirmed"] else "unconfirmed", result["message"], result)
    result = original(request, operation, prepared)
    return Outcome("confirmed" if result["confirmed"] else "unconfirmed", result["message"], result)


_BUDGET_FIELDS = ("period", "period_key", "period_spent", "spent_dollars", "over_budget", "max_dollars")


def _budget(agent_id: str) -> dict:
    from maverick import external_agents

    try:
        sidecar = external_agents._load_sidecar(strict=True)
    except (OSError, ValueError, external_agents.ExternalAgentsError) as exc:
        raise HTTPException(503, "Saved outside-agent spending could not be checked. Nothing was changed.") from exc
    meta = sidecar.get(agent_id)
    if meta is None:
        raise HTTPException(404, "This outside agent is no longer available. Refresh the page before continuing.")
    return {key: meta.get(key) for key in _BUDGET_FIELDS}


def _budget_readable(budget: dict) -> dict:
    return {"period": "This month" if budget["period"] == "monthly" else "All time",
            "amount_counted": budget["period_spent"],
            "reported_all_time": budget["spent_dollars"],
            "limit": budget["max_dollars"],
            "over_limit": budget["over_budget"] is True}


def budget_snapshot(request: Request, selection: dict):
    from .product_operations import Snapshot

    context = _authority(request)
    agent_id = _selected_id(selection)
    budget = _budget(agent_id)
    limit = budget["max_dollars"]
    period = "this month" if budget["period"] == "monthly" else "all time"
    blocked = "This agent is not over its spending limit." if budget["over_budget"] is not True else ""
    return Snapshot(digest([context, agent_id, budget]), "Current outside-agent spending limit and reported use",
        "Resetting starts a new allowance period; its all-time reported total stays unchanged.",
        {"agent": agent_id, "reported_this_period": budget["period_spent"],
         "period": period, "limit": limit, "reported_all_time": budget["spent_dollars"],
         "over_limit": budget["over_budget"]}, blocked=blocked)


def prepare_budget_reset(request: Request, selection: dict, _arguments: dict) -> dict:
    context = _authority(request, write=True)
    agent_id = _selected_id(selection)
    budget = _budget(agent_id)
    if budget["over_budget"] is not True:
        raise HTTPException(409, "This agent is not currently over its spending limit. Refresh its saved status.")
    bound = {"agent_id": agent_id, "budget": budget, "context": context,
             "actor": tools_ops_api.actor(request), "expires": time.time() + TTL}
    _authority(request, context, write=True)
    return {"summary": "Reset this outside agent's current spending allowance. Its all-time reported total remains in the history.",
            "changes": [{"field": "Agent", "after": agent_id},
                        {"field": "Current amount counted", "before": budget["period_spent"], "after": 0},
                        {"field": "All-time reported amount", "after": budget["spent_dollars"]},
                        {"field": "Effect", "after": "The agent can request work again within its saved limits."}],
            "native": bound}


def original_budget(request: Request, operation: str, prepared: dict | None = None) -> dict:
    context = _authority(request)
    row = _owned(request, operation, prepared)
    agent_id = prepared["agent_id"] if prepared else (row or {}).get("agent_id", "")
    result = {"agent": agent_id, "confirmed": False,
              "message": "The original spending reset is not confirmed. Check Outside agents before deciding what to do next; nothing was repeated."}
    if row and row.get("state") == "confirmed" and not row.get("_audit_pending"):
        result.update(confirmed=True, recorded_at=row["updated_at"],
                      message="The original reviewed spending reset is confirmed. Nothing was repeated.")
    if agent_id:
        try:
            result["current_spending"] = _budget_readable(_budget(agent_id))
        except HTTPException:
            result["current_spending"] = "Unavailable; check Outside agents."
    _authority(request, context)
    return result


def apply_budget_reset(request: Request, prepared: dict, operation: str):
    from maverick import external_agents

    from .product_operations import Outcome

    context = _authority(request, prepared["context"], write=True)
    actor = tools_ops_api.actor(request)
    with authority_effect_fence():
        if _owned(request, operation, prepared):
            result = original_budget(request, operation, prepared)
            return Outcome("replayed" if result["confirmed"] else "unconfirmed", result["message"], result)

        def guard() -> None:
            _authority(request, context, write=True)
            expires = prepared.get("expires")
            if (prepared.get("actor") != actor or type(expires) not in {float, int}
                    or not time.time() < expires <= time.time() + TTL):
                raise HTTPException(409, "This review expired or access changed. Review the current spending again; nothing was changed.")
            _authority(request, context, write=True)

        claim = _store().create({"id": "EAC-" + operation, "actor": actor, "context": context,
            "agent_id": prepared["agent_id"], "kind": "budget_reset",
            "before": prepared["budget"], "binding": digest([operation, prepared]), "state": "started"},
            action="external_agent_access.budget_reset_started", actor=actor, max_records=10000)
        if claim.get("_audit_pending"):
            raise HTTPException(503, "The activity record could not confirm this request. Spending was not reset.")
        try:
            changed = external_agents.reset_budget(prepared["agent_id"], reset_by=actor,
                expected_budget=prepared["budget"], effect_guard=guard)
            if not changed:
                raise HTTPException(409, "This outside agent is no longer available. Spending was not reset.")
            _store().update(claim["id"], lambda value: value.update(state="confirmed"),
                            expected_revision=claim["revision"],
                            action="external_agent_access.budget_reset", actor=actor)
        except Exception:
            result = original_budget(request, operation, prepared)
            return Outcome("confirmed" if result["confirmed"] else "unconfirmed", result["message"], result)
    result = original_budget(request, operation, prepared)
    return Outcome("confirmed" if result["confirmed"] else "unconfirmed", result["message"], result)
