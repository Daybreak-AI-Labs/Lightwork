"""Safe account preferences with explicit scope, inheritance, and review versions."""
from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from maverick import experience_preferences as preferences
from pydantic import BaseModel, Field

from . import auth
from .draft_api import context_for

router = APIRouter()


def _app():
    from . import app
    return app


def _principal(request: Request) -> str:
    auth.require_permission(request, "view")
    principal = auth.caller_principal(request)
    if not principal:
        raise HTTPException(403, "Sign in to save preferences to your account. Display preferences can still be saved on this device.")
    if not preferences.enabled():
        raise HTTPException(403, "Personal preferences are turned off in Settings.")
    return principal


def _destinations(request: Request) -> dict[str, str]:
    groups = _app()._nav_context(request)["finder_pages"]
    return {link["href"]: link["label"] for group in groups for link in group["links"]}


def _thread(request: Request, thread: int | None) -> int | None:
    if thread is not None:
        goal = _app()._world().get_goal(thread)
        if goal is None or goal.owner != auth.caller_principal(request):
            raise HTTPException(403, "Choose a conversation that belongs to your account.")
        from maverick.chat_threads import ChatThreads
        binding = ChatThreads(_app()._world()).binding(thread)
        if binding is not None:
            return binding.root_id
    return thread


def _state(request: Request, thread: int | None = None) -> dict:
    principal = _principal(request)
    state = preferences.state(principal, thread=_thread(request, thread))
    destinations = _destinations(request)
    state["values"]["favorites"] = [item for item in state["values"]["favorites"] if item in destinations]
    if state["values"]["start_page"] not in destinations:
        state["values"]["start_page"] = "/chat"
    return {**state, "context": context_for(request), "destinations": destinations,
            "scopes": ["personal"] + (["workspace"] if auth.has_permission(request, "admin") else []) +
                      (["organization"] if auth.has_global_permission(request, "admin") else [])}


@router.get('/preferences')
def preferences_page(request: Request, thread: int | None = None):
    from maverick.persona import load_persona, style_choices
    from maverick.styles import BUILTIN_STYLES

    state = _state(request, thread)
    return _app().templates.TemplateResponse(request, 'preferences.html', {
        "preference_state": state, "preference_thread": thread, "tones": style_choices(),
        "output_styles": BUILTIN_STYLES, "legacy_name": load_persona().get("user_name", "") if auth.has_global_permission(request, "admin") else "",
    }, headers={"Cache-Control": "private, no-store"})


class PreferenceUpdate(BaseModel):
    expected_revision: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    context: str
    scope: str = "personal"
    thread: int | None = None
    changes: dict[str, Any]


def _check_display(key: str, value: str) -> None:
    from .i18n import LANGS

    allowed = {"theme": {*_app()._valid_theme_names(), "system"},
               "font": _app()._VALID_FONTS, "density": _app()._VALID_DENSITIES,
               "language": set(LANGS), "text_size": {"standard", "large", "larger"}}
    if key not in allowed or not isinstance(value, str) or value not in allowed[key]:
        raise HTTPException(422, "Choose an available display preference.")


@router.post('/api/v1/preferences')
def update_preferences(request: Request, body: PreferenceUpdate) -> dict:
    _app()._require_same_origin(request)
    principal = _principal(request)
    if body.thread is not None and body.scope != "personal":
        raise HTTPException(422, "Conversation styles belong to one person; choose Personal scope.")
    if not hmac.compare_digest(body.context, context_for(request)):
        raise HTTPException(409, "The account or workspace changed. Reload before saving preferences.")
    if body.scope == "organization":
        auth.require_global_permission(request, "admin")
    elif body.scope == "workspace":
        auth.require_permission(request, "admin")
    elif body.scope != "personal":
        raise HTTPException(422, "Choose Personal, Workspace, or Organization.")
    destinations = _destinations(request)
    for key, value in body.changes.items():
        if value is None:
            continue
        if key in {"theme", "font", "density", "language", "text_size"}:
            _check_display(key, value)
        if key == "start_page" and (not isinstance(value, str) or value not in destinations):
            raise HTTPException(403, "That starting page is not available to your account.")
        if key == "favorites" and (not isinstance(value, list) or any(not isinstance(item, str) or item not in destinations for item in value)):
            raise HTTPException(403, "Only currently available pages can be pinned.")
        if key == "today_cards" and (not isinstance(value, list) or any(not isinstance(item, str) or item not in {"tasks", "decisions", "recent"} for item in value)):
            raise HTTPException(422, "Choose an available Today card.")
    try:
        preferences.save(principal, scope=body.scope, changes=body.changes,
                         expected_revision=body.expected_revision, thread=_thread(request, body.thread))
    except preferences.PreferenceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _state(request, body.thread)


