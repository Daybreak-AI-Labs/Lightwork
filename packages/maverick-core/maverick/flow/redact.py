"""Redaction + size bounds for a flow run's threaded ``data``.

A flow's ``data`` dict accumulates whatever agent/action nodes produce and is
both persisted (``store.save_run``) and returned over the API. A connector can
put a token, password, or other credential into it, so before that dict crosses
a trust boundary we (a) mask values whose KEY looks like a secret and (b) bound
the payload so one huge tool result can't bloat the run store or a response.
"""
from __future__ import annotations

import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|apikey|authorization|auth|"
    r"bearer|credential|private[_-]?key|client[_-]?secret|access[_-]?key)", re.IGNORECASE)
_MASK = "***redacted***"
_MAX_STR = 8_000          # truncate any single string value past this
_MAX_KEYS = 500           # cap the breadth of a single dict


def redact(value: Any) -> Any:
    """A deep copy with secret-looking keys masked. Structure is preserved so the
    UI still shows the shape of the data, just not the secret."""
    if isinstance(value, dict):
        return {k: (_MASK if isinstance(k, str) and _SECRET_KEY.search(k) else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def cap(value: Any, _depth: int = 0) -> Any:
    """A deep copy with oversized strings truncated and wide dicts/lists bounded,
    so a runaway tool result can't bloat the persisted run. Deep nesting past a
    small limit collapses to a marker (a cheap cycle/blow-up guard)."""
    if _depth > 12:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…[truncated]"
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_KEYS:
                out["…"] = f"[{len(value) - _MAX_KEYS} more keys]"
                break
            out[k] = cap(v, _depth + 1)
        return out
    if isinstance(value, list):
        capped = [cap(v, _depth + 1) for v in value[:_MAX_KEYS]]
        if len(value) > _MAX_KEYS:
            capped.append(f"…[{len(value) - _MAX_KEYS} more items]")
        return capped
    return value


__all__ = ["redact", "cap"]
