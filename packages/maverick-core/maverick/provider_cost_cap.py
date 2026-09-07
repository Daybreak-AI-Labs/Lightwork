"""Provider-level cost caps across all runs (roadmap: 2028 H1 safety).

:class:`maverick.budget.Budget` caps **one run**; :mod:`maverick.quotas` caps
a **principal** per day. Neither bounds what the deployment as a whole may
spend at a given *provider* — fifty perfectly-budgeted runs are still a
$250 Anthropic day nobody approved. This module is the third axis: a ceiling
per provider per UTC period (day or month), enforced at the LLM dispatch
path via an atomic :func:`reserve` for each physical provider call.

Config (opt-in; nothing configured = no caps = behavior unchanged)::

    [budget]
    provider_caps_period = "day"        # "day" (default) | "month"

    [budget.provider_caps]
    anthropic = 50.0                    # dollars per period
    openai    = 20.0

``MAVERICK_PROVIDER_CAPS_PERIOD`` overrides the period knob (env wins, the
house convention). Config readers are fail-soft: an unreadable config means
no caps, never a crash.

The ledger (``data_dir("provider_spend.json")``) maps
``{period_key: {provider: dollars}}`` — period keys are ``YYYY-MM-DD`` (day)
or ``YYYY-MM`` (month), always UTC so the window doesn't shift with host
timezone/DST. Writes are atomic (unique temp file + ``os.replace``, 0600)
and the whole read-modify-write is serialized by an in-process lock **plus a
cross-process advisory flock** on a ``.lock`` sidecar, mirroring
:mod:`maverick.quotas`; the ledger reloads per call rather than caching, so
concurrent processes don't clobber each other's totals (without the flock two
processes both load the same total, both add, and the second ``os.replace``
wins -- dollars vanish from a *spend-cap* ledger and the deployment overspends
past the configured ceiling). Reservations and settlement receipts share that
transaction. The numeric counter includes confirmed costs and outstanding
reservations, so old numeric readers remain conservative. Configured admission
refuses unreadable accounting and unknown final charges; failures never recreate
a damaged ledger as zero. A required marker distinguishes lost established
accounting from fresh setup. Shared PostgreSQL uses its transactional authority
and never substitutes a local file. Crashed reservations do not expire.

Projected prices are estimates. An actual response can cost more than its
projection; settlement records the full charge and blocks subsequent calls at
the cap. The application cannot guarantee an external provider's final bill.

Clock is injectable everywhere (``now=`` epoch seconds) so rollover tests
are deterministic. Stdlib-only, pure library, nothing imports it by default.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .file_lock import (
    atomic_create_text,
    atomic_read_text,
    atomic_write_text,
    cross_process_lock,
    ensure_private_directory,
    ensure_private_file,
)
from .paths import data_dir

log = logging.getLogger(__name__)

PERIOD_DAY = "day"
PERIOD_MONTH = "month"

_LEDGER_LOCK = threading.Lock()

# Cached shared-world handle for the cluster-wide ledger (see _shared_world).
_shared_world_cache = None
_shared_world_cache_key = None
_shared_world_lock = threading.Lock()


def authority_identity():
    """Opaque identity of the actual configured counter authority, never a DSN."""
    from .world_model_backends import is_postgres_configured
    if is_postgres_configured():
        from .world_model_backends.postgres import _rls_enabled
        # PostgresWorldModel resolves the effective DSN from this environment.
        # Credential changes conservatively invalidate reviews too.
        settings = ["postgres", os.environ.get("MAVERICK_PG_DSN", ""), _rls_enabled()]
    else:
        settings = ["local", str(_ledger_path().absolute())]
    return hashlib.sha256(json.dumps(settings).encode("utf-8")).hexdigest()


def _shared_world():
    """Resolve the configured authority; never substitute a local ledger on failure."""
    global _shared_world_cache, _shared_world_cache_key
    try:
        from .world_model_backends import is_postgres_configured
        if not is_postgres_configured():
            return None
        identity = authority_identity()
        with _shared_world_lock:
            if _shared_world_cache is None or _shared_world_cache_key != identity:
                from .world_model import open_world
                selected = open_world()
                if authority_identity() != identity:
                    raise ProviderAccountingUnavailable
                # A previous in-flight Reservation retains its exact old handle;
                # do not close it when selecting a new configured authority.
                _shared_world_cache, _shared_world_cache_key = selected, identity
            return _shared_world_cache
    except Exception as exc:
        _shared_world_cache = None
        _shared_world_cache_key = None
        raise ProviderAccountingUnavailable from exc


class ProviderAccountingUnavailable(RuntimeError):
    """Configured spending authority cannot safely admit another provider call."""

    def __init__(self):
        super().__init__(
            "Provider accounting is unavailable or has unresolved charges. "
            "New calls are blocked; reconcile the retained accounting before retrying."
        )


class ProviderCapExceeded(Exception):
    """A provider's per-period spend ceiling has been reached."""

    def __init__(self, provider: str, spent: float, cap: float, period_key: str):
        super().__init__(
            f"provider {provider!r} has spent ${spent:.2f} of its "
            f"${cap:.2f} cap for {period_key}; refusing further calls. "
            f"Raise [budget.provider_caps] {provider} or wait for the next period."
        )
        self.provider = provider
        self.spent = spent
        self.cap = cap
        self.period_key = period_key


