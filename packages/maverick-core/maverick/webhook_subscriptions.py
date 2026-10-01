"""Webhook subscriptions, the delivery log and retries.

Outbound webhooks used to be a list of addresses in ``[webhooks] outbound``
that each event was posted to once: no retry, no record of what was sent.
This module keeps that list working as a *legacy default subscription* and
adds:

* **Subscriptions as records** (``webhook_subscriptions`` table): an address,
  an event filter (``*``, ``dsar.*`` or exact names), a signing secret (sealed
  at rest like other sensitive columns), an on/off switch and a description.
* **A delivery log** (``webhook_deliveries`` table): one row per subscription
  per event, with every attempt's status code, latency and a short response
  snippet, and when the next retry is due.
* **Retries with backoff and jitter**: five attempts over about an hour
  (immediately, then after 1, 5, 15 and 40 minutes, each +/-20 %). A delivery
  that is still failing after the last attempt is ``dead`` (dead-letter) and
  stays in the log until someone presses "Retry now".

Signing is unchanged: ``webhooks.build_headers`` computes the same
``X-Maverick-Signature`` / ``X-Maverick-Timestamp`` pair per attempt, so a
receiver that verifies today's format verifies retried deliveries too.

Addresses are checked with the same SSRF guard the dispatcher already uses
(``tools._ssrf``): a subscription can never point at a loopback, link-local or
private address. Every mutation is written to the audit log first.
"""
from __future__ import annotations

import json
import logging
import random
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Seconds to wait before retry N (attempt N+1); the first attempt is immediate.
RETRY_DELAYS: tuple[int, ...] = (60, 300, 900, 2400)
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1
JITTER = 0.2
RESPONSE_SNIPPET_CHARS = 200
LEGACY_SUBSCRIPTION_ID = "legacy-config"
STATUSES = ("pending", "delivered", "dead")
_MAX_URL = 2048
_MAX_DESCRIPTION = 500
_MAX_EVENTS = 64
_MAX_SECRET = 4096

SQLITE_SCHEMA: list[str] = [
    "CREATE TABLE IF NOT EXISTS webhook_subscriptions ("
    " id TEXT PRIMARY KEY, workspace TEXT NOT NULL DEFAULT '',"
    " url TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',"
    " events TEXT NOT NULL DEFAULT '*', secret TEXT NOT NULL DEFAULT '',"
    " enabled INTEGER NOT NULL DEFAULT 1, created_by TEXT NOT NULL DEFAULT '',"
    " created_at REAL NOT NULL, updated_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS webhook_deliveries ("
    " id TEXT PRIMARY KEY, workspace TEXT NOT NULL DEFAULT '',"
    " subscription_id TEXT NOT NULL, event_id TEXT NOT NULL,"
    " event_type TEXT NOT NULL, url TEXT NOT NULL, body TEXT NOT NULL,"
    " status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,"
    " last_status_code INTEGER, last_latency_ms INTEGER,"
    " last_response TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',"
    " attempt_log TEXT NOT NULL DEFAULT '[]', next_attempt_at REAL,"
    " created_at REAL NOT NULL, updated_at REAL NOT NULL, delivered_at REAL)",
    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_due"
    " ON webhook_deliveries(status, next_attempt_at)",
    "CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_sub"
    " ON webhook_deliveries(subscription_id, created_at DESC)",
]
POSTGRES_SCHEMA: list[str] = [s.replace(" REAL", " DOUBLE PRECISION") for s in SQLITE_SCHEMA]


class SubscriptionError(ValueError):
    """A subscription field was rejected."""


# --------------------------------------------------------------------- store
def _world(world: Any = None) -> Any:
    if world is not None:
        return world
    from .world_model import open_world
    return open_world()


def workspace_id() -> str:
    try:
        from .paths import current_tenant_id
        return current_tenant_id() or ""
    except Exception:
        return ""


def _is_sqlite(world: Any) -> bool:
    return hasattr(world, "_writing")


def _rows(world: Any, sql: str, params: tuple = ()) -> list[dict]:
    if not _is_sqlite(world):
        sql = sql.replace("?", "%s")
    return [dict(r) for r in world._read_all(sql, params)]


def _row(world: Any, sql: str, params: tuple = ()) -> dict | None:
    if not _is_sqlite(world):
        sql = sql.replace("?", "%s")
    found = world._read_one(sql, params)
    return dict(found) if found is not None else None


