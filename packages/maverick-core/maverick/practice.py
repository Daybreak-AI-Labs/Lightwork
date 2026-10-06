"""Practice mode: try agents, tasks and approvals without touching real systems.

A person switches practice on for themselves (the dashboard keeps the choice
in their browser session). While it is on for a request or a task run:

* tasks, results, approvals and conversations live in a separate practice
  world database (:func:`world_path`), so nothing practised is mixed with real
  evidence, learning or reports;
* only tools that read the practice workspace or coordinate work inside
  Lightwork run (:data:`PRACTICE_TOOLS`); every other tool call (email, chat
  messages, payments, connectors, web access, file changes, commands) is
  written to the practice outbox instead of running (:func:`record`);
* alerts, webhooks and approval cards that would leave Lightwork are
  recorded the same way.

Spending is real money, so practice runs keep the live spending limits and
budgets: nothing here changes how a run is admitted or capped.

``[practice] enable`` (default on) offers the switch. Practice is unavailable
when the world model runs on Postgres or tasks run on an outside worker,
because the separate practice database could not be guaranteed there.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import threading
import time
from pathlib import Path

_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar("maverick_practice", default=False)
_LOCK = threading.Lock()
_OUTBOX_MAX = 500

# Tools that only read the practice workspace or coordinate work inside
# Lightwork. Anything not listed is recorded, never run, while practising.
PRACTICE_TOOLS = frozenset({
    # read the (sample) workspace and this task's own files
    "read_file", "list_dir", "read_tool_result", "read_attachment", "list_attachments",
    "read_pdf", "view_image", "spreadsheet_read", "preview_diff", "repo_map", "dep_graph",
    # coordinate inside Lightwork
    "ask_user", "spawn_subagent", "spawn_swarm", "spawn_specialist", "list_specialists",
    "send_to_agent", "recv_from_agent", "delegate_to_agent", "find_tools",
    "recall_past_goals", "budget_status", "diagnose", "spend_report",
})


def enabled() -> bool:
    """``[practice] enable``: whether people are offered practice mode."""
    try:
        from .config import config_source_errors, load_config

        config = load_config()
        if config_source_errors() or not isinstance(config, dict):
            return False
        section = config.get("practice")
        if section is None:
            return True
        if not isinstance(section, dict):
            return False
        value = section.get("enable", True)
        return value if isinstance(value, bool) else False
    except Exception:  # unreadable settings: stay live, offer nothing
        return False


def unavailable_reason() -> str:
    """Why practice cannot be switched on here, or ``""`` when it can."""
    if not enabled():
        return "Practice mode is turned off in Settings."
    try:
        from .world_model_backends import is_postgres_configured

        if is_postgres_configured():
            return "Practice mode needs the built-in database. This workspace keeps its records on a separate database server."
    except Exception:  # backends extra absent: the built-in database is in use
        pass
    from .runner import LocalThreadDispatcher, get_dispatcher

    if not isinstance(get_dispatcher(), LocalThreadDispatcher):
        return "Practice mode needs tasks to run in this server. This workspace runs tasks on separate workers."
    return ""


def active() -> bool:
    """True while the current request or task run is practising."""
    return bool(_ACTIVE.get())


def activate(on: bool = True):
    """Mark the current context as practising; returns a token for :func:`reset`."""
    return _ACTIVE.set(bool(on))


def reset(token) -> None:
    try:
        _ACTIVE.reset(token)
    except (ValueError, LookupError):  # pragma: no cover -- cross-context reset
        pass


@contextlib.contextmanager
def scope(on: bool = True):
    token = activate(on)
    try:
        yield
    finally:
        reset(token)


def world_path(tenant: str | None = "__active__") -> Path:
    """The practice world database for ``tenant`` (the active one by default)."""
    from .paths import data_dir

    return data_dir("practice", "world.db", tenant=tenant)


def tool_allowed(name: str) -> bool:
    return name in PRACTICE_TOOLS


def _outbox_path() -> Path:
    from .paths import data_dir

    return data_dir("practice", "outbox.jsonl")


def _summary(detail: object) -> str:
    try:
        text = json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # an odd payload still gets a readable line
        text = str(detail)
    return text if len(text) <= 400 else text[:399] + "…"


def record(kind: str, name: str, detail: object = None, *, goal_id: int | None = None) -> str:
    """Write what would have left Lightwork to the practice outbox, and say so."""
    from .file_lock import ensure_private_directory

    row = {"at": time.time(), "kind": str(kind)[:40], "name": str(name)[:120],
           "detail": _summary(detail), "goal_id": goal_id}
    path = _outbox_path()
    with _LOCK:
        ensure_private_directory(path.parent)
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        lines.append(json.dumps(row, ensure_ascii=False))
        path.write_text("\n".join(lines[-_OUTBOX_MAX:]) + "\n", encoding="utf-8")
    return (f"Practice mode: {name} did not run. Nothing was sent, paid or changed outside practice; "
            "the request was written to the practice outbox. Continue as if it had worked, and say "
            "in your answer that it was practice.")


def outbox(limit: int = 100) -> list[dict]:
    """Newest first: what practice runs would have sent or changed."""
    path = _outbox_path()
    if not path.exists():
        return []
    rows = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
        if len(rows) >= limit:
            break
    return rows


def clear_outbox() -> None:
    path = _outbox_path()
    with _LOCK:
        if path.exists():
            path.unlink()


__all__ = ["PRACTICE_TOOLS", "activate", "active", "clear_outbox", "enabled", "outbox", "record",
           "reset", "scope", "tool_allowed", "unavailable_reason", "world_path"]
