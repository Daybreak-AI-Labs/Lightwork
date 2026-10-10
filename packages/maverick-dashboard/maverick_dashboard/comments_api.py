"""Comment threads and @mentions on tasks, runs, approvals and policies (NOT-4).

Who may read a thread is exactly who may open its object: a task or run's
owner (and anyone holding a reviewed content-read grant for that task), the
people who can open an approval card, and the people who manage a policy.
The mention list offers only those people, the server checks every mention
against the same rule, and a mention is withdrawn from the notification inbox
if the person can no longer open the object.
"""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from . import auth

router = APIRouter()
log = logging.getLogger(__name__)

#: The policy pages that carry a thread: (title, page, permission to read).
POLICIES = {
    "tools": ("Tool permissions", "/permissions", "operate"),
    "spending-limits": ("Spending limits", "/spending-limits", "admin"),
    "data-use": ("Data use rules", "/data-use", "operate"),
}
_TASK_PERMISSION = "view"


def _comments():
    from maverick import comments

    from .feature_gate import feature_off

    if not comments.enabled():
        raise feature_off("collaboration.comments", "Comments")
    return comments


def _world():
    from ._shared import _world as world

    return world()


def _goal_readers(goal) -> set[str]:
    """A task's owner plus anyone holding a current reviewed read grant for it."""
    from . import content_access

    owner = getattr(goal, "owner", "") or ""
    readers = {owner} if owner else set()
    if not content_access.enabled():
        return readers
    try:
        for grant in content_access.STORE.list():
            requester = grant.get("requester")
            if isinstance(requester, str) and requester and content_access._grant_current(
                    grant, owner=owner, resource_kind="goal", resource_id=str(goal.id)):
                readers.add(requester)
    except Exception:  # noqa: BLE001 -- an unreadable grant store opens nothing more
        return {owner} if owner else set()
    return readers


def _signed_in_readers(readers: set[str]) -> set[str]:
    """Readers who can still sign in: the company directory (SCIM) has not
    turned them off. An unreadable directory offers nobody."""
    from . import people_directory

    try:
        return people_directory.active_only(readers)
    except Exception:  # noqa: BLE001 -- an unreadable directory offers nobody
        log.warning("comment readers unavailable: the company directory could not be read")
        return set()


def _object(request: Request | None, kind: str, object_id: str, *, principal: str = "") -> dict:
    """The object a thread is about, after checking the reader may open it.

    With a request, the caller's own access is checked (404 or 403 as the
    object's page would answer). Without one, ``principal`` is checked against
    the same readers, for a mention shown later in a notification.
    """
    from . import people_directory

    world = _world()
    if kind == "task":
        goal = world.get_goal(int(object_id)) if object_id.isdigit() else None
        if goal is None:
            raise HTTPException(404, "That task no longer exists.")
        if request is not None:
            auth.require_permission(request, _TASK_PERMISSION)
            auth.assert_goal_access(request, goal)
        readers = _signed_in_readers(_goal_readers(goal))
        return {"label": f"Task: {str(goal.title)[:120]}", "href": f"/chat/goal/{goal.id}", "readers": readers}
    if kind == "run":
        from maverick.flow import store

        if request is not None:
            auth.require_permission(request, "operate")
            from .api import _load_owned_run

            run = _load_owned_run(request, object_id)
        else:
            run = store.load_run(object_id)
        if run is None:
            raise HTTPException(404, "That run no longer exists.")
        owner = getattr(run, "owner", "") or ""
        flow_id = getattr(run, "flow_id", "") or "flow"
        return {"label": "Workflow run", "href": f"/flows/{flow_id}/runs/{object_id}",
                "readers": _signed_in_readers({owner} if owner else set())}
    if kind == "approval":
        from . import content_access

        if not object_id.isdigit():
            raise HTTPException(404, "No such approval.")
        if request is not None:
            auth.require_permission(request, "operate")
            approval = content_access.require_approval_content(request, object_id, world)
        else:
            approval = world.get_approval(int(object_id))
            if approval is None:
                raise HTTPException(404, "No such approval.")
        readers = people_directory.allowed(content_access.approval_readers(approval, world), "operate")
        return {"label": f"Request #{approval.id}", "href": f"/approvals#approval-{approval.id}",
                "readers": readers}
    if kind == "policy":
        if object_id not in POLICIES:
            raise HTTPException(404, "That policy has no comments.")
        title, href, permission = POLICIES[object_id]
        if request is not None:
            auth.require_permission(request, permission)
        readers = {p["principal"] for p in people_directory.candidates(permission)}
        if principal and not people_directory.may(principal, permission):
            readers.discard(principal)
        return {"label": f"Policy: {title}", "href": href, "readers": readers}
    raise HTTPException(404, "Comments work on tasks, runs, approvals and policies.")