@dataclass(frozen=True)
class CapStatus:
    """One provider's standing against its cap. ``cap``/``remaining`` are
    ``None`` when no cap is configured (always allowed)."""

    allowed: bool
    spent: float
    cap: float | None
    remaining: float | None
    reserved: float = 0.0
    unresolved: bool = False


def _canon(provider: str) -> str:
    return str(provider or "").strip().lower()


def _budget_cfg() -> dict:
    try:
        from .config import load_config
        return (load_config() or {}).get("budget") or {}
    except Exception:  # config must never crash the call path
        return {}


def caps_from_config() -> dict[str, float]:
    """``[budget.provider_caps]`` as ``{provider: dollars}``; fail-soft.

    Non-numeric and non-positive values are dropped (a zero/negative cap is
    a misconfiguration, not a "block everything" switch — that's what the
    killswitch is for).
    """
    raw = _budget_cfg().get("provider_caps")
    if not isinstance(raw, dict):
        return {}
    caps: dict[str, float] = {}
    for name, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if float(value) > 0:
            caps[_canon(name)] = float(value)
    return caps


def period_from_config() -> str:
    """``"day"`` (default) or ``"month"``; env wins; unrecognized -> day."""
    env = os.environ.get("MAVERICK_PROVIDER_CAPS_PERIOD", "").strip().lower()
    if env in (PERIOD_DAY, PERIOD_MONTH):
        return env
    val = str(_budget_cfg().get("provider_caps_period", PERIOD_DAY)).strip().lower()
    return val if val in (PERIOD_DAY, PERIOD_MONTH) else PERIOD_DAY


def period_key(now: float | None = None, period: str | None = None) -> str:
    """The UTC ledger key for ``now``: ``YYYY-MM-DD`` (day) or ``YYYY-MM``."""
    period = period or period_from_config()
    dt = datetime.fromtimestamp(time.time() if now is None else float(now),
                                tz=timezone.utc)
    return dt.strftime("%Y-%m-%d") if period == PERIOD_DAY else dt.strftime("%Y-%m")


def _ledger_path() -> Path:
    return data_dir("provider_spend.json")


_RECEIPTS = "__provider_reservations_v1__"
_MAX_LEDGER_BYTES = 16 * 1024 * 1024
_MAX_RECEIPTS = 100_000
_PERIOD = re.compile(r"[0-9]{4}-[0-9]{2}(?:-[0-9]{2})?\Z")


def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderAccountingUnavailable
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ProviderAccountingUnavailable
    return value


