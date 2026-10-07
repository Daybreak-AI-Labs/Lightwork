"""Alerts when a privacy or governance record is due.

Nothing used to tell anyone that a data-subject request, an incident's
72-hour notification clock, an assessment renewal, a vendor contract renewal
or an approval was about to run out. This module evaluates every such record
in the workspace and sends plain-language alerts through the notification
backends already configured in ``[notifications]`` (ntfy, Pushover, Slack,
Discord today; e-mail or Teams when those backends exist, since delivery goes
through :func:`maverick.notifications.notify`).

Each record gets at most one alert per *state* (``due_soon_7d``,
``due_soon_2d``, ``due_today``, ``overdue`` ...) per deadline. The ledger
(``deadline_alerts`` table in the world database) records what was sent, so a
restart never re-sends and the record page can show its alert history. A
deadline that moves (an extension) is a new deadline and starts over.

Configuration (``~/.maverick/config.toml``)::

    [deadline_alerts]
    enabled = true          # default: on when the privacy register is on
    mode = "immediate"      # or "digest": one message per recipient per cycle
    poll_seconds = 900
    quiet_hours = "22:00-07:00"   # server local time; incidents still go through
    owner = "the privacy owner"   # who is alerted when a record has no assignee

    [deadline_alerts.lead_days]
    dsar = [7, 2]
    assessment = [30, 7]
    vendor = [30, 7]

    [deadline_alerts.lead_hours]
    incident = [24, 4]
    approval = [24, 4]

Routing: a record with an assignee is alerted "for" that person; otherwise
the workspace's privacy/governance owner named above. Per-person channels do
not exist in the notification layer yet, so both go to the workspace's
configured backends with the recipient named in the message; when a
per-recipient backend lands, :func:`deliver` is the single seam to route by
``recipient``.
"""
from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

_DAY = 86_400.0
_HOUR = 3_600.0

SQLITE_SCHEMA: list[str] = [
    "CREATE TABLE IF NOT EXISTS deadline_alerts ("
    " id TEXT PRIMARY KEY, workspace TEXT NOT NULL DEFAULT '',"
    " record_type TEXT NOT NULL, record_id TEXT NOT NULL, state TEXT NOT NULL,"
    " due_at REAL NOT NULL, recipient TEXT NOT NULL DEFAULT '',"
    " recipient_kind TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',"
    " message TEXT NOT NULL DEFAULT '', link TEXT NOT NULL DEFAULT '',"
    " sent_at REAL NOT NULL, backends INTEGER NOT NULL DEFAULT 0,"
    " mode TEXT NOT NULL DEFAULT 'immediate')",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_deadline_alerts_once"
    " ON deadline_alerts(workspace, record_type, record_id, state, due_at)",
    "CREATE INDEX IF NOT EXISTS idx_deadline_alerts_record"
    " ON deadline_alerts(record_type, record_id, sent_at DESC)",
]
POSTGRES_SCHEMA: list[str] = [s.replace(" REAL", " DOUBLE PRECISION") for s in SQLITE_SCHEMA]

# Record types and the plain words the alerts use for them.
RECORD_LABELS: dict[str, str] = {
    "dsar": "Data request",
    "dsar_acknowledgement": "Data request acknowledgement",
    "incident": "Privacy incident (72-hour notification clock)",
    "assessment_renewal": "Assessment renewal",
    "assessment_review": "Assessment review",
    "vendor_contract": "Vendor contract renewal",
    "approval": "Approval",
}
# Which lead-time table a record type reads: days or hours.
_LEAD_FAMILY: dict[str, tuple[str, str]] = {
    "dsar": ("lead_days", "dsar"),
    "dsar_acknowledgement": ("lead_days", "dsar"),
    "incident": ("lead_hours", "incident"),
    "assessment_renewal": ("lead_days", "assessment"),
    "assessment_review": ("lead_days", "assessment"),
    "vendor_contract": ("lead_days", "vendor"),
    "approval": ("lead_hours", "approval"),
}
# Webhook event per record type for a "due soon" or "overdue" state.
_WEBHOOK_EVENTS: dict[str, tuple[str, str]] = {
    "dsar": ("dsar.due_soon", "dsar.overdue"),
    "dsar_acknowledgement": ("dsar.due_soon", "dsar.overdue"),
    "incident": ("incident.due_soon", "incident.due_soon"),
    "assessment_renewal": ("assessment.renewal_due", "assessment.renewal_due"),
    "assessment_review": ("assessment.renewal_due", "assessment.renewal_due"),
    "vendor_contract": ("vendor.contract_renewal_due", "vendor.contract_renewal_due"),
    "approval": ("", "approval.expired"),
}
# What each alert asks its recipient to do (NOT-1: why, what and by when).
RECORD_ACTIONS: dict[str, str] = {
    "dsar": "Respond to the data request",
    "dsar_acknowledgement": "Acknowledge the data request",
    "incident": "Decide whether to notify the regulator and the people affected",
    "assessment_renewal": "Renew the assessment or retire it",
    "assessment_review": "Review the assessment",
    "vendor_contract": "Renew the contract or end it",
    "approval": "Approve or reject the request",
}
# Types whose alerts ignore quiet hours (a statutory clock keeps running).
_ALWAYS_URGENT = frozenset({"incident"})

