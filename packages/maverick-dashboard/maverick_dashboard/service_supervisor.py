"""Supervised Lightwork services started from Settings > Maintenance.

The background worker (``maverick worker``), the chat channel server
(``maverick serve``) and the tool server for outside apps (``maverick mcp``,
over local HTTP) are ordinarily started by the desktop app or the server's own
service manager. Neither exposes a start/stop protocol to the dashboard, so an
administrator who starts one here gets a child process this dashboard owns:

* started from a fixed argument list (never a shell) through
  :class:`maverick.owned_process.OwnedProcess`, which contains the whole
  process tree;
* one instance per kind;
* output drained into a small bounded log (secrets masked) so a stopped
  service can say why;
* stopped politely, then forcibly, on request and when the dashboard shuts
  down (registered with the dashboard's shutdown supervisor, plus an exit hook).

A service started some other way is not visible here and is never touched.
"""
from __future__ import annotations

import atexit
import collections
import os
import queue
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

MCP_PORT = 8771
KINDS: dict[str, dict[str, Any]] = {
    "worker": {"label": "Background worker", "args": ("worker",),
               "about": "Runs scheduled and queued tasks."},
    "channels": {"label": "Chat channel server", "args": ("serve",),
                 "about": "Receives and answers messages from your connected chat apps."},
    "tools": {"label": "Tool server for outside apps",
              "args": ("mcp", "--http", "--host", "127.0.0.1", "--port", str(MCP_PORT)),
              "about": "Lets outside apps on this computer use Lightwork's tools."},
}
LOG_LINES = 200
LOG_LINE_CHARS = 400
STOP_GRACE_SECONDS = 10.0

_LOCK = threading.Lock()
_threads: list[threading.Thread] = []


@dataclass
class Service:
    kind: str
    process: Any = None
    started_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    last_error: str = ""
    stopping: bool = False
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=LOG_LINES))


_SERVICES: dict[str, Service] = {kind: Service(kind) for kind in KINDS}


class ServiceError(RuntimeError):
    """A plain sentence the page shows."""


def command(kind: str) -> list[str]:
    """The fixed argument list for ``kind``: the same interpreter (or frozen
    release binary) as this dashboard, running the ``maverick`` command."""
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "maverick.cli"]
    return [*base, *KINDS[kind]["args"]]


def _scrub(text: str) -> str:
    try:
        from maverick.safety.secret_detector import redact
        text, _ = redact(text)
    except Exception:  # noqa: BLE001 -- masking is best effort; lines stay bounded
        pass
    return text[:LOG_LINE_CHARS]


def _spawn(kind: str):
    from maverick.owned_process import OwnedProcess
    return OwnedProcess(command(kind), env=dict(os.environ))


def _drain(service: Service, process: Any) -> None:
    """Keep the child's bounded pipes flowing into the bounded log, and notice
    when it exits."""
    pending = {"stdout": b"", "stderr": b""}
    open_streams = {"stdout", "stderr"}
    while open_streams or process.proc.poll() is None:
        idle = True
        for name in tuple(open_streams):
            try:
                chunk = getattr(process, name).get(timeout=0.05)
            except queue.Empty:
                continue
            idle = False
            if chunk is None:
                open_streams.discard(name)
                chunk = b"\n"
            pending[name] += chunk
            *lines, pending[name] = pending[name].split(b"\n")
            with _LOCK:
                for line in lines:
                    if line.strip():
                        service.log.append(_scrub(line.decode("utf-8", errors="replace")))
        if idle and not open_streams:
            time.sleep(0.05)
    code = process.proc.returncode
    with _LOCK:
        if service.process is process:
            service.exit_code = code
            service.stopped_at = time.time()
            if not service.stopping and code not in (0, None):
                tail = [line for line in list(service.log)[-3:] if line]
                service.last_error = (f"Stopped unexpectedly (exit code {code})."
                                      + (" Last message: " + tail[-1] if tail else ""))
            service.process = None
            service.stopping = False
    try:
        process.close()
    except Exception:  # noqa: BLE001 -- the tree is already gone; cleanup is best effort
        pass


def _running(service: Service) -> bool:
    return service.process is not None and service.process.proc.poll() is None


def status() -> list[dict[str, Any]]:
    with _LOCK:
        out = []
        for kind, meta in KINDS.items():
            service = _SERVICES[kind]
            out.append({
                "kind": kind, "label": meta["label"], "about": meta["about"],
                "running": _running(service),
                "since": service.started_at if _running(service) else service.stopped_at,
                "last_error": service.last_error,
                "log": list(service.log)[-40:],
            })
        return out


def start(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ServiceError("Choose a service from the list.")
    with _LOCK:
        service = _SERVICES[kind]
        if _running(service):
            raise ServiceError(f"{KINDS[kind]['label']} is already running.")
        try:
            process = _spawn(kind)
        except OSError as exc:
            service.last_error = "Could not start: the program could not be launched."
            raise ServiceError(f"{KINDS[kind]['label']} could not be started on this computer.") from exc
        service.process = process
        service.started_at = time.time()
        service.stopped_at = None
        service.exit_code = None
        service.last_error = ""
        service.stopping = False
        service.log.clear()
        thread = threading.Thread(target=_drain, args=(service, process), name=f"lightwork-service-{kind}",
                                  daemon=True)
        _threads[:] = [t for t in _threads if t.is_alive()]
        _threads.append(thread)
    thread.start()
    return next(row for row in status() if row["kind"] == kind)


def _signal_stop(service: Service) -> Any:
    process = service.process
    if process is None or process.proc.poll() is not None:
        return None
    service.stopping = True
    try:
        if os.name == "nt":
            process.proc.terminate()
        else:
            os.killpg(process.proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass
    return process


def _finish_stop(process: Any, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while process.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.proc.poll() is None:
        process.stop_tree()


def stop(kind: str, *, timeout: float = STOP_GRACE_SECONDS) -> dict[str, Any]:
    if kind not in KINDS:
        raise ServiceError("Choose a service from the list.")
    with _LOCK:
        process = _signal_stop(_SERVICES[kind])
    if process is None:
        raise ServiceError(f"{KINDS[kind]['label']} is not running from this app.")
    _finish_stop(process, timeout)
    return next(row for row in status() if row["kind"] == kind)


def restart(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise ServiceError("Choose a service from the list.")
    with _LOCK:
        process = _signal_stop(_SERVICES[kind])
    if process is not None:
        _finish_stop(process, STOP_GRACE_SECONDS)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with _LOCK:
                if not _running(_SERVICES[kind]) and _SERVICES[kind].process is None:
                    break
            time.sleep(0.02)
    return start(kind)


def stop_all(*, timeout: float = STOP_GRACE_SECONDS, wait: bool = True) -> None:
    """Dashboard shutdown: signal every owned service; with ``wait`` also end them."""
    with _LOCK:
        processes = [p for p in (_signal_stop(s) for s in _SERVICES.values()) if p is not None]
        if not processes and wait:
            processes = [s.process for s in _SERVICES.values() if s.process is not None]
    if wait:
        per = timeout / max(1, len(processes)) if processes else 0
        for process in processes:
            _finish_stop(process, per)


atexit.register(lambda: stop_all(timeout=2.0))

__all__ = ["KINDS", "MCP_PORT", "ServiceError", "command", "status", "start", "stop", "restart", "stop_all"]
