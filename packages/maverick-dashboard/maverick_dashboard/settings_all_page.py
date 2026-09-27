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

import dataclasses
import os
import threading
import time
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


def _layer_value(setting: catalog.Setting, layer: object) -> tuple[bool, object]:
    """``(present, value)`` of ``setting`` in one configuration layer."""
    present, value = catalog.lookup(layer, setting.path)
    if setting.key_kind == "entries":
        if not present or not isinstance(value, dict):
            return False, None
        rows = {k: v for k, v in value.items()
                if k not in setting.fixed_keys and not isinstance(v, dict)}
        return (bool(rows), rows or None)
    return present, value


def _resolve_table(setting: catalog.Setting, layers: dict[str, Any]) -> dict[str, Any]:
    """Tables merge like the kernel's config loader: later layers add rows."""
    merged: dict[str, object] = {}
    source = "default"
    for name, label in (("base", "installer"), ("here", "here")):
        present, value = _layer_value(setting, layers[name])
        if present and isinstance(value, dict):
            merged.update(value)
            source = label
    present, value = _layer_value(setting, layers["operator"])
    if present:
        merged.update(value if isinstance(value, dict) else {})
        return {"source": "deployment", "locked": True, "value": merged, "known": True}
    return {"source": source, "locked": False, "value": merged, "known": True}


def _resolve(setting: catalog.Setting, layers: dict[str, Any]) -> dict[str, Any]:
    """Effective value, where it comes from, and whether this page may change it."""
    if _env_locked(setting):
        return {"source": "deployment", "locked": True, "value": None, "known": False}
    if setting.control == "table":
        return _resolve_table(setting, layers)
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


# ------------------------------------------------------------ live choices
_TOOL_CACHE: dict[str, Any] = {"at": 0.0, "names": None}
_TOOL_LOCK = threading.Lock()
_TOOL_TTL = 300.0
_CONNECTOR_LABELS = {"salesforce": "Salesforce", "servicenow": "ServiceNow"}
TOOL_FAMILIES: tuple[tuple[str, str], ...] = (
    ("mcp_*", "Every tool from outside tool servers"),
)


def _tool_names() -> list[str]:
    """Registered tool names (cached briefly: building the list is slow)."""
    with _TOOL_LOCK:
        cached = _TOOL_CACHE["names"]
        if cached is not None and time.monotonic() - _TOOL_CACHE["at"] < _TOOL_TTL:
            return list(cached)
    try:
        from maverick.tools import base_tool_names

        names = sorted(base_tool_names() or ())
    except Exception:  # a missing optional extra must not break the page
        names = []
    with _TOOL_LOCK:
        _TOOL_CACHE.update(at=time.monotonic(), names=tuple(names))
    return names


def _people_choices() -> list[tuple[str, str]]:
    from . import identity_format, rbac

    try:
        users = sorted(rbac.list_users())
    except Exception:
        users = []
    out = [("", "Nobody: changes wait until someone approves them outside Lightwork")]
    for principal in users:
        who = identity_format.humanize_principal(principal)
        out.append((principal, f"{who.name} ({who.detail})" if who.detail else who.name))
    return out


def _source_choices(source: str) -> list[tuple[str, str]]:  # noqa: C901 - one branch per source
    from . import identity_format

    if source == "people":
        return _people_choices()
    if source == "governed_connectors":
        try:
            from maverick.governed_rest import available_rest_connectors

            names = available_rest_connectors()
        except Exception:
            names = []
        return [(n, _CONNECTOR_LABELS.get(n, identity_format.humanize(n))) for n in names]
    if source == "gateway_models":
        from maverick.llm import MODEL_CATALOG, PROVIDER_LABELS

        out: list[tuple[str, str]] = []
        for provider, ids in MODEL_CATALOG.items():
            for model in ids:
                if all(model != v for v, _ in out):
                    out.append((model, f"{model} ({PROVIDER_LABELS.get(provider, provider)})"))
        return out
    if source == "local_models":
        from maverick.llm import catalog_specs
        from maverick.provider_local_first import LOCAL_PROVIDERS, is_local

        from .model_pickers import config_catalog

        out = [("", "None chosen")]
        specs = [spec for spec, _label in catalog_specs()] + config_catalog()
        for spec in dict.fromkeys(specs):
            if is_local(spec) and spec.split(":", 1)[0] in LOCAL_PROVIDERS:
                provider, _, model = spec.partition(":")
                out.append((spec, f"{model} ({identity_format.humanize(provider)})"))
        return out
    if source == "tools":
        return [*TOOL_FAMILIES,
                *((n, identity_format.humanize(n)) for n in _tool_names())]
    return []


