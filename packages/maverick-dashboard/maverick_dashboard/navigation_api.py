"""Pinned and recent pages in the sidebar, and people results for the command menu.

Pins are the person's ``favorites`` preference -- the one list Today and Your
preferences also show. Someone who has never chosen pins starts from their
role's preset (``ui_visibility.pin_preset``); their first change saves their
own list. Recent pages are the last pages the person opened in this workspace
(``experience_preferences.recent_pages``). Every read filters both to the pages
the person may open right now -- the page finder's destinations, the same
role-filtered registry as the sidebar -- so a page someone can no longer open is
never offered. Nothing here grants access: a pin is only a link.
"""
from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from maverick import experience_preferences as preferences
from pydantic import BaseModel, Field

from . import auth

router = APIRouter()
_PRIVATE = {"Cache-Control": "private, no-store"}
# People results per request; the Users page itself shows everyone.
PEOPLE_LIMIT = 20


def _app():
    from . import app
    return app


def _links(finder_pages) -> dict[str, dict]:
    return {link["href"]: link for group in finder_pages for link in group["links"]}


def pin_hrefs(state: dict, allowed, role: str | None) -> tuple[list[str], bool]:
    """The person's pins among ``allowed`` pages, and whether they are still
    the role's starting set (the person has never chosen their own)."""
    if "favorites" in state.get("overrides", {}).get("personal", {}):
        return [href for href in state["values"]["favorites"] if href in allowed], False
    from .ui_visibility import pin_preset

    return pin_preset(role, allowed), True


def _state(request: Request, principal: str) -> dict:
    cached = getattr(request.state, "experience_preferences", None)
    if cached is None:
        cached = preferences.state(principal)
        request.state.experience_preferences = cached
    return cached


def _personal_view(request: Request, role, finder_pages, off_pages, can_turn_on) -> dict | None:
    principal = auth.caller_principal(request)
    if not principal:
        return None
    try:
        if not preferences.enabled():
            return None
        state = _state(request, principal)
        recent = preferences.recent_pages(principal)
    except Exception:  # unreadable preferences: the sidebar keeps its shared lists
        return None
    # Like the sidebar groups: someone who cannot turn a module on gets no link
    # into its "...is turned off" page; an administrator sees it marked Off.
    offered = {href: {**link, "off": href in off_pages} for href, link in _links(finder_pages).items()
               if can_turn_on or href not in off_pages}
    pins, preset = pin_hrefs(state, offered, role)
    path = request.url.path
    current = path if path in offered else ""
    return {
        "pins": [offered[href] for href in pins],
        "preset": preset,
        "recent": [offered[href] for href in recent if href in offered],
        "current": current,
        "current_label": offered[current]["label"] if current else "",
        "current_pinned": current in pins,
        # The page asks to be remembered only when it is not already the latest.
        "record": bool(current) and recent[:1] != [current],
    }


def personal_view(request: Request, role, finder_pages, off_pages=frozenset(), can_turn_on=False):
    """A template helper returning the sidebar's personal lists for this
    request (computed once, on first use), or None: no account, personal
    preferences off, or preferences unreadable."""
    memo: list = []

    def view() -> dict | None:
        if not memo:
            memo.append(_personal_view(request, role, finder_pages, off_pages, can_turn_on))
        return memo[0]

    return view


def _invites_on() -> bool:
    try:
        from .invites import invites_enabled

        return invites_enabled()
    except Exception:  # unreadable configuration: no invite command
        return False


def commands(*, can_create_goal: bool, can_halt: bool, can_open_page, global_admin: bool, personal) -> list[str]:
    """The command menu's commands this person may use, in menu order.

    Each one opens the same control, form or confirmation the page itself
    uses: none decides, stops work or saves anything from the menu alone.
    """
    available = [
        ("new-task", can_create_goal),
        # Decided on the item's own card, with the page's confirmation.
        ("approvals", can_open_page("/approvals")),
        # Opens the emergency stop's own confirmation.
        ("halt", can_halt),
        ("invite", global_admin and can_open_page("/users") and _invites_on()),
        ("setting", True),
        ("pin", bool(personal() and personal()["current"])),
        ("shortcuts", True),
    ]
    return [name for name, offered in available if offered]


def command_list(**flags):
    """A template helper returning :func:`commands` (computed on first use)."""
    memo: list = []

    def view() -> list[str]:
        if not memo:
            try:
                memo.append(commands(**flags))
            except Exception:  # shell rendering stays fail-closed
                memo.append([])
        return memo[0]

    return view


