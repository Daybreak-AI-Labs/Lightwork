"""Employee content authority is distinct from administrator policy authority.

The default is exact ownership. Exceptional reads require a purpose-bound,
independently approved, expiring resource grant with an acknowledged audit.
This does not grant mutation, publishing, or access to another task in a thread.
"""
from __future__ import annotations

import hashlib
import json
import time

from fastapi import HTTPException
from maverick.governed_records import GovernedRecordStore

STORE = GovernedRecordStore("employee_content_access", "ECA", "employee_content_access",
                            authority_scope="deployment_global")

# Only fixed dashboard operation names may be shown in the metadata view.
# Arbitrary action text can describe an employee's work, so a slug pattern is
# not sufficient protection here.
VISIBLE_CONTROL_ACTIONS = frozenset({
    "invite_created", "invite_revoked", "department_access_set", "department_access_removed",
    "channel_saved", "channel_settings_removed", "settings_models", "settings_budget",
    "settings_roles", "settings_allowed", "settings_discover", "settings_discovery",
    "settings_providers", "settings_provider_test", "settings_capabilities",
    "settings_features", "settings_visibility", "settings_visibility_reset",
    "audit_download", "audit_forward", "audit_seal", "audit_rotate_key",
    "audit_write_once_push", "benchmark_score_recorded",
})


def enabled() -> bool:
    from maverick.config import get_features

    return get_features().get("employee_content_access_requests") is True


def tenant() -> str:
    from maverick.paths import current_tenant_id_strict

    return current_tenant_id_strict() or ""


def _reviewer_current(record: dict) -> bool:
    from . import auth, rbac, scim_groups

    reviewer = record.get("reviewed_by")
    if not reviewer or reviewer == record.get("requester"):
        return False
    if scim_groups.active_for_principal(reviewer) is False:
        return False
    if reviewer == record.get("owner"):
        scope = record.get("tenant")
        role = rbac.get_tenant_role(scope, reviewer) if scope else auth.global_role_for_principal(reviewer)
        return "view" in rbac.permissions_for(role) if role else False
    return "admin" in rbac.permissions_for(auth.global_role_for_principal(reviewer))


def may_read_employee_content(request, owner: str, *, resource_kind: str, resource_id: str, resource_revision: str | None = None) -> bool:
    from maverick.audit import record as record_audit

    from . import auth, scim_groups

    actor = auth._authorization_principal(request)
    if actor is None:
        # An installation credential identifies no employee. It can still open
        # genuinely unowned local work, but never bypass named-owner privacy.
        return not owner
    if owner and actor == owner:
        return True
    eligible = auth.has_permission(request, "operate") if resource_kind == "approval" else auth.has_global_permission(request, "admin")
    if not enabled() or not eligible:
        return False
    if scim_groups.active_for_principal(actor) is False:
        return False
    try:
        for grant in STORE.list():
            if (grant.get("tenant") != tenant() or grant.get("requester") != actor
                    or grant.get("owner") != owner or grant.get("resource_kind") != resource_kind
                    or grant.get("resource_id") != str(resource_id)
                    or (resource_revision is not None and grant.get("resource_revision") != resource_revision)
                    or grant.get("status") != "approved" or grant.get("_audit_pending")
                    or grant.get("expires_at", 0) <= time.time() or not _reviewer_current(grant)):
                continue
            # Every actual exceptional read needs its own confirmed receipt.
            return record_audit("employee_content_read", agent=actor, actor=actor,
                                access_request=grant["id"], purpose=grant["purpose"],
                                resource_kind=resource_kind, resource_id=str(resource_id)) is True
    except Exception:
        return False
    return False


def approval_resource(approval, world) -> dict:
    owner = approval.requested_by or ""
    if approval.goal_id:
        goal = world.get_goal(approval.goal_id)
        if goal is None:
            raise HTTPException(404, "No such approval.")
        owner = goal.owner or ""
    revision = hashlib.sha256(json.dumps([approval.id, owner, approval.goal_id,
        approval.action, approval.scope, approval.detail], sort_keys=True).encode()).hexdigest()
    return {"resource_kind": "approval", "resource_id": str(approval.id), "owner": owner,
            "resource_revision": revision, "label": f"Approval {approval.id} — {approval.status}"}


