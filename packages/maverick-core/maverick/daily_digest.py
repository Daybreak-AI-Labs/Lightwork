"""The personal daily summary: the same content as the My work page.

:func:`compose_digest` gathers one person's pending approvals, open
questions an agent is waiting on, recent results and this month's spending.
The dashboard page ``/me`` shows it; :func:`run_digest_cycle` (driven by
``maverick_dashboard.daily_digest_scheduler``) sends it once a day at the
person's own hour in their own time zone through their linked channels:

* Slack DM as Block Kit (``maverick.slack_interactive``),
* email through the platform SMTP backend (``maverick.notifications``),
* any sender another adapter registers with :func:`register_digest_sender`
  (the Teams adapter hooks in here).

An empty summary is skipped unless the person opted into "send even when
empty"; a person can turn the summary off on ``/me``. Every send is written
to the audit log with the recipient, the channel and item counts only.
"""
from __future__ import annotations

import html
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

RECENT_DAYS = 7
MAX_ITEMS = 25
_STATUS_LABELS = {
    "done": "Finished", "blocked": "Needs attention", "failed": "Did not finish",
    "cancelled": "Cancelled", "active": "In progress", "pending": "Waiting to start",
}
_senders: dict[str, Callable[..., bool]] = {}
_senders_lock = threading.Lock()
DigestSender = Callable[[str, dict[str, Any], list[dict[str, Any]], dict[str, Any]], bool]


def _zone(name: str):
    from zoneinfo import ZoneInfo

    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # noqa: BLE001 -- an unknown zone falls back to UTC
        return timezone.utc


def month_start(now: float, time_zone: str) -> float:
    local = datetime.fromtimestamp(now, tz=_zone(time_zone))
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _approvals(world, principal: str, *, can_decide: bool, now: float) -> list[dict[str, Any]]:
    from .channel_approvals import card_content

    rows = []
    for row in world.pending_approvals()[:500]:
        mine = row.requested_by == principal
        if can_decide and not mine:
            # The queue the person may decide: not claimed by somebody else,
            # not already voted on by them (mirrors the Action Center).
            if row.claimed_by and row.claimed_by != principal:
                continue
            state = world.approval_state(row.id) or {}
            if state.get("status") != "pending" or principal in (state.get("approvers") or []):
                continue
        elif not mine:
            continue
        content = card_content(row, now=now)
        content["waiting_on"] = "someone else" if (mine or not can_decide) else "you"
        content["requested_at"] = float(row.requested_at or 0)
        rows.append(content)
        if len(rows) >= MAX_ITEMS:
            break
    return rows


def _results(world, principal: str, *, owner_filter: str | None, now: float) -> list[dict[str, Any]]:
    since = now - RECENT_DAYS * 86400
    out = []
    for goal in world.list_goals(owner=owner_filter, limit=200, order="desc"):
        if goal.status in ("active", "pending") or float(goal.updated_at or 0) < since:
            continue
        summary = " ".join(str(goal.result or "").split())
        out.append({
            "id": goal.id, "title": str(goal.title)[:200], "status": goal.status,
            "status_label": _STATUS_LABELS.get(goal.status, goal.status.replace("_", " ")),
            "updated_at": float(goal.updated_at or 0),
            "summary": summary[:240] + ("…" if len(summary) > 240 else ""),
            "href": f"/chat/goal/{goal.id}",
        })
        if len(out) >= MAX_ITEMS:
            break
    return out


def _questions(world, principal: str, *, owner_filter: str | None) -> list[dict[str, Any]]:
    out = []
    goals: dict[int, Any] = {}
    for question in world.open_questions()[:500]:
        goal = None
        if question.goal_id is not None:
            if question.goal_id not in goals:
                goals[question.goal_id] = world.get_goal(question.goal_id)
            goal = goals[question.goal_id]
        owner = getattr(goal, "owner", "") if goal is not None else ""
        if owner_filter is not None and owner != principal:
            continue
        out.append({
            "id": question.id, "goal_id": question.goal_id,
            "goal_title": str(getattr(goal, "title", "") or "")[:200],
            "question": " ".join(str(question.question or "").split())[:400],
            "asked_at": float(question.asked_at or 0),
            "href": f"/chat/goal/{question.goal_id}" if question.goal_id else "/chat",
        })
        if len(out) >= MAX_ITEMS:
            break
    return out


