"""Dashboard-owned registry of polled event triggers.

An event trigger binds a saved template to an :class:`~maverick.automation_events.EventSource`
(plus its config and operator-set default params). A scheduled poll asks the
source for items newer than the stored ``cursor``; each fires the template as a
goal. Persisted to ``~/.maverick/dashboard-event-triggers.toml`` -- dashboard-
owned like the webhook triggers, deliberately NOT merged into the kernel config.

Mirrors ``triggers_store``'s atomic, 0600, cross-process-locked write and its
JSON-in-TOML encoding for the nested ``params``/``config`` tables and the
mutable ``cursor``.
"""
from __future__ import annotations

import json
import re
import threading
import time

from maverick import config

# Reuse the webhook store's slug (identical charset/length rules) so the two
# can't drift.
from .triggers_store import slugify

_LOCK = threading.Lock()

# URL/TOML-safe slug, same shape as webhook trigger names.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def _locked():
    from contextlib import ExitStack

    from maverick.file_lock import cross_process_lock
    stack = ExitStack()
    stack.enter_context(_LOCK)
    stack.enter_context(cross_process_lock(_path()))
    return stack


def _tomllib():
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
        import tomli as tomllib  # type: ignore
    return tomllib


def _path():
    return config.dashboard_overrides_path().parent / "dashboard-event-triggers.toml"


def _decode_obj(raw: object) -> dict:
    try:
        val = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def list_triggers() -> list[dict]:
    """Every registered event trigger as a dict (params/config decoded)."""
    p = _path()
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            raw = _tomllib().load(f)
    except (OSError, ValueError):
        return []
    out: list[dict] = []
    for name, t in (raw.get("trigger") or {}).items():
        if not isinstance(t, dict):
            continue
        out.append({
            "name": name,
            "template": str(t.get("template") or ""),
            "source": str(t.get("source") or ""),
            "config": _decode_obj(t.get("config")),
            "params": {str(k): str(v) for k, v in _decode_obj(t.get("params")).items()},
            "cursor": str(t.get("cursor") or ""),
            "owner": str(t.get("owner") or ""),
            "interval_seconds": int(t.get("interval_seconds") or 0),
            "created": float(t.get("created") or 0.0),
        })
    out.sort(key=lambda t: (t["created"], t["name"]))
    return out


def get_trigger(name: str) -> dict | None:
    for t in list_triggers():
        if t["name"] == name:
            return t
    return None


def _dump(triggers: list[dict]) -> str:
    lines = [
        "# Dashboard-managed polled event triggers. Edit via the dashboard,",
        "# not by hand. Your config.toml is never touched.",
        "",
    ]
    for t in sorted(triggers, key=lambda x: x["name"]):
        lines.append(f"[trigger.{t['name']}]")
        lines.append(f"template = {json.dumps(str(t['template']))}")
        lines.append(f"source = {json.dumps(str(t.get('source') or ''))}")
        lines.append(f"config = {json.dumps(json.dumps(t.get('config') or {}, sort_keys=True))}")
        lines.append(f"params = {json.dumps(json.dumps(t.get('params') or {}, sort_keys=True))}")
        lines.append(f"cursor = {json.dumps(str(t.get('cursor') or ''))}")
        lines.append(f"owner = {json.dumps(str(t.get('owner') or ''))}")
        lines.append(f"interval_seconds = {int(t.get('interval_seconds') or 0)}")
        lines.append(f"created = {float(t.get('created') or 0.0)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _write(triggers: list[dict]) -> None:
    from maverick.file_lock import atomic_write_text
    atomic_write_text(_path(), _dump(triggers))


def set_trigger(name: str, template: str, source: str, *, config: dict | None = None,
                params: dict | None = None, owner: str = "", cursor: str = "",
                interval_seconds: int = 0) -> dict:
    """Create or replace an event trigger (by slugified name). Raises ValueError
    on an un-sluggable name. Preserves the existing cursor on replace unless one
    is passed, so re-saving a trigger doesn't replay its whole backlog.
    ``interval_seconds`` is the per-trigger poll cadence (0 = the scheduler
    default)."""
    slug = slugify(name)
    if not _NAME_RE.match(slug):
        raise ValueError("trigger name must contain a letter or digit (a-z, 0-9, -)")
    with _locked():
        existing = list_triggers()
        kept = [t for t in existing if t["name"] != slug]
        prior = next((t for t in existing if t["name"] == slug), None)
        rec = {
            "name": slug,
            "template": str(template),
            "source": str(source),
            "config": dict(config or {}),
            "params": {str(k): str(v) for k, v in (params or {}).items()},
            "cursor": cursor or (prior["cursor"] if prior else ""),
            # Preserve the prior owner on a re-save that doesn't pass one, like
            # cursor/created, so an update can't silently orphan the trigger.
            "owner": str(owner) if owner else (prior["owner"] if prior else ""),
            "interval_seconds": int(interval_seconds) if interval_seconds
            else (prior.get("interval_seconds", 0) if prior else 0),
            "created": (prior["created"] if prior else time.time()),
        }
        kept.append(rec)
        _write(kept)
    return rec


def set_cursor(name: str, cursor: str) -> bool:
    """Advance a trigger's cursor after a poll. Returns False if it's gone.
    Skips the rewrite when the cursor is unchanged, so an idle poll (no new
    items) doesn't rewrite the whole store every tick."""
    cursor = str(cursor)
    with _locked():
        triggers = list_triggers()
        match = next((t for t in triggers if t["name"] == name), None)
        if match is None:
            return False
        if match["cursor"] != cursor:
            match["cursor"] = cursor
            _write(triggers)
        return True


def delete_trigger(name: str, owner: str | None = None) -> bool:
    """Remove a trigger. When ``owner`` is given, only if it belongs to that
    owner (ownership check + removal under one lock)."""
    with _locked():
        triggers = list_triggers()
        match = next((t for t in triggers if t["name"] == name), None)
        if match is None or (owner is not None and match.get("owner", "") != owner):
            return False
        _write([t for t in triggers if t["name"] != name])
    return True
