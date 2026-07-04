"""JobQueue-backed automation for the dashboard.

Replaces the old bespoke asyncio + ``fcntl.flock`` scheduler with maverick-core's
durable :class:`~maverick.job_queue.JobQueue` + :class:`~maverick.worker.Worker`:

* **One recurring ``automation_tick`` cron job** drives polling. Each run reads
  the event-trigger store and polls only the triggers whose per-trigger interval
  is due, so every trigger has its own cadence (``interval_seconds``) without a
  cron job per trigger -- and create/delete need no queue bookkeeping, the next
  tick just sees the new store.
* **Fired goals are enqueued as durable ``run_goal`` jobs**, so a transient run
  failure is retried with backoff and a poison one lands in the ``failed``
  dead-letter state (``maverick queue failed``) -- instead of a bare daemon
  thread whose failure was lost.
* **Dreaming is its own ``dream_cycle`` cron job**, re-armed by the worker.
* JobQueue's exactly-once ``claim`` is the single-consumer guarantee across
  dashboard workers -- the flock is gone.

It runs INSIDE the dashboard process (the only one that can read the dashboard-
owned trigger store) on its OWN ``automation-jobs.db``, so its dashboard-specific
job kinds never reach -- and get dead-lettered by -- a core ``maverick worker``
draining the main queue. TestClient never starts it (the lifespan doesn't run in
tests); the handlers/arming are unit-tested directly.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from maverick import config

log = logging.getLogger("maverick_dashboard.automation_queue")

TICK_KIND = "automation_tick"
DREAM_KIND = "dream_cycle"
_DEFAULT_INTERVAL = 300          # per-trigger poll cadence when unset (5 min)
_TICK_CRON = "* * * * *"         # evaluate due triggers every minute
_DREAM_CRON_DEFAULT = "0 */6 * * *"  # consolidate every 6h

_LOCK = threading.Lock()
_queue = None
_worker = None
_worker_thread = None


def _db_path():
    return config.dashboard_overrides_path().parent / "automation-jobs.db"


def queue():
    """The dashboard automation JobQueue (its own db). Singleton."""
    global _queue
    with _LOCK:
        if _queue is None:
            from maverick.job_queue import JobQueue
            _queue = JobQueue(db_path=_db_path())
        return _queue


# ---- per-trigger due tracking (a marker mtime, so an idle poll never rewrites
# the trigger store) --------------------------------------------------------

def _poll_marker(name: str):
    return config.dashboard_overrides_path().parent / "automation-poll" / name


def _interval(trig: dict) -> int:
    try:
        v = int(trig.get("interval_seconds") or 0)
    except (TypeError, ValueError):
        return _DEFAULT_INTERVAL
    return v if v >= 60 else _DEFAULT_INTERVAL


def _due(name: str, interval: int) -> bool:
    p = _poll_marker(name)
    try:
        return (time.time() - p.stat().st_mtime) >= interval
    except FileNotFoundError:
        return True
    except Exception:  # pragma: no cover -- never block a tick on a stat error
        return False


def _mark_polled(name: str) -> None:
    p = _poll_marker(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.utime(p, None)
    except Exception:  # pragma: no cover
        pass


# ---- handlers ---------------------------------------------------------------

def _world():
    from maverick_dashboard.app import _world as _w
    return _w()


def _handle_tick(_job=None) -> None:
    """Poll every DUE event trigger and fire its goals as durable run_goal jobs.
    A source/template error is recorded (firing history) inside poll_and_fire and
    doesn't fail the tick -- the next tick retries, and cursor dedup makes a
    re-poll idempotent."""
    from maverick import automation_events
    if not automation_events.enabled():
        return
    from maverick_dashboard import event_poll, event_triggers_store
    due = [t for t in event_triggers_store.list_triggers() if _due(t["name"], _interval(t))]
    if not due:
        return
    q = queue()

    def _fire(goal_id: int) -> None:
        q.enqueue("run_goal", {"goal_id": int(goal_id)})   # durable: retry + dead-letter

    event_poll.poll_and_fire(_world(), due, fire=_fire)
    for t in due:
        _mark_polled(t["name"])


def _handle_dream(_job=None) -> None:
    from maverick import dreaming
    if not dreaming.enabled():
        return
    dreaming.dream_cycle(_world())


# ---- cron seeding -----------------------------------------------------------

def _pending(kind: str) -> list:
    return [j for j in queue().list(status="pending", limit=1000) if j.kind == kind]


def _ensure_cron(kind: str, expr: str) -> None:
    """Ensure exactly one recurring job of ``kind`` is armed. The worker re-arms
    it on each run (payload carries ``__cron__``); this only seeds the first one
    and cancels any duplicate a prior double-seed may have left."""
    from maverick.scheduler import schedule_cron
    q = queue()
    pending = _pending(kind)
    if pending:
        for extra in pending[1:]:   # dedup: keep one lineage
            q.cancel(extra.id)
        return
    schedule_cron(q, expr, kind, {"__cron__": expr})


def _dream_cron() -> str:
    try:
        expr = str((config.load_config().get("dreaming") or {}).get("cron") or "").strip()
        return expr or _DREAM_CRON_DEFAULT
    except Exception:  # pragma: no cover
        return _DREAM_CRON_DEFAULT


def reconcile() -> None:
    """Seed the tick + dream cron jobs for whichever subsystems are enabled."""
    from maverick import automation_events, dreaming
    if automation_events.enabled():
        _ensure_cron(TICK_KIND, _TICK_CRON)
    if dreaming.enabled():
        _ensure_cron(DREAM_KIND, _dream_cron())


# ---- lifecycle --------------------------------------------------------------

def start() -> bool:
    """Start the automation worker (its own thread) + seed cron jobs. No-op when
    neither event triggers nor dreaming is enabled, and idempotent. Returns
    whether a worker was started."""
    global _worker, _worker_thread
    from maverick import automation_events, dreaming
    if not (automation_events.enabled() or dreaming.enabled()):
        return False
    q = queue()   # resolve the singleton BEFORE taking _LOCK (queue() locks too)
    with _LOCK:
        if _worker_thread is not None:
            return True
        from maverick.worker import Worker
        w = Worker(queue=q)
        w.register(TICK_KIND, _handle_tick)
        w.register(DREAM_KIND, _handle_dream)
        _worker = w
        _worker_thread = threading.Thread(
            target=w.run_forever, name="automation-worker", daemon=True)
        t = _worker_thread
    reconcile()
    t.start()
    log.info("automation worker started (db=%s)", _db_path())
    return True


def stop() -> None:
    global _worker, _worker_thread
    with _LOCK:
        w, t = _worker, _worker_thread
        _worker = _worker_thread = None
    if w is not None:
        w.stop()
    if t is not None:
        t.join(timeout=5.0)


__all__ = ["start", "stop", "reconcile", "queue", "TICK_KIND", "DREAM_KIND"]