def _people(readers: set[str], viewer: str) -> list[dict]:
    from .identity_format import humanize_principal

    out = []
    for principal in sorted(readers - {viewer}):
        ident = humanize_principal(principal)
        out.append({"principal": principal,
                    "label": f"{ident.name} ({ident.detail})" if ident.detail and ident.detail != "Email" else ident.name})
    return sorted(out, key=lambda person: person["label"].lower())


def _view(row: dict, viewer: str) -> dict:
    from .identity_format import humanize_principal

    return {"id": row["id"], "author": humanize_principal(row["author"]).name,
            "mine": row["author"] == viewer, "body": row["body"], "created_at": row["created_at"],
            "mentions": [humanize_principal(p).name for p in row["mentions"]]}


def _thread(request: Request, kind: str, object_id: str) -> dict:
    store = _comments()
    viewer = auth.caller_principal(request) or ""
    target = _object(request, kind, object_id)
    rows = store.list_for(kind, object_id)
    return {"comments": [_view(row, viewer) for row in rows],
            "people": _people(target["readers"], viewer) if viewer else [],
            "can_comment": viewer.startswith("user:"),
            "label": target["label"]}


@router.get("/api/v1/comments")
async def thread(request: Request, kind: str = Query(..., max_length=16),
                 id: str = Query(..., min_length=1, max_length=128)):  # noqa: A002 -- the query name people see
    return await run_in_threadpool(_thread, request, kind, id)


class CommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=16)
    id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=4000)
    mentions: list[str] = Field(default_factory=list, max_length=20)


def _post(request: Request, body: CommentIn) -> dict:
    store = _comments()
    author = auth.caller_principal(request) or ""
    if not author.startswith("user:"):
        raise HTTPException(403, "Sign in with your own account to comment.")
    target = _object(request, body.kind, body.id)
    outside = [m for m in body.mentions if m not in target["readers"] or m == author]
    if outside:
        raise HTTPException(422, "You can mention only people who can open this item. Choose someone from the list.")
    try:
        row = store.add(body.kind, body.id, author=author, body=body.body, mentions=body.mentions)
    except store.CommentError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**_thread(request, body.kind, body.id), "posted": _view(row, author)}


@router.post("/api/v1/comments", status_code=201)
async def post(request: Request, body: CommentIn):
    return await run_in_threadpool(_post, request, body)


def _remove(request: Request, comment_id: int) -> dict:
    store = _comments()
    viewer = auth.caller_principal(request) or ""
    row = store.get(comment_id)
    if row is None:
        raise HTTPException(404, "That comment no longer exists.")
    _object(request, row["kind"], row["object_id"])
    if row["author"] != viewer or not store.remove(comment_id, by=viewer):
        raise HTTPException(403, "Only the person who wrote a comment can delete it.")
    return _thread(request, row["kind"], row["object_id"])


@router.delete("/api/v1/comments/{comment_id}")
async def remove(request: Request, comment_id: int):
    return await run_in_threadpool(_remove, request, comment_id)


def mention_sources(actor: str, *, since: float | None = None) -> list[dict]:
    """Personal notification sources for comments that mention ``actor``.

    Each mention is checked again now: someone who can no longer open the
    item does not see the mention (or its author's words, which stay in the
    app; the alert carries only who mentioned you and where)."""
    from maverick import comments

    from .identity_format import humanize_principal

    if not comments.enabled():
        return []
    start = time.time() - 7 * 86400 if since is None else since
    out = []
    for row in comments.mentions_for(actor, since=start):
        try:
            target = _object(None, row["kind"], row["object_id"], principal=actor)
        except HTTPException:
            continue
        if actor not in target["readers"]:
            continue
        who = humanize_principal(row["author"]).name
        out.append({"ref": f"comment:{row['id']}", "version": "1", "category": "mention",
                    "title": target["label"], "label": "Mentioned you",
                    # The approval card itself is the place to read its thread.
                    "href": target["href"] if row["kind"] == "approval"
                    else f"{target['href']}#comment-{row['id']}",
                    "goal_id": int(row["object_id"]) if row["kind"] == "task" else None,
                    "project_id": None, "occurred_at": row["created_at"],
                    "reason": f"{who} mentioned you in a comment",
                    "action": "Read the comment and reply", "no_due": "Nothing is due"})
    return out
