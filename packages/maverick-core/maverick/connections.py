"""Named SaaS connections -- a first-class, sealed alternative to env-var creds.

An enterprise connector resolves ``<NAME>_BASE_URL`` / ``<NAME>_TOKEN`` from the
environment. That's fine for an operator but a non-starter for a citizen user
who can't touch env vars. A *connection* stores those same two values under a
name (``salesforce`` / ``salesforce-eu``), sealed at rest with the tenant's KMS
key exactly like the OAuth vault, so a connector can be wired from the dashboard
without shell access.

Resolution order (see :func:`resolve`): a connection named after the connector
wins ONLY when the env var is unset, so this is purely additive -- an operator's
existing ``<NAME>_TOKEN`` still takes precedence and nothing about the current
env-based path changes.

Gated behind :func:`enabled` (``[connections] enable`` / ``MAVERICK_CONNECTIONS``);
off by default. Token values are sealed and never returned by the listing API --
only ``has_token`` is surfaced.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

from .paths import data_dir
from .tenant.kms import seal_text_for_tenant, unseal_text_for_tenant

_lock = threading.Lock()
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def enabled() -> bool:
    if os.environ.get("MAVERICK_CONNECTIONS", "").strip() in ("1", "true", "yes"):
        return True
    try:
        from .config import load_config
        return bool((load_config().get("connections") or {}).get("enable", False))
    except Exception:  # pragma: no cover -- config never gates a read to a crash
        return False


def _path():
    return data_dir("connections", "connections.sealed")


def _seal_tenant() -> str | None:
    from .paths import current_tenant_id
    return current_tenant_id()


def _load() -> dict[str, dict]:
    path = _path()
    if not path.exists():
        return {}
    try:
        raw = unseal_text_for_tenant(_seal_tenant(), path.read_bytes())
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _save(data: dict[str, dict]) -> None:
    path = _path()
    blob = seal_text_for_tenant(_seal_tenant(), json.dumps(data, sort_keys=True))
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".sealed.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    os.replace(tmp, path)


def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", (name or "").strip().lower()).strip("-")[:64]


def set_connection(name: str, *, connector: str = "", base_url: str = "",
                   token: str = "", owner: str = "") -> dict:
    """Create or replace a named connection (sealed). ``connector`` is the tool
    name it credentials (e.g. ``salesforce``); ``name`` may differ so one tool
    can have several accounts. Raises ValueError on an unusable name."""
    slug = normalize_name(name)
    if not _NAME_RE.match(slug):
        raise ValueError("connection name must be a slug (a-z, 0-9, -, _)")
    rec = {
        "name": slug, "connector": normalize_name(connector) or slug,
        "base_url": str(base_url).strip().rstrip("/"), "token": str(token),
        "created": time.time(), "owner": str(owner or ""),
    }
    with _lock:
        data = _load()
        data[slug] = rec
        _save(data)
    return _public(rec)


def get_connection(name: str) -> dict | None:
    """The full sealed record (INCLUDING the token) for internal resolution."""
    return _load().get(normalize_name(name))


def delete_connection(name: str, owner: str | None = None) -> bool:
    slug = normalize_name(name)
    with _lock:
        data = _load()
        rec = data.get(slug)
        if rec is None or (owner is not None and rec.get("owner", "") != owner):
            return False
        del data[slug]
        _save(data)
    return True


def _public(rec: dict) -> dict:
    """A record safe to return over the API -- the token is never included, only
    whether one is set."""
    return {
        "name": rec.get("name", ""), "connector": rec.get("connector", ""),
        "base_url": rec.get("base_url", ""), "has_token": bool(rec.get("token")),
        "created": float(rec.get("created") or 0.0), "owner": rec.get("owner", ""),
    }


def list_connections(owner: str | None = None) -> list[dict]:
    out = [_public(r) for r in _load().values() if isinstance(r, dict)]
    if owner is not None:
        out = [c for c in out if c.get("owner", "") == owner]
    out.sort(key=lambda c: (c["created"], c["name"]))
    return out


def resolve(connector: str) -> tuple[str, str] | None:
    """(base_url, token) for the first connection credentialing ``connector``,
    or None. Used as a FALLBACK by the connector layer only when the env var is
    unset, so it never overrides an operator's environment."""
    if not enabled():
        return None
    slug = normalize_name(connector)
    # a connection named exactly after the connector wins; else the first one
    # whose "connector" field points at it.
    data = _load()
    rec = data.get(slug)
    if rec is None:
        rec = next((r for r in data.values()
                    if isinstance(r, dict) and r.get("connector") == slug), None)
    if not rec or not rec.get("token"):
        return None
    return (str(rec.get("base_url") or ""), str(rec.get("token")))


__all__ = [
    "enabled", "set_connection", "get_connection", "delete_connection",
    "list_connections", "resolve", "normalize_name",
]