def compose_digest(world, principal: str, *, can_decide: bool, sees_everything: bool = False,
                   now: float | None = None, time_zone: str = "UTC",
                   spend_limit: float | None = None) -> dict[str, Any]:
    """Everything one person has waiting, as plain data.

    ``can_decide`` (the ``operate`` permission) shows the approvals queue they
    may decide; without it only their own requests appear. ``sees_everything``
    (a break-glass administrator) lifts the owner filter on results, questions
    and spending, exactly like the Goals page.
    """
    current = time.time() if now is None else float(now)
    owner_filter = None if sees_everything else principal
    if spend_limit is None:
        try:
            from .config import get_my_work

            spend_limit = float(get_my_work().get("monthly_spend_limit_dollars") or 0)
        except Exception:  # noqa: BLE001 -- an unreadable config shows no limit
            spend_limit = 0.0
    start = month_start(current, time_zone)
    try:
        spend = world.total_spend(owner=owner_filter, since=start)
    except Exception:  # noqa: BLE001 -- the page still renders without the figure
        log.debug("digest spend lookup failed", exc_info=True)
        spend = {"dollars": 0.0, "runs": 0}
    local = datetime.fromtimestamp(current, tz=_zone(time_zone))
    digest = {
        "principal": principal,
        "generated_at": current,
        "date_label": local.strftime("%A %d %B %Y"),
        "local_date": local.strftime("%Y-%m-%d"),
        "approvals": _approvals(world, principal, can_decide=can_decide, now=current),
        "results": _results(world, principal, owner_filter=owner_filter, now=current),
        "questions": _questions(world, principal, owner_filter=owner_filter),
        "spending": {
            "dollars": round(float(spend.get("dollars") or 0), 4),
            "runs": int(spend.get("runs") or 0),
            "limit_dollars": float(spend_limit or 0),
            "month_label": local.strftime("%B %Y"),
            "over_limit": bool(spend_limit and float(spend.get("dollars") or 0) > spend_limit),
        },
    }
    digest["counts"] = {
        "approvals": len(digest["approvals"]),
        "results": len(digest["results"]),
        "questions": len(digest["questions"]),
    }
    digest["empty"] = not any(digest["counts"].values())
    return digest


# ----------------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------------

def _spend_line(spend: dict[str, Any]) -> str:
    limit = spend.get("limit_dollars") or 0
    line = f"Spending this month: ${float(spend.get('dollars') or 0):.2f}"
    if limit:
        line += f" of your ${float(limit):.2f} monthly limit"
        if spend.get("over_limit"):
            line += " (over the limit)"
    return line + "."


def render_text(digest: dict[str, Any], *, dashboard_url: str = "") -> str:
    lines = [f"Your Lightwork summary for {digest['date_label']}", ""]
    if digest["approvals"]:
        lines.append(f"{len(digest['approvals'])} waiting for your decision:")
        lines += [f"  - #{a['id']} {a['action']} ({a['risk_label']})" for a in digest["approvals"]]
        lines.append("")
    if digest["questions"]:
        lines.append(f"{len(digest['questions'])} question(s) an agent is waiting on:")
        lines += [f"  - {q['question']}" for q in digest["questions"]]
        lines.append("")
    if digest["results"]:
        lines.append(f"{len(digest['results'])} recent result(s):")
        lines += [f"  - {r['title']} ({r['status_label']})" for r in digest["results"]]
        lines.append("")
    if digest["empty"]:
        lines += ["Nothing is waiting for you today.", ""]
    lines.append(_spend_line(digest["spending"]))
    if dashboard_url:
        lines += ["", f"Open My work: {dashboard_url.rstrip('/')}/me"]
    return "\n".join(lines)


