"""Fleet-safe readiness and metrics projection for tenant quota authority."""
from __future__ import annotations

import hashlib
from collections.abc import Callable


def _tenant_label(tenant_id: str) -> str:
    """Stable pseudonymous metric label; never emit the raw customer id."""
    return hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]


def collect_tenant_quota_statuses(
    world_provider: Callable[[], object],
) -> tuple[bool, list[dict]]:
    from maverick.paths import current_tenant_id
    from maverick.tenant_quota_authority import (
        RESOURCE_NAMES,
        status,
        validate_deployment_policy,
    )

    from . import automation_queue

    # Validate the complete base vector even before the first tenant exists.
    # Otherwise an empty roster could publish ready while a malformed policy is
    # waiting to fail only after the first customer request.
    validate_deployment_policy()
    records: list[dict] = []

    def _collect() -> None:
        tenant = current_tenant_id()
        # The deployment-wide base pass is an administrative namespace, not a
        # tenant capacity pool. Named client floors and roster tenants follow.
        if not tenant:
            return
        state = status(str(tenant), world=world_provider())
        state["tenant_label"] = _tenant_label(str(tenant))
        records.append(state)

    complete = automation_queue._run_for_active_tenants(
        "tenant quota authority probe",
        _collect,
    )
    healthy = bool(complete)
    for record in records:
        limits = record.get("limits") or {}
        used = record.get("used") or {}
        reserved = record.get("reserved") or {}
        if any(limits.get(name) is None for name in RESOURCE_NAMES):
            healthy = False
            continue
        if any(
            float(used.get(name) or 0) + float(reserved.get(name) or 0)
            > float(limits[name])
            for name in RESOURCE_NAMES
        ):
            # A lowered mutable policy can put already-settled usage or a
            # pre-existing hold above its new ceiling. Admission remains
            # closed, and readiness must make that state visible until it is
            # reconciled rather than publishing a deceptively healthy vector.
            healthy = False
    return healthy, records


def quota_prometheus_lines(records: list[dict], *, healthy: bool) -> list[str]:
    from maverick.tenant_quota_authority import RESOURCE_NAMES

    lines = [
        "# HELP maverick_tenant_quota_authority_ready Whether every tenant "
        "quota vector and authority read is available",
        "# TYPE maverick_tenant_quota_authority_ready gauge",
        f"maverick_tenant_quota_authority_ready {1 if healthy else 0}",
        "# HELP maverick_tenant_quota_policy_generation Shared policy generation",
        "# TYPE maverick_tenant_quota_policy_generation gauge",
        "# HELP maverick_tenant_quota_resource Tenant quota resource standing",
        "# TYPE maverick_tenant_quota_resource gauge",
    ]
    for record in sorted(records, key=lambda item: str(item.get("tenant_label") or "")):
        tenant = str(record.get("tenant_label") or "unknown")
        used = record.get("used") or {}
        reserved = record.get("reserved") or {}
        limits = record.get("limits") or {}
        generation = int(record.get("authority_policy_generation") or 0)
        lines.append(
            "maverick_tenant_quota_policy_generation"
            f'{{tenant="{tenant}"}} {generation}'
        )
        for resource in RESOURCE_NAMES:
            available = limits.get(resource) is not None
            used_value = float(used.get(resource) or 0)
            reserved_value = float(reserved.get(resource) or 0)
            limit_value = float(limits[resource]) if available else -1.0
            values = {
                "used": used_value,
                "reserved": reserved_value,
                "limit": limit_value,
                "available": 1.0 if available else 0.0,
                "remaining": (
                    max(limit_value - used_value - reserved_value, 0.0)
                    if available
                    else -1.0
                ),
                "over_limit": (
                    1.0
                    if available and used_value + reserved_value > limit_value
                    else 0.0
                ),
            }
            for standing, value in values.items():
                lines.append(
                    "maverick_tenant_quota_resource"
                    f'{{tenant="{tenant}",resource="{resource}",standing="{standing}"}} '
                    f"{value:g}"
                )
    return lines


__all__ = ["collect_tenant_quota_statuses", "quota_prometheus_lines"]
