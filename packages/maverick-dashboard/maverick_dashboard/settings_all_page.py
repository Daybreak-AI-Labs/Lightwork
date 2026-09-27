"""Settings > All settings: every catalogued product setting with a real control.

``GET /settings/all`` renders the page; ``GET /api/v1/settings/catalog`` lists
the current state and ``POST /api/v1/settings/catalog`` saves or resets one or
more settings in a single overlay write. Every route is global-admin only (the
overlay is deployment-wide), every write is same-origin checked, and each
changed setting lands one ``setting_changed`` audit row that never carries a
secret's value. Changes that lower a protection are refused unless the request
carries ``confirm: true`` -- the page asks the person first, and the server
enforces it independently of the page.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import auth
from . import settings_catalog as catalog

router = APIRouter()

PAGE_PATH = "/settings/all"
API_PATH = "/api/v1/settings/catalog"

SOURCE_TEXT = {
    "deployment": "Set by your server's deployment settings",
    "here": "Set here",
    "installer": "Set by your installer",
    "default": "Default",
}


def _app():
    from . import app as app_module

    return app_module


class _Refused(Exception):
    """A request the page explains in its own plain sentence."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- state
def _sources() -> dict[str, Any]:
    """The configuration layers the page reports, lowest precedence first."""
    from maverick import config

    from . import settings_store

    config.reset_config_cache()
    try:
        base = config._load_config_file(config.config_path())
    except Exception:  # an unreadable base file shows as "not set by installer"
        base = {}
    operator: dict = {}
    overlay_env = os.environ.get(config.CONFIG_OVERLAY_ENV, "").strip()
    if overlay_env:
        try:
            operator = config._load_config_file(Path(overlay_env).expanduser())
        except Exception:
            operator = {}
    return {
        "base": base if isinstance(base, dict) else {},
        "here": settings_store.load_overlay(),
        "operator": operator if isinstance(operator, dict) else {},
    }


def _env_locked(setting: catalog.Setting) -> bool:
    return any(os.environ.get(name, "").strip() for name in setting.env)


def _default_of(setting: catalog.Setting) -> object:
    default = setting.default
    return list(default) if isinstance(default, list) else default


def _resolve(setting: catalog.Setting, layers: dict[str, Any]) -> dict[str, Any]:
    """Effective value, where it comes from, and whether this page may change it."""
    if _env_locked(setting):
        return {"source": "deployment", "locked": True, "value": None, "known": False}
    present, value = catalog.lookup(layers["operator"], setting.path)
    if present:
        return {"source": "deployment", "locked": True, "value": value, "known": True}
    present, value = catalog.lookup(layers["here"], setting.path)
    if present:
        return {"source": "here", "locked": False, "value": value, "known": True}
    present, value = catalog.lookup(layers["base"], setting.path)
    if present:
        return {"source": "installer", "locked": False, "value": value, "known": True}
    return {"source": "default", "locked": False, "value": _default_of(setting), "known": True}


def _row(setting: catalog.Setting, layers: dict[str, Any]) -> dict[str, Any]:
    state = _resolve(setting, layers)
    value = state["value"]
    row: dict[str, Any] = {
        "id": setting.id,
        "label": setting.label,
        "help": setting.help,
        "control": setting.control,
        "group": setting.group,
        "choices": [{"value": v, "label": label} for v, label in setting.choices],
        "minimum": setting.minimum,
        "maximum": setting.maximum,
        "step": setting.step or (1 if setting.integer else "any"),
        "unit": setting.unit,
        "restart": setting.restart,
        "warning": setting.warning,
        "warning_lead": catalog.warning_lead(setting) if setting.warning else "",
        "source": state["source"],
        "source_text": SOURCE_TEXT[state["source"]],
        "locked": state["locked"],
        "default_text": catalog.display_value(setting, _default_of(setting)),
        "max_length": setting.max_length,
        "aliases": setting.aliases,
        "optional": setting.default is None,
    }
    if setting.secret:
        row["is_set"] = bool(value) if state["known"] else True
        row["value"] = None
        row["value_text"] = "Set" if row["is_set"] else "Not set"
    elif not state["known"]:
        row["value"] = None
        row["value_text"] = "Controlled by the server"
    else:
        row["value"] = _form_value(setting, value)
        row["value_text"] = catalog.display_value(setting, value)
    if setting.control == "rate":
        parsed = catalog.parse_rate(value) if state["known"] else None
        row["rate_count"] = parsed[0] if parsed else ""
        row["rate_seconds"] = parsed[1] if parsed else 60
        row["periods"] = [{"value": s, "label": f"Every {n}"} for s, n in catalog.RATE_PERIODS]
    return row