def _write(world: Any, sql: str, params: tuple = ()) -> None:
    if _is_sqlite(world):
        with world._writing() as conn:
            conn.execute(sql, params)
        return
    with world._tx() as cur:
        cur.execute(sql.replace("?", "%s"), params)


# ---------------------------------------------------------------- validation
def validate_url(url: object) -> str:
    """An https/http address on a public host, or :class:`SubscriptionError`.

    Reuses the dispatcher's SSRF guard (``tools._ssrf.resolve_pinned_ip``) so
    a saved subscription can never reach an internal address."""
    text = str(url or "").strip()
    if not text or len(text) > _MAX_URL:
        raise SubscriptionError("the address must be between 1 and 2048 characters")
    parts = urlsplit(text)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SubscriptionError("the address must start with https:// and name a server")
    if parts.username or parts.password:
        raise SubscriptionError("the address must not carry a user name or password")
    try:
        from .tools._ssrf import BlockedHost, resolve_pinned_ip
    except Exception as exc:  # pragma: no cover - guard always ships with the kernel
        raise SubscriptionError("the address could not be checked") from exc
    try:
        resolve_pinned_ip(parts.hostname)
    except BlockedHost as exc:
        raise SubscriptionError(f"the address is not allowed: {exc}") from exc
    except Exception as exc:
        raise SubscriptionError("the address could not be resolved") from exc
    return text


def normalize_events(events: object) -> list[str]:
    """``"*"``, ``["dsar.*", "incident.opened"]`` or a comma list -> clean list."""
    from .webhook_events import EVENT_TYPES

    if events is None or events == "" or events == "*":
        return ["*"]
    if isinstance(events, str):
        items = [e.strip() for e in events.split(",")]
    elif isinstance(events, (list, tuple)):
        items = [str(e).strip() for e in events]
    else:
        raise SubscriptionError("events must be a list of event names")
    out: list[str] = []
    for item in items:
        if not item:
            continue
        if item == "*":
            return ["*"]
        if item.endswith(".*"):
            prefix = item[:-2]
            if not any(t.startswith(prefix + ".") for t in EVENT_TYPES):
                raise SubscriptionError(f"unknown event family: {item}")
        elif item not in EVENT_TYPES:
            raise SubscriptionError(f"unknown event: {item}")
        if item not in out:
            out.append(item)
    if not out:
        return ["*"]
    if len(out) > _MAX_EVENTS:
        raise SubscriptionError(f"at most {_MAX_EVENTS} events per subscription")
    return out


def matches(events: list[str] | str, event_type: str) -> bool:
    """Does a subscription's filter cover ``event_type``?"""
    if isinstance(events, str):
        try:
            events = json.loads(events) if events.startswith("[") else [events]
        except ValueError:
            events = [events]
    for item in events:
        if item == "*" or item == event_type:
            return True
        if item.endswith(".*") and event_type.startswith(item[:-1]):
            return True
    return False


def _audit(action: str, actor: str, **detail: Any) -> None:
    from .audit import audit_event

    audit_event("webhook_subscription_changed", agent="dashboard", action=action,
                actor=actor or "system", **detail)


def _public(row: dict) -> dict:
    out = dict(row)
    secret = out.pop("secret", "")
    out["secret_set"] = bool(secret)
    out["enabled"] = bool(out.get("enabled"))
    try:
        out["events"] = json.loads(out.get("events") or '["*"]')
    except ValueError:
        out["events"] = ["*"]
    return out


# ------------------------------------------------------------- subscriptions
def create_subscription(
    url: str,
    *,
    events: object = "*",
    secret: str = "",
    description: str = "",
    enabled: bool = True,
    actor: str = "",
    world: Any = None,
    now: float | None = None,
) -> dict:
    from .world_model import _enc_field

    world = _world(world)
    address = validate_url(url)
    filters = normalize_events(events)
    if len(str(secret or "")) > _MAX_SECRET:
        raise SubscriptionError("the secret is too long")
    stamp = time.time() if now is None else float(now)
    sub_id = "whs_" + uuid.uuid4().hex[:16]
    ws = workspace_id()
    _audit("create", actor, subscription_id=sub_id, host=urlsplit(address).hostname,
           events=filters, enabled=bool(enabled))
    _write(
        world,
        "INSERT INTO webhook_subscriptions(id, workspace, url, description, events, secret,"
        " enabled, created_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (sub_id, ws, address, str(description or "")[:_MAX_DESCRIPTION],
         json.dumps(filters), _enc_field(str(secret or "")) or "", 1 if enabled else 0,
         str(actor or "")[:256], stamp, stamp),
    )
    return get_subscription(sub_id, world=world)