def _resolved(
    setting: catalog.Setting, current: object = None, *, full: bool = True,
) -> catalog.Setting:
    """``setting`` with live options filled in (keeping whatever is set now).

    ``full=False`` skips the tool list, which is slow to build and which the
    page loads separately; the rows already set still get their labels.
    """
    if not setting.choices_from:
        return setting
    from . import identity_format

    if setting.choices_from == "tools" and not full:
        choices = list(TOOL_FAMILIES)
    else:
        choices = list(_source_choices(setting.choices_from))
    known = {v for v, _ in choices}
    extra = current if isinstance(current, (list, dict)) else [current]
    for value in extra:
        if value not in (None, "") and str(value) not in known:
            choices.append((str(value), identity_format.humanize(value)))
            known.add(str(value))
    if setting.control == "table":
        choices = [c for c in choices if c[0] not in setting.fixed_keys]
    return dataclasses.replace(setting, choices=tuple(choices))


def _row(setting: catalog.Setting, layers: dict[str, Any]) -> dict[str, Any]:
    state = _resolve(setting, layers)
    value = state["value"]
    setting = _resolved(setting, value if state["known"] else None, full=False)
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
        "subgroup": setting.subgroup,
        "item_label": setting.item_label,
        "item_hint": setting.item_hint,
    }
    if setting.choices_from == "people" and len(setting.choices) <= 1:
        row["empty_hint"] = "Nobody has been added yet. Add people on the People page first."
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
    periods = [{"value": s, "label": f"Every {n}"} for s, n in catalog.RATE_PERIODS]
    if setting.control == "rate":
        parsed = catalog.parse_rate(value) if state["known"] else None
        row["rate_count"] = parsed[0] if parsed else ""
        row["rate_seconds"] = parsed[1] if parsed else 60
        row["periods"] = periods
    if setting.control == "keys":
        row["key_items"] = [
            {"value": key, "fingerprint": catalog.key_fingerprint(key)}
            for key in (value if isinstance(value, list) else [])
        ]
    if setting.control == "table":
        if setting.choices_from == "tools":
            row["choices"] = []  # long list: the page loads it when needed
        row["periods"] = periods
        row["risks"] = [{"value": v, "label": label} for v, label in catalog.RISK_LEVELS]
        row["row_kind"] = setting.row_kind
        row["rows"] = _table_rows(setting, value)
    return row


def _table_rows(setting: catalog.Setting, value: object) -> list[dict[str, Any]]:
    labels = dict(setting.choices)
    rows = []
    for name, spec in sorted((value or {}).items()) if isinstance(value, dict) else []:
        row: dict[str, Any] = {"name": name, "label": labels.get(name, name)}
        if setting.row_kind == "rate":
            parsed = catalog.parse_rate(spec)
            row["count"], row["seconds"] = parsed or ("", 60)
        else:
            row["risk"] = spec.get("risk", "high") if isinstance(spec, dict) else "high"
        rows.append(row)
    return rows


def _form_value(setting: catalog.Setting, value: object) -> object:
    """The value as the page's control shows it."""
    if setting.control == "select":
        return "" if value is None else str(value)
    if setting.control in {"multiselect", "list"}:
        return [str(v) for v in value] if isinstance(value, list) else []
    if setting.control == "number" and setting.scale != 1.0 and catalog._is_number(value):
        shown = round(float(value) / setting.scale, 4)
        return int(shown) if shown == int(shown) else shown
    if setting.control in {"keys", "table"}:
        return None  # carried by ``keys`` / ``rows``
    return value


def _effective(setting: catalog.Setting, layers: dict[str, Any]) -> object:
    state = _resolve(setting, layers)
    return state["value"] if state["known"] else None


def catalog_state() -> dict[str, Any]:
    layers = _sources()
    groups = []
    for name in catalog.GROUPS:
        rows = [_row(s, layers) for s in catalog.SETTINGS if s.group == name]
        subgroups = list(dict.fromkeys(r["subgroup"] for r in rows if r["subgroup"]))
        for r in rows:
            r["subgroup_slug"] = _slug(name + " " + r["subgroup"]) if r["subgroup"] else ""
        groups.append({
            "name": name,
            "slug": _slug(name),
            "intro": catalog.GROUP_INTROS.get(name, ""),
            "settings": rows,
            "subgroups": [{"name": sub, "slug": _slug(name + " " + sub)} for sub in subgroups],
        })
    return {"groups": groups}