def _form_value(setting: catalog.Setting, value: object) -> object:
    """The value as the page's control shows it."""
    if setting.control == "select":
        return "" if value is None else str(value)
    if setting.control in {"multiselect", "list"}:
        return [str(v) for v in value] if isinstance(value, list) else []
    return value


def _effective(setting: catalog.Setting, layers: dict[str, Any]) -> object:
    state = _resolve(setting, layers)
    return state["value"] if state["known"] else None


def catalog_state() -> dict[str, Any]:
    layers = _sources()
    groups = []
    for name in catalog.GROUPS:
        rows = [_row(s, layers) for s in catalog.SETTINGS if s.group == name]
        groups.append({
            "name": name,
            "slug": _slug(name),
            "intro": catalog.GROUP_INTROS.get(name, ""),
            "settings": rows,
        })
    return {"groups": groups}


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-").replace("--", "-")


def _module_rows() -> list[dict[str, Any]]:
    """In-app module switches, with their effective on/off state."""
    from . import api

    try:
        loaded = api._load_module_config()
    except Exception:
        loaded = None
    rows = []
    for section, meta in sorted(api._MODULE_SWITCHES.items(), key=lambda kv: kv[1]["label"].lower()):
        enabled = (
            api._module_activation_state(loaded, section)["enabled"] is True
            if isinstance(loaded, dict) else None
        )
        rows.append({
            "section": section,
            "label": meta["label"],
            "enabled": enabled,
            "needs": [api._MODULE_SWITCHES[p]["label"] for p in meta["requires"]],
        })
    return rows


# ---------------------------------------------------------------- page
@router.get(PAGE_PATH, response_class=HTMLResponse)
def all_settings_page(request: Request) -> HTMLResponse:
    auth.require_global_permission(request, "admin")
    state = catalog_state()
    count = sum(len(g["settings"]) for g in state["groups"])
    changed = sum(
        1 for g in state["groups"] for s in g["settings"] if s["source"] == "here"
    )
    return _app().templates.TemplateResponse(request, "settings_all.html", {
        "groups": state["groups"],
        "modules": _module_rows(),
        "setting_count": count,
        "changed_count": changed,
    })


# ---------------------------------------------------------------- API
class SettingChange(BaseModel):
    id: str = Field(min_length=3, max_length=120)
    value: Any = None
    reset: bool = False


class CatalogUpdate(BaseModel):
    changes: list[SettingChange] = Field(min_length=1, max_length=len(catalog.SETTINGS))
    confirm: bool = False


@router.get(API_PATH)
async def catalog_list(request: Request) -> JSONResponse:
    auth.require_global_permission(request, "admin")
    state = await run_in_threadpool(catalog_state)
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


def _audit_value(setting: catalog.Setting, value: object) -> object:
    if setting.secret:
        return "set" if value else "not set"
    return value