def get_subscription(sub_id: str, *, world: Any = None) -> dict | None:
    row = _row(_world(world), "SELECT * FROM webhook_subscriptions WHERE id = ? AND workspace = ?",
               (str(sub_id or "")[:64], workspace_id()))
    return _public(row) if row else None


def list_subscriptions(*, world: Any = None, include_disabled: bool = True) -> list[dict]:
    sql = "SELECT * FROM webhook_subscriptions WHERE workspace = ?"
    if not include_disabled:
        sql += " AND enabled = 1"
    return [_public(r) for r in _rows(_world(world), sql + " ORDER BY created_at", (workspace_id(),))]


def update_subscription(
    sub_id: str,
    *,
    url: str | None = None,
    events: object = None,
    secret: str | None = None,
    description: str | None = None,
    enabled: bool | None = None,
    actor: str = "",
    world: Any = None,
    now: float | None = None,
) -> dict | None:
    from .world_model import _enc_field

    world = _world(world)
    current = get_subscription(sub_id, world=world)
    if current is None:
        return None
    sets: list[str] = []
    values: list[Any] = []
    changed: dict[str, Any] = {}
    if url is not None:
        address = validate_url(url)
        sets.append("url = ?")
        values.append(address)
        changed["host"] = urlsplit(address).hostname
    if events is not None:
        filters = normalize_events(events)
        sets.append("events = ?")
        values.append(json.dumps(filters))
        changed["events"] = filters
    if secret is not None:
        if len(secret) > _MAX_SECRET:
            raise SubscriptionError("the secret is too long")
        sets.append("secret = ?")
        values.append(_enc_field(secret) or "")
        changed["secret_set"] = bool(secret)
    if description is not None:
        sets.append("description = ?")
        values.append(str(description)[:_MAX_DESCRIPTION])
    if enabled is not None:
        sets.append("enabled = ?")
        values.append(1 if enabled else 0)
        changed["enabled"] = bool(enabled)
    if not sets:
        return current
    sets.append("updated_at = ?")
    values.append(time.time() if now is None else float(now))
    _audit("update", actor, subscription_id=current["id"], **changed)
    _write(world, f"UPDATE webhook_subscriptions SET {', '.join(sets)} WHERE id = ? AND workspace = ?",
           (*values, current["id"], workspace_id()))
    return get_subscription(sub_id, world=world)


def _subscription_secret(sub_id: str, world: Any) -> str | None:
    from .world_model import _dec_field

    if sub_id == LEGACY_SUBSCRIPTION_ID:
        from .webhooks import _load_config_outbound
        return _load_config_outbound()[1]
    row = _row(world, "SELECT secret FROM webhook_subscriptions WHERE id = ?", (sub_id,))
    if not row:
        return None
    return _dec_field(row.get("secret") or "") or None


def targets_for(event_type: str, *, world: Any = None) -> list[dict]:
    """Every place ``event_type`` goes: enabled matching subscriptions plus the
    legacy ``[webhooks] outbound`` list (one virtual subscription per address)."""
    out: list[dict] = []
    for sub in list_subscriptions(world=world, include_disabled=False):
        if matches(sub["events"], event_type):
            out.append({"subscription_id": sub["id"], "url": sub["url"]})
    from .webhooks import _load_config_outbound
    urls, _secret = _load_config_outbound()
    for url in urls:
        out.append({"subscription_id": LEGACY_SUBSCRIPTION_ID, "url": url})
    return out


# ---------------------------------------------------------------- deliveries
def retry_delay(attempts_done: int, *, rng: Callable[[], float] = random.random) -> float | None:
    """Seconds until the next attempt after ``attempts_done`` failures, with
    +/-20 % jitter; ``None`` once the schedule is exhausted (dead-letter)."""
    if attempts_done < 1:
        return 0.0
    if attempts_done >= MAX_ATTEMPTS:
        return None
    base = RETRY_DELAYS[attempts_done - 1]
    return base * (1.0 + JITTER * (2.0 * rng() - 1.0))