DEFAULT_LEAD_DAYS: dict[str, list[int]] = {"dsar": [7, 2], "assessment": [30, 7], "vendor": [30, 7]}
DEFAULT_LEAD_HOURS: dict[str, list[int]] = {"incident": [24, 4], "approval": [24, 4]}
DEFAULT_OWNER = "the privacy owner"


# ------------------------------------------------------------------ settings
def _lead_list(raw: object, fallback: list[int], *, maximum: int) -> list[int]:
    if raw is None:
        return list(fallback)
    if isinstance(raw, str):
        raw = [x for x in raw.split(",")]
    if not isinstance(raw, (list, tuple)):
        return list(fallback)
    out: list[int] = []
    for item in raw:
        try:
            value = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 1 <= value <= maximum and value not in out:
            out.append(value)
    return sorted(out, reverse=True) if out else list(fallback)


def _parse_quiet_hours(raw: object) -> tuple[int, int] | None:
    """``"22:00-07:00"`` -> ``(1320, 420)`` minutes; empty -> None."""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        start, end = text.split("-", 1)
        sh, sm = (int(x) for x in start.strip().split(":"))
        eh, em = (int(x) for x in end.strip().split(":"))
    except ValueError:
        log.warning("deadline_alerts: quiet_hours %r is not HH:MM-HH:MM; ignored", text)
        return None
    if not (0 <= sh < 24 and 0 <= eh < 24 and 0 <= sm < 60 and 0 <= em < 60):
        return None
    return sh * 60 + sm, eh * 60 + em


def settings(config: dict | None = None) -> dict[str, Any]:
    """The ``[deadline_alerts]`` table with defaults filled in."""
    if config is None:
        try:
            from .config import load_config
            config = load_config() or {}
        except Exception:
            config = {}
    section = dict(config.get("deadline_alerts") or {})
    enabled = section.get("enabled")
    if enabled is None:
        try:
            from . import privacy_ops
            enabled = privacy_ops.enabled()
        except Exception:
            enabled = False
    mode = str(section.get("mode") or "immediate").strip().lower()
    if mode not in ("immediate", "digest"):
        mode = "immediate"
    try:
        poll = int(section.get("poll_seconds") or 900)
    except (TypeError, ValueError):
        poll = 900
    days = dict(section.get("lead_days") or {})
    hours = dict(section.get("lead_hours") or {})
    return {
        "enabled": bool(enabled),
        "mode": mode,
        "poll_seconds": max(60, min(poll, 86_400)),
        "quiet_hours": str(section.get("quiet_hours") or ""),
        "owner": str(section.get("owner") or DEFAULT_OWNER)[:200],
        "lead_days": {k: _lead_list(days.get(k), v, maximum=365) for k, v in DEFAULT_LEAD_DAYS.items()},
        "lead_hours": {k: _lead_list(hours.get(k), v, maximum=720) for k, v in DEFAULT_LEAD_HOURS.items()},
    }


