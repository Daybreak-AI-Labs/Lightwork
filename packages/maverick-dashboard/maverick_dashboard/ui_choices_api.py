"""Read-only lists that fill selectors on the rebuilt workflow pages.

A person picks an owner or reviewer from a list instead of typing a sign-in
subject. The list is the current workspace's members (or, with no workspace
selected, the installation's assigned users), labelled with the same identity formatting the Users page
uses. Reading it needs ``operate`` (the floor of every form that assigns an
owner); it never changes anything.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .auth import require_permission

router = APIRouter(prefix="/api/v1/choices", tags=["ui-choices"])

_LIMIT = 500


def people_choices() -> list[dict]:
    """[{value, label}] for the active workspace, sorted by label."""
    from maverick.paths import current_tenant_id

    from . import rbac
    from .identity_format import humanize_principal

    principals: set[str] = set()
    tenant = current_tenant_id() or ""
    if tenant:
        # A workspace lists only its own members, never other workspaces'.
        principals.update(rbac.list_tenant_roles(tenant))
    else:
        principals.update(rbac.list_users())
    rows = []
    for principal in principals:
        if not principal or not principal.startswith("user:"):
            continue
        ident = humanize_principal(principal)
        label = ident.name if not ident.detail or ident.detail == "Email" else f"{ident.name} ({ident.detail})"
        rows.append({"value": principal, "label": label})
    rows.sort(key=lambda r: r["label"].lower())
    return rows[:_LIMIT]


@router.get("/people")
async def list_people(request: Request) -> dict:
    require_permission(request, "operate")
    try:
        people = await run_in_threadpool(people_choices)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="The list of people is not available right now.") from exc
    return {"people": people}
