"""Event triggers: fire a saved workflow when something new appears in an
external source -- the "when X happens" half of workflow automation.

Push-capable services already work via the inbound webhook trigger
(``/webhook/run``). This is the POLL side, for sources that don't push: an
:class:`EventSource` is polled on a schedule; each item newer than the stored
cursor fires the bound template as a goal, with the item's fields available to
fill declared params.

An ``EventSource.poll(config, cursor)`` returns the new events plus the next
cursor. First poll establishes a baseline (records the newest id, fires
nothing) so arming a trigger never floods on historical items; later polls
return only what appeared since. Sources self-register (like automation_import),
so new connectors are additive.

Off by default (``[event_triggers] enable`` / ``MAVERICK_EVENT_TRIGGERS``):
polling reaches out to third-party endpoints, so the operator opts in
(kernel rule 1). ``fetch``/HTTP goes through the SSRF guard.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class EventSourceError(RuntimeError):
    """Configuration / fetch problem for an event source (bad URL, unreachable)."""


@dataclass
class PollResult:
    """The outcome of polling a source once.

    ``events`` are the new items (each a flat dict of fields usable as params),
    oldest-first so they fire in the order they occurred. ``cursor`` is the
    opaque position to pass to the next poll.
    """
    events: list[dict] = field(default_factory=list)
    cursor: str = ""


@runtime_checkable
class EventSource(Protocol):
    source: str

    def poll(self, config: dict, cursor: str) -> PollResult:
        """Return items newer than ``cursor`` + the next cursor."""
        ...


_SOURCES: dict[str, Callable[[], EventSource]] = {}


def register(source: str, factory: Callable[[], EventSource]) -> None:
    """Register an event-source factory under its name (idempotent)."""
    _SOURCES[source] = factory


def available_sources() -> list[str]:
    return sorted(_SOURCES)


def get_source(source: str) -> EventSource:
    factory = _SOURCES.get(source)
    if factory is None:
        raise EventSourceError(
            f"unknown event source {source!r}; "
            f"available: {', '.join(available_sources()) or '(none)'}"
        )
    return factory()


def enabled() -> bool:
    """Whether event-trigger polling is switched on (off by default)."""
    from .config import env_flag
    v = env_flag("MAVERICK_EVENT_TRIGGERS")
    if v is not None:
        return v
    try:
        from .config import load_config
        return bool((load_config().get("event_triggers") or {}).get("enable", False))
    except Exception:  # pragma: no cover -- config never blocks
        return False


# ---- helpers ---------------------------------------------------------------

_MAX_EVENTS = 25  # per poll, so a backlog can't spawn an unbounded goal burst


def _dig(obj: Any, path: str) -> Any:
    """Follow a dotted ``a.b.c`` path into nested dicts; '' returns obj as-is."""
    cur = obj
    for part in (p for p in (path or "").split(".") if p):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _flatten(item: dict, *, prefix: str = "", out: dict | None = None, depth: int = 0) -> dict:
    """Flatten a nested event dict into ``a.b`` string keys, so declared params
    can pull ``issue.title`` etc. Values are stringified + length-bounded; depth
    and breadth are capped so a hostile payload can't explode the param map."""
    out = {} if out is None else out
    if depth > 4 or len(out) >= 100:
        return out
    for k, v in item.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            _flatten(v, prefix=f"{key}.", out=out, depth=depth + 1)
        elif isinstance(v, (list, tuple)):
            out[key] = ", ".join(str(x) for x in v)[:2000]
        else:
            out[key] = ("" if v is None else str(v))[:2000]
        if len(out) >= 100:
            break
    return out