def _plan(body: CatalogUpdate) -> tuple[dict, list[dict], list[dict], dict]:  # noqa: C901
    """Validate a request against the catalog; nothing is written here."""
    layers = _sources()
    writes: dict[tuple[str, ...], object] = {}
    changes: list[dict] = []
    weakening: list[dict] = []
    seen: set[str] = set()
    prospective = {s.id: _effective(s, layers) for s in catalog.SETTINGS}
    for item in body.changes:
        setting = catalog.BY_ID.get(item.id)
        if setting is None:
            raise _Refused(400, "That setting is not available here.")
        if item.id in seen:
            raise _Refused(400, "Each setting can be changed once per save.")
        seen.add(item.id)
        state = _resolve(setting, layers)
        if state["locked"]:
            raise _Refused(
                409,
                f"{setting.label} is set by your server's deployment settings, "
                "so it can't be changed here.",
            )
        old = state["value"]
        if item.reset:
            default = _default_of(setting)
            base_present, base_value = catalog.lookup(layers["base"], setting.path)
            if (not setting.secret and base_present and default is not None
                    and base_value != default):
                # The installer chose something else: write the default so the
                # setting really returns to it.
                writes[setting.path] = default
                new = default
            else:
                writes[setting.path] = None  # drop the value set here
                new = base_value if base_present else default
        else:
            try:
                new = catalog.coerce(setting, item.value)
            except catalog.SettingError as exc:
                raise _Refused(400, str(exc)) from exc
            if new is None:
                base_present, base_value = catalog.lookup(layers["base"], setting.path)
                writes[setting.path] = None
                new = base_value if base_present else _default_of(setting)
            else:
                writes[setting.path] = new
        unchanged = old == new and (not item.reset or state["source"] != "here")
        if unchanged and (item.reset or not setting.secret):
            writes.pop(setting.path, None)
            continue
        prospective[setting.id] = new
        changes.append({"setting": setting, "old": old, "new": new})
        if catalog.weakens(setting, old, new):
            weakening.append({"id": setting.id, "label": setting.label,
                              "warning": setting.warning})
    try:
        catalog.cross_checks(prospective)
    except catalog.SettingError as exc:
        raise _Refused(400, str(exc)) from exc
    return writes, changes, weakening, layers


def _record(changes: list[dict], actor: str, confirmed: bool) -> None:
    from maverick.audit import EventKind, audit_event

    for change in changes:
        setting: catalog.Setting = change["setting"]
        audit_event(
            EventKind.SETTING_CHANGED,
            agent="dashboard",
            _global=True,
            actor=actor,
            section=setting.section,
            key=setting.key,
            old=_audit_value(setting, change["old"]),
            new=_audit_value(setting, change["new"]),
            confirmed=bool(confirmed and catalog.weakens(setting, change["old"], change["new"])),
        )


@router.post(API_PATH)
async def catalog_update(request: Request, body: CatalogUpdate) -> JSONResponse:
    _app()._require_same_origin(request)
    auth.require_global_permission(request, "admin")
    from maverick.audit import AuditRefused

    from . import settings_store

    try:
        writes, changes, weakening, _layers = await run_in_threadpool(_plan, body)
    except _Refused as exc:
        # ``message`` is written for people (the page shows it as is).
        return JSONResponse({"detail": exc.message, "message": exc.message},
                            status_code=exc.status, headers={"Cache-Control": "no-store"})
    if weakening and not body.confirm:
        return JSONResponse(
            {"detail": "confirmation required", "confirm": weakening},
            status_code=409,
            headers={"Cache-Control": "no-store"},
        )
    if not writes:
        state = await run_in_threadpool(catalog_state)
        return JSONResponse({"changed": [], "restart": False, **state},
                            headers={"Cache-Control": "no-store"})
    payload = {path: (settings_store.REMOVE if value is None else value)
               for path, value in writes.items()}
    try:
        previous = await run_in_threadpool(settings_store.set_catalog_values, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="That setting is not available here.") from exc
    except (OSError, TypeError, settings_store.SecuritySuiteConfigUnavailable) as exc:
        raise HTTPException(
            status_code=503,
            detail="Settings could not be saved right now. Nothing was changed.",
        ) from exc
    actor = auth.caller_principal(request) or "local administrator"
    try:
        await run_in_threadpool(_record, changes, actor, body.confirm)
    except AuditRefused as exc:
        # The activity record refused the change: put the previous values back
        # so nothing changes without a record of it.
        try:
            await run_in_threadpool(settings_store.set_catalog_values, previous)
        except Exception as rollback_exc:
            raise HTTPException(
                status_code=500,
                detail="The change could not be recorded and could not be undone.",
            ) from rollback_exc
        raise HTTPException(
            status_code=503,
            detail="The change could not be recorded, so it was undone.",
        ) from exc
    state = await run_in_threadpool(catalog_state)
    return JSONResponse(
        {
            "changed": [c["setting"].id for c in changes],
            "restart": any(c["setting"].restart for c in changes),
            **state,
        },
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["router", "catalog_state", "PAGE_PATH", "API_PATH"]