def can_read_approval(request, approval, world=None) -> bool:
    if approval is None:
        return False
    from . import auth, settings_quorum
    if settings_quorum.is_settings_approval(approval) and auth.has_global_permission(request, "admin"):
        if settings_quorum.has_bound_admin_review(approval):
            return True
    if world is None:
        from ._shared import _world
        world = _world()
    try:
        resource = approval_resource(approval, world)
    except HTTPException:
        return False
    return may_read_employee_content(request, resource["owner"], resource_kind="approval",
        resource_id=resource["resource_id"], resource_revision=resource["resource_revision"])


def require_approval_content(request, approval_id, world=None):
    if world is None:
        from ._shared import _world
        world = _world()
    row = world.get_approval(int(approval_id))
    if row is None:
        raise HTTPException(404, "No such approval.")
    if not can_read_approval(request, row, world):
        raise HTTPException(403, "This approval contains private employee content. Request its exact approval-card read in Content access; existing decision authority is still required.")
    return row


def require_employee_content(request, owner: str, *, resource_kind: str, resource_id: str) -> None:
    if not may_read_employee_content(request, owner, resource_kind=resource_kind, resource_id=resource_id):
        raise HTTPException(403, "Employee content is private. Request a scoped, independently reviewed read in Content access.")


def _audit_fields(event: dict, *, kind: str = "") -> dict:
    """Control metadata only; free-form explanations and tool names stay private."""
    safe = {key: event[key] for key in (
        "actor", "principal", "tenant", "status", "ok", "cost_usd", "input_tokens", "output_tokens",
        "revision", "record_type", "record_id", "record_sha256", "access_request",
    ) if key in event and isinstance(event[key], (str, bool, int, float))}
    if kind == "setting_changed":
        # Setting receipts may contain private configuration values. Only
        # bounded identifiers and the completion state belong in metadata.
        import re

        for name in ("section", "key"):
            value = event.get(name)
            if isinstance(value, str) and len(value) <= 128 and re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
                safe[name] = value
        if isinstance(event.get("phase"), str) and event["phase"] in {"prepared", "completed"}:
            safe["phase"] = event["phase"]
    if isinstance(event.get("stage"), str) and event["stage"] in {"input", "tool", "output"}:
        safe["stage"] = event["stage"]
    if isinstance(event.get("decision"), str) and event["decision"] in {"approve", "deny", "timeout"}:
        safe["decision"] = event["decision"]
    if isinstance(event.get("rule"), str) and event["rule"] in {"capability", "deny_actions", "deny_min_risk", "deny_above",
                            "require_human_actions", "require_human_min_risk", "require_human_above"}:
        safe["rule"] = event["rule"]
    action = event.get("action")
    if kind == "control_plane_action" and isinstance(action, str) and action in VISIBLE_CONTROL_ACTIONS:
        safe["action"] = action
    score = event.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1:
        safe["score"] = score
    # These citations come from the shipped control catalog, not arbitrary
    # employee text substituted into a regulation/article field.
    from maverick.regulatory_compiler import REGULATIONS
    obligations = {item.id: item for group in REGULATIONS.values() for item in group}
    obligation = obligations.get(event.get("obligation_id")) if isinstance(event.get("obligation_id"), str) else None
    if obligation:
        safe.update(obligation_id=obligation.id, regulation=obligation.regulation, article=obligation.article)
    checked = event.get("obligations_checked")
    if isinstance(checked, list):
        safe["obligations_checked"] = [item for item in checked[:16] if isinstance(item, str) and item in obligations]
    return safe


def audit_metadata(event: dict) -> dict:
    """Allowlist audit metadata BEFORE rendering, matching or exporting it.

    Raw payloads, titles, tool arguments and arbitrary text never survive.
    These projected rows are not claimed to be the original signed bytes.
    Both native flattened rows and nested transport rows retain control fields.
    """
    safe = {key: event[key] for key in ("ts", "kind", "event_id", "hash", "prev_hash")
            if key in event and isinstance(event[key], (str, int, float))}
    if isinstance(event.get("goal_id"), int) and not isinstance(event["goal_id"], bool):
        safe["goal_id"] = event["goal_id"]
    kind = safe.get("kind", "")
    safe.update(_audit_fields(event, kind=kind))
    payload = event.get("payload")
    if isinstance(payload, dict):
        safe["payload"] = _audit_fields(payload, kind=kind)
    safe["projection"] = "audit_metadata_only"
    return safe
