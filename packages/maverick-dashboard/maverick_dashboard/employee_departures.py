"""Employee departure inventory, deactivation and explicitly retained handoff.

Local credential removal is never described as provider revocation. The case
stays open while running work, pending review or external closure is unresolved.
No content is erased and no private-read authority is granted to a custodian.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager

from fastapi import HTTPException
from maverick.governed_records import GovernedRecordStore

STORE = GovernedRecordStore("employee_departures", "EDP", "employee_departure",
                            authority_scope="deployment_global")


@contextmanager
def _tenant(tenant):
    from maverick.paths import reset_tenant, set_tenant
    token = set_tenant(tenant)
    try:
        yield
    finally:
        reset_tenant(token)


def enabled():
    from maverick.config import get_features
    return get_features().get("employee_departures") is True


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def candidates():
    from . import admin_pages, auth, rbac, scim_groups
    from .identity_format import humanize_principal
    principals = set(rbac.list_users()) | set(admin_pages._signed_in_principals())
    return [{"principal": principal, "label": humanize_principal(principal).name,
             "active": scim_groups.active_for_principal(principal) is not False,
             "admin": "admin" in rbac.permissions_for(auth.global_role_for_principal(principal))}
            for principal in sorted(principals) if principal.startswith("user:")]


def _workspace_inventory(principal):
    from maverick import connections
    from maverick.job_queue import JobQueue
    from maverick.project_reviews import _PACKETS
    from maverick.world_model import close_world_if_owned, open_world

    rows = []
    world = open_world()
    try:
        goals = world.list_goals(owner=principal, limit=5001)
        for goal in goals[:5000]:
            rows.append({"kind": "task", "id": str(goal.id), "status": goal.status,
                         "followup": goal.status not in {"done", "completed", "failed", "cancelled", "error"}})
        for project in world.list_projects(owner=principal):
            rows.append({"kind": "project", "id": str(project["id"]), "status": project.get("status", ""), "followup": True})
        for approval in world.pending_approvals():
            if principal in {approval.requested_by, approval.claimed_by}:
                rows.append({"kind": "approval", "id": str(approval.id), "status": "pending", "followup": True})
    finally:
        close_world_if_owned(world)
    for connection in connections.list_connections(owner=principal):
        rows.append({"kind": "connection", "id": connection["name"], "revision": connection["revision"],
                     "status": "connected", "followup": True, "provider": connection["connector"]})
    for job in JobQueue().iter_pending("run_goal", include_running=True):
        if (job.payload or {}).get("owner") == principal:
            rows.append({"kind": "scheduled_work", "id": str(job.id), "status": job.status,
                         "payload_digest": _digest(job.payload), "followup": True})
    for packet in _PACKETS.list():
        if principal in {packet.get("owner"), packet.get("reviewer")} and packet.get("status") == "pending":
            rows.append({"kind": "review", "id": packet["id"], "status": packet["status"],
                         "revision": packet["revision"], "followup": True})
    return rows, len(goals) > 5000


def preview(principal):
    from maverick.tenant.registry import list_tenants
    from . import rbac, scim_groups

    if principal not in {row["principal"] for row in candidates()}:
        raise HTTPException(404, "Choose a known signed-in employee.")
    scopes = [None] + [record.id for record in list_tenants()]
    items, truncated = [], False
    for tenant in scopes:
        with _tenant(tenant):
            rows, partial = _workspace_inventory(principal)
            items.extend({**row, "tenant": tenant} for row in rows)
            truncated = truncated or partial
            role = rbac.get_tenant_role(tenant, principal) if tenant else rbac.get_stored_role(principal)
            if role:
                items.append({"kind": "membership", "id": tenant or "installation", "tenant": tenant,
                              "status": role, "followup": False})
    result = {"principal": principal, "active": scim_groups.active_for_principal(principal) is not False,
              "items": items, "truncated": truncated,
              "coverage": "Dashboard tasks, projects, approvals, project reviews, run-goal schedules and owned connections. Other module queues, external identities and provider grants require separate closure evidence.",
              "preservation": "All existing data and preservation holds are retained. Custodian assignment does not grant access to private employee content."}
    result["snapshot"] = _digest(result)
    return result


def begin(principal, custodian, expected_snapshot, actor, authorize):
    from maverick import connections
    from maverick.authority_effects import authority_effect_fence
    from maverick.job_queue import JobQueue
    from . import auth, rbac, scim

    with authority_effect_fence():
        authorize()
        if principal == actor or "admin" in rbac.permissions_for(auth.global_role_for_principal(principal)):
            raise HTTPException(409, "Assign another administrator and explicitly change this account's administrator role before departure. You cannot deactivate your own account here.")
        if custodian == principal or not any(row["principal"] == custodian and row["active"] and row["admin"] for row in candidates()):
            raise HTTPException(422, "Choose a different, active installation administrator as handoff owner.")
        inventory = preview(principal)
        if inventory["snapshot"] != expected_snapshot:
            raise HTTPException(409, "This employee's work changed. Review a fresh inventory before deactivating.")
        if inventory["truncated"]:
            raise HTTPException(409, "The inventory exceeds this view's supported window. Do not treat it as complete.")
        case_id = "EDP-" + _digest([principal, expected_snapshot, actor])[:32]
        existing = STORE.get(case_id)
        if existing:
            return existing
        row = STORE.create({"id": case_id, "principal": principal, "custodian": custodian,
                            "status": "prepared", "inventory": inventory, "steps": [],
                            "retained_data_owner": custodian}, action="departure_reviewed", actor=actor)
        if row.get("_audit_pending"):
            return row  # No identity or credential effect before audit acknowledgement.
        def checkpoint(step):
            nonlocal row
            def mutate(current):
                current["steps"].append(step)
                current["status"] = "handoff_required"
            row = STORE.update(row["id"], mutate, expected_revision=row["revision"], action="departure_progress", actor=actor)
            if row.get("_audit_pending"):
                raise HTTPException(503, "Departure progress is saved but its activity receipt is pending. Review the saved case before any further action.")
        checkpoint({"kind": "identity", "status": "dispatching"})
        scim._retire_user_identity({"externalId": principal.removeprefix("user:")})
        checkpoint({"kind": "identity", "status": "deactivated", "sessions": "revoked"})
        for item in inventory["items"]:
            authorize()
            with _tenant(item["tenant"]):
                if item["kind"] == "connection":
                    checkpoint({"kind": "connection", "tenant": item["tenant"], "id": item["id"], "status": "dispatching"})
                    removed = connections.delete_connection(item["id"], owner=principal, expected_revision=item["revision"])
                    checkpoint({"kind": "connection", "tenant": item["tenant"], "id": item["id"],
                                "status": "removed_from_lightwork" if removed else "needs_review",
                                "provider_revocation": "unconfirmed"})
                elif item["kind"] == "scheduled_work" and item["status"] == "pending":
                    checkpoint({"kind": "scheduled_work", "tenant": item["tenant"], "id": item["id"], "status": "dispatching"})
                    queue = JobQueue()
                    current = queue.get(int(item["id"]))
                    cancelled = bool(current and current.status == "pending" and _digest(current.payload) == item["payload_digest"] and queue.cancel(current.id))
                    checkpoint({"kind": "scheduled_work", "tenant": item["tenant"], "id": item["id"],
                                "status": "cancelled" if cancelled else "needs_review"})
        # Completion is deliberately unavailable until every module and external
        # authority can supply verifiable transfer/revocation/retention evidence.
        return row


def record_handoff(case_id, revision, *, item_key, owner, disposition, evidence, actor, authorize):
    """Record a person's explicit handoff responsibility, never a content grant.

    External evidence is a reviewed human attestation, not provider verification.
    The case remains open because the covered queues are not every module.
    """
    from maverick.authority_effects import authority_effect_fence
    with authority_effect_fence():
        authorize()
        if not any(row["principal"] == owner and row["active"] and row["admin"] for row in candidates()):
            raise HTTPException(422, "Choose an active installation administrator as the responsible handoff owner.")
        def mutate(row):
            items = row["inventory"]["items"]
            if item_key != "external" and item_key not in {_digest(item) for item in items}:
                raise HTTPException(404, "This item does not belong to the reviewed departure inventory.")
            row.setdefault("handoffs", {})[item_key] = {"owner": owner, "disposition": disposition,
                "evidence": evidence, "recorded_by": actor, "verification": "human_attestation"}
            row["status"] = "handoff_required"
        return STORE.update(case_id, mutate, expected_revision=revision, action="departure_handoff", actor=actor)


def public(row):
    from .identity_format import humanize_principal
    result = {key: row.get(key) for key in ("id", "revision", "principal", "custodian", "status", "steps", "handoffs")}
    result["person"] = humanize_principal(row["principal"]).name
    result["audit_pending"] = bool(row.get("_audit_pending"))
    result["inventory"] = {**row["inventory"], "items": [{**item, "key": _digest(item)} for item in row["inventory"]["items"]]}
    return result
