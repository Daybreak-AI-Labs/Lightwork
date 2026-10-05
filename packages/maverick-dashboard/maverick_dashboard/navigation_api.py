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

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from maverick import experience_preferences as preferences
from pydantic import BaseModel, Field

from . import auth

router = APIRouter()
_PRIVATE = {"Cache-Control": "private, no-store"}
# The go-to keys (G, then a letter). The shortcuts dialog lists them -- and its
# script binds exactly what it lists -- and the command menu shows each one
# beside its page. Home is everyone's; each other key appears only for people
# who may open its page.
GO_TO_KEYS: tuple[tuple[str, str, str], ...] = (
    ("h", "/", "Go to Home, your start page"),
    ("t", "/goals", "Go to Tasks"),
    ("a", "/approvals", "Go to Approvals"),
    ("r", "/deliverables", "Go to Results"),
    ("s", "/settings", "Go to Settings"),
)
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


# -- The job-named navigation ("areas") ---------------------------------------
# ``[dashboard] navigation`` picks the sidebar for everyone: "classic" (the
# default, the registry groups) or "areas" (one entry per job, see
# ui_visibility.AREAS). ``[dashboard] navigation_personal`` lets each person
# choose their own in Your preferences. Nothing here changes who may open a
# page: areas are built from the page finder's destinations.
NAVIGATION_MODES = ("classic", "areas")


def navigation_settings() -> tuple[str, bool]:
    """The installation's navigation style and whether people may choose."""
    from maverick import config

    try:
        table = config.load_config().get("dashboard", {})
    except Exception:  # unreadable configuration: the classic sidebar
        return "classic", False
    mode = table.get("navigation")
    return (mode if mode in NAVIGATION_MODES else "classic"), table.get("navigation_personal") is True


def navigation_choice(request: Request) -> tuple[str, list[str]]:
    """This person's navigation style and their own order of sidebar areas
    ([] = their role's preset)."""
    mode, personal = navigation_settings()
    try:
        principal = auth.caller_principal(request)
        if not principal or not preferences.enabled():
            return mode, []
        values = _state(request, principal)["values"]
    except Exception:  # unreadable preferences: the installation's choice
        return mode, []
    chosen = values.get("navigation", "")
    if personal and chosen in NAVIGATION_MODES:
        mode = chosen
    layout = values.get("navigation_areas") or []
    return mode, list(layout) if isinstance(layout, list) else []


def area_context(request: Request, role, finder_pages, off_pages, can_turn_on) -> dict:
    """The template values for the navigation style: ``nav_mode`` always;
    in areas mode also ``nav_areas`` (the areas, and where this page sits)."""
    try:
        mode, layout = navigation_choice(request)
    except Exception:  # shell rendering stays on the classic sidebar
        mode, layout = "classic", []
    if mode != "areas":
        return {"nav_mode": "classic", "nav_areas": None}
    from .ui_visibility import area_nav, page_for, place

    nav = area_nav(finder_pages, role, off=off_pages, can_turn_on=can_turn_on, layout=layout)
    shown = {area["title"] for area in nav["sidebar"]}
    nav["hidden"] = [area for area in nav["by_title"].values() if area["sidebar"] and area["title"] not in shown]
    nav["here"] = None
    for area in nav["by_title"].values():  # link straight to an address's new home
        area["href"] = redirect_target(area["href"]) or area["href"]
        for section in area["sections"]:
            section["href"] = redirect_target(section["href"]) or section["href"]
            for link in section["links"]:
                link["href"] = redirect_target(link["href"]) or link["href"]
    page = page_for("/today" if request.url.path == "/home" else request.url.path)
    spot = place(page["path"]) if page else None
    area = nav["by_title"].get(spot[0]) if spot else None
    if area is not None:
        section = next((item for item in area["sections"] if item["title"] == spot[1]), None)
        nav["here"] = {"area": area, "section": section, "path": redirect_target(page["path"]) or page["path"],
                       "label": page["label"], "hub": page["path"] == area["hub"]}
    # Ctrl+K shows each page as "Area › Section".
    for group in finder_pages:
        for link in group["links"]:
            spot = place(link["href"])
            if spot is not None:
                link["area"], link["section"] = spot
    return {"nav_mode": "areas", "nav_areas": nav}


