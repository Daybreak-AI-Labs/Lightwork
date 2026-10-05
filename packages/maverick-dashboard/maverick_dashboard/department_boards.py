"""Owned board projections for current department grants; no company rollups."""
from __future__ import annotations

import hmac
import math
import time
from collections import Counter
from types import SimpleNamespace

from fastapi import HTTPException
from maverick.governed_operations import digest

from . import auth
from ._shared import _world
from .draft_api import context_for

SCOPED_BOARDS = frozenset({"overview", "spend"})
_DAYS = (30, 90, 180, 365)
_LIMIT = 100_000
SCOPE_NOTICE = (
    "Your own tasks in the departments you can access, plus your general tasks. "
    "Other people's work and inaccessible departments are excluded. These are not company totals."
)
COST_NOTICE = (
    "Known runtime prices only; missing or unsupported prices are counted separately, not treated as free. "
    "Runtime prices can be measured, estimated or bounded. They are not verified provider invoices "
    "and exclude subscriptions, taxes, outside tools and unreconciled adjustments."
)


def scoped(request) -> bool:
    return auth.caller_suites(request) is not None


def context(request) -> str:
    allowed = auth.caller_suites(request)
    return digest({"account_workspace": context_for(request), "role": auth.caller_role(request),
                   "departments": sorted(allowed) if allowed is not None else None})


def check_context(request) -> str:
    auth.require_permission(request, "view")
    current = context(request)
    supplied = request.headers.get("X-Lightwork-Board-Context")
    if supplied is not None and not hmac.compare_digest(supplied, current):
        raise HTTPException(409, "Your account, workspace or department access changed. Reload this page before reading this board.")
    return current


def _permitted(request, row, cache):
    # Exact owner is already in the SQL predicate. Use the native content gate
    # as well, including inherited and removed-pack department restrictions.
    domain = str(row["domain"] or "")
    if domain not in cache:
        cache[domain] = auth.can_access_goal(request, SimpleNamespace(
            id=row["id"], owner=row["owner"], domain=domain))
    return cache[domain]


def _rows(world, sql, values):
    rows = world.query(sql + " LIMIT ?", (*values, _LIMIT + 1))
    if len(rows) > _LIMIT:
        raise HTTPException(503, "This board is too large to read safely right now. Your permitted tasks remain available in Chat.")
    return rows


def _population(request, world, owner):
    columns = ("id", "status", "created_at", "updated_at", "domain", "owner")
    rows = _rows(world, "SELECT " + ",".join(columns) + " FROM goals WHERE owner = ? ORDER BY id", (owner,))
    cache = {}
    return [row for values in rows if _permitted(request, row := dict(zip(columns, values, strict=True)), cache)]


def _recent(request, world, goals):
    rows = []
    for source in sorted(goals, key=lambda row: row["id"], reverse=True)[:12]:
        goal = world.get_goal(source["id"])
        if auth.can_access_goal(request, goal):
            rows.append({"id": goal.id, "title": goal.title, "status": goal.status, "updated_at": goal.updated_at})
    return rows


def _overview(request, world, goals, days):
    from maverick.departments import department_title
    from maverick.domain import suites_for_domain

    from .api import _board_day_list

    dates, since, previous = _board_day_list(days)
    daily = {day.isoformat(): {"d": day.isoformat(), "started": 0, "delivered": 0} for day in dates}
    by_status = dict(Counter(row["status"] for row in goals))
    now = time.time()
    def count(column, lo, hi, done=False):
        return sum(lo <= row[column] < hi and (not done or row["status"] == "done") for row in goals)
    domains = {}
    from datetime import datetime, timezone
    for row in goals:
        for column, key in (("created_at", "started"), ("updated_at", "delivered")):
            if key == "delivered" and row["status"] != "done":
                continue
            day = datetime.fromtimestamp(row[column], timezone.utc).date().isoformat()
            if day in daily:
                daily[day][key] += 1
        suites = suites_for_domain(row["domain"]) if row["domain"] else frozenset()
        label = ", ".join(department_title(key) for key in sorted(suites)) if suites else "General work"
        group = domains.setdefault(label, {"label": label, "total": 0, "done": 0})
        group["total"] += 1
        group["done"] += row["status"] == "done"
    return {"totals": {"total": len(goals), "active": by_status.get("active", 0),
                       "done": by_status.get("done", 0), "blocked": by_status.get("blocked", 0)},
            "by_status": by_status,
            "window": {"started": count("created_at", since, now), "delivered": count("updated_at", since, now, True),
                       "started_prev": count("created_at", previous, since), "delivered_prev": count("updated_at", previous, since, True)},
            "daily": list(daily.values()), "domains": sorted(domains.values(), key=lambda row: row["total"], reverse=True),
            "recent": _recent(request, world, goals)}