# ---------------------------------------------------------------- deadlines
@dataclass(frozen=True)
class Deadline:
    record_type: str
    record_id: str
    title: str
    due_at: float
    link: str = ""
    assignee: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Alert:
    deadline: Deadline
    state: str          # due_soon_7d / due_soon_4h / due_today / overdue
    recipient: str
    recipient_kind: str  # assignee / owner
    urgent: bool


def _link(path: str) -> str:
    from .webhook_events import record_link
    return record_link(path)


def _dsar_deadlines(now: float) -> list[Deadline]:
    from . import privacy_ops
    if not privacy_ops.enabled():
        return []
    out: list[Deadline] = []
    for r in privacy_ops.list_dsars():
        if r.get("status") not in ("open", "awaiting_erasure", "verified"):
            continue
        rid = str(r.get("id") or "")
        title = f"{r.get('kind') or 'access'!s} request from {r.get('subject_id') or 'a person'}"
        link = _link(f"/privacy/dsar/{rid}")
        assignee = str(r.get("assignee") or r.get("assigned_to") or "")
        # Per-jurisdiction fields when present (extended_until wins over due_at);
        # the single due_at is what every record carries today.
        due = r.get("extended_until") or r.get("due_at")
        if due:
            out.append(Deadline("dsar", rid, title, float(due), link, assignee,
                                {"kind": r.get("kind"), "jurisdiction": r.get("jurisdiction")}))
        ack = r.get("acknowledge_by")
        if ack and not r.get("acknowledged_at"):
            out.append(Deadline("dsar_acknowledgement", rid, title, float(ack), link, assignee))
    return out


def _incident_deadlines(now: float) -> list[Deadline]:
    from . import privacy_ops
    if not privacy_ops.enabled():
        return []
    out: list[Deadline] = []
    for r in privacy_ops.list_incidents():
        if r.get("status") != "open" or r.get("notification") is not None:
            continue
        rid = str(r.get("id") or "")
        out.append(Deadline("incident", rid, str(r.get("title") or "privacy incident"),
                            float(r.get("notify_deadline_at") or 0), _link("/privacy#incidents"),
                            str(r.get("assignee") or ""), {"severity": r.get("severity")}))
    return [d for d in out if d.due_at > 0]


def _assessment_deadlines(now: float) -> list[Deadline]:
    from . import assessment
    out: list[Deadline] = []
    for s in assessment.list_saved():
        if s.get("status") in ("rejected",):
            continue
        rid = str(s.get("id") or "")
        title = f"{s.get('subject') or 'assessment'} ({s.get('type') or 'assessment'})"
        link = _link(f"/assessments#{rid}")
        assignee = str(s.get("assignee") or "")
        if s.get("renewal_at"):
            out.append(Deadline("assessment_renewal", rid, title, float(s["renewal_at"]), link, assignee))
        if s.get("status") == "approved" and s.get("next_review_at"):
            out.append(Deadline("assessment_review", rid, title, float(s["next_review_at"]), link, assignee))
    return out


def _vendor_deadlines(now: float) -> list[Deadline]:
    from . import vendor_management as vm
    if not vm.enabled():
        return []
    out: list[Deadline] = []
    for v in vm.list_vendors():
        if v.get("status") == "offboarded":
            continue
        vid = str(v.get("id") or "")
        for c in v.get("contracts") or []:
            renews = float(c.get("renews_at") or 0)
            if renews <= 0:
                continue
            kind = str(c.get("kind") or "contract")
            out.append(Deadline("vendor_contract", f"{vid}:{kind}",
                                f"{kind.upper()} with {v.get('name') or vid}", renews,
                                _link(f"/privacy/vendors/{vid}"), str(v.get("owner") or ""),
                                {"vendor_id": vid, "kind": kind}))
    return out


def _approval_deadlines(now: float, world: Any) -> list[Deadline]:
    if world is None:
        return []
    from .approval_cards import ApprovalCardError, decode_approval_detail
    out: list[Deadline] = []
    for appr in world.pending_approvals():
        try:
            card = decode_approval_detail(getattr(appr, "detail", None))
        except ApprovalCardError:
            continue
        if not card or not card.get("expires_at"):
            continue
        out.append(Deadline("approval", str(appr.id), str(getattr(appr, "action", "") or "approval"),
                            float(card["expires_at"]), _link(f"/approvals#approval-{appr.id}"), "",
                            {"risk": getattr(appr, "risk", "")}))
    return out