def _validate_receipts(total, receipts):
    total = _amount(total)
    if not isinstance(receipts, dict) or len(receipts) > _MAX_RECEIPTS:
        raise ProviderAccountingUnavailable
    charged = 0.0
    for identity, row in receipts.items():
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", identity):
            raise ProviderAccountingUnavailable
        if not isinstance(row, dict) or set(row) - {"projected", "state", "actual", "reconciliation"} or not {"projected", "state", "actual"} <= set(row):
            raise ProviderAccountingUnavailable
        if "reconciliation" in row and (not isinstance(row["reconciliation"], str)
                                       or not re.fullmatch(r"PAR-[A-Za-z0-9_-]+", row["reconciliation"])):
            raise ProviderAccountingUnavailable
        projection = _amount(row["projected"])
        if row["state"] not in {"held", "unknown", "settled"}:
            raise ProviderAccountingUnavailable
        if row["state"] == "settled":
            charged += _amount(row["actual"])
        else:
            if row["actual"] is not None:
                raise ProviderAccountingUnavailable
            charged += projection
    if not math.isfinite(charged) or charged > total + 1e-8:
        raise ProviderAccountingUnavailable
    return total, receipts


def _load(path: Path) -> dict:
    try:
        ensure_private_directory(path.parent)
        ensure_private_file(path, 0o600)
        if path.stat().st_size > _MAX_LEDGER_BYTES:
            raise ProviderAccountingUnavailable
        raw_text = atomic_read_text(path)
        if len(raw_text.encode("utf-8")) > _MAX_LEDGER_BYTES:
            raise ProviderAccountingUnavailable
        raw = json.loads(raw_text)
        if not isinstance(raw, dict):
            raise ProviderAccountingUnavailable
        metadata = raw.get(_RECEIPTS, {})
        if not isinstance(metadata, dict):
            raise ProviderAccountingUnavailable
        for period, bucket in raw.items():
            if period == _RECEIPTS:
                continue
            if not _PERIOD.fullmatch(period) or not isinstance(bucket, dict):
                raise ProviderAccountingUnavailable
            for name, total in bucket.items():
                if not isinstance(name, str) or not name or name != _canon(name):
                    raise ProviderAccountingUnavailable
                _validate_receipts(total, metadata.get(period, {}).get(name, {}))
        for period, providers in metadata.items():
            if not isinstance(providers, dict) or period not in raw:
                raise ProviderAccountingUnavailable
            for name, receipts in providers.items():
                if name not in raw[period]:
                    raise ProviderAccountingUnavailable
                _validate_receipts(raw[period][name], receipts)
        return raw
    except FileNotFoundError as exc:
        if path.with_suffix(path.suffix + ".required").exists():
            raise ProviderAccountingUnavailable from exc
        return {}
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        raise ProviderAccountingUnavailable from exc


def _save(path: Path, data: dict) -> None:
    ensure_private_directory(path.parent)
    encoded = json.dumps(data, allow_nan=False)
    if len(encoded.encode("utf-8")) > _MAX_LEDGER_BYTES:
        raise ProviderAccountingUnavailable
    # Publish the required marker first: a crash between marker and ledger is
    # safely unavailable, never an apparently fresh zero-balance authority.
    marker = path.with_suffix(path.suffix + ".required")
    try:
        atomic_create_text(marker, "provider accounting v1\n")
    except FileExistsError:
        ensure_private_file(marker, 0o600)
    atomic_write_text(path, encoded, mode=0o600)


@dataclass(frozen=True)
class Reservation:
    provider: str
    period: str
    request_id: str
    projected: float
    path: Path | None = field(repr=False)
    shared: object = field(default=None, repr=False, compare=False)


def _mutate(name, period, operation, *, path=None, shared=None):
    def validated(total, receipts):
        total, receipts, result = operation(total, receipts)
        _validate_receipts(total, receipts)
        if len(json.dumps(receipts, allow_nan=False).encode("utf-8")) > _MAX_LEDGER_BYTES:
            raise ProviderAccountingUnavailable
        return total, receipts, result

    try:
        if shared is not None:
            return shared.mutate_provider_cap(period, name, validated)
        with _LEDGER_LOCK, cross_process_lock(path, strict=True):
            data = _load(path)
            total = data.get(period, {}).get(name, 0.0)
            receipts = data.get(_RECEIPTS, {}).get(period, {}).get(name, {})
            total, receipts, result = validated(total, receipts)
            data.setdefault(period, {})[name] = total
            if receipts:
                data.setdefault(_RECEIPTS, {}).setdefault(period, {})[name] = receipts
            _save(path, data)
            return result
    except (ProviderCapExceeded, ProviderAccountingUnavailable):
        raise
    except Exception as exc:
        raise ProviderAccountingUnavailable from exc