def render_html(digest: dict[str, Any], *, dashboard_url: str = "") -> str:
    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    def section(title: str, items: list[str]) -> str:
        if not items:
            return ""
        rows = "".join(f"<li>{item}</li>" for item in items)
        return f"<h2 style=\"font-size:16px;margin:18px 0 6px\">{esc(title)}</h2><ul>{rows}</ul>"

    body = [f"<h1 style=\"font-size:20px\">Your Lightwork summary</h1><p>{esc(digest['date_label'])}</p>"]
    body.append(section(f"{len(digest['approvals'])} waiting for your decision", [
        f"#{a['id']} {esc(a['action'])} <em>({esc(a['risk_label'])})</em>" for a in digest["approvals"]]))
    body.append(section("Questions an agent is waiting on", [esc(q["question"]) for q in digest["questions"]]))
    body.append(section("Recent results", [
        f"{esc(r['title'])} <em>({esc(r['status_label'])})</em>" for r in digest["results"]]))
    if digest["empty"]:
        body.append("<p>Nothing is waiting for you today.</p>")
    body.append(f"<p>{esc(_spend_line(digest['spending']))}</p>")
    if dashboard_url:
        link = esc(dashboard_url.rstrip("/") + "/me")
        body.append(f"<p><a href=\"{link}\">Open My work</a></p>")
    return ("<!doctype html><html lang=\"en\"><body style=\"font-family:sans-serif;"
            "max-width:640px;margin:auto;color:#222\">" + "".join(body) + "</body></html>")


# ----------------------------------------------------------------------------
# Sending
# ----------------------------------------------------------------------------

def register_digest_sender(channel: str, sender: DigestSender | None) -> None:
    """Register (or with ``None`` remove) the sender for ``channel``.

    A sender is ``fn(principal, digest, links, prefs) -> bool`` where ``links``
    are that channel's linked accounts for the person. The Teams adapter
    registers ``"teams"`` here; ``"slack"`` and ``"email"`` are built in.
    """
    with _senders_lock:
        if sender is None:
            _senders.pop(channel, None)
        else:
            _senders[channel] = sender


def dashboard_url() -> str:
    try:
        from .env_setting import env_or_config

        return str(env_or_config("MAVERICK_PUBLIC_BASE_URL", "channels", "public_base_url", "") or "").strip()
    except Exception:  # noqa: BLE001 -- no link is better than a wrong one
        return ""


def _slack_bot_token() -> str:
    import os

    try:
        from .config import load_config

        section = (load_config().get("channels") or {}).get("slack") or {}
    except Exception:  # noqa: BLE001 -- fall back to the environment
        section = {}
    from .notifications import _resolve_env_ref

    saved = _resolve_env_ref(section.get("bot_token")) if isinstance(section.get("bot_token"), str) else None
    return (os.environ.get("SLACK_BOT_TOKEN") or saved or "").strip()


def _send_slack(principal: str, digest: dict[str, Any], links: list[dict[str, Any]],
                prefs: dict[str, Any]) -> bool:
    from .slack_interactive import digest_blocks, send_dm

    token = _slack_bot_token()
    if not token or not links:
        return False
    base = dashboard_url()
    text = render_text(digest, dashboard_url=base)
    blocks = digest_blocks(digest, dashboard_url=base)
    return any(send_dm(token, link["external_id"], text, blocks) for link in links)


def _send_email(principal: str, digest: dict[str, Any], links: list[dict[str, Any]],
                prefs: dict[str, Any]) -> bool:
    from .notifications import send_email

    addresses = [link["external_id"] for link in links] or (
        [prefs["email"]] if prefs.get("email") else [])
    if not addresses:
        return False
    base = dashboard_url()
    subject = f"Your Lightwork summary for {digest['date_label']}"
    return any(send_email(address, subject, render_text(digest, dashboard_url=base),
                          html_body=render_html(digest, dashboard_url=base))
               for address in addresses)