_COLLECTORS: tuple[tuple[str, Callable[..., list[Deadline]]], ...] = (
    ("dsar", _dsar_deadlines),
    ("incident", _incident_deadlines),
    ("assessment", _assessment_deadlines),
    ("vendor", _vendor_deadlines),
)


def collect(*, now: float | None = None, world: Any = None) -> list[Deadline]:
    """Every open deadline in the workspace. A source that is off or broken
    contributes nothing (and is logged), never an exception."""
    current = time.time() if now is None else float(now)
    found: list[Deadline] = []
    for name, collector in _COLLECTORS:
        try:
            found.extend(collector(current))
        except Exception as exc:
            log.warning("deadline_alerts: %s deadlines unavailable: %s", name, type(exc).__name__)
    try:
        found.extend(_approval_deadlines(current, world))
    except Exception as exc:
        log.warning("deadline_alerts: approval deadlines unavailable: %s", type(exc).__name__)
    return found


# ---------------------------------------------------------------- evaluate
def _same_local_day(a: float, b: float, localtime: Callable[[float], time.struct_time]) -> bool:
    x, y = localtime(a), localtime(b)
    return (x.tm_year, x.tm_yday) == (y.tm_year, y.tm_yday)


def current_state(
    deadline: Deadline, now: float, cfg: dict[str, Any],
    *, localtime: Callable[[float], time.struct_time] = time.localtime,
) -> str | None:
    """The most urgent state the deadline is in right now, or ``None``."""
    remaining = deadline.due_at - now
    if remaining < 0:
        return "overdue"
    family, key = _LEAD_FAMILY.get(deadline.record_type, ("lead_days", "dsar"))
    if family == "lead_days":
        if _same_local_day(deadline.due_at, now, localtime):
            return "due_today"
        # Most urgent lead first: a deadline 1 day out is "2 days", not "7 days".
        for lead in sorted(cfg["lead_days"].get(key) or []):
            if remaining <= lead * _DAY:
                return f"due_soon_{lead}d"
        return None
    for lead in sorted(cfg["lead_hours"].get(key) or []):
        if remaining <= lead * _HOUR:
            return f"due_soon_{lead}h"
    return None


def in_quiet_hours(now: float, cfg: dict[str, Any],
                   *, localtime: Callable[[float], time.struct_time] = time.localtime) -> bool:
    window = _parse_quiet_hours(cfg.get("quiet_hours"))
    if window is None:
        return False
    start, end = window
    local = localtime(now)
    minute = local.tm_hour * 60 + local.tm_min
    if start <= end:
        return start <= minute < end
    return minute >= start or minute < end


def recipient_for(deadline: Deadline, cfg: dict[str, Any]) -> tuple[str, str]:
    if deadline.assignee.strip():
        return deadline.assignee.strip()[:200], "assignee"
    return cfg["owner"], "owner"


def evaluate(
    deadlines: list[Deadline], now: float, cfg: dict[str, Any],
    *, localtime: Callable[[float], time.struct_time] = time.localtime,
) -> list[Alert]:
    alerts: list[Alert] = []
    for d in deadlines:
        state = current_state(d, now, cfg, localtime=localtime)
        if state is None:
            continue
        who, kind = recipient_for(d, cfg)
        urgent = state == "overdue" or d.record_type in _ALWAYS_URGENT
        alerts.append(Alert(d, state, who, kind, urgent))
    return alerts


# ------------------------------------------------------------------ ledger
def _is_sqlite(world: Any) -> bool:
    return hasattr(world, "_writing")


def _rows(world: Any, sql: str, params: tuple = ()) -> list[dict]:
    if not _is_sqlite(world):
        sql = sql.replace("?", "%s")
    return [dict(r) for r in world._read_all(sql, params)]


def _write(world: Any, sql: str, params: tuple = ()) -> None:
    if _is_sqlite(world):
        with world._writing() as conn:
            conn.execute(sql, params)
        return
    with world._tx() as cur:
        cur.execute(sql.replace("?", "%s"), params)


