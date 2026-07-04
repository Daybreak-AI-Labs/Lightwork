"""JobQueue-backed automation for the dashboard.

Replaces the old bespoke asyncio scheduler with maverick-core's durable
:class:`~maverick.job_queue.JobQueue` + :class:`~maverick.worker.Worker`:

* **One recurring ``automation_tick`` cron job** drives polling. Each run reads
  the event-trigger store and polls only the triggers whose per-trigger interval
  is due, so every trigger has its own cadence (``interval_seconds``) without a
  cron job per trigger -- and create/delete need no queue bookkeeping, the next
  tick just sees the new store.
* **Fired goals are enqueued as durable ``run_goal`` jobs**, so a transient run
  failure is retried with backoff and a poison one lands in the ``failed``
  dead-letter state (``maverick queue failed``) -- instead of a bare daemon
  thread whose failure was lost. A small pool of worker threads drains them so a
  slow goal doesn't stall polling.
* **Dreaming is its own ``dream_cycle`` cron job**, re-armed by the worker.
* JobQueue's exactly-once ``claim`` dedupes the tick/dream *jobs*; a non-blocking
  ``flock`` around the poll body dedupes the *poll/cursor* critical section, so
  two overlapping ticks (N dashboard workers, a slow source) can't both advance
  the same cursor and double-create goals.

It runs INSIDE the dashboard process (the only one that can read the dashboard-
owned trigger store) on its OWN ``automation-jobs.db``, so its dashboard-specific
job kinds never reach -- and get dead-lettered by -- a core ``maverick worker``
draining the main queue. TestClient never starts it (the lifespan doesn't run in
tests); the handlers/arming are unit-tested directly.
"""
from __future__ import annotations

import contextlib
import logging
import os
import threading
import time

from maverick import config

log = logging.getLogger("maverick_dashboard.automation_queue")

TICK_KIND = "automation_tick"
DREAM_KIND = "dream_cycle"
FLYWHEEL_KIND = "flywheel_cycle"
_MIN_INTERVAL = 60               # floor for a per-trigger cadence (matches the UI/schema)
_DEFAULT_INTERVAL = 300          # per-trigger poll cadence when unset (5 min)
_TICK_CRON = "* * * * *"         # evaluate due triggers every minute
_DREAM_CRON_DEFAULT = "0 */6 * * *"  # consolidate every 6h
_FLYWHEEL_CRON_DEFAULT = "30 */6 * * *"  # turn the flywheel every 6h (offset from dream)
_DEFAULT_WORKERS = 3             # goal-runner threads (so a slow goal can't stall the tick)

_LOCK = threading.RLock()   # reentrant: start()/stop() call queue(), which also locks
_queue = None
_worker = None
_worker_threads: list = []


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


def _cfg_int(section: str, key: str) -> int | None:
    try:
        v = (config.load_config().get(section) or {}).get(key)
        return int(v) if v is not None else None
    except Exception:  # pragma: no cover -- config never blocks the scheduler
        return None


# ---- per-trigger due tracking (a marker mtime, so an idle poll never rewrites
# the trigger store) --------------------------------------------------------

def _poll_marker(name: str):
    return config.dashboard_overrides_path().parent / "automation-poll" / name


def _default_interval() -> int:
    v = _cfg_int("automation", "default_interval_seconds")
    return max(_MIN_INTERVAL, v) if v else _DEFAULT_INTERVAL


def _interval(trig: dict) -> int:
    """Per-trigger cadence: a set value floored at 60s (matching the schema/UI),
    else the configurable default."""
    try:
        v = int(trig.get("interval_seconds") or 0)
    except (TypeError, ValueError):
        return _default_interval()
    return max(_MIN_INTERVAL, v) if v > 0 else _default_interval()


def _due(name: str, interval: int) -> bool:
    p = _poll_marker(name)
    try:
        return (time.time() - p.stat().st_mtime) >= interval
    except FileNotFoundError:
        return True
    except Exception:  # pragma: no cover -- a stat error shouldn't mute a trigger
        return True


def _mark_polled(name: str) -> None:
    p = _poll_marker(name)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()   # sets mtime to now
    except Exception:  # pragma: no cover
        pass


@contextlib.contextmanager
def _poll_guard():
    """Non-blocking cross-process lock around the poll body. Yields True when
    acquired (this process polls), False when another process is already polling
    (skip). JobQueue.claim dedupes the tick JOB; this dedupes the poll/cursor
    read-modify-write it performs, so overlapping ticks can't double-fire."""
    try:
        import fcntl
    except Exception:  # pragma: no cover -- non-POSIX: no cross-process guard
        yield True
        return
    p = config.dashboard_overrides_path().parent / "automation-poll.lock"
    fd = None
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:  # pragma: no cover
                pass
            os.close(fd)


# ---- handlers ---------------------------------------------------------------

def _world():
    from maverick_dashboard.app import _world as _w
    return _w()


def _poll_due_triggers() -> None:
    from maverick_dashboard import event_poll, event_triggers_store
    due = [t for t in event_triggers_store.list_triggers() if _due(t["name"], _interval(t))]
    if not due:
        return
    q = queue()

    def _fire(goal_id: int) -> None:
        q.enqueue("run_goal", {"goal_id": int(goal_id)})   # durable: retry + dead-letter

    results = event_poll.poll_and_fire(_world(), due, fire=_fire)
    # Advance the due-marker only for triggers that actually polled; a trigger
    # whose source errored is left un-marked so it retries on the next tick
    # rather than going dark for a full interval.
    errored = {r["name"] for r in results if r.get("poll_error")}
    for t in due:
        if t["name"] not in errored:
            _mark_polled(t["name"])