def _slug(name: str) -> str:
    text = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    while "--" in text:
        text = text.replace("--", "-")
    return text


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


@router.get(API_PATH + "/choices/{setting_id}")
async def catalog_choices(request: Request, setting_id: str) -> JSONResponse:
    """The options of one setting whose list is too long to put in the page."""
    auth.require_global_permission(request, "admin")
    setting = catalog.BY_ID.get(setting_id)
    if setting is None or not setting.choices_from:
        return JSONResponse({"detail": "That setting has no list of options.",
                             "message": "That setting has no list of options."},
                            status_code=404)
    resolved = await run_in_threadpool(_resolved, setting)
    return JSONResponse(
        {"choices": [{"value": v, "label": label} for v, label in resolved.choices]},
        headers={"Cache-Control": "no-store"},
    )


@router.get(API_PATH)
async def catalog_list(request: Request) -> JSONResponse:
    auth.require_global_permission(request, "admin")
    state = await run_in_threadpool(catalog_state)
    return JSONResponse(state, headers={"Cache-Control": "no-store"})


def _audit_value(setting: catalog.Setting, value: object) -> object:
    if setting.secret:
        return "set" if value else "not set"
    if setting.control == "keys" and isinstance(value, list):
        return [catalog.key_fingerprint(k) for k in value]
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
        if setting.control == "table":
            new, table_writes = _plan_table(setting, item, old, layers)
            if old == new and not (item.reset and table_writes):
                continue
            writes.update(table_writes)
            prospective[setting.id] = new
            changes.append({"setting": setting, "old": old, "new": new})
            if catalog.weakens(setting, old, new):
                weakening.append({"id": setting.id, "label": setting.label,
                                  "warning": setting.warning})
            continue
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
                new = catalog.coerce(_resolved(setting, old), item.value)
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


# A tool newly offered through the tool gateway: the gateway needs a
# description and an input shape; the conservative "any object" shape is the
# same one the installer writes, to be narrowed by the tool's reviewer.
_NEW_GATEWAY_TOOL = {
    "description": "Reviewed tool offered through the gateway",
    "input_schema": {"type": "object", "properties": {}},
}


def _plan_table(
    setting: catalog.Setting, item: SettingChange, old: object, layers: dict[str, Any],
) -> tuple[dict, dict[tuple[str, ...], object]]:
    """Rows to write for a table setting, and the table the kernel will see."""
    before = old if isinstance(old, dict) else {}
    _present, base_rows = _layer_value(setting, layers["base"])
    base_rows = dict(base_rows) if isinstance(base_rows, dict) else {}
    _present, here_rows = _layer_value(setting, layers["here"])
    here_rows = dict(here_rows) if isinstance(here_rows, dict) else {}
    if item.reset:
        wanted = dict(base_rows)
    else:
        try:
            wanted = catalog.coerce(_resolved(setting, before), item.value)
        except catalog.SettingError as exc:
            raise _Refused(400, str(exc)) from exc
        kept = [name for name in base_rows if name not in wanted]
        if kept:
            label = dict(_resolved(setting, before).choices).get(kept[0], kept[0])
            raise _Refused(
                400,
                f"{setting.label}: “{label}” was set by your installer, so it can be "
                "changed here but not removed.",
            )
        if setting.row_kind == "risk":
            wanted = {
                name: {**(before.get(name) if isinstance(before.get(name), dict)
                          else _NEW_GATEWAY_TOOL), **spec}
                for name, spec in wanted.items()
            }
    writes: dict[tuple[str, ...], object] = {}
    if setting.key_kind == "entries":
        for name in here_rows:
            if name not in wanted or wanted[name] == base_rows.get(name):
                writes[(*setting.path, name)] = None
        for name, spec in wanted.items():
            if base_rows.get(name) != spec and here_rows.get(name) != spec:
                writes[(*setting.path, name)] = spec
    else:
        overlay = {name: spec for name, spec in wanted.items() if base_rows.get(name) != spec}
        if overlay != here_rows:
            writes[setting.path] = overlay or None
    return {**base_rows, **wanted}, writes


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
