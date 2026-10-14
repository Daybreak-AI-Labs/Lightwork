"""Shared, immutable dashboard role/permission policy.

The dashboard authorizer and the production identity-posture attestor must hash
the same permission lattice that the request gates actually consume.  Keeping
the lattice in the core package avoids a second, attestation-only copy that
could drift from runtime authorization.
"""

from __future__ import annotations

ROLES = ("admin", "operator", "auditor", "viewer")

PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset({"admin", "audit", "operate", "view"}),
    "operator": frozenset({"operate", "view"}),
    "auditor": frozenset({"audit", "view"}),
    "viewer": frozenset({"view"}),
}


def canonical_permission_policy() -> dict[str, list[str]]:
    """Return the exact built-in permission lattice as canonical JSON data."""

    return {role: sorted(PERMISSIONS[role]) for role in ROLES}


__all__ = ["PERMISSIONS", "ROLES", "canonical_permission_policy"]