def _check_context(request: Request, supplied: str) -> bool:
    from .draft_api import context_for

    try:
        return hmac.compare_digest(supplied, context_for(request))
    except HTTPException:
        return False


def _link_row(link: dict) -> dict:
    return {"href": link["href"], "label": link["label"], "icon": link.get("icon") or "dot"}


def _moved(pins: list[str], op: str, href: str) -> list[str]:
    pins = list(pins)
    if op == "add":
        return pins if href in pins else [*pins, href]
    if op == "remove":
        return [pin for pin in pins if pin != href]
    if href in pins:
        index = pins.index(href)
        other = index - 1 if op == "up" else index + 1
        if 0 <= other < len(pins):
            pins[index], pins[other] = pins[other], pins[index]
    return pins


class PinChange(BaseModel):
    op: Literal["add", "remove", "up", "down"]
    href: str = Field(..., max_length=180)
    context: str = Field(..., max_length=128)


@router.post("/api/v1/navigation/pins")
def change_pin(request: Request, body: PinChange) -> JSONResponse:
    """Pin, unpin or move one page in the person's Pinned pages."""
    from .preferences_api import _principal

    app = _app()
    app._require_same_origin(request)
    principal = _principal(request)
    if not _check_context(request, body.context):
        raise HTTPException(409, "The account or workspace changed. Reload the page before changing pins.")
    links = _links(app._nav_context(request)["finder_pages"])
    if body.href not in links:
        raise HTTPException(403, "Only pages you can open can be pinned.")
    role = app._page_role(request)
    for attempt in (1, 2):
        state = preferences.state(principal)
        pins, _preset = pin_hrefs(state, links, role)
        pins = _moved(pins, body.op, body.href)
        try:
            saved = preferences.save(principal, scope="personal", changes={"favorites": pins},
                                     expected_revision=state["revision"])
            break
        except preferences.PreferenceConflict as exc:
            if attempt == 2:
                raise HTTPException(409, "Your pins changed in another window. Reload and try again.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    return JSONResponse({"pins": [_link_row(links[href]) for href in pins], "pinned": body.href in pins,
                         "revision": saved["revision"]}, headers=_PRIVATE)


class PageVisit(BaseModel):
    path: str = Field(..., max_length=2048)
    context: str = Field(..., max_length=128)


@router.post("/api/v1/navigation/recent")
def remember_page(request: Request, body: PageVisit) -> JSONResponse:
    """Remember that the person opened a page, for their Recent pages.

    A page they may not open, a stale page (another account or workspace) or
    personal preferences turned off records nothing; the answer says so
    rather than failing, because nothing the person asked for depends on it.
    """
    app = _app()
    app._require_same_origin(request)
    nothing = JSONResponse({"recorded": False}, headers=_PRIVATE)
    principal = auth.caller_principal(request)
    if not principal or not preferences.enabled() or not _check_context(request, body.context):
        return nothing
    if body.path not in _links(app._nav_context(request)["finder_pages"]):
        return nothing
    try:
        preferences.record_visit(principal, body.path)
    except (OSError, RuntimeError, ValueError):
        return nothing
    return JSONResponse({"recorded": True}, headers=_PRIVATE)


def people_available(request: Request) -> bool:
    """Whether the person may see the people directory (the Users page)."""
    try:
        return auth.has_permission(request, "admin") and _app()._nav_context(request)["can_open_page"]("/users")
    except Exception:  # an unreadable policy offers nothing
        return False


@router.get("/nav/finder/people", include_in_schema=False)
def people_results(request: Request, q: str = Query("", max_length=200)) -> JSONResponse:
    """People for the command menu's ``@`` search: exactly the people the
    Users page lists for this person, matched by name, opening that page."""
    auth.require_permission(request, "admin")
    if not people_available(request):
        raise HTTPException(403, "The people directory is not available to your account.")
    from .admin_pages import _users_context
    from .identity_format import role_label

    terms = q.casefold().split()[:8]
    people = _users_context(request)["people"]
    rows = [{"label": person["label"], "role": role_label(person["role"]) if person.get("role") else "",
             "href": "/users"}
            for person in people if all(term in person["label"].casefold() for term in terms)]
    return JSONResponse({"people": rows[:PEOPLE_LIMIT], "total": len(rows)}, headers=_PRIVATE)
