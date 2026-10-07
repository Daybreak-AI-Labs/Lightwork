"""Background thread for deadline alerts and webhook retries.

One daemon thread, started with the dashboard, does two jobs:

* every ``[deadline_alerts] poll_seconds`` (default 15 minutes) it runs
  :func:`maverick.deadline_alerts.run_cycle` for the active workspace, which
  alerts people about requests, incidents, assessments, vendor contracts and
  approvals that are due -- once per record and state, recorded in the ledger;
* every ``_RETRY_PUMP_SECONDS`` it runs
  :func:`maverick.webhook_subscriptions.process_due`, so a webhook delivery
  whose receiver was down is retried on its backoff schedule even when no new
  event arrives;
* every hour it re-checks the compliance protections
  (:func:`maverick.compliance_history.recheck_if_due`), so a protection that
  breaks shows as failing within the hour, with when it changed.

The retry pump runs whenever the thread runs; the deadline evaluation only
when alerts are enabled. Both are wrapped so one bad cycle is logged and the
next one still happens. Stopped through the dashboard's shutdown supervisor
(see ``_lifecycle.register_main_components``).
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

_scheduler_lock = threading.Lock()
_scheduler_stop: threading.Event | None = None
_scheduler_thread: threading.Thread | None = None
_RETRY_PUMP_SECONDS = 30.0
_last_cycle: dict = {}


def _world():
    from ._shared import _world as shared_world
    return shared_world()


def run_once(*, now: float | None = None) -> dict:
    """One evaluation cycle plus one retry pump (the API's "run now")."""
    from maverick import deadline_alerts, webhook_subscriptions

    world = _world()
    summary = deadline_alerts.run_cycle(now=now, world=world)
    summary["webhook_retries"] = webhook_subscriptions.process_due(world=world, now=now)
    summary["ran_at"] = time.time() if now is None else float(now)
    _last_cycle.clear()
    _last_cycle.update({k: v for k, v in summary.items() if k != "alerts"})
    return summary


def last_cycle() -> dict:
    return dict(_last_cycle)


def _scheduler_loop(stop: threading.Event) -> None:
    from maverick import deadline_alerts, webhook_subscriptions

    next_eval = 0.0
    while not stop.is_set():
        now = time.time()
        try:
            webhook_subscriptions.process_due(world=_world(), now=now)
        except Exception:  # noqa: BLE001 - one bad pump must not end the thread
            log.warning("webhook retry pump failed", exc_info=True)
        try:
            from maverick.compliance_history import recheck_if_due

            recheck_if_due(now=now)
        except Exception:  # noqa: BLE001 - a failed check is retried next pass
            log.warning("compliance re-check failed", exc_info=True)
        try:
            cfg = deadline_alerts.settings()
            if cfg["enabled"] and now >= next_eval:
                run_once(now=now)
                next_eval = now + cfg["poll_seconds"]
        except Exception:  # noqa: BLE001
            log.warning("deadline alert cycle failed", exc_info=True)
            next_eval = now + 300.0
        stop.wait(_RETRY_PUMP_SECONDS)


def start_deadline_alerts_scheduler() -> bool:
    """Start the thread unless it is already running. Returns True when started."""
    global _scheduler_stop, _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return False
        stop = threading.Event()
        worker = threading.Thread(target=_scheduler_loop, args=(stop,),
                                  name="deadline-alerts-scheduler", daemon=True)
        _scheduler_stop = stop
        _scheduler_thread = worker
        worker.start()
        return True


def stop_deadline_alerts_scheduler(*, timeout: float = 5.0, wait: bool = True) -> bool:
    """Signal first; retain a stopping worker until its exit is observed."""
    global _scheduler_stop, _scheduler_thread
    with _scheduler_lock:
        stop = _scheduler_stop
        worker = _scheduler_thread
        if stop is not None:
            stop.set()
    if stop is None or worker is None:
        return False
    if not wait:
        return not worker.is_alive()
    worker.join(max(0.0, min(float(timeout), 30.0)))
    if worker.is_alive():
        return False
    with _scheduler_lock:
        if _scheduler_thread is worker:
            _scheduler_stop = None
            _scheduler_thread = None
    return True


__all__ = ["last_cycle", "run_once", "start_deadline_alerts_scheduler",
           "stop_deadline_alerts_scheduler"]