def _delivery_public(row: dict) -> dict:
    out = dict(row)
    try:
        out["attempt_log"] = json.loads(out.get("attempt_log") or "[]")
    except ValueError:
        out["attempt_log"] = []
    return out


def enqueue(
    event_type: str,
    event_id: str,
    body: bytes,
    *,
    targets: list[dict],
    world: Any = None,
    now: float | None = None,
) -> list[str]:
    """Record one pending delivery per target; returns the delivery ids."""
    world = _world(world)
    stamp = time.time() if now is None else float(now)
    text = body.decode("utf-8")
    ids: list[str] = []
    ws = workspace_id()
    for target in targets:
        delivery_id = "whd_" + uuid.uuid4().hex[:16]
        _write(
            world,
            "INSERT INTO webhook_deliveries(id, workspace, subscription_id, event_id, event_type,"
            " url, body, status, attempts, next_attempt_at, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,'pending',0,?,?,?)",
            (delivery_id, ws, target["subscription_id"], event_id, event_type,
             target["url"], text, stamp, stamp, stamp),
        )
        ids.append(delivery_id)
    return ids


def get_delivery(delivery_id: str, *, world: Any = None) -> dict | None:
    row = _row(_world(world), "SELECT * FROM webhook_deliveries WHERE id = ? AND workspace = ?",
               (str(delivery_id or "")[:64], workspace_id()))
    return _delivery_public(row) if row else None


def list_deliveries(
    *, world: Any = None, subscription_id: str | None = None,
    status: str | None = None, limit: int = 100,
) -> list[dict]:
    sql = "SELECT * FROM webhook_deliveries WHERE workspace = ?"
    params: list[Any] = [workspace_id()]
    if subscription_id:
        sql += " AND subscription_id = ?"
        params.append(subscription_id)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 500)))
    rows = _rows(_world(world), sql, tuple(params))
    return [_delivery_public(r) for r in rows]


def attempt_delivery(
    delivery_id: str,
    *,
    world: Any = None,
    now: float | None = None,
    transport: Callable[..., tuple] | None = None,
    rng: Callable[[], float] = random.random,
) -> dict | None:
    """Run one attempt for a pending delivery and record the outcome.

    ``transport(url, body, headers, timeout)`` returns
    ``(status_code | None, latency_ms, response_snippet, error_text)``; the
    default is the dispatcher's guarded HTTP POST."""
    from .webhooks import _post_result, build_headers

    world = _world(world)
    row = _row(world, "SELECT * FROM webhook_deliveries WHERE id = ?", (delivery_id,))
    if row is None or row["status"] != "pending":
        return _delivery_public(row) if row else None
    stamp = time.time() if now is None else float(now)
    body = str(row["body"]).encode("utf-8")
    secret = _subscription_secret(row["subscription_id"], world)
    headers = build_headers(body, row["event_type"], secret, event_id=row["event_id"])
    send = transport or _post_result
    from .enterprise import enterprise_egress_denial
    deny = enterprise_egress_denial(row["url"], tool="webhook")
    if deny is not None:
        code, latency, snippet, error = None, 0, "", "blocked by the enterprise egress lock"
        exhausted = True
    else:
        try:
            code, latency, snippet, error = send(row["url"], body, headers, 10.0)
        except Exception as exc:  # the transport promises not to raise; belt and braces
            code, latency, snippet, error = None, 0, "", f"{type(exc).__name__}"
        exhausted = False
    attempts = int(row["attempts"] or 0) + 1
    ok = code is not None and code < 400
    try:
        history = json.loads(row.get("attempt_log") or "[]")
    except ValueError:
        history = []
    history.append({"attempt": attempts, "at": stamp, "status_code": code,
                    "latency_ms": latency, "error": (error or "")[:200],
                    "response": (snippet or "")[:RESPONSE_SNIPPET_CHARS]})
    if ok:
        status, next_at, delivered_at = "delivered", None, stamp
    else:
        delay = None if exhausted else retry_delay(attempts, rng=rng)
        if delay is None:
            status, next_at, delivered_at = "dead", None, None
        else:
            status, next_at, delivered_at = "pending", stamp + delay, None
    _write(
        world,
        "UPDATE webhook_deliveries SET status = ?, attempts = ?, last_status_code = ?,"
        " last_latency_ms = ?, last_response = ?, last_error = ?, attempt_log = ?,"
        " next_attempt_at = ?, updated_at = ?, delivered_at = ? WHERE id = ?",
        (status, attempts, code, int(latency or 0), (snippet or "")[:RESPONSE_SNIPPET_CHARS],
         (error or "")[:200], json.dumps(history[-MAX_ATTEMPTS * 2:]), next_at, stamp,
         delivered_at, delivery_id),
    )
    if status == "dead":
        log.warning("webhooks: delivery %s to subscription %s is dead after %d attempts",
                    delivery_id, row["subscription_id"], attempts)
    return get_delivery(delivery_id, world=world)