def digest_senders() -> dict[str, DigestSender]:
    with _senders_lock:
        table = dict(_senders)
    table.setdefault("slack", _send_slack)
    table.setdefault("email", _send_email)
    return table


def send_digest(principal: str, digest: dict[str, Any], *, links: list[dict[str, Any]],
                prefs: dict[str, Any]) -> list[dict[str, Any]]:
    """Send through every channel the person has linked; audit each attempt."""
    from . import audit

    by_channel: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        by_channel.setdefault(link["channel"], []).append(link)
    if prefs.get("email") and "email" not in by_channel:
        by_channel["email"] = []
    outcomes = []
    for channel, sender in digest_senders().items():
        if channel not in by_channel:
            continue
        try:
            ok = bool(sender(principal, digest, by_channel[channel], prefs))
        except Exception:  # noqa: BLE001 -- one channel failing never blocks the others
            log.warning("digest send through %s failed", channel, exc_info=True)
            ok = False
        audit.audit_event(
            audit.EventKind.DIGEST_SENT, agent="system:daily-digest", recipient=principal,
            channel=channel, ok=ok, **digest["counts"],
        )
        outcomes.append({"channel": channel, "ok": ok})
    return outcomes


# ----------------------------------------------------------------------------
# Scheduling
# ----------------------------------------------------------------------------

def due_now(prefs: dict[str, Any], *, now: float | None = None) -> tuple[bool, str]:
    """Whether the person's summary is due at ``now`` and their local date.

    Due when it is their chosen hour (or later, so a paused scheduler still
    sends once it wakes) and nothing has gone out yet for that local date.
    """
    current = time.time() if now is None else float(now)
    local = datetime.fromtimestamp(current, tz=_zone(str(prefs.get("time_zone") or "UTC")))
    local_date = local.strftime("%Y-%m-%d")
    if not prefs.get("digest_enabled", True):
        return False, local_date
    if local.hour < int(prefs.get("digest_hour", 8)):
        return False, local_date
    return prefs.get("last_digest_date") != local_date, local_date


def run_digest_cycle(world, *, principals: list[str], can_decide: Callable[[str], bool],
                     sees_everything: Callable[[str], bool] | None = None,
                     now: float | None = None) -> dict[str, Any]:
    """Send every due summary once. Returns counts for the scheduler's log."""
    from . import channel_identities, user_preferences

    current = time.time() if now is None else float(now)
    summary = {"checked": 0, "sent": 0, "skipped_empty": 0, "skipped_unlinked": 0, "failed": 0}
    for principal in principals:
        summary["checked"] += 1
        prefs = user_preferences.get_preferences(principal)
        due, local_date = due_now(prefs, now=current)
        if not due:
            continue
        links = channel_identities.links_for(principal)
        if not links and not prefs.get("email"):
            summary["skipped_unlinked"] += 1
            user_preferences.mark_digest_sent(principal, local_date)
            continue
        digest = compose_digest(
            world, principal, can_decide=can_decide(principal),
            sees_everything=bool(sees_everything and sees_everything(principal)),
            now=current, time_zone=prefs["time_zone"],
        )
        if digest["empty"] and not prefs.get("send_when_empty"):
            summary["skipped_empty"] += 1
            user_preferences.mark_digest_sent(principal, local_date)
            continue
        outcomes = send_digest(principal, digest, links=links, prefs=prefs)
        if any(o["ok"] for o in outcomes):
            summary["sent"] += 1
        else:
            summary["failed"] += 1
        user_preferences.mark_digest_sent(principal, local_date)
    return summary


__all__ = [
    "RECENT_DAYS",
    "compose_digest",
    "dashboard_url",
    "digest_senders",
    "due_now",
    "month_start",
    "register_digest_sender",
    "render_html",
    "render_text",
    "run_digest_cycle",
    "send_digest",
]
