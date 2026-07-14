"""Tenant lifecycle / provisioning registry — the hosted control plane's roster.

A :class:`Workspace` isolates one tenant's *data*; this registry is the
*operator's* record of which tenants exist, their status (active / suspended),
plan, and per-tenant quota. It lives at the **un-namespaced** root
(``<home>/tenant_registry.json``, ``tenant=None``) because it is cross-tenant
control-plane state, not any one tenant's data.

Lifecycle: ``create`` → ``suspend`` / ``resume`` → ``delete`` (optionally
purging the tenant's data dir). :func:`assert_tenant_active` is the enforcement
hook a request path calls before doing work for a tenant, so a suspended tenant
is refused. The registry is opt-in: with no tenants provisioned the file does
not exist and :func:`assert_tenant_active` is a no-op, so single-tenant and
unprovisioned deployments are unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, replace

from ..file_lock import atomic_write_text, cross_process_lock
from ..paths import _tenant_segment, maverick_home

log = logging.getLogger(__name__)


def _warn_if_unknown_plan(plan: str) -> str | None:
    """Warn (and return the message) if ``plan`` is not a known billing plan.

    ``billing.entitlements_for`` silently resolves an unknown plan to the
    ``free`` entitlements, so an operator who typos ``--plan pro`` would think a
    tenant is paid when it is not. We warn rather than raise so a plan can be
    pre-assigned before it is defined in ``[billing.plans]``."""
    p = str(plan or "free")
    try:
        from ..billing import known_plan_names
        known = known_plan_names()
    except Exception:  # pragma: no cover -- never block provisioning on billing
        return None
    if p in known:
        return None
    msg = (f"plan {p!r} is not a known billing plan "
           f"({', '.join(sorted(known))}); its entitlements fall back to 'free' "
           f"until it is defined in [billing.plans]")
    log.warning("tenant registry: %s", msg)
    return msg

ACTIVE = "active"
SUSPENDED = "suspended"
_STATUSES = frozenset({ACTIVE, SUSPENDED})

# Serializes the roster load-modify-save across threads in this process; the
# cross_process_lock below extends that across processes (the registry is edited
# from both the CLI and the dashboard, which are separate processes).
_REGISTRY_LOCK = threading.Lock()

# Serializes the in-flight reservations load-modify-save (a separate file from
# the roster, so a separate lock: reserving a run's dollars must not contend
# with a suspend/set_quota roster edit). Cross-process flock on the sidecar
# extends it across the CLI / dashboard / serve processes.
_RESERVE_LOCK = threading.Lock()


class TenantSuspended(PermissionError):
    """Raised when work is attempted for a suspended (or deleted) tenant."""


class UnknownTenant(KeyError):
    """Raised when an operation targets a tenant that was never provisioned."""


def _registry_path():
    return maverick_home() / "tenant_registry.json"


@dataclass(frozen=True)
class TenantRecord:
    """One provisioned tenant."""

    id: str
    status: str = ACTIVE
    plan: str = "free"
    display_name: str = ""
    # Per-tenant aggregate spend cap (USD/day); 0 = unlimited (defer to global).
    max_daily_dollars: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "plan": self.plan,
            "display_name": self.display_name,
            "max_daily_dollars": self.max_daily_dollars,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TenantRecord:
        if not isinstance(d, dict) or not d.get("id"):
            raise ValueError("tenant record must be an object with an id")
        status = str(d.get("status") or ACTIVE)
        if status not in _STATUSES:
            status = ACTIVE
        try:
            cap = float(d.get("max_daily_dollars") or 0.0)
        except (TypeError, ValueError):
            cap = 0.0
        return cls(
            id=str(d["id"]), status=status, plan=str(d.get("plan") or "free"),
            display_name=str(d.get("display_name") or ""),
            max_daily_dollars=max(0.0, cap),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
        )

    @property
    def active(self) -> bool:
        return self.status == ACTIVE


def _validate_id(tenant_id: str) -> str:
    tid = (tenant_id or "").strip()
    if not tid:
        raise ValueError("tenant id is required")
    # Reuse the path encoder's guard so a registry id is always a safe segment
    # (it raises InvalidTenantError on an over-long id).
    _tenant_segment(tid)
    return tid


def _load() -> dict[str, TenantRecord]:
    try:
        raw = json.loads(_registry_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, TenantRecord] = {}
    for d in raw.get("tenants", []) if isinstance(raw, dict) else []:
        try:
            rec = TenantRecord.from_dict(d)
        except ValueError:
            continue
        out[rec.id] = rec
    return out


def _save(records: dict[str, TenantRecord]) -> None:
    payload = {"tenants": [records[k].to_dict() for k in sorted(records)]}
    # Atomic temp+replace (0600): a bare O_TRUNC write truncates in place, so a
    # concurrent _load()/is_active() reader sees a half-written file -> its
    # JSONDecodeError is swallowed as an EMPTY roster, which spuriously refuses a
    # legitimately-active tenant. Each mutator also holds _REGISTRY_LOCK +
    # cross_process_lock across the whole load-modify-save so two edits (e.g. a
    # suspend racing a set_quota) can't have the second save clobber the first.
    atomic_write_text(
        _registry_path(),
        json.dumps(payload, indent=2, sort_keys=True),
    )


def _locked():
    """Serialize a roster load-modify-save in-process AND cross-process."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(_REGISTRY_LOCK)
    stack.enter_context(cross_process_lock(_registry_path()))
    return stack