def _handle_tick(_job=None) -> None:
    """Poll every DUE event trigger and fire its goals as durable run_goal jobs.
    Fail-open: a raise here must NOT fail the job (which would retry-storm every
    60s); the next cron tick re-runs. Single-polled across workers via flock."""
    from maverick import automation_events
    if not automation_events.enabled():
        return
    try:
        with _poll_guard() as acquired:
            if acquired:
                _poll_due_triggers()
    except Exception:  # pragma: no cover -- fail-open, never retry-storm
        log.exception("automation tick failed")


def _handle_dream(_job=None) -> None:
    """Fail-open: swallow errors so a partial dream isn't re-applied by the
    worker's retry (dream_cycle is not transactional); the next cron runs it."""
    from maverick import dreaming
    if not dreaming.enabled():
        return
    try:
        dreaming.dream_cycle(_world())
    except Exception:  # pragma: no cover -- fail-open
        log.exception("scheduled dream cycle failed")


def _handle_flywheel(_job=None) -> None:
    """Turn the Cognitive Data Engine flywheel over the accumulated Operating
    Record + grounded outcomes. This is the missing scheduled driver: outcomes
    (failures, sign-offs, feedback, real system-of-record results) land
    continuously, but nothing consolidated them into guardrails/habits without a
    manual `maverick flywheel`. Gated (`maybe_run` is a no-op unless
    ``[data_engine]`` is on) and fail-open like the dream cycle."""
    from maverick import flywheel
    try:
        flywheel.maybe_run()
    except Exception:  # pragma: no cover -- fail-open
        log.exception("scheduled flywheel cycle failed")


# ---- cron seeding -----------------------------------------------------------

def _pending(kind: str) -> list:
    return [j for j in queue().list(status="pending", limit=1000) if j.kind == kind]


def _ensure_cron(kind: str, expr: str) -> None:
    """Ensure exactly one recurring job of ``kind`` is armed. The worker re-arms
    it on each run (payload carries ``__cron__``); this seeds the first one, and
    collapses to one lineage if two dashboard workers happened to seed
    concurrently at boot (both saw no pending job before either enqueued). A
    surviving duplicate is harmless anyway -- the poll flock serializes them."""
    from maverick.scheduler import schedule_cron
    q = queue()
    pending = _pending(kind)
    if pending:
        for extra in pending[1:]:
            q.cancel(extra.id)
        return
    schedule_cron(q, expr, kind, {"__cron__": expr})


def _dream_cron() -> str:
    try:
        expr = str((config.load_config().get("dreaming") or {}).get("cron") or "").strip()
        return expr or _DREAM_CRON_DEFAULT
    except Exception:  # pragma: no cover
        return _DREAM_CRON_DEFAULT


def _flywheel_cron() -> str:
    try:
        expr = str((config.load_config().get("data_engine") or {}).get("cron") or "").strip()
        return expr or _FLYWHEEL_CRON_DEFAULT
    except Exception:  # pragma: no cover
        return _FLYWHEEL_CRON_DEFAULT


def reconcile() -> None:
    """Seed the tick + dream + flywheel cron jobs for whichever subsystems are
    enabled."""
    from maverick import automation_events, data_engine, dreaming
    if automation_events.enabled():
        _ensure_cron(TICK_KIND, _TICK_CRON)
    if dreaming.enabled():
        _ensure_cron(DREAM_KIND, _dream_cron())
    if data_engine.enabled():
        _ensure_cron(FLYWHEEL_KIND, _flywheel_cron())


# ---- lifecycle --------------------------------------------------------------

def _worker_count() -> int:
    v = _cfg_int("automation", "workers")
    return max(1, min(8, v)) if v else _DEFAULT_WORKERS


def start() -> bool:
    """Start the automation worker pool + seed cron jobs. No-op when none of
    event triggers, dreaming, or the data engine is enabled, and idempotent.
    Returns whether the pool was started."""
    global _worker, _worker_threads
    from maverick import automation_events, data_engine, dreaming
    if not (automation_events.enabled() or dreaming.enabled() or data_engine.enabled()):
        return False
    with _LOCK:
        if _worker_threads:
            return True
        from maverick.worker import Worker
        w = Worker(queue=queue())
        w.register(TICK_KIND, _handle_tick)
        w.register(DREAM_KIND, _handle_dream)
        w.register(FLYWHEEL_KIND, _handle_flywheel)
        # Seed the cron jobs + actually start the threads BEFORE committing to the
        # module globals, so a failure in reconcile() (e.g. a boot-time sqlite
        # write error) leaves us cleanly un-started and retryable -- not wedged
        # with recorded-but-never-started threads that stop() would join().
        reconcile()
        # One Worker, N threads sharing it: JobQueue.claim is atomic, so the
        # threads safely divide the jobs -- a slow run_goal on one thread can't
        # block the tick/dream on another.
        threads = [
            threading.Thread(target=w.run_forever, name=f"automation-worker-{i}", daemon=True)
            for i in range(_worker_count())
        ]
        for t in threads:
            t.start()
        _worker = w
        _worker_threads = threads
    log.info("automation worker started (%d threads, db=%s)", len(threads), _db_path())
    return True


def stop() -> None:
    global _worker, _worker_threads
    with _LOCK:
        w = _worker
        threads = list(_worker_threads)
        _worker = None
        _worker_threads = []
    if w is not None:
        w.stop()
    for t in threads:
        t.join(timeout=5.0)


__all__ = ["start", "stop", "reconcile", "queue",
           "TICK_KIND", "DREAM_KIND", "FLYWHEEL_KIND"]