class DisplayUpdate(BaseModel):
    key: str
    value: str
    context: str
    expected_revision: str | None = None
    device_only: bool = False


@router.post('/api/v1/preferences/display')
def display_preference(request: Request, body: DisplayUpdate) -> JSONResponse:
    _app()._require_same_origin(request)
    auth.require_permission(request, "view")
    _check_display(body.key, body.value)
    context = context_for(request)
    if not hmac.compare_digest(body.context, context):
        raise HTTPException(409, "Your account or workspace changed. Reload before changing preferences.")
    principal = auth.caller_principal(request)
    if principal and preferences.enabled() and body.key in preferences.state(principal)["locked"]:
        raise HTTPException(403, "This display preference is set by organization policy.")
    result = {"value": body.value}
    if principal and not body.device_only:
        if not body.expected_revision:
            raise HTTPException(409, "Refresh your preferences before saving.")
        try:
            result.update(preferences.save(principal, scope="personal", changes={body.key: body.value},
                                           expected_revision=body.expected_revision))
        except preferences.PreferenceConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    response = JSONResponse(result, headers={"Cache-Control": "private, no-store"})
    cookie = "mvk_lang" if body.key == "language" else "mvk_" + body.key
    if body.device_only or not principal:
        response.set_cookie(cookie, body.value, max_age=365*24*3600, samesite="lax")
        response.set_cookie('mvk_pref_context', context, max_age=365*24*3600, samesite="lax")
    else:
        response.delete_cookie(cookie)
    return response


def display_context(request: Request) -> dict:
    principal = auth.caller_principal(request)
    state = None
    if principal and preferences.enabled():
        try:
            state = preferences.state(principal)
        except Exception:
            state = None
    return {"display_preference_context": context_for(request),
            "display_preference_revision": state["revision"] if state else "",
            "display_text_size": resolved_display(request, "text_size", "standard"),
            "display_signed_in": bool(principal and preferences.enabled())}


def resolved_display(request: Request, key: str, fallback: str | None = None):
    principal = auth.caller_principal(request)
    cookie = "mvk_lang" if key == "language" else "mvk_" + key
    cached = None
    if principal and preferences.enabled():
        try:
            cached = getattr(request.state, "experience_preferences", None)
            if cached is None:
                cached = preferences.state(principal)
                request.state.experience_preferences = cached
            if key in cached["locked"]:
                return cached["values"].get(key, fallback)
        except Exception:
            return fallback
    if request.cookies.get('mvk_pref_context') == context_for(request) and request.cookies.get(cookie):
        return request.cookies[cookie]
    if cached is not None:
        return cached["values"].get(key, fallback)
    if not principal and not request.cookies.get('mvk_pref_context'):
        return request.cookies.get(cookie, fallback)
    return fallback


@router.get('/today')
def today_page(request: Request):
    from datetime import datetime, timezone

    principal = _principal(request)
    model = _state(request)
    world = _app()._world()
    goals = world.list_goals(owner=principal, limit=100)
    active = [goal for goal in goals if goal.status in {'pending', 'active', 'running', 'blocked'}]
    recent = sorted([goal for goal in goals if goal.status in {'done', 'completed'}],
                    key=lambda goal: goal.updated_at, reverse=True)[:10]
    return _app().templates.TemplateResponse(request, 'today.html', {
        'today_preferences': model, 'today_active': active[:20], 'today_recent': recent,
        'today_freshness': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
        'today_decisions_allowed': auth.has_permission(request, 'operate'),
    }, headers={'Cache-Control': 'private, no-store'})