def list_tenants() -> list[TenantRecord]:
    records = _load()
    return [records[k] for k in sorted(records)]


def get_tenant(tenant_id: str) -> TenantRecord | None:
    return _load().get((tenant_id or "").strip())


def create_tenant(
    tenant_id: str, *, plan: str = "free", display_name: str = "",
    max_daily_dollars: float = 0.0,
) -> TenantRecord:
    """Provision a tenant + its workspace dir. Raises ValueError if it exists."""
    tid = _validate_id(tenant_id)
    # Provisioning a named tenant IS multi-tenant mode — a paid (Platinum)
    # capability. Fail-open: a no-op unless license enforcement is on and the
    # deployment isn't entitled (a genuine single-tenant box never calls this —
    # it uses the default tenant).
    try:
        from ..entitlements import require
        allowed = require("multi_tenant")
    except Exception:  # pragma: no cover - entitlements missing => don't block
        allowed = True
    if not allowed:
        raise PermissionError(
            "multi-tenant provisioning requires a Platinum license "
            "(license enforcement is on and this deployment is not entitled)")
    _warn_if_unknown_plan(plan)
    with _locked():
        records = _load()
        if tid in records:
            raise ValueError(f"tenant already exists: {tid!r}")
        now = time.time()
        rec = TenantRecord(
            id=tid, status=ACTIVE, plan=plan, display_name=display_name,
            max_daily_dollars=max(0.0, float(max_daily_dollars or 0.0)),
            created_at=now, updated_at=now,
        )
        records[tid] = rec
        _save(records)
    # Materialize the workspace home so the tenant's data dir exists.
    from ..workspace import Workspace
    Workspace(tid).root.mkdir(parents=True, exist_ok=True)
    return rec


def _mutate(tenant_id: str, **changes) -> TenantRecord:
    tid = (tenant_id or "").strip()
    with _locked():
        records = _load()
        rec = records.get(tid)
        if rec is None:
            raise UnknownTenant(tid)
        rec = replace(rec, updated_at=time.time(), **changes)
        records[tid] = rec
        _save(records)
    return rec


def suspend_tenant(tenant_id: str) -> TenantRecord:
    return _mutate(tenant_id, status=SUSPENDED)


def resume_tenant(tenant_id: str) -> TenantRecord:
    return _mutate(tenant_id, status=ACTIVE)


