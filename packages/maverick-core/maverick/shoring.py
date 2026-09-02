"""Autonomous Shoring: the engagement book of work + measured service levels.

"Shoring" moves a whole business FUNCTION (accounts payable, tier-1 support,
contract review) onto the governed agentic workforce. A shoring engagement is
sold and run against four things this module makes first-class:

* a **Book of Work** -- the functions in scope, each mapped to an operating
  department, with a scope description, an owner, target service levels, and a
  lifecycle status in {scoping, demonstrating, running, proving};
* an **SLA view** -- each function's target service levels versus the ACTUAL
  measured from real completed work (read straight from the Operating Record,
  :mod:`maverick.operating_record`), returning a met / at-risk / breached
  indicator per SLA;
* an **exception desk** -- the human sign-off queue
  (:meth:`~maverick.world_model.WorldModel.pending_approvals`) filtered to a
  shored function's department, the items awaiting a human for that function.

The book is a small governed store, NOT a world-model migration (migrations
are immutable/checksum-locked): a tenant-scoped JSON file written with the
atomic private-file helpers, mirroring :mod:`maverick.fleet_memory`. SLA and
status computation is PURE over an injected ``world`` -- no LLM, no new run
state, read-only -- so it is trivially testable and never invents a number.

On by default (``[shoring] enable`` / ``MAVERICK_SHORING``): shoring is the
product, not a paid add-on, so there is no entitlement gate -- the knob only
lets an operator turn the surface off.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import env_flag
from .file_lock import atomic_read_text, atomic_write_text, ensure_private_file

log = logging.getLogger(__name__)

#: Lifecycle a shored function moves through: scoped -> demonstrated (the
#: demonstration-driven transition) -> running under SLA -> proving value.
LIFECYCLE = ("scoping", "demonstrating", "running", "proving")

#: The SLA metrics we can MEASURE from the Operating Record. ``direction``
#: says which way is good: ``min`` (deliver at least the target, higher is
#: better -- throughput) or ``max`` (stay under the target, lower is better --
#: exception rate). ``unit`` is display only.
METRICS: dict[str, dict[str, str]] = {
    "throughput_per_day": {"direction": "min", "unit": "/day"},
    "max_exception_rate_pct": {"direction": "max", "unit": "%"},
    "max_turnaround_hours": {"direction": "max", "unit": "h"},
}

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_TEXT = 400
_MAX_SLAS = 12
_MAX_FUNCTIONS = 500
_DEFAULT_WINDOW_DAYS = 90

# SLA banding: a ratio of actual/target. For a ``min`` metric met means the
# ratio is at/above 1.0; for a ``max`` metric met means at/below 1.0. The
# 20%/25% shoulders below give the "at-risk" amber band before "breached".
_AT_RISK_MIN = 0.8   # min metric: >= 80% of target is amber, below is red
_AT_RISK_MAX = 1.25  # max metric: <= 125% of target is amber, above is red


@dataclass
class SLATarget:
    """One named service-level target on a shored function."""
    name: str
    metric: str
    target: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Function:
    """One business function in a shoring engagement's Book of Work."""
    key: str
    name: str
    department: str = ""
    scope: str = ""
    status: str = "scoping"
    owner: str = ""
    slas: list[SLATarget] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["slas"] = [s.to_dict() if isinstance(s, SLATarget) else s for s in self.slas]
        return d


# --------------------------------------------------------------------------- #
# The knob
# --------------------------------------------------------------------------- #
def enabled() -> bool:
    """Whether the shoring surface is on (default ON).

    Mirrors :func:`maverick.fleet_memory.enabled` in shape -- an env override
    (``MAVERICK_SHORING``) wins, else the ``[shoring] enable`` config knob --
    but shoring is the product, so it DEFAULTS ON and has no entitlement gate.
    """
    _v = env_flag("MAVERICK_SHORING")
    if _v is not None:
        return _v
    try:
        from .config import load_config
        section = load_config().get("shoring") or {}
        value = section.get("enable", True)
        return value if isinstance(value, bool) else True
    except Exception:  # pragma: no cover -- never crash on a config read
        return True


# --------------------------------------------------------------------------- #
# The store (tenant-scoped JSON, atomic private write -- NOT a migration)
# --------------------------------------------------------------------------- #
def _dir(tenant: str | None = None) -> Path:
    from .paths import current_tenant_id, data_dir

    # Resolve the active tenant dynamically (like fleet_memory): force the
    # legacy shared root only when no tenant is active, never pin whichever
    # tenant imported this module first.
    tid = tenant if tenant is not None else current_tenant_id()
    return data_dir("shoring", tenant=tid or None)


def _book_path(tenant: str | None = None) -> Path:
    return _dir(tenant) / "book.json"


