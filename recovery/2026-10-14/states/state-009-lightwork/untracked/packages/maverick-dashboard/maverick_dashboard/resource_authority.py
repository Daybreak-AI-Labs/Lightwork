"""Shared production resource-ownership authority for dashboard operations.

Route handlers and the exact endpoint-policy resolver use the same helpers so
the machine-readable ``ownership`` field is executable policy rather than a
descriptive string.  Resource denials deliberately return 404 to avoid
cross-owner existence probes.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException, Request, WebSocket

from .auth import (
    assert_goal_access,
    caller_principal,
    goal_owner_filter,
    is_dashboard_admin,
    require_global_permission,
)

Connection = Request | WebSocket


def can_access_flow(connection: Connection, flow: Any) -> bool:
    """Whether the authenticated caller may read or mutate ``flow``."""
    principal = caller_principal(connection)  # type: ignore[arg-type]
    if principal is None or is_dashboard_admin(principal):
        return True
    return getattr(flow, "owner", "") == principal


def assert_flow_access(connection: Connection, flow: Any) -> None:
    """Hide a flow owned by another principal behind the ordinary 404."""
    if not can_access_flow(connection, flow):
        raise HTTPException(status_code=404, detail="no such flow")


def load_owned_run(connection: Connection, run_id: str):
    """Load one principal-owned flow run, or return the non-enumerating 404."""
    from maverick.flow import store

    run = store.load_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no such run")
    owner = goal_owner_filter(connection)  # type: ignore[arg-type]
    if owner is not None and run.owner != owner:
        raise HTTPException(status_code=404, detail="no such run")
    return run


def _principal(connection: Connection) -> str:
    principal = str(caller_principal(connection) or "").strip()  # type: ignore[arg-type]
    if not principal:
        raise HTTPException(status_code=401, detail="authenticated identity required")
    return principal


def _request_method(connection: Connection) -> str:
    if connection.scope.get("type") == "websocket":
        return "WEBSOCKET"
    return str(getattr(connection, "method", "")).upper()


def _path_parameter(connection: Connection, name: str) -> str:
    value = str((connection.path_params or {}).get(name) or "").strip()
    if not value:
        raise HTTPException(status_code=503, detail="resource policy is unavailable")
    return value


def _read_only(connection: Connection) -> None:
    if _request_method(connection) not in {"GET", "HEAD"}:
        raise HTTPException(status_code=503, detail="resource policy is unavailable")


def _goal_owner(connection: Connection) -> None:
    raw = _path_parameter(connection, "goal_id")
    try:
        goal_id = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="no such goal") from exc
    from ._shared import _world

    goal = _world().get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="no such goal")
    assert_goal_access(connection, goal)  # type: ignore[arg-type]


def _flow_owner(connection: Connection) -> None:
    from maverick.flow import store

    flow = store.load_flow(_path_parameter(connection, "flow_id"))
    if flow is None:
        raise HTTPException(status_code=404, detail="no such flow")
    assert_flow_access(connection, flow)


def _run_owner(connection: Connection) -> None:
    load_owned_run(connection, _path_parameter(connection, "run_id"))


def _global_admin(connection: Connection) -> None:
    require_global_permission(connection, "admin")  # type: ignore[arg-type]


def _attributable_principal(connection: Connection) -> None:
    _principal(connection)


def _owner_assigned(connection: Connection) -> None:
    _principal(connection)
    if _request_method(connection) in {"GET", "HEAD"}:
        raise HTTPException(status_code=503, detail="resource policy is unavailable")


def _owner_filtered(connection: Connection) -> None:
    _principal(connection)
    _read_only(connection)


def _principal_tool_acl(connection: Connection) -> None:
    _principal(connection)
    _read_only(connection)


def _signed_token_and_run_owner(connection: Connection) -> None:
    _principal(connection)
    _path_parameter(connection, "token")
    # Cryptographic token, paused-run generation, assignee, and run-owner
    # checks are performed together by app._load_paused_approval_run and
    # app._approval_link_actor after the token selects its signed tenant.


def _service_credential(connection: Connection) -> None:
    _read_only(connection)


def _oidc_transaction(connection: Connection) -> None:
    _read_only(connection)


OWNERSHIP_GUARDS: dict[str, Callable[[Connection], None]] = {
    "deployment_auditor": _attributable_principal,
    "flow_owner_check": _flow_owner,
    "global_admin": _global_admin,
    "global_control_plane": _attributable_principal,
    "global_operator_queue": _attributable_principal,
    "goal_owner_check": _goal_owner,
    "oidc_transaction": _oidc_transaction,
    "owner_assigned": _owner_assigned,
    "owner_filtered": _owner_filtered,
    "principal": _attributable_principal,
    "principal_author": _attributable_principal,
    "principal_tool_acl": _principal_tool_acl,
    "read_only": _read_only,
    "run_owner_check": _run_owner,
    "service_credential": _service_credential,
    "signed_token_and_run_owner": _signed_token_and_run_owner,
}
SUPPORTED_OWNERSHIP_GUARDS = frozenset(OWNERSHIP_GUARDS)


def enforce_ownership_guard(connection: Connection, guard_id: str) -> None:
    """Execute one reviewed ownership resolver or fail closed on policy drift."""
    guard = OWNERSHIP_GUARDS.get(str(guard_id or ""))
    if guard is None:
        raise HTTPException(status_code=503, detail="resource policy is unavailable")
    try:
        guard(connection)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="resource policy is unavailable",
        ) from exc


__all__ = [
    "OWNERSHIP_GUARDS",
    "SUPPORTED_OWNERSHIP_GUARDS",
    "assert_flow_access",
    "can_access_flow",
    "enforce_ownership_guard",
    "load_owned_run",
]