def _audit_billing_change(field: str, tenant_id: str, *, old, new) -> None:
    """Record a tamper-evident audit row for a change to a tenant's billing
    terms (plan / daily cap), so an upgrade or cap change is provable rather than
    a silent edit. Fail-soft: an audit error never blocks the mutation."""
    try:
        from ..audit import EventKind, record
        kind = (EventKind.TENANT_PLAN_CHANGED if field == "plan"
                else EventKind.TENANT_QUOTA_CHANGED)
        record(kind, agent="operator", tenant=tenant_id, field=field,
               old=old, new=new)
    except Exception:  # pragma: no cover -- audit must never break provisioning
        pass


def set_quota(tenant_id: str, max_daily_dollars: float) -> TenantRecord:
    old = get_tenant(tenant_id)
    rec = _mutate(tenant_id, max_daily_dollars=max(0.0, float(max_daily_dollars or 0.0)))
    _audit_billing_change("quota", rec.id, old=(old.max_daily_dollars if old else None),
                          new=rec.max_daily_dollars)
    return rec


def set_plan(tenant_id: str, plan: str) -> TenantRecord:
    _warn_if_unknown_plan(plan)
    old = get_tenant(tenant_id)
    rec = _mutate(tenant_id, plan=str(plan or "free"))
    _audit_billing_change("plan", rec.id, old=(old.plan if old else None), new=rec.plan)
    return rec


def delete_tenant(tenant_id: str, *, purge: bool = False) -> bool:
    """Remove a tenant from the registry. With ``purge=True`` also delete its
    data directory (irreversible). Returns False if the tenant was unknown."""
    tid = (tenant_id or "").strip()
    with _locked():
        records = _load()
        if tid not in records:
            return False
        del records[tid]
        _save(records)
    if purge:
        import shutil

        from ..workspace import Workspace
        root = Workspace(tid).root
        # Only purge under the tenants/ tree, never the shared root.
        if "tenants" in root.parts:
            shutil.rmtree(root, ignore_errors=True)
    return True


def is_active(tenant_id: str | None) -> bool:
    """Whether a tenant may do work.

    The registry is opt-in: before any roster file exists, named tenants are
    accepted for single-tenant and unprovisioned deployments. Once a roster
    exists, only provisioned active tenants may do work; unknown tenant IDs
    include deleted tenants and are refused.
    """
    tid = (tenant_id or "").strip()
    if not tid:
        return True
    rec = _load().get(tid)
    if rec is None:
        return not _registry_path().exists()
    return rec.active


def assert_tenant_active(tenant_id: str | None) -> None:
    """Enforcement hook: raise :class:`TenantSuspended` for inactive tenants.
    No-op for None / deployments without a registry, so existing flows are
    unchanged until tenant provisioning is enabled.
    """
    if not is_active(tenant_id):
        raise TenantSuspended(f"tenant is suspended or unknown: {tenant_id!r}")


def tenant_spend_today(tenant_id: str) -> float:
    """Today's recorded spend (dollars) across the tenant's usage ledger.

    Reads the tenant-scoped ledger (``tenants/<t>/usage/ledger.json`` — the
    same file the orchestrator's per-principal recording lands in when the
    run is tenant-pinned) and sums every principal's bucket for the current
    UTC day. Fail-soft: an unreadable ledger counts as zero spend.
    """
    from ..paths import data_dir
    from ..quotas import UsageLedger, _today
    try:
        ledger = UsageLedger(data_dir("usage", "ledger.json", tenant=tenant_id))
        data = ledger._load()
        day = _today()
        return sum(
            float((days.get(day) or {}).get("dollars", 0.0))
            for days in data.values() if isinstance(days, dict)
        )
    except Exception as e:
        # Spend read never blocks serving, but it must not be SILENT: an
        # unreadable ledger counts as 0 spend, which under-enforces the daily
        # cap (a tenant at its limit looks unused). Surface it.
        log.warning("tenant_spend_today: unreadable usage ledger for %r: %s; "
                    "counting 0 spend (daily cap under-enforced)", tenant_id, e)
        return 0.0


