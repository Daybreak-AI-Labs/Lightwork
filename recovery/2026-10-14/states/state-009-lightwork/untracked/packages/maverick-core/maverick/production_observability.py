"""Low-cardinality production-assurance health and metric signals.

This module is deliberately independent of the dashboard.  Core enforcement
boundaries can record a small, fixed event vocabulary, while the dashboard can
render the same process-local counters and live posture checks through its
existing ``/metrics`` and ``/readyz`` surfaces.

The assurance series never carry a tenant, principal, path, destination, key
identifier, exception, or payload label.  Their only label is one of the fixed
signal names below.  That makes them safe to aggregate across a fleet and keeps
attacker-controlled input from creating unbounded Prometheus cardinality.

Development behavior is unchanged.  Live checks and metric series are enabled
only for an artifact that explicitly requires the reviewed production surface.
An unreadable production predicate fails toward enabling the checks; it is not
interpreted as permission to fall back to development posture.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

# Runtime events.  These are the only accepted counter labels.
SHIELD_ENFORCEMENT_FAILURE = "shield_enforcement_failure"
SHIELD_BYPASS = "shield_bypass"
DURABLE_AUDIT_REFUSAL = "durable_audit_refusal"
AUDIT_FORWARDER_FAILURE = "audit_forwarder_failure"
INDETERMINATE_SIDE_EFFECT = "indeterminate_side_effect"
POLICY_FALLBACK = "policy_fallback"

EVENT_SIGNALS = (
    SHIELD_ENFORCEMENT_FAILURE,
    SHIELD_BYPASS,
    DURABLE_AUDIT_REFUSAL,
    AUDIT_FORWARDER_FAILURE,
    INDETERMINATE_SIDE_EFFECT,
    POLICY_FALLBACK,
)

# Live posture/value signals.  This vocabulary is also fixed and tenant-free.
SHIELD_ENFORCING = "shield_enforcing"
SHIELD_FAILURES = "shield_failures"
SHIELD_BYPASSES = "shield_bypasses"
DURABLE_AUDIT_REFUSALS = "durable_audit_refusals"
AUDIT_FORWARDER_FAILURES = "audit_forwarder_failures"
AUDIT_FORWARDER_BACKLOG = "audit_forwarder_backlog"
AUDIT_ANCHOR_LAG_SECONDS = "audit_anchor_lag_seconds"
KMS_KEY_CUSTODY = "kms_key_custody"
CONTROL_PLANE_LEASE_OWNED = "control_plane_lease_owned"
POSTGRES_RLS_VERIFIED = "postgres_rls_verified"
TRANSPORT_TLS_VERIFIED = "transport_tls_verified"
INDETERMINATE_SIDE_EFFECTS = "indeterminate_side_effects"
QUEUE_PENDING = "queue_pending"
QUEUE_DEAD_LETTER = "queue_dead_letter"
QUEUE_STALE_LEASES = "queue_stale_leases"
KILL_SWITCH_CLEAR = "kill_switch_clear"
POLICY_INTEGRITY = "policy_integrity"
POLICY_FALLBACKS = "policy_fallbacks"

SIGNAL_NAMES = (
    SHIELD_ENFORCING,
    SHIELD_FAILURES,
    SHIELD_BYPASSES,
    DURABLE_AUDIT_REFUSALS,
    AUDIT_FORWARDER_FAILURES,
    AUDIT_FORWARDER_BACKLOG,
    AUDIT_ANCHOR_LAG_SECONDS,
    KMS_KEY_CUSTODY,
    CONTROL_PLANE_LEASE_OWNED,
    POSTGRES_RLS_VERIFIED,
    TRANSPORT_TLS_VERIFIED,
    INDETERMINATE_SIDE_EFFECTS,
    QUEUE_PENDING,
    QUEUE_DEAD_LETTER,
    QUEUE_STALE_LEASES,
    KILL_SWITCH_CLEAR,
    POLICY_INTEGRITY,
    POLICY_FALLBACKS,
)

# Reviewed production thresholds.  They are exported as metric series so alert
# rules compare against the exact values the readiness gate uses.
AUDIT_FORWARDER_BACKLOG_MAX = 0.0
AUDIT_ANCHOR_LAG_MAX_SECONDS = 26.0 * 60.0 * 60.0
QUEUE_PENDING_MAX = 100.0
QUEUE_DEAD_LETTER_MAX = 0.0
QUEUE_STALE_LEASE_MAX = 0.0
QUEUE_LEASE_SECONDS = 60.0 * 60.0
EVENT_MAX = 0.0

_EVENT_LOCK = threading.Lock()
_EVENT_COUNTS = dict.fromkeys(EVENT_SIGNALS, 0)

# KMS round-trips and database policy verification can involve a remote service.
# Cache only those expensive checks; lease, queue, killswitch, policy, and event
# state remain fresh on every scrape/readiness probe.
_EXPENSIVE_CACHE_SECONDS = 30.0
_CACHE_LOCK = threading.Lock()
_EXPENSIVE_CACHE: (
    tuple[float, tuple[int, str | None], tuple[AssuranceSignal, ...]] | None
) = None
_monotonic = time.monotonic


@dataclass(frozen=True)
class AssuranceSignal:
    """One alert-ready posture or bounded-value check."""

    name: str
    healthy: bool
    value: float
    threshold: float
    detail: str


@dataclass(frozen=True)
class AssuranceReport:
    """The exact assurance snapshot rendered by readiness and Prometheus."""

    enabled: bool
    healthy: bool
    signals: tuple[AssuranceSignal, ...]


def production_assurance_required() -> bool:
    """Whether this process must publish and enforce production assurance.

    A malformed requirement flag is already a startup error in
    :mod:`maverick.production_scope`.  For a health/auth decision, however, it
    must also fail toward the production boundary instead of making sensitive
    metrics behave like an unprotected developer install.
    """
    try:
        from .production_scope import production_surface_required

        return bool(production_surface_required())
    except Exception:  # failure-policy: fail_closed
        return True


def record_event(signal: str) -> bool:
    """Increment one fixed assurance event counter.

    Unknown labels are ignored rather than exported.  Callers use constants
    from this module, and an observability typo must neither break enforcement
    nor create attacker-controlled metric cardinality.
    """
    if signal not in _EVENT_COUNTS:
        return False
    with _EVENT_LOCK:
        _EVENT_COUNTS[signal] += 1
    return True


def event_counts() -> dict[str, int]:
    """Return all fixed event counters, including explicit zeroes."""
    with _EVENT_LOCK:
        return {name: int(_EVENT_COUNTS[name]) for name in EVENT_SIGNALS}


def _bool_signal(name: str, check: Callable[[], bool]) -> AssuranceSignal:
    try:
        healthy = bool(check())
    except Exception:  # failure-policy: fail_closed
        healthy = False
    return AssuranceSignal(
        name=name,
        healthy=healthy,
        value=1.0 if healthy else 0.0,
        threshold=1.0,
        detail="verified" if healthy else "verification unavailable or failed",
    )


def _at_most_signal(
    name: str,
    value: Callable[[], float] | float,
    threshold: float,
) -> AssuranceSignal:
    try:
        observed = float(value() if callable(value) else value)
        healthy = observed >= 0.0 and observed <= threshold
    except Exception:  # failure-policy: fail_closed
        observed = -1.0
        healthy = False
    return AssuranceSignal(
        name=name,
        healthy=healthy,
        value=observed,
        threshold=float(threshold),
        detail="within threshold" if healthy else "threshold exceeded or unavailable",
    )


def _shield_enforcing() -> bool:
    # Reuse the active verifier: it constructs the configured Shield and checks
    # every input/tool/output sink instead of treating importability as health.
    from .deployment import _verify_shield

    return bool(_verify_shield().passed)


def _audit_forwarder_backlog(world: Any) -> float:
    """Unacknowledged encrypted SIEM batches across local tenant outboxes."""
    del world  # the SIEM spool is tenant-scoped independently of the world DB
    from .audit.siem_outbox import aggregate_outbox_snapshot

    snapshot = aggregate_outbox_snapshot()
    return float(snapshot["backlog"])


def _audit_anchor_probe() -> tuple[bool, float]:
    """Return ``(integrity_and_freshness_ok, oldest_unanchored_lag_seconds)``."""
    from .audit.signing import (
        ANCHOR_FILENAME,
        _anchor_marker_path,
        _anchored_days,
        day_files,
        verify_anchors,
    )
    from .paths import data_dir

    # Respect the current tenant namespace. Production metrics require a global
    # admin but do not acquire authority to read another tenant's audit files.
    audit_dir = data_dir("audit")
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    completed = [path for path in day_files(audit_dir) if path.stem < today]
    anchored = _anchored_days(audit_dir)
    unanchored = [path.stem for path in completed if path.stem not in anchored]
    lag = 0.0
    if unanchored:
        oldest = datetime.strptime(min(unanchored), "%Y-%m-%d").replace(
            tzinfo=timezone.utc,
        )
        lag = max(0.0, (now - (oldest + timedelta(days=1))).total_seconds())

    marker_requires_ledger = (
        _anchor_marker_path(audit_dir).exists()
        and not (audit_dir / ANCHOR_FILENAME).exists()
    )
    breaks = verify_anchors(audit_dir)
    # A first completed file gets the freshness grace period before its first
    # ledger is expected.  Once the durable marker says a ledger existed, a
    # missing ledger is an integrity failure immediately, not ordinary lag.
    non_freshness_breaks = [
        item
        for item in breaks
        if getattr(item, "reason", "") != "anchor_ledger_missing"
        or marker_requires_ledger
    ]
    healthy = (
        not non_freshness_breaks
        and not marker_requires_ledger
        and lag <= AUDIT_ANCHOR_LAG_MAX_SECONDS
    )
    return healthy, lag


def _audit_anchor_signal() -> AssuranceSignal:
    try:
        healthy, lag = _audit_anchor_probe()
    except Exception:  # failure-policy: fail_closed
        healthy, lag = False, -1.0
    return AssuranceSignal(
        name=AUDIT_ANCHOR_LAG_SECONDS,
        healthy=healthy,
        value=float(lag),
        threshold=AUDIT_ANCHOR_LAG_MAX_SECONDS,
        detail="anchors current and intact" if healthy else "anchors stale or unverifiable",
    )


def _kms_key_custody_healthy() -> bool:
    """Actively prove remote KMS round-trip and off-host audit-key custody."""
    import hashlib

    from .audit.signing import active_key_is_offhost
    from .paths import current_tenant_id_strict
    from .tenant.kms import LocalKMS, get_kms

    kms = get_kms(current_tenant_id_strict())
    if isinstance(kms, LocalKMS):
        return False
    plaintext = hashlib.sha256(b"lightwork-production-kms-health-v1").digest()
    context = b"lightwork-production-assurance/v1"
    wrapped = kms.wrap(plaintext, context=context)
    return bool(
        wrapped
        and kms.unwrap(wrapped, context=context) == plaintext
        and active_key_is_offhost()
    )


def _control_plane_lease_owned() -> bool:
    from .control_plane_lease import posture

    state = posture()
    return bool(
        state.get("state") == "owned"
        and state.get("single_writer_enforced") is True
    )


def _postgres_rls_verified(world: Any) -> bool:
    if world is None or getattr(world, "_rls", False) is not True:
        return False
    from .world_model_backends.postgres import _RLS_POLICY_TABLES

    verifier = getattr(world, "_rls_policy_is_active", None)
    return bool(
        _RLS_POLICY_TABLES
        and callable(verifier)
        and all(bool(verifier(table)) for table in _RLS_POLICY_TABLES)
    )


def _postgres_tls_verified(world: Any) -> bool:
    """Verify both negotiated PostgreSQL TLS and hostname-validation policy."""
    if world is None or not hasattr(world, "_tx"):
        return False
    with world._tx() as cursor:
        connection = getattr(cursor, "connection", None)
        info = getattr(connection, "info", None)
        params = info.get_parameters() if info is not None else {}
        sslmode = str((params or {}).get("sslmode") or "").strip().lower()
        if sslmode != "verify-full":
            return False
        cursor.execute(
            "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()",
        )
        row = cursor.fetchone()
    return bool(row and row[0] is True)


def _queue_tls_verified() -> bool:
    from .queue_dispatcher import (
        _configured_arq_redis_settings,
        _queue_redis_config,
        _redis_host_is_loopback,
        _redis_tls_verified,
    )

    cfg = _queue_redis_config()
    backend = str(cfg.get("backend") or "").strip().lower()
    if not backend:
        return True
    if backend != "arq":
        return False
    from arq.connections import RedisSettings

    settings = _configured_arq_redis_settings(RedisSettings)
    return bool(
        getattr(settings, "unix_socket_path", None)
        or _redis_host_is_loopback(getattr(settings, "host", None))
        or _redis_tls_verified(settings)
    )


def _transport_tls_verified(world: Any) -> bool:
    from ._envparse import env_bool

    return bool(
        env_bool("MAVERICK_TLS_TERMINATED", False)
        and _postgres_tls_verified(world)
        and _queue_tls_verified()
    )


def _queue_snapshot() -> dict[str, int]:
    from .job_queue import JobQueue

    return JobQueue().health_snapshot(lease_seconds=QUEUE_LEASE_SECONDS)


def _kill_switch_clear() -> bool:
    from .killswitch import Halted, check

    try:
        check(force_refresh=True, fail_closed=True)
    except Halted:
        return False
    return True


def _policy_integrity_healthy() -> bool:
    from .config import config_source_errors, load_config
    from .governance import Policy
    from .runtime_overrides import denied_tools

    load_config()
    if config_source_errors():
        return False
    Policy.from_config()
    denied_tools()
    return True


def _expensive_signals(world: Any) -> tuple[AssuranceSignal, ...]:
    """Return cached remote/DB-heavy assurance checks."""
    global _EXPENSIVE_CACHE
    now = _monotonic()
    try:
        from .paths import current_tenant_id_strict

        tenant = current_tenant_id_strict()
    except Exception:  # failure-policy: fail_closed
        tenant = "<unreadable>"
    # A PostgresWorldModel is process-global while KMS and audit anchors are
    # tenant-scoped. Never reuse one tenant's successful custody/integrity
    # result in another tenant merely because they share the backend object.
    key = (id(world), tenant)
    with _CACHE_LOCK:
        cached = _EXPENSIVE_CACHE
        if (
            cached is not None
            and cached[1] == key
            and 0.0 <= now - cached[0] < _EXPENSIVE_CACHE_SECONDS
        ):
            return cached[2]

    signals = (
        _audit_anchor_signal(),
        _bool_signal(KMS_KEY_CUSTODY, _kms_key_custody_healthy),
        _bool_signal(POSTGRES_RLS_VERIFIED, lambda: _postgres_rls_verified(world)),
        _bool_signal(TRANSPORT_TLS_VERIFIED, lambda: _transport_tls_verified(world)),
    )
    with _CACHE_LOCK:
        _EXPENSIVE_CACHE = (now, key, signals)
    return signals


def collect_assurance(world: Any = None) -> AssuranceReport:
    """Collect the exact production assurance snapshot.

    Every probe fails closed to its own fixed signal instead of aborting the
    entire scrape.  This keeps ``/metrics`` available during an incident while
    ``/readyz`` still refuses traffic because ``report.healthy`` is false.
    """
    if not production_assurance_required():
        return AssuranceReport(enabled=False, healthy=True, signals=())

    counts = event_counts()
    try:
        queue = _queue_snapshot()
    except Exception:  # failure-policy: fail_closed
        queue = {"pending": -1, "failed": -1, "stale_running": -1}

    signals = [
        _bool_signal(SHIELD_ENFORCING, _shield_enforcing),
        _at_most_signal(
            SHIELD_FAILURES,
            counts[SHIELD_ENFORCEMENT_FAILURE],
            EVENT_MAX,
        ),
        _at_most_signal(SHIELD_BYPASSES, counts[SHIELD_BYPASS], EVENT_MAX),
        _at_most_signal(
            DURABLE_AUDIT_REFUSALS,
            counts[DURABLE_AUDIT_REFUSAL],
            EVENT_MAX,
        ),
        _at_most_signal(
            AUDIT_FORWARDER_FAILURES,
            counts[AUDIT_FORWARDER_FAILURE],
            EVENT_MAX,
        ),
        _at_most_signal(
            AUDIT_FORWARDER_BACKLOG,
            lambda: _audit_forwarder_backlog(world),
            AUDIT_FORWARDER_BACKLOG_MAX,
        ),
        _bool_signal(CONTROL_PLANE_LEASE_OWNED, _control_plane_lease_owned),
        _at_most_signal(
            INDETERMINATE_SIDE_EFFECTS,
            counts[INDETERMINATE_SIDE_EFFECT],
            EVENT_MAX,
        ),
        _at_most_signal(
            QUEUE_PENDING,
            queue.get("pending", -1),
            QUEUE_PENDING_MAX,
        ),
        _at_most_signal(
            QUEUE_DEAD_LETTER,
            queue.get("failed", -1),
            QUEUE_DEAD_LETTER_MAX,
        ),
        _at_most_signal(
            QUEUE_STALE_LEASES,
            queue.get("stale_running", -1),
            QUEUE_STALE_LEASE_MAX,
        ),
        _bool_signal(KILL_SWITCH_CLEAR, _kill_switch_clear),
        _bool_signal(POLICY_INTEGRITY, _policy_integrity_healthy),
        _at_most_signal(
            POLICY_FALLBACKS,
            counts[POLICY_FALLBACK],
            EVENT_MAX,
        ),
        *_expensive_signals(world),
    ]
    ordered = tuple(sorted(signals, key=lambda signal: SIGNAL_NAMES.index(signal.name)))
    if tuple(signal.name for signal in ordered) != SIGNAL_NAMES:
        # The module's own coverage contract is exact.  A developer adding or
        # deleting a collector cannot silently omit the signal from readiness.
        raise RuntimeError("production assurance signal inventory drifted")
    return AssuranceReport(
        enabled=True,
        healthy=all(signal.healthy for signal in ordered),
        signals=ordered,
    )


def health_checks(report: AssuranceReport) -> dict[str, str]:
    """Render safe readiness details without infrastructure identifiers."""
    if not report.enabled:
        return {}
    return {
        f"production_{signal.name}": (
            "ok"
            if signal.healthy
            else f"fail: value={signal.value:g} threshold={signal.threshold:g}"
        )
        for signal in report.signals
    }


def prometheus_lines(report: AssuranceReport) -> list[str]:
    """Render fixed-label Prometheus text for one assurance report."""
    if not report.enabled:
        return []
    lines = [
        "# HELP maverick_production_assurance_state Production assurance check (1 healthy, 0 failed or unknown)",
        "# TYPE maverick_production_assurance_state gauge",
    ]
    for signal in report.signals:
        lines.append(
            "maverick_production_assurance_state"
            f'{{signal="{signal.name}"}} {1 if signal.healthy else 0}'
        )
    lines += [
        "# HELP maverick_production_assurance_value Current bounded assurance value",
        "# TYPE maverick_production_assurance_value gauge",
    ]
    for signal in report.signals:
        lines.append(
            "maverick_production_assurance_value"
            f'{{signal="{signal.name}"}} {signal.value:g}'
        )
    lines += [
        "# HELP maverick_production_assurance_threshold Readiness and alert threshold for the assurance value",
        "# TYPE maverick_production_assurance_threshold gauge",
    ]
    for signal in report.signals:
        lines.append(
            "maverick_production_assurance_threshold"
            f'{{signal="{signal.name}"}} {signal.threshold:g}'
        )
    lines += [
        "# HELP maverick_production_assurance_events_total Production enforcement and ambiguity events",
        "# TYPE maverick_production_assurance_events_total counter",
    ]
    for name, count in event_counts().items():
        lines.append(
            "maverick_production_assurance_events_total"
            f'{{signal="{name}"}} {count}'
        )
    return lines


def reset_for_testing() -> None:
    """Clear process-local counters and cached probes (tests only)."""
    global _EXPENSIVE_CACHE
    with _EVENT_LOCK:
        for name in EVENT_SIGNALS:
            _EVENT_COUNTS[name] = 0
    with _CACHE_LOCK:
        _EXPENSIVE_CACHE = None


__all__ = [
    "AUDIT_FORWARDER_FAILURE",
    "AssuranceReport",
    "AssuranceSignal",
    "DURABLE_AUDIT_REFUSAL",
    "EVENT_SIGNALS",
    "INDETERMINATE_SIDE_EFFECT",
    "POLICY_FALLBACK",
    "SHIELD_BYPASS",
    "SHIELD_ENFORCEMENT_FAILURE",
    "SIGNAL_NAMES",
    "collect_assurance",
    "event_counts",
    "health_checks",
    "production_assurance_required",
    "prometheus_lines",
    "record_event",
    "reset_for_testing",
]