def record(provider: str, dollars: float, *, now: float | None = None,
           path: Path | None = None) -> None:
    """Record legacy/unreserved spend without replacing damaged accounting.

    An incurred call cannot be undone. Failure is logged, and configured
    admission subsequently refuses the unavailable ledger instead of zeroing it.
    Reserved dispatch uses settle(), which retains its durable charge on failure.
    """
    name = _canon(provider)
    amount = max(0.0, float(dollars or 0.0))
    if not name or amount == 0.0:
        return
    try:
        amount = _amount(amount)
        shared = _shared_world() if path is None else None
        def add(total, receipts):
            total, receipts = _validate_receipts(total, receipts)
            return total + amount, receipts, None
        _mutate(name, period_key(now), add, path=path or _ledger_path(), shared=shared)
    except (OSError, ProviderAccountingUnavailable):
        log.warning("Provider spend could not be recorded; accounting requires reconciliation.")


def _status(total, receipts, cap):
    total, receipts = _validate_receipts(total, receipts)
    held = sum(row["projected"] for row in receipts.values() if row["state"] != "settled")
    unknown = any(row["state"] == "unknown" for row in receipts.values())
    return CapStatus(cap is None or (total < cap and not unknown), max(0.0, total - held),
                     cap, None if cap is None else max(0.0, cap - total), held, unknown)


def check(provider: str, *, now: float | None = None,
          path: Path | None = None) -> CapStatus:
    name = _canon(provider)
    cap = caps_from_config().get(name)
    try:
        shared = _shared_world() if path is None else None
        if shared is not None:
            total, receipts = shared.read_provider_cap(period_key(now), name)
        else:
            data = _load(path or _ledger_path())
            total = data.get(period_key(now), {}).get(name, 0.0)
            receipts = data.get(_RECEIPTS, {}).get(period_key(now), {}).get(name, {})
        return _status(total, receipts, cap)
    except Exception as exc:
        if cap is None:
            return CapStatus(True, 0.0, None, None)
        if isinstance(exc, ProviderAccountingUnavailable):
            raise
        raise ProviderAccountingUnavailable from exc


def reserve(provider: str, projected_dollars: float, *, now=None, path=None,
            request_id: str | None = None) -> Reservation | None:
    """Durably encumber capacity before each physical call, including hedges.

    Holds never expire automatically. A crashed caller cannot reopen capacity;
    uncertain completion retains the hold until an exact actual-cost receipt.
    The legacy numeric total includes holds, so older readers stay conservative.
    """
    name = _canon(provider)
    cap = caps_from_config().get(name)
    if cap is None:
        return None
    projected = _amount(projected_dollars)
    identity = request_id or uuid.uuid4().hex
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", identity):
        raise ProviderAccountingUnavailable
    period = period_key(now)
    shared = _shared_world() if path is None else None
    ledger = None if shared is not None else (path or _ledger_path()).absolute()
    def admit(total, receipts):
        total, receipts = _validate_receipts(total, receipts)
        # An admission identity grants one physical dispatch, never a retry.
        if identity in receipts or any(row["state"] == "unknown" for row in receipts.values()):
            raise ProviderAccountingUnavailable
        if total >= cap or total + projected > cap:
            raise ProviderCapExceeded(name, total, cap, period)
        if len(receipts) >= _MAX_RECEIPTS:
            raise ProviderAccountingUnavailable
        receipts[identity] = {"projected": projected, "state": "held", "actual": None}
        return total + projected, receipts, None
    _mutate(name, period, admit, path=ledger, shared=shared)
    return Reservation(name, period, identity, projected, ledger, shared)