def _enforce_plan_caps() -> bool:
    """Opt-in: when a tenant has no explicit registry spend cap, fall back to its
    billing plan's entitlement-level daily cap so a config-defined plan cap is
    actually enforced rather than decorative (audit #81).

    Off by default: a registry cap of 0 means "unlimited" today, so enabling this
    changes already-provisioned tenants. ``MAVERICK_ENFORCE_PLAN_CAPS`` env wins
    over ``[billing] enforce_plan_caps``."""
    env = os.environ.get("MAVERICK_ENFORCE_PLAN_CAPS")
    if env is not None and env.strip() != "":
        return env.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from ..config import load_config
        return bool(((load_config() or {}).get("billing") or {}).get("enforce_plan_caps"))
    except Exception:  # pragma: no cover -- config never blocks quota resolution
        return False


def tenant_over_quota(tenant_id: str | None) -> str | None:
    """Human-readable reason when ``tenant_id`` is over its provisioned
    daily-spend cap, else None.

    The cap is ``max_daily_dollars`` on the tenant's roster record (set via
    ``maverick tenant quota``); 0/unset, an unprovisioned tenant, or no
    roster all mean "no cap" — enforcement is opt-in per tenant.
    """
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    if _load().get(tid) is None:
        return None
    cap = _tenant_daily_cap(tid)
    if cap <= 0:
        return None
    spent = tenant_spend_today(tid)
    if spent >= cap:
        return (f"workspace {tid!r} is over its daily spend cap "
                f"(${spent:.2f} >= ${cap:.2f}); resets at midnight UTC")
    return None


def _tenant_daily_cap(tenant_id: str) -> float:
    """The effective daily spend cap (USD) for ``tenant_id``: the registry cap,
    falling back to the plan entitlement cap when plan-cap enforcement is on.
    ``0`` means no cap. Shared by :func:`tenant_over_quota` and
    :func:`tenant_remaining_today` so both read the same ceiling."""
    rec = _load().get((tenant_id or "").strip())
    if rec is None:
        return 0.0
    cap = rec.max_daily_dollars
    if cap <= 0 and _enforce_plan_caps():
        try:
            from ..billing import entitlements_for
            cap = entitlements_for(rec.plan).max_daily_dollars
        except Exception:  # pragma: no cover -- billing never blocks quota
            cap = 0.0
    return cap if cap > 0 else 0.0


# ---- In-flight dollar reservations (concurrent daily-cap enforcement) --------
#
# The usage ledger is written only at run END, so N runs that START for one
# tenant before any of them records spend would each read the same remaining
# allowance and each clamp to the full remainder -- collectively overshooting
# the daily ceiling by up to N x (finding #2). A run therefore RESERVES its
# clamped per-run dollar cap here at start; tenant_remaining_today subtracts
# outstanding (non-expired) reservations as well as recorded spend, so a
# concurrent start sees the in-flight hold. Each reservation carries a TTL, so a
# crashed run that never records spend (and so never releases) only dents the
# cap until the hold expires -- the cap self-heals rather than being permanently
# reduced. Stored as a sibling of the tenant usage ledger
# (``tenants/<t>/usage/reservations.json``) with the same atomic
# temp-file+os.replace + cross-process flock discipline the ledger uses.


def _reservations_path(tenant_id: str):
    from ..paths import data_dir
    return data_dir("usage", "reservations.json", tenant=tenant_id)