def diff_new_items(items: list[dict], cursor: str, id_field: str, *,
                   newest_first: bool = True) -> PollResult:
    """Given the raw items a source returned, compute the new ones vs ``cursor``.

    ``cursor`` is the id of the newest item seen at the last poll. First poll
    (empty cursor) fires nothing and just records the newest id -- so arming a
    trigger never replays history. Otherwise the items ahead of the cursor id
    are new; they're returned oldest-first and the cursor advances to the newest.
    Items are flattened to ``a.b`` param-friendly dicts and bounded.
    """
    ordered = list(items) if newest_first else list(reversed(items))  # newest-first

    def _id(it: dict) -> str:
        return str(_dig(it, id_field) or "")

    ids = [_id(it) for it in ordered]
    newest = ids[0] if ids else cursor
    if not cursor:
        return PollResult(events=[], cursor=newest)   # baseline only
    fresh: list[dict] = []
    for it, iid in zip(ordered, ids, strict=False):
        if iid and iid == cursor:
            break                                       # reached last-seen
        fresh.append(it)
    fresh.reverse()                                     # oldest-first for firing
    # Fire the OLDEST _MAX_EVENTS of a backlog and advance the cursor only to the
    # newest item we actually fired -- so a burst larger than the cap drains over
    # subsequent polls instead of the older items being skipped and lost.
    fresh = fresh[:_MAX_EVENTS]
    if not fresh:
        return PollResult(events=[], cursor=newest)
    next_cursor = _id(fresh[-1]) or newest
    return PollResult(events=[_flatten(it) for it in fresh], cursor=next_cursor)


# ---- built-in source: poll a JSON HTTP endpoint ----------------------------


def _base_config(config: dict, source: str) -> tuple[str, str, dict]:
    """Pull the fields every source shares: ``url`` (required), ``id_field``,
    ``headers``. Raises if ``url`` is missing/blank."""
    url = str((config or {}).get("url") or "").strip()
    if not url:
        raise EventSourceError(f"{source} event source needs a 'url'")
    id_field = str((config or {}).get("id_field") or "id")
    raw_headers = (config or {}).get("headers")
    headers = raw_headers if isinstance(raw_headers, dict) else {}
    return url, id_field, headers


def _http_get(url: str, headers: dict | None = None, timeout: float = 15.0) -> Any:
    """GET ``url`` through the SSRF guard, returning the checked response (an
    event-source URL is operator config, but a loopback/metadata host would make
    the poller an SSRF proxy). Shared by the JSON and text fetchers."""
    try:
        from .tools._ssrf import BlockedHost, safe_get
    except Exception as e:  # pragma: no cover -- guard unavailable
        raise EventSourceError("SSRF guard unavailable") from e
    try:
        resp = safe_get(url, headers=headers or {}, timeout=timeout)
    except BlockedHost as e:
        raise EventSourceError(f"blocked by SSRF guard: {e}") from e
    except Exception as e:
        raise EventSourceError(f"fetch failed: {type(e).__name__}: {e}") from e
    if getattr(resp, "status_code", 200) >= 400:
        raise EventSourceError(f"source returned HTTP {resp.status_code}")
    return resp


def _http_get_json(url: str, headers: dict | None = None, timeout: float = 15.0) -> Any:
    """GET ``url`` and parse JSON. Isolated so tests can inject a fake."""
    resp = _http_get(url, headers=headers, timeout=timeout)
    try:
        return resp.json()
    except Exception as e:
        raise EventSourceError("source did not return JSON") from e


class HttpJsonSource:
    """Poll a JSON HTTP endpoint for new items.

    ``config``: ``{url, items_path?, id_field?, headers?, newest_first?}``.
    ``items_path`` digs to the array (e.g. ``data.issues``; default = the whole
    body when it's already a list). ``id_field`` is the per-item unique id used
    for dedup (dotted paths allowed; default ``id``). This one connector covers
    a large slice of "new row / new record / new lead" REST feeds.
    """
    source = "http_json"

    def _extra_headers(self, config: dict) -> dict:
        """Per-poll headers merged over the static ``headers`` config (e.g. a
        live ``Authorization``). Base source adds none; subclasses override."""
        return {}

    def poll(self, config: dict, cursor: str) -> PollResult:
        url, id_field, headers = _base_config(config, self.source)
        items_path = str((config or {}).get("items_path") or "")
        newest_first = bool((config or {}).get("newest_first", True))
        headers = {**headers, **self._extra_headers(config)}
        body = _http_get_json(url, headers=headers)
        items = _dig(body, items_path) if items_path else body
        if not isinstance(items, list):
            raise EventSourceError(
                "expected a JSON array of items"
                + (f" at {items_path!r}" if items_path else "")
            )
        items = [it for it in items if isinstance(it, dict)]
        return diff_new_items(items, cursor, id_field, newest_first=newest_first)