def process_due(
    *, world: Any = None, now: float | None = None, limit: int = 50,
    transport: Callable[..., tuple] | None = None,
) -> int:
    """Attempt every pending delivery whose retry time has come. Returns the
    number attempted."""
    world = _world(world)
    stamp = time.time() if now is None else float(now)
    rows = _rows(
        world,
        "SELECT id FROM webhook_deliveries WHERE status = 'pending' AND next_attempt_at <= ?"
        " ORDER BY next_attempt_at LIMIT ?",
        (stamp, max(1, int(limit))),
    )
    count = 0
    for row in rows:
        attempt_delivery(row["id"], world=world, now=stamp, transport=transport)
        count += 1
    return count


def retry_now(
    delivery_id: str, *, actor: str = "", world: Any = None, now: float | None = None,
    transport: Callable[..., tuple] | None = None,
) -> dict | None:
    """Put a failed or dead delivery back on the queue and try it immediately."""
    world = _world(world)
    current = get_delivery(delivery_id, world=world)
    if current is None:
        return None
    stamp = time.time() if now is None else float(now)
    _audit("retry_delivery", actor, delivery_id=current["id"],
           subscription_id=current["subscription_id"], event_type=current["event_type"])
    _write(world, "UPDATE webhook_deliveries SET status = 'pending', attempts = 0,"
                  " next_attempt_at = ?, updated_at = ? WHERE id = ?",
           (stamp, stamp, current["id"]))
    return attempt_delivery(current["id"], world=world, now=stamp, transport=transport)


def dispatch(
    event_type: str, envelope: dict, body: bytes, *, world: Any = None,
    now: float | None = None, submit: Callable[[str], None] | None = None,
) -> int:
    """Record deliveries for every target of ``event_type`` and hand each to
    ``submit(delivery_id)`` (the dispatcher's thread pool) for its first
    attempt. Returns how many deliveries were recorded."""
    world = _world(world)
    targets = targets_for(event_type, world=world)
    if not targets:
        return 0
    ids = enqueue(event_type, str(envelope.get("id") or ""), body, targets=targets,
                  world=world, now=now)
    for delivery_id in ids:
        if submit is not None:
            submit(delivery_id)
        else:
            attempt_delivery(delivery_id, world=world, now=now)
    return len(ids)


def send_test_event(
    sub_id: str, *, actor: str = "", world: Any = None, now: float | None = None,
    transport: Callable[..., tuple] | None = None,
) -> dict | None:
    """Post a ``webhook.test`` event to one subscription right away."""
    from .webhooks import build_envelope, encode_body

    world = _world(world)
    sub = get_subscription(sub_id, world=world)
    if sub is None:
        return None
    stamp = time.time() if now is None else float(now)
    envelope = build_envelope(
        "webhook.test", {"subscription_id": sub["id"], "requested_by": actor or "dashboard"},
        record={"type": "webhook_subscription", "id": sub["id"], "title": sub["description"] or sub["url"]},
        occurred_at=stamp,
    )
    _audit("send_test", actor, subscription_id=sub["id"])
    ids = enqueue("webhook.test", envelope["id"], encode_body(envelope),
                  targets=[{"subscription_id": sub["id"], "url": sub["url"]}],
                  world=world, now=stamp)
    return attempt_delivery(ids[0], world=world, now=stamp, transport=transport)


__all__ = [
    "LEGACY_SUBSCRIPTION_ID", "MAX_ATTEMPTS", "RETRY_DELAYS", "SubscriptionError",
    "attempt_delivery", "create_subscription", "dispatch", "get_delivery",
    "get_subscription", "list_deliveries", "list_subscriptions", "matches",
    "normalize_events", "process_due", "retry_delay", "retry_now",
    "send_test_event", "targets_for", "update_subscription", "validate_url",
]