def _coerce_sla(raw: Any) -> SLATarget | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name", "") or "").strip()[:_MAX_TEXT]
    metric = str(raw.get("metric", "") or "").strip()
    if not name or metric not in METRICS:
        return None
    try:
        target = float(raw.get("target", 0) or 0)
    except (TypeError, ValueError):
        return None
    return SLATarget(name=name, metric=metric, target=target)


def _coerce_function(raw: Any) -> Function | None:
    """Load one stored row defensively -- a malformed row is skipped, never
    fatal, so a hand-edited or partially-written book still opens."""
    if not isinstance(raw, dict):
        return None
    key = str(raw.get("key", "") or "").strip()
    if not _KEY_RE.match(key):
        return None
    name = str(raw.get("name", "") or "").strip()[:_MAX_TEXT]
    if not name:
        return None
    status = str(raw.get("status", "") or "scoping")
    if status not in LIFECYCLE:
        status = "scoping"
    slas: list[SLATarget] = []
    for item in (raw.get("slas") or [])[:_MAX_SLAS]:
        sla = _coerce_sla(item)
        if sla is not None:
            slas.append(sla)
    try:
        updated_at = float(raw.get("updated_at", 0) or 0)
    except (TypeError, ValueError):
        updated_at = 0.0
    return Function(
        key=key, name=name,
        department=str(raw.get("department", "") or "").strip()[:_MAX_TEXT],
        scope=str(raw.get("scope", "") or "").strip()[:_MAX_TEXT],
        status=status,
        owner=str(raw.get("owner", "") or "").strip()[:_MAX_TEXT],
        slas=slas, updated_at=updated_at,
    )


def load(tenant: str | None = None) -> list[Function]:
    """The Book of Work for the active (or given) tenant, newest-edited first.

    Tenant-isolated: two tenants never see each other's book (the path is under
    ``data_dir("shoring", tenant=...)``). A missing or unreadable book is an
    empty book, never an error.
    """
    path = _book_path(tenant)
    if not path.exists():
        return []
    try:
        ensure_private_file(path)
        raw = json.loads(atomic_read_text(path))
    except (OSError, ValueError):
        return []
    rows = raw if isinstance(raw, list) else []
    out: list[Function] = []
    for row in rows:
        fn = _coerce_function(row)
        if fn is not None:
            out.append(fn)
    out.sort(key=lambda f: f.updated_at, reverse=True)
    return out


def save(functions: list[Function], tenant: str | None = None) -> None:
    """Atomically persist the whole book (0600, private parent)."""
    path = _book_path(tenant)
    payload = [f.to_dict() for f in functions[:_MAX_FUNCTIONS]]
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def upsert(function: Function, tenant: str | None = None) -> Function:
    """Create or replace a function by ``key``; returns the stored row.

    Stamps ``updated_at`` so the book orders by most-recently-touched.
    """
    if not _KEY_RE.match(function.key or ""):
        raise ValueError(
            "function key must be a slug ([a-z0-9][a-z0-9_-]{0,63})")
    function.updated_at = time.time()
    book = load(tenant)
    book = [f for f in book if f.key != function.key]
    book.append(function)
    save(book, tenant)
    return function


def remove(key: str, tenant: str | None = None) -> bool:
    """Drop a function from the book. Returns whether a row was removed."""
    book = load(tenant)
    kept = [f for f in book if f.key != key]
    if len(kept) == len(book):
        return False
    save(kept, tenant)
    return True


# --------------------------------------------------------------------------- #
# SLA + status computation (pure, read-only over an injected world)
# --------------------------------------------------------------------------- #
def _measure(department: str, world: Any, *, window_days: int,
             now: float | None = None) -> dict:
    """Delivered / blocked / throughput for one department from the Operating
    Record. Fail-open: an unreadable world measures zero, never raises."""
    from . import operating_record

    delivered = blocked = in_flight = 0
    try:
        records = operating_record.assemble(world, limit=2000)
        goals = operating_record.query(records, department=department, kind="goal")
    except Exception:  # pragma: no cover -- assembly is itself fail-open
        goals = []
    cutoff = (now if now is not None else time.time()) - window_days * 86400
    for r in goals:
        if r.ts and r.ts < cutoff:
            continue
        outcome = (r.outcome or "").lower()
        if outcome == "done":
            delivered += 1
        elif outcome in ("blocked", "cancelled", "failed", "error"):
            blocked += 1
        else:
            in_flight += 1
    completed = delivered + blocked
    return {
        "department": department,
        "window_days": window_days,
        "delivered": delivered,
        "blocked": blocked,
        "in_flight": in_flight,
        "completed": completed,
        "throughput_per_day": (delivered / window_days) if window_days else 0.0,
        "exception_rate_pct": (100.0 * blocked / completed) if completed else 0.0,
    }


