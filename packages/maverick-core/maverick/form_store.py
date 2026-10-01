"""A bounded append-only store of hosted-form submissions.

A ``form`` event trigger points a public web form (or any POST) at
``/form/<token>``; each submission is appended here under that token, and the
``form`` :class:`~maverick.automation_events.EventSource` polls it so the
submission fires a flow -- the push-to-poll bridge that lets a form be a
first-class trigger without a bespoke inbound queue.

Private NDJSON in the tenant data dir. A submission is one
line ``{"seq", "ts", "fields"}``; ``seq`` is a per-token monotonic counter used
as the poll cursor (deletion-safe, unlike id-walking). Full stores refuse new
submissions instead of silently dropping entries that a trigger has not polled.
"""
from __future__ import annotations

import json
import os
import re

_MAX_FIELDS = 50          # per submission
_MAX_VALUE = 2000         # per field value
_MAX_LINES = 5000         # per form; refuse rather than lose pending events
_MAX_BYTES = 32 * 1024 * 1024
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")


class FormStoreFull(RuntimeError):
    """The form cannot accept another submission without losing existing data."""


def valid_token(token: str) -> bool:
    return isinstance(token, str) and bool(_TOKEN_RE.fullmatch(token))


def storage_key(token: str) -> str:
    """Keep pre-upgrade form files addressable by their original token."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", str(token or "").strip())
    return s[:120] or "form"


def _path(token: str):
    from .paths import data_dir
    return data_dir("forms") / f"{storage_key(token)}.ndjson"


def _bound_fields(fields: dict) -> dict:
    out: dict = {}
    for k, v in (fields or {}).items():
        if len(out) >= _MAX_FIELDS:
            break
        out[str(k)[:200]] = ("" if v is None else str(v))[:_MAX_VALUE]
    return out


def append(token: str, fields: dict, *, ts: float = 0.0) -> int:
    """Append one submission for ``token``; return its ``seq`` (1-based). Fields
    are stringified + bounded. Returns 0 on write failure and raises
    :class:`FormStoreFull` when the bounded store has reached capacity."""
    p = _path(token)
    from .file_lock import cross_process_lock, ensure_private_directory, open_private_append

    try:
        # This is a dedicated Lightwork-owned directory. Harden an older
        # installation's existing directory as well as newly created ones.
        ensure_private_directory(p.parent)
        with cross_process_lock(p, strict=True):
            count, highest = _state(p)
            if count >= _MAX_LINES:
                raise FormStoreFull("form submission limit reached")
            seq = highest + 1
            line = (json.dumps({"seq": seq, "ts": float(ts), "fields": _bound_fields(fields)},
                               separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
            size = p.stat().st_size if p.exists() else 0
            if size + len(line) > _MAX_BYTES:
                raise FormStoreFull("form storage limit reached")
            fd = open_private_append(p, require_private_parent=True)
            with os.fdopen(fd, "ab") as f:
                f.write(line)
            return seq
    except (OSError, RuntimeError) as exc:
        if isinstance(exc, FormStoreFull):
            raise
        return 0


def _state(p) -> tuple[int, int]:
    """Read count and high-water sequence under the caller's file lock."""
    count = highest = 0
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                count += 1
                try:
                    seq = json.loads(line).get("seq")
                    if type(seq) is int and seq > highest:
                        highest = seq
                except (ValueError, AttributeError):
                    continue
    except FileNotFoundError:
        pass
    return count, highest


def since(token: str, cursor: str) -> tuple[list[dict], str]:
    """Submissions with ``seq`` greater than ``cursor`` (empty cursor = baseline:
    fire nothing, just record the high-water mark). Returns ``(events, cursor)``
    where each event is the flat ``fields`` dict plus ``_seq``/``_ts``."""
    p = _path(token)
    rows: list[dict] = []
    if not p.exists():
        # Do not create a lock sidecar on a baseline poll for a new form.
        return [], cursor or "0"
    from .file_lock import cross_process_lock

    try:
        with cross_process_lock(p, strict=True), open(p, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        # A non-empty baseline cursor even for a missing store ("0"), so the
        # first submission after the trigger is armed locks in and then fires --
        # returning "" would leave the trigger's cursor unadvanced and swallow it.
        return [], cursor or "0"
    if not rows:
        return [], cursor or "0"
    highest = str(rows[-1].get("seq") or len(rows))
    try:
        after = int(cursor) if cursor else None
    except (TypeError, ValueError):
        after = None
    if after is None:
        return [], highest                       # baseline only
    fresh = [r for r in rows if int(r.get("seq") or 0) > after]
    if not fresh:
        return [], highest
    events = []
    for r in fresh:
        ev = dict(r.get("fields") or {})
        ev["_seq"], ev["_ts"] = r.get("seq"), r.get("ts")
        events.append(ev)
    return events, str(fresh[-1].get("seq") or highest)


__all__ = ["FormStoreFull", "append", "since", "storage_key", "valid_token"]