def _workspace() -> str:
    from .webhook_subscriptions import workspace_id
    return workspace_id()


def already_sent(world: Any, alert: Alert) -> bool:
    d = alert.deadline
    rows = _rows(world, "SELECT id FROM deadline_alerts WHERE workspace = ? AND record_type = ?"
                        " AND record_id = ? AND state = ? AND due_at = ? LIMIT 1",
                 (_workspace(), d.record_type, d.record_id, alert.state, float(d.due_at)))
    return bool(rows)


def _record_sent(world: Any, alert: Alert, message: str, *, sent_at: float,
                 backends: int, mode: str) -> dict:
    d = alert.deadline
    alert_id = "dla_" + uuid.uuid4().hex[:16]
    _write(world, "INSERT INTO deadline_alerts(id, workspace, record_type, record_id, state, due_at,"
                  " recipient, recipient_kind, title, message, link, sent_at, backends, mode)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (alert_id, _workspace(), d.record_type, d.record_id, alert.state, float(d.due_at),
            alert.recipient, alert.recipient_kind, d.title[:300], message[:2000], d.link[:1000],
            sent_at, int(backends), mode))
    return {"id": alert_id, "record_type": d.record_type, "record_id": d.record_id,
            "state": alert.state, "due_at": d.due_at, "recipient": alert.recipient,
            "recipient_kind": alert.recipient_kind, "sent_at": sent_at, "backends": backends}


def alerts_for(record_type: str, record_id: str, *, world: Any = None, limit: int = 50) -> list[dict]:
    """What was sent about one record, newest first (the record page's
    "Alerts" section)."""
    if world is None:
        from .world_model import open_world
        world = open_world()
    return _rows(world, "SELECT * FROM deadline_alerts WHERE workspace = ? AND record_type = ?"
                        " AND record_id = ? ORDER BY sent_at DESC LIMIT ?",
                 (_workspace(), str(record_type)[:64], str(record_id)[:128], max(1, min(int(limit), 500))))


def recent_alerts(*, world: Any = None, limit: int = 100) -> list[dict]:
    if world is None:
        from .world_model import open_world
        world = open_world()
    return _rows(world, "SELECT * FROM deadline_alerts WHERE workspace = ? ORDER BY sent_at DESC LIMIT ?",
                 (_workspace(), max(1, min(int(limit), 500))))


# ---------------------------------------------------------------- messages
def _when(due_at: float, now: float, localtime: Callable[[float], time.struct_time]) -> str:
    remaining = due_at - now
    stamp = time.strftime("%d %b %Y %H:%M", localtime(due_at))
    if remaining < 0:
        gone = -remaining
        if gone >= _DAY:
            return f"was due {int(gone // _DAY)} day(s) ago ({stamp})"
        return f"was due {max(1, int(gone // _HOUR))} hour(s) ago ({stamp})"
    if remaining >= 2 * _DAY:
        return f"is due in {int(remaining // _DAY)} days ({stamp})"
    if remaining >= _DAY:
        return f"is due tomorrow ({stamp})"
    return f"is due in {max(1, int(remaining // _HOUR))} hour(s) ({stamp})"


def message_for(alert: Alert, now: float,
                *, localtime: Callable[[float], time.struct_time] = time.localtime) -> tuple[str, str]:
    """``(title, body)`` in plain language, with a link to the record."""
    d = alert.deadline
    label = RECORD_LABELS.get(d.record_type, d.record_type)
    when = _when(d.due_at, now, localtime)
    if alert.state == "overdue":
        title = f"Overdue: {label.lower()} {d.title}"
    elif alert.state == "due_today":
        title = f"Due today: {label.lower()} {d.title}"
    else:
        title = f"Due soon: {label.lower()} {d.title}"
    action = RECORD_ACTIONS.get(d.record_type, "Open the record and act on it")
    why = ("this record is assigned to them" if alert.recipient_kind == "assignee"
           else "no one is assigned to this record")
    body = f"{label}: {d.title} {when}. Needed: {action}. For: {alert.recipient}, because {why}."
    if d.link:
        body += f" Open: {d.link}"
    return title[:200], body