def _band(metric: str, target: float, actual: float | None) -> str:
    """met / at_risk / breached / no_data for one SLA."""
    if actual is None:
        return "no_data"
    if target <= 0:
        # A non-positive target can't be measured against meaningfully.
        return "no_data"
    ratio = actual / target
    direction = METRICS.get(metric, {}).get("direction", "min")
    if direction == "max":
        if ratio <= 1.0:
            return "met"
        return "at_risk" if ratio <= _AT_RISK_MAX else "breached"
    # min: higher is better
    if ratio >= 1.0:
        return "met"
    return "at_risk" if ratio >= _AT_RISK_MIN else "breached"


def sla_status(function: Function, world: Any, *,
               window_days: int = _DEFAULT_WINDOW_DAYS,
               now: float | None = None) -> list[dict]:
    """Per-SLA target-vs-actual with a met/at-risk/breached indicator.

    Actuals come from real completed work in the Operating Record, scoped to
    the function's department. A metric with no measurable data (or a function
    with no completed work) reports ``no_data`` rather than a false breach.
    """
    m = _measure(function.department, world, window_days=window_days, now=now)
    out: list[dict] = []
    for sla in function.slas:
        metric = sla.metric
        if metric == "throughput_per_day":
            actual: float | None = m["throughput_per_day"]
        elif metric == "max_exception_rate_pct":
            actual = m["exception_rate_pct"] if m["completed"] else None
        else:
            # Declared but not measured from the record in v1 (e.g. turnaround).
            actual = None
        status = _band(metric, sla.target, actual)
        out.append({
            "name": sla.name,
            "metric": metric,
            "unit": METRICS.get(metric, {}).get("unit", ""),
            "target": round(float(sla.target), 2),
            "actual": round(actual, 2) if actual is not None else None,
            "status": status,
        })
    return out


_STATUS_RANK = {"breached": 3, "at_risk": 2, "met": 1, "no_data": 0}


def _matches_department(approval: Any, department: str) -> bool:
    """Whether a pending approval belongs to a function's department.

    Approvals are not department-tagged in the schema, so match the department
    id (case-insensitive) against the fields that carry routing context --
    scope first (the consent scope), then provenance/action/detail.
    """
    dept = (department or "").strip().lower()
    if not dept:
        return False
    for attr in ("scope", "provenance", "action", "detail"):
        value = getattr(approval, attr, None)
        if isinstance(value, str) and dept in value.lower():
            return True
    return False


def exceptions_for(function: Function, approvals: list) -> list[dict]:
    """The pending approvals awaiting human sign-off for this function.

    ``approvals`` is a list of pending approval rows
    (``world.pending_approvals()``); this is the exception-desk lens over that
    shared queue -- it never forks the approval logic, only filters it.
    """
    out: list[dict] = []
    for a in approvals:
        if not _matches_department(a, function.department):
            continue
        out.append({
            "id": getattr(a, "id", None),
            "action": getattr(a, "action", ""),
            "risk": getattr(a, "risk", ""),
            "scope": getattr(a, "scope", None),
            "detail": getattr(a, "detail", None),
            "requested_at": getattr(a, "requested_at", None),
        })
    return out


def function_status(function: Function, world: Any, *,
                    window_days: int = _DEFAULT_WINDOW_DAYS,
                    now: float | None = None) -> dict:
    """One function's full status card: measured throughput, per-SLA
    indicators, the exception-desk count, and a rolled-up overall indicator
    (the worst SLA band)."""
    m = _measure(function.department, world, window_days=window_days, now=now)
    slas = sla_status(function, world, window_days=window_days, now=now)
    try:
        pending = list(world.pending_approvals())
    except Exception:  # pragma: no cover -- older/stub worlds
        pending = []
    exceptions = exceptions_for(function, pending)
    graded = [s for s in slas if s["status"] != "no_data"]
    overall = max((s["status"] for s in graded),
                  key=lambda s: _STATUS_RANK[s], default="no_data")
    return {
        "key": function.key,
        "name": function.name,
        "department": function.department,
        "scope": function.scope,
        "lifecycle": function.status,
        "owner": function.owner,
        "delivered": m["delivered"],
        "blocked": m["blocked"],
        "in_flight": m["in_flight"],
        "throughput_per_day": round(m["throughput_per_day"], 2),
        "exception_rate_pct": round(m["exception_rate_pct"], 1),
        "window_days": window_days,
        "slas": slas,
        "exceptions": exceptions,
        "exception_count": len(exceptions),
        "overall": overall,
    }


def book_status(world: Any, *, tenant: str | None = None,
                window_days: int = _DEFAULT_WINDOW_DAYS,
                now: float | None = None) -> list[dict]:
    """Every function in the book with its computed status (the page/API feed)."""
    return [
        function_status(fn, world, window_days=window_days, now=now)
        for fn in load(tenant)
    ]


__all__ = [
    "LIFECYCLE", "METRICS", "SLATarget", "Function", "enabled",
    "load", "save", "upsert", "remove",
    "sla_status", "exceptions_for", "function_status", "book_status",
]