def _spend(request, world, owner, days):
    from datetime import datetime, timezone

    from maverick.workforce_value import _is_done
    from maverick.world_model import _dec_field

    from .api import _board_day_list

    dates, since, previous = _board_day_list(days)
    columns = ("episode_id", "goal_id", "started_at", "ended_at", "outcome", "cost_dollars",
               "input_tokens", "output_tokens", "tool_calls", "domain", "owner")
    rows = _rows(world, "SELECT e.id,e.goal_id,e.started_at,e.ended_at,e.outcome,e.cost_dollars,"
                 "e.input_tokens,e.output_tokens,e.tool_calls,g.domain,g.owner "
                 "FROM episodes e JOIN goals g ON e.goal_id=g.id WHERE g.owner=? ORDER BY e.id", (owner,))
    cache, episodes = {}, []
    for values in rows:
        row = dict(zip(columns, values, strict=True))
        if _permitted(request, {**row, "id": row["goal_id"]}, cache):
            row["outcome"] = _dec_field(row["outcome"])
            try:
                price = float(row["cost_dollars"])
                row["cost_dollars"] = price if math.isfinite(price) and price >= 0 else None
            except (TypeError, ValueError, OverflowError):
                row["cost_dollars"] = None
            episodes.append(row)
    now = time.time()
    finished = [row for row in episodes if row["ended_at"] is not None]
    selected = [row for row in finished if since <= row["started_at"] < now]
    def add_price(left, right):
        value = left + right
        if not math.isfinite(value):
            raise HTTPException(503, "These recorded prices cannot be totaled safely. Review the task cost records before using this report.")
        return value
    def total(values):
        amount = 0.0
        for row in values:
            amount = add_price(amount, row["cost_dollars"] or 0)
        return {"dollars": amount,
                "runs": len(values), "unknown_prices": sum(row["cost_dollars"] is None for row in values),
                **{key: sum(int(row[key] or 0) for row in values) for key in ("input_tokens", "output_tokens", "tool_calls")}}
    daily = {day.isoformat(): {"d": day.isoformat(), "cost": 0.0, "runs": 0, "unknown_prices": 0} for day in dates}
    top = {}
    for row in selected:
        day = datetime.fromtimestamp(row["started_at"], timezone.utc).date().isoformat()
        bucket = daily.get(day)
        if bucket is not None:
            bucket["runs"] += 1
            bucket["cost"] = add_price(bucket["cost"], row["cost_dollars"] or 0)
            bucket["unknown_prices"] += row["cost_dollars"] is None
        group = top.setdefault(row["goal_id"], {"goal_id": row["goal_id"], "cost": 0.0, "runs": 0, "unknown_prices": 0})
        group["cost"] = add_price(group["cost"], row["cost_dollars"] or 0)
        group["runs"] += 1
        group["unknown_prices"] += row["cost_dollars"] is None
    top_rows = []
    for row in sorted(top.values(), key=lambda value: value["cost"], reverse=True)[:8]:
        goal = world.get_goal(row["goal_id"])
        if auth.can_access_goal(request, goal):
            top_rows.append({**row, "title": goal.title})
    outcomes = {"success": 0, "failed": 0, "running": 0}
    for row in episodes:
        if since <= row["started_at"] < now:
            outcomes["running" if row["ended_at"] is None else "success" if _is_done(row["outcome"]) else "failed"] += 1
    recent = [{key: row[key] for key in columns[:9]} for row in sorted(episodes, key=lambda row: row["episode_id"], reverse=True)[:50]]
    for row in recent:
        row["id"] = row.pop("episode_id")
    return {"totals": total(selected), "all_time": total(finished),
            "prev": total([row for row in finished if previous <= row["started_at"] < since]),
            "daily": list(daily.values()), "outcomes": outcomes,
            "outcomes_population": "Your permitted attempts started in this period. A task can have several attempts; running attempts are excluded from finished-attempt cost totals.",
            "per_run": [{"id": row["id"], "goal_id": row["goal_id"], "cost": row["cost_dollars"]} for row in recent],
            "top_goals": top_rows, "by_platform": [], "episodes": recent, "cost_notice": COST_NOTICE,
            "_read_domains": [domain for domain, admitted in cache.items() if admitted]}


def build(request, board: str, days: int | None = None) -> dict:
    original_context = check_context(request)
    if board not in SCOPED_BOARDS or not scoped(request):
        raise HTTPException(403, "This department board is unavailable for your current access. Reload to open your current board.")
    owner = auth.goal_owner_filter(request)
    if owner is None:
        raise HTTPException(403, "Sign in to read your permitted work.")
    days = days if days in _DAYS else 90
    world = _world()
    goals = _population(request, world, owner) if board == "overview" else None
    payload = _overview(request, world, goals, days) if board == "overview" else _spend(request, world, owner, days)
    admitted_domains = {row["domain"] or "" for row in goals} if goals is not None else payload.pop("_read_domains")
    # A fork can acquire an inherited department or a pack can disappear while
    # the grants remain unchanged. Check the native mapping again at release.
    if any(not auth.domain_suites_allowed(request, domain) for domain in admitted_domains):
        raise HTTPException(409, "A specialist's department access changed while this report was read. Reload this page to read current permitted work.")
    if not hmac.compare_digest(original_context, check_context(request)):
        raise HTTPException(409, "Your account, workspace or department access changed. Reload this page.")
    payload.update(board=board, days=days, scope="permitted_own", scope_notice=SCOPE_NOTICE,
                   context=original_context, generated_at=time.time())
    return payload


def page(request, board: str):
    from .app import templates
    if board not in SCOPED_BOARDS or not scoped(request):
        raise HTTPException(404, "This board is unavailable.")
    return templates.TemplateResponse(request, "department_board.html",
        {"board": board, "board_context": check_context(request), "scope_notice": SCOPE_NOTICE,
         "cost_notice": COST_NOTICE})