register("http_json", HttpJsonSource)


# ---- built-in source: poll an RSS / Atom feed -------------------------------


def _http_get_text(url: str, headers: dict | None = None, timeout: float = 15.0) -> str:
    """GET ``url`` as text. Isolated for test injection."""
    return _http_get(url, headers=headers, timeout=timeout).text or ""


def _parse_feed(text: str) -> list[dict]:
    """Parse an RSS or Atom feed into flat item dicts (id/title/link/summary/...).

    Refuses any feed carrying a DOCTYPE/ENTITY declaration before parsing -- a
    cheap guard against XXE / billion-laughs entity-expansion in the stdlib XML
    parser, since the feed body is external."""
    head = text[:4000].lower()
    if "<!doctype" in head or "<!entity" in text.lower():
        raise EventSourceError("feed declares a DOCTYPE/ENTITY (refused for safety)")
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise EventSourceError(f"feed is not valid XML: {e}") from e
    items: list[dict] = []
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):   # RSS <item> / Atom <entry>
            continue
        d: dict = {}
        for child in node:
            ctag = child.tag.split("}")[-1]
            val = (child.text or child.get("href") or "").strip()
            if ctag == "guid":
                d["id"] = val
            elif ctag in ("title", "link", "summary", "description", "id",
                          "published", "updated", "pubDate"):
                d.setdefault(ctag, val)
        d.setdefault("id", d.get("link") or d.get("title") or "")
        items.append(d)
    return items


class RssSource:
    """Poll an RSS or Atom feed; each new ``<item>``/``<entry>`` fires. Dedup is
    by ``id_field`` (default ``id``, sourced from ``<guid>``/``<id>``/link).
    ``config``: ``{url, id_field?, headers?}``."""
    source = "rss"

    def poll(self, config: dict, cursor: str) -> PollResult:
        url, id_field, headers = _base_config(config, "rss")
        items = _parse_feed(_http_get_text(url, headers=headers))
        return diff_new_items(items, cursor, id_field, newest_first=True)


register("rss", RssSource)


# ---- OAuth-authenticated sources -------------------------------------------
#
# A polled source that authenticates with a live OAuth token pulled from the
# sealed per-tenant vault (never a token in the trigger config), refreshed on
# demand. The token lives in the vault under a ``provider`` key; the trigger
# stores only that reference. Covers the large class of bearer-auth REST feeds
# (Slack, Gmail, GitHub, most SaaS APIs) with one connector, plus a couple of
# named native conveniences on top.


def _oauth_refresher(config: dict):
    """A ``refresher(record) -> token_dict`` for ``vault.access_token``, or
    ``None`` when the trigger supplied no refresh config (then a stored,
    still-valid token is used as-is). ``client_secret`` is read from an env-var
    name (``client_secret_env``), never stored in the trigger config."""
    token_url = str((config or {}).get("token_url") or "").strip()
    client_id = str((config or {}).get("client_id") or "").strip()
    if not (token_url and client_id):
        return None
    secret_env = str((config or {}).get("client_secret_env") or "").strip()

    def _refresh(record: dict) -> dict:
        import os

        from .tools.oauth_helper import _post_form  # SSRF + https-guarded POST
        data = {"grant_type": "refresh_token", "client_id": client_id,
                "refresh_token": str(record.get("refresh_token") or "")}
        if secret_env and os.environ.get(secret_env):
            data["client_secret"] = os.environ[secret_env]
        try:
            return _post_form(token_url, data)
        except Exception as e:
            raise EventSourceError(f"OAuth token refresh failed: {type(e).__name__}") from e
    return _refresh


