"""Employee content authority is distinct from administrator policy authority.

The default is exact ownership. Exceptional reads require a purpose-bound,
independently approved, expiring resource grant with an acknowledged audit.
This does not grant mutation, publishing, or access to another task in a thread.
"""
from __future__ import annotations

import time

from fastapi import HTTPException
from maverick.governed_records import GovernedRecordStore

STORE = GovernedRecordStore("employee_content_access", "ECA", "employee_content_access",
                            authority_scope="deployment_global")


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


def may_read_employee_content(request, owner: str, *, resource_kind: str, resource_id: str) -> bool:
    from maverick.audit import record as record_audit

    from . import auth, scim_groups

    actor = auth._authorization_principal(request)
    if actor is None:
        # An installation credential identifies no employee. It can still open
        # genuinely unowned local work, but never bypass named-owner privacy.
        return not owner
    if owner and actor == owner:
        return True
    if not enabled() or not auth.has_global_permission(request, "admin"):
        return False
    if scim_groups.active_for_principal(actor) is False:
        return False
    try:
        for grant in STORE.list():
            if (grant.get("tenant") != tenant() or grant.get("requester") != actor
                    or grant.get("owner") != owner or grant.get("resource_kind") != resource_kind
                    or grant.get("resource_id") != str(resource_id)
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


def require_employee_content(request, owner: str, *, resource_kind: str, resource_id: str) -> None:
    if not may_read_employee_content(request, owner, resource_kind=resource_kind, resource_id=resource_id):
        raise HTTPException(403, "Employee content is private. Request a scoped, independently reviewed read in Content access.")


def audit_metadata(event: dict) -> dict:
    """Allowlist audit metadata BEFORE rendering, matching or exporting it.

    Raw payloads, titles, tool arguments and arbitrary text never survive.
    These projected rows are not claimed to be the original signed bytes.
    """
    safe = {key: event[key] for key in ("ts", "kind", "goal_id", "event_id", "hash", "prev_hash") if key in event}
    payload = event.get("payload")
    if isinstance(payload, dict):
        safe["payload"] = {key: payload[key] for key in (
            "actor", "tenant", "status", "ok", "cost_usd", "input_tokens", "output_tokens",
            "revision", "record_type", "record_id", "record_sha256", "access_request",
        ) if key in payload and isinstance(payload[key], (str, bool, int, float))}
    safe["projection"] = "audit_metadata_only"
    return safe