def settle(reservation: Reservation, actual_dollars: float | None) -> None:
    """Replace one hold with exact usage, or retain it as unresolved.

    Repeated identical receipts are no-ops; conflicting receipts and missing
    identities refuse. None never means free: provider errors/cancellation may
    have incurred external cost. Settlement uses the original backend/period.
    """
    actual = None if actual_dollars is None else _amount(actual_dollars)
    def finish(total, receipts):
        total, receipts = _validate_receipts(total, receipts)
        row = receipts.get(reservation.request_id)
        if row is None or row["projected"] != reservation.projected:
            raise ProviderAccountingUnavailable
        if row["state"] == "settled":
            if actual != row["actual"]:
                raise ProviderAccountingUnavailable
            return total, receipts, None
        if actual is None:
            row["state"] = "unknown"
            return total, receipts, None
        total = max(0.0, total - row["projected"]) + actual
        row.update(state="settled", actual=actual)
        return total, receipts, None
    _mutate(reservation.provider, reservation.period, finish,
            path=reservation.path, shared=reservation.shared)


def would_exceed(provider: str, projected_dollars: float, *, now=None, path=None) -> bool:
    st = check(provider, now=now, path=path)
    if st.unresolved and st.cap is not None:
        raise ProviderAccountingUnavailable
    return st.cap is not None and st.spent + st.reserved + _amount(projected_dollars) > st.cap


# (provider, period) pairs we've already paged the operator about, so a blown
# cap alerts ONCE per period instead of on every subsequently-blocked dispatch.
_alerted: set[tuple[str, str]] = set()


def enforce(provider: str, *, projected_dollars: float = 0.0,
            now: float | None = None, path: Path | None = None) -> CapStatus:
    """Preflight configured cap and accounting availability without admission.

    Physical dispatch must additionally reserve capacity atomically; this
    read-only check alone cannot serialize concurrent calls. No configured cap
    leaves dispatch unchanged. Projection is an estimate of external cost.
    """
    st = check(provider, now=now, path=path)
    if st.unresolved and st.cap is not None:
        raise ProviderAccountingUnavailable
    projected = _amount(projected_dollars)
    reached = not st.allowed
    would_exceed_cap = st.cap is not None and st.spent + st.reserved + projected > st.cap
    if reached or would_exceed_cap:
        period = period_key(now)
        canon = _canon(provider)
        # Page the operator ONCE per period only when the cap is actually
        # reached (spend >= cap). A projected-over block on a still-under-cap
        # provider is normal backpressure, not an exhaustion event.
        if reached and (canon, period) not in _alerted:
            _alerted.add((canon, period))
            try:
                from .ops_alert import alert
                alert("provider_cost_cap_exhausted",
                      f"{canon} spend ${st.spent:.2f} reached the ${st.cap:.2f} "
                      f"cap for period {period}; LLM calls are now blocked",
                      severity="critical")
            except Exception:  # pragma: no cover - alerting never blocks the gate
                pass
        raise ProviderCapExceeded(canon, st.spent, float(st.cap or 0.0), period)
    return st


def prune(*, now: float | None = None, path: Path | None = None) -> int:
    """Drop every ledger period except the current one; returns how many
    period buckets were removed.

    Old periods are dead weight once their window closes (and a stale key
    from a period-knob change would otherwise linger forever). Call it
    opportunistically — e.g. after recording at period rollover.
    """
    current = period_key(now)
    ledger = path if path is not None else _ledger_path()
    with _LEDGER_LOCK, cross_process_lock(ledger):
        data = _load(ledger)
        metadata = data.get(_RECEIPTS, {})
        stale = [k for k in data if k not in {current, _RECEIPTS}
                 and not any(row["state"] != "settled"
                             for rows in metadata.get(k, {}).values() for row in rows.values())]
        if not stale:
            return 0
        for k in stale:
            del data[k]
            metadata.pop(k, None)
        try:
            _save(ledger, data)
        except OSError as e:
            log.warning("provider_cost_cap: prune failed for %s: %s", ledger, e)
            return 0
    return len(stale)


__all__ = [
    "ProviderCapExceeded",
    "ProviderAccountingUnavailable",
    "Reservation",
    "reserve",
    "settle",
    "CapStatus",
    "PERIOD_DAY",
    "PERIOD_MONTH",
    "caps_from_config",
    "period_from_config",
    "period_key",
    "record",
    "check",
    "would_exceed",
    "enforce",
    "prune",
]