def _load_reservations(tenant_id: str) -> dict:
    try:
        raw = json.loads(_reservations_path(tenant_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _live_reservations(data: dict, now: float) -> dict:
    """Return only the well-formed, non-expired, positive-amount holds in
    ``data`` (the pruning + parsing shared by the read and write paths)."""
    out: dict = {}
    for rid, v in data.items():
        if not isinstance(v, dict):
            continue
        try:
            exp = float(v.get("expires_at", 0.0))
            amt = float(v.get("dollars", 0.0))
        except (TypeError, ValueError):
            continue
        if exp > now and amt > 0:
            out[str(rid)] = {"dollars": amt, "expires_at": exp}
    return out


def _outstanding_reservations(tenant_id: str | None, *, now: float | None = None) -> float:
    """Sum of a tenant's outstanding (non-expired) in-flight dollar holds."""
    tid = (tenant_id or "").strip()
    if not tid:
        return 0.0
    at = time.time() if now is None else now
    return sum(v["dollars"] for v in _live_reservations(_load_reservations(tid), at).values())


def reserve_tenant_dollars(tenant_id: str | None, amount: float,
                           ttl_seconds: float, reservation_id: str) -> None:
    """Hold ``amount`` dollars in-flight against ``tenant_id`` under
    ``reservation_id`` for ``ttl_seconds`` from now.

    Makes concurrent run starts see each other's not-yet-recorded spend:
    :func:`tenant_remaining_today` subtracts outstanding reservations. The TTL is
    a backstop -- a crashed run that never releases only reduces the cap until
    the hold expires. Atomic read-modify-write (in-process lock + cross-process
    flock + temp-file replace) that also prunes expired holds. A blank tenant/id
    or a non-positive amount is a no-op. Callers wrap this (it must never raise
    into the run path)."""
    tid = (tenant_id or "").strip()
    if not tid or not reservation_id:
        return
    try:
        amt = float(amount or 0.0)
        ttl = float(ttl_seconds or 0.0)
    except (TypeError, ValueError):
        return
    if amt <= 0:
        return
    now = time.time()
    path = _reservations_path(tid)
    with _RESERVE_LOCK, cross_process_lock(path):
        data = _live_reservations(_load_reservations(tid), now)
        data[str(reservation_id)] = {"dollars": amt, "expires_at": now + ttl}
        atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True))


def release_tenant_reservation(tenant_id: str | None, reservation_id: str) -> bool:
    """Release a hold taken by :func:`reserve_tenant_dollars` (e.g. once the run
    has recorded its actual spend, so it isn't double-counted against the cap for
    the rest of its TTL). Also prunes expired holds. Returns True if the id was
    present; a no-op returning False for a blank tenant/id."""
    tid = (tenant_id or "").strip()
    if not tid or not reservation_id:
        return False
    now = time.time()
    path = _reservations_path(tid)
    with _RESERVE_LOCK, cross_process_lock(path):
        data = _load_reservations(tid)
        existed = str(reservation_id) in data
        data.pop(str(reservation_id), None)
        atomic_write_text(
            path, json.dumps(_live_reservations(data, now), indent=2, sort_keys=True))
    return existed


def tenant_remaining_today(tenant_id: str | None) -> float | None:
    """Dollars a tenant may still spend today before hitting its daily cap.

    Returns ``None`` when no cap applies (no tenant, unprovisioned, cap 0/unset,
    enforcement off) so callers leave their own ceiling untouched. Otherwise the
    non-negative remainder ``cap - spent_today - outstanding_reservations`` --
    which a per-run budget can clamp to so a single run can't overshoot the
    tenant's aggregate ceiling (#78). Subtracting outstanding in-flight
    reservations (spend that concurrent runs have committed to but not yet
    recorded) is what closes the concurrency hole in finding #2: two runs that
    start before either records spend see each other's holds instead of both
    clamping to the full remainder. Coordinates the per-run cap with the
    per-tenant cap; without it the over-quota gate only fires *between* runs,
    after the overshoot."""
    tid = (tenant_id or "").strip()
    if not tid:
        return None
    cap = _tenant_daily_cap(tid)
    if cap <= 0:
        return None
    return max(0.0, cap - tenant_spend_today(tid) - _outstanding_reservations(tid))


__all__ = [
    "ACTIVE", "SUSPENDED", "TenantRecord", "TenantSuspended", "UnknownTenant",
    "list_tenants", "get_tenant", "create_tenant", "suspend_tenant",
    "resume_tenant", "delete_tenant", "set_quota", "set_plan",
    "is_active", "assert_tenant_active", "tenant_spend_today",
    "tenant_over_quota", "tenant_remaining_today",
    "reserve_tenant_dollars", "release_tenant_reservation",
]
