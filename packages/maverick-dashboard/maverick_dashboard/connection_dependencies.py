"""Permission-scoped connection attention and explicit dependency review."""
from __future__ import annotations

import time

from .auth import has_permission


def owned_connections(request):
    from maverick.connections import list_connections

    from .api import _request_identity

    actor = _request_identity(request)
    rows = list_connections(owner=actor)
    # An auth-off local session does not inherit credentials owned by named
    # employees from a prior authenticated session.
    return [row for row in rows if row.get("owner") == actor or (actor is None and not row.get("owner"))]


def connection_attention(request):
    from maverick import connections

    if not has_permission(request, "operate"):
        return {"items": [], "coverage": "unavailable"}
    if not connections.enabled():
        return {"items": [], "coverage": "disabled"}
    try:
        items = []
        for record in owned_connections(request):
            oauth = record.get("oauth") or {}
            test = record.get("last_test") or {}
            reason = None
            if oauth and oauth.get("renewal_state") != "ready":
                reason = oauth.get("recovery_action") or "Reconnect this account before using it."
            elif oauth.get("expires_at") and float(oauth["expires_at"]) <= time.time() and not oauth.get("available"):
                reason = "This authorization expired. Reconnect the account before using it."
            elif test.get("revision") == record.get("revision") and test.get("outcome") in {"sign_in_refused", "blocked_by_policy", "unreachable", "address_missing"}:
                reason = "The current connection check failed. Review its result in Connections."
            if reason:
                items.append({"name": record["name"], "title": record["name"], "revision": record["revision"],
                              "reason": reason, "href": "/connections"})
        return {"items": items, "coverage": "current"}
    except (connections.ConnectionStoreUnavailable, ValueError, TypeError):
        return {"items": [], "coverage": "unavailable"}