# Old addresses that move in areas mode (report section 5): (old, new, ready,
# why it waits). In classic mode none fires. A redirect fires in areas mode
# only once its new address holds the old page's job; until the merge it
# depends on is built, sending people there would hide the old page's
# content, so it waits. Two more need a decision first and are not listed:
# /goal-builder (compose-form field parity) and /flows/designer (product
# decision). A held redirect also needs areas_redirect() called in its old
# page's handler when it is switched on.
REDIRECTS: tuple[tuple[str, str, bool, str], ...] = (
    ("/today", "/home", True, ""),
    ("/me", "/home", False, "Home does not yet show My work's questions, spending and chat accounts."),
    ("/overview", "/home", False, "Home does not yet show the Overview's activity and statistics."),
    ("/action-center", "/approvals?view=all", False, "Approvals does not yet hold the Action Center's other kinds."),
    ("/my-work", "/goals?owner=me&include=background", False, "Tasks does not yet list background work."),
    ("/plan-tree-3d", "/graph-editor?view=3d", False, "The task map has no 3D view yet."),
    ("/tasks/{id}/diagnostics", "/goals/{id}/diagnostics", True, ""),
    ("/project-workspaces", "/projects", False, "Projects does not yet hold project workspaces."),
    ("/project-workspaces/{id}", "/projects/{id}?tab=plan", False, "A project has no Plan tab yet."),
    ("/run-tree", "/replay?tab=branches", False, "Task replay has no Branches tab yet."),
    ("/walkthroughs", "/replay?tab=video", False, "Task replay has no Video tab yet."),
    ("/trust", "/external-agents?tab=access", False, "Outside agents has no Access tab yet."),
    ("/discovery", "/permissions?tab=inventory", False, "Permissions has no Inventory tab yet."),
    ("/autonomous-compliance", "/compliance?tab=overview", False, "Compliance has no Overview tab yet."),
    ("/cortex", "/knowledge-access", False, "Retire only after checking whether anyone uses its diagnostics."),
)


def redirect_target(path: str) -> str | None:
    """The new address for ``path`` among the ready redirects, or None."""
    parts = path.rstrip("/").split("/") if path != "/" else [""]
    for old, new, ready, _why in REDIRECTS:
        pattern = old.split("/")
        if not ready or len(pattern) != len(parts):
            continue
        values = {}
        for want, got in zip(pattern, parts, strict=True):
            if want == "{id}" and got.isdigit():
                values["id"] = got
            elif want != got:
                break
        else:
            return new.replace("{id}", values.get("id", ""))
    return None


def areas_on(request: Request) -> bool:
    try:
        return navigation_choice(request)[0] == "areas"
    except Exception:  # unreadable choice: the classic addresses
        return False


def areas_redirect(request: Request) -> RedirectResponse | None:
    """In areas mode, the redirect from an old address to its new one; in
    classic mode (the default) always None, so old addresses stay as they are."""
    target = redirect_target(request.url.path)
    if target is None or not areas_on(request):
        return None
    return RedirectResponse(target, status_code=303)


def preset_start(request: Request) -> str | None:
    """In areas mode, the start page of the person's role preset, when they
    have not chosen their own and may open it; otherwise None."""
    context = _app()._nav_context(request)
    nav = context.get("nav_areas")
    if context.get("nav_mode") != "areas" or not nav:
        return None
    # Home serves the Today page, so whoever may open Today may open Home.
    probe = "/today" if nav["start"] == "/home" else nav["start"]
    return nav["start"] if context["can_open_page"](probe) else None


def _arranged(layout: list[str], op: str, area: str) -> list[str]:
    if op == "reset":
        return []
    if op == "hide":
        return [title for title in layout if title != area]
    if op == "show":
        return layout if area in layout else [*layout, area]
    return _moved(layout, op, area)


@router.post("/navigation/areas", include_in_schema=False)
def arrange_areas(
    request: Request,
    op: Literal["up", "down", "hide", "show", "reset"] = Form(...),
    area: str = Form("", max_length=40),
    context: str = Form(..., max_length=128),
    back: str = Form("/", max_length=2048),
) -> RedirectResponse:
    """Move, hide or show one sidebar area for this person (or go back to
    their role's layout), then return to the page they were on. Only the
    order and visibility of entries change; floors still decide every page."""
    from .preferences_api import _principal

    app = _app()
    app._require_same_origin(request)
    principal = _principal(request)
    if not _check_context(request, context):
        raise HTTPException(409, "The account or workspace changed. Reload the page before arranging areas.")
    nav = app._nav_context(request).get("nav_areas")
    if not nav:
        raise HTTPException(409, "Areas navigation is not turned on for your account.")
    offered = [item["title"] for item in nav["sidebar"]]
    if op != "reset" and area not in {*offered, *(item["title"] for item in nav["hidden"])}:
        raise HTTPException(403, "Only areas you can open can be arranged.")
    if op == "hide" and offered == [area]:
        raise HTTPException(422, "Keep at least one area in the sidebar.")
    for attempt in (1, 2):
        state = preferences.state(principal)
        try:
            preferences.save(principal, scope="personal",
                             changes={"navigation_areas": _arranged(offered, op, area)},
                             expected_revision=state["revision"])
            break
        except preferences.PreferenceConflict as exc:
            if attempt == 2:
                raise HTTPException(409, "Your areas changed in another window. Reload and try again.") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    target = back if back.startswith("/") and not back.startswith("//") and "\\" not in back else "/"
    return RedirectResponse(target, status_code=303)