def deliver(
    title: str, body: str, *, urgent: bool, recipient: str,
    notify: Callable[..., int] | None = None,
) -> int:
    """Send one alert through the notification backends. ``recipient`` is the
    seam for per-person routing once a backend supports it; today every
    backend configured for the workspace receives it."""
    del recipient  # routed by name inside the message until backends carry recipients
    send = notify
    if send is None:
        from .notifications import notify as send
    try:
        return int(send(body, title=title, priority="high" if urgent else "default",
                        category="deadline", async_dispatch=False, batch=False) or 0)
    except Exception as exc:
        log.warning("deadline_alerts: notification failed: %s", type(exc).__name__)
        return 0


def _emit_webhook(alert: Alert, world: Any) -> None:
    due_soon_event, overdue_event = _WEBHOOK_EVENTS.get(alert.deadline.record_type, ("", ""))
    event = overdue_event if alert.state == "overdue" else due_soon_event
    if not event:
        return
    from .webhook_events import emit
    d = alert.deadline
    emit(event, record_type=d.record_type, record_id=d.record_id, title=d.title,
         summary={"due_at": d.due_at, "state": alert.state, "assignee": d.assignee, **d.detail},
         link=d.link, payload={"state": alert.state, "due_at": d.due_at,
                               "recipient": alert.recipient, "recipient_kind": alert.recipient_kind},
         world=world)


# --------------------------------------------------------------- run cycle
def run_cycle(
    *,
    now: float | None = None,
    world: Any = None,
    config: dict | None = None,
    notify: Callable[..., int] | None = None,
    localtime: Callable[[float], time.struct_time] = time.localtime,
) -> dict[str, Any]:
    """Evaluate every deadline and send what is new. Returns a summary."""
    cfg = settings(config)
    current = time.time() if now is None else float(now)
    if world is None:
        from .world_model import open_world
        world = open_world()
    summary: dict[str, Any] = {"evaluated": 0, "sent": 0, "held_quiet": 0,
                               "already_sent": 0, "alerts": [], "enabled": cfg["enabled"]}
    if not cfg["enabled"]:
        return summary
    quiet = in_quiet_hours(current, cfg, localtime=localtime)
    pending: list[Alert] = []
    for alert in evaluate(collect(now=current, world=world), current, cfg, localtime=localtime):
        summary["evaluated"] += 1
        if already_sent(world, alert):
            summary["already_sent"] += 1
            continue
        if quiet and not alert.urgent:
            summary["held_quiet"] += 1
            continue
        pending.append(alert)
    if cfg["mode"] == "digest":
        _send_digest(pending, current, cfg, world, notify, localtime, summary)
    else:
        for alert in pending:
            title, body = message_for(alert, current, localtime=localtime)
            fired = deliver(title, body, urgent=alert.urgent, recipient=alert.recipient, notify=notify)
            summary["alerts"].append(_record_sent(world, alert, body, sent_at=current,
                                                  backends=fired, mode="immediate"))
            summary["sent"] += 1
            _emit_webhook(alert, world)
    return summary


def _send_digest(pending, current, cfg, world, notify, localtime, summary) -> None:
    by_recipient: dict[str, list[Alert]] = {}
    for alert in pending:
        by_recipient.setdefault(alert.recipient, []).append(alert)
    for recipient, alerts in by_recipient.items():
        lines = [message_for(a, current, localtime=localtime)[1] for a in alerts]
        body = f"{len(alerts)} deadline(s) need attention. For: {recipient}.\n" + "\n".join(f"- {ln}" for ln in lines)
        urgent = any(a.urgent for a in alerts)
        fired = deliver(f"Deadlines for {recipient}", body, urgent=urgent, recipient=recipient, notify=notify)
        for alert in alerts:
            summary["alerts"].append(_record_sent(world, alert, message_for(alert, current, localtime=localtime)[1],
                                                  sent_at=current, backends=fired, mode="digest"))
            summary["sent"] += 1
            _emit_webhook(alert, world)


__all__ = [
    "Alert", "Deadline", "RECORD_LABELS", "alerts_for", "collect", "current_state",
    "deliver", "evaluate", "in_quiet_hours", "message_for", "recent_alerts",
    "recipient_for", "run_cycle", "settings",
]
