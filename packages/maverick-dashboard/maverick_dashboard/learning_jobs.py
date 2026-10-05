"""Background jobs for the learning, training and workforce buttons.

A button that runs a learning pass, trains a model or checks every
specialist must not hold the request open, so the route starts a job here
and the page polls its status. Jobs live in this process only (a restart
forgets them, and the page then says so); each one runs in a thread with a
copy of the request's context, so the workspace and identity the kernel sees
are the caller's.

Stopping is cooperative. A job that can stop early says how: ``on_cancel``
tightens its own spending limit (so the kernel's next ``budget.check()``
ends it) and the job's function checks ``job.cancelled`` between steps. A
job with no safe stopping point cannot be stopped, and the page offers no
button for it. Only one job of a kind runs at a time, and at most a few run
at once, so a button pressed twice never doubles the spend.
"""
from __future__ import annotations

import contextvars
import copy
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

MAX_RUNNING = 3
_MAX_KEPT = 40
_FAILED = "This task stopped because of a problem. Work completed before the failure may remain. Read its current result before deciding what to do next; do not repeat it automatically."


class JobRefused(Exception):
    """A job could not start; the message is written for people."""


@dataclass
class Job:
    id: str
    kind: str
    label: str
    started_by: str
    workspace: str
    needs_global: bool
    cancellable: bool
    started_at: float = field(default_factory=time.time)
    state: str = "running"  # running | stopping | done | failed | stopped
    finished_at: float | None = None
    result: dict | None = None
    problem: str = ""
    on_cancel: Callable[[], None] | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _stop_receipt: dict | None = field(default=None, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def active(self) -> bool:
        return self.state in ("running", "stopping")

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "label": self.label,
                "state": self.state, "started_at": self.started_at,
                "finished_at": self.finished_at, "can_stop": self.cancellable and self.active,
                "result": self.result, "problem": self.problem}


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _prune() -> None:
    finished = sorted((j for j in _jobs.values() if not j.active), key=lambda j: j.started_at)
    while len(_jobs) > _MAX_KEPT and finished:
        _jobs.pop(finished.pop(0).id, None)


def _run(job: Job, fn: Callable[[Job], dict], problem: Callable[[Exception], str | None]) -> None:
    try:
        result = fn(job)
    except Exception as exc:  # noqa: BLE001 -- every failure becomes one plain sentence
        text = None
        try:
            text = problem(exc)
        except Exception:  # noqa: BLE001 -- the mapper must never hide the failure
            text = None
        if text is None:
            log.warning("dashboard job %s (%s) failed", job.id, job.kind, exc_info=True)
        with _lock:
            job.problem = text or _FAILED
            job.state = "failed"
            job.finished_at = time.time()
        return
    with _lock:
        job.result = result if isinstance(result, dict) else {}
        job.state = "stopped" if job.cancelled else "done"
        job.finished_at = time.time()


def start(kind: str, label: str, fn: Callable[[Job], dict], *, started_by: str,
          workspace: str = "", needs_global: bool = True, cancellable: bool = False,
          on_cancel: Callable[[], None] | None = None,
          problem: Callable[[Exception], str | None] = lambda exc: None) -> Job:
    """Start ``fn(job)`` in a thread and return the job.

    ``problem`` turns an exception into a sentence for people (``None`` keeps
    the generic one and logs the failure). Raises :class:`JobRefused` when the
    same kind is already running or too many jobs are running."""
    with _lock:
        running = [j for j in _jobs.values() if j.active]
        if any(j.kind == kind for j in running):
            raise JobRefused("This is already running. Wait for it to finish, or stop it first.")
        if len(running) >= MAX_RUNNING:
            raise JobRefused("Too many jobs are running right now. Try again when one finishes.")
        job = Job(id=uuid.uuid4().hex[:16], kind=kind, label=label, started_by=started_by,
                  workspace=workspace, needs_global=needs_global, cancellable=cancellable,
                  on_cancel=on_cancel)
        _jobs[job.id] = job
        _prune()
    ctx = contextvars.copy_context()
    threading.Thread(target=ctx.run, args=(_run, job, fn, problem), daemon=True,
                     name=f"lightwork-job-{kind}").start()
    return job


def get(job_id: str) -> Job | None:
    with _lock:
        return _jobs.get(job_id)


def listed() -> list[Job]:
    with _lock:
        return sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)


def current_view(job: Job) -> dict:
    """A coherent retained view; private result values never escape by reference."""
    with _lock:
        if _jobs.get(job.id) is not job:
            raise JobRefused("The original result is no longer available. Its absence does not mean the work failed; do not repeat it automatically.")
        return copy.deepcopy(job.view())


def _revision(job: Job) -> str:
    from maverick.governed_operations import digest

    return digest(job.view())


def reviewed_view(job: Job) -> dict:
    with _lock:
        if _jobs.get(job.id) is not job:
            raise JobRefused("The original result is no longer available. No work was repeated.")
        return {"job": copy.deepcopy(job.view()), "revision": _revision(job)}


def stop_receipt(job: Job, operation: str | None, actor: str, context: str) -> dict | None:
    with _lock:
        row = job._stop_receipt if _jobs.get(job.id) is job else None
        if row is None or (row["actor"], row["context"]) != (actor, context) or operation is not None and row["operation"] != operation:
            return None
        return copy.deepcopy(row)


def cancel_reviewed(job: Job, revision: str, operation: str, actor: str, context: str,
                    before_stop: Callable[[], None]) -> dict:
    """Bind one reviewed stop to its original actor inside the native job lock."""
    with _lock:
        if _jobs.get(job.id) is not job:
            raise JobRefused("The original result is unavailable. No work was repeated.")
        row = job._stop_receipt
        if row is not None and (row["operation"], row["actor"], row["context"]) == (operation, actor, context):
            return copy.deepcopy(row)
        if _revision(job) != revision or job.state != "running" or not job.cancellable or job.cancelled:
            raise JobRefused("This task changed or can no longer stop part-way. Read its current progress and review again. Nothing was stopped by this request.")
        before_stop()
        job._cancel.set()
        job.state = "stopping"
        row = {"operation": operation, "actor": actor, "context": context,
               "job": job.id, "revision": revision, "requested_at": time.time()}
        job._stop_receipt = row
        hook = job.on_cancel
    if hook is not None:
        try:
            hook()
        except Exception:  # noqa: BLE001 -- the native safe-point stop flag is retained
            log.debug("job %s stop hook failed", job.id, exc_info=True)
    return copy.deepcopy(row)


def cancel(job: Job) -> bool:
    """Ask a job to stop at its next safe point. False when it cannot stop."""
    with _lock:
        if not (job.cancellable and job.active):
            return False
        job._cancel.set()
        job.state = "stopping"
        hook = job.on_cancel
    if hook is not None:
        try:
            hook()
        except Exception:  # noqa: BLE001 -- the stop flag is already set
            log.debug("job %s stop hook failed", job.id, exc_info=True)
    return True


def stop_budget(budget) -> Callable[[], None]:
    """An ``on_cancel`` that ends a kernel run at its next ``budget.check()``:
    it lowers the run's own wall-clock limit to the time already used. It
    only ever tightens the limit, so no spending guard is loosened."""
    def _stop() -> None:
        used = max(0.0, float(budget.elapsed()))
        current = budget.max_wall_seconds
        if current is None or current > used:
            budget.max_wall_seconds = used
    return _stop


def clear_for_tests() -> None:
    with _lock:
        _jobs.clear()


__all__ = ["Job", "JobRefused", "MAX_RUNNING", "cancel", "clear_for_tests", "get",
           "listed", "start", "stop_budget"]