def _oauth_bearer(config: dict) -> str:
    """Resolve a live Bearer token for an OAuth'd source from the sealed vault
    (``provider`` key), refreshing if the trigger supplied refresh config.
    Raises when the vault is off or no usable token exists."""
    provider = str((config or {}).get("provider") or "").strip()
    if not provider:
        raise EventSourceError("oauth source needs a 'provider' (the vault key)")
    try:
        from . import oauth_vault
    except Exception as e:  # pragma: no cover -- vault module unavailable
        raise EventSourceError("OAuth vault unavailable") from e
    if not oauth_vault.enabled():
        raise EventSourceError("OAuth vault is off ([oauth] vault / MAVERICK_OAUTH_VAULT)")
    token = oauth_vault.get_vault().access_token(
        provider, refresher=_oauth_refresher(config))
    if not token:
        raise EventSourceError(
            f"no valid OAuth token for provider {provider!r}; authorize it first "
            "(and set token_url + client_id to auto-refresh an expired one)")
    return token


class OAuthHttpJsonSource(HttpJsonSource):
    """Poll a Bearer-authenticated JSON HTTP endpoint. Same as ``http_json`` but
    the ``Authorization`` header is a live OAuth token from the sealed vault
    (``provider``) rather than a static header -- so long-lived polling of
    Slack/Gmail/GitHub/most SaaS REST APIs works with token refresh. ``config``:
    ``{provider, url, items_path?, id_field?, newest_first?, token_url?,
    client_id?, client_secret_env?, headers?}``."""
    source = "oauth_http_json"

    def _extra_headers(self, config: dict) -> dict:
        return {"Authorization": f"Bearer {_oauth_bearer(config)}"}


register("oauth_http_json", OAuthHttpJsonSource)


class GithubIssuesSource:
    """Poll a GitHub repo's issues; each newly-opened issue fires (dedup by
    number). ``config``: ``{owner, repo, state?, provider?}``. Auth: a vaulted
    OAuth token when ``provider`` is set, else ``GITHUB_TOKEN`` env, else
    anonymous (public repos, rate-limited). A named native convenience over
    ``oauth_http_json`` so a trigger needs no GitHub URL knowledge."""
    source = "github_issues"

    def poll(self, config: dict, cursor: str) -> PollResult:
        owner = str((config or {}).get("owner") or "").strip()
        repo = str((config or {}).get("repo") or "").strip()
        if not (owner and repo):
            raise EventSourceError("github_issues source needs 'owner' and 'repo'")
        state = str((config or {}).get("state") or "open")
        from .tools.github_issues import _headers, _list_url
        headers = _headers()  # GITHUB_TOKEN or anonymous
        if str((config or {}).get("provider") or "").strip():
            headers = {**headers, "Authorization": f"Bearer {_oauth_bearer(config)}"}
        items = _http_get_json(_list_url(owner, repo, state, 50), headers=headers)
        if not isinstance(items, list):
            raise EventSourceError("GitHub did not return an issue array")
        # The issues endpoint also lists PRs; drop them. Dedup by issue number.
        items = [it for it in items if isinstance(it, dict) and "pull_request" not in it]
        return diff_new_items(items, cursor, "number", newest_first=True)


register("github_issues", GithubIssuesSource)


def poll_source(source: str, config: dict, cursor: str) -> PollResult:
    """Poll one named source; raises :class:`EventSourceError` on trouble."""
    return get_source(source).poll(config or {}, cursor or "")


__all__ = [
    "EventSource", "EventSourceError", "PollResult",
    "available_sources", "get_source", "register", "enabled",
    "poll_source", "diff_new_items", "HttpJsonSource", "RssSource",
    "OAuthHttpJsonSource", "GithubIssuesSource",
]
