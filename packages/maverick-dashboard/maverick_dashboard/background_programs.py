"""Background programs the dashboard runs while their setting is on.

Three helpers used to exist only as ``python -m`` commands an operator had to
start by hand, so their settings changed nothing:

* performance profiling (``[perf.profiling] enabled``, ``MAVERICK_PROFILING``):
  ``maverick.profiling_daemon`` samples this dashboard's process with py-spy;
* the kernel activity monitor (``[ebpf_monitor] enable``): ``maverick.ebpf_monitor
  run`` follows this dashboard's process tree with bpftrace (it needs root and
  bpftrace, and otherwise stops straight away and says why);
* the model proxy (``[model_proxy] enable``): ``maverick.model_proxy`` holds the
  AI service key outside the agent process. The key itself stays in the
  server's environment (``MAVERICK_PROXY_KEY``) by design: a key kept in the
  settings would be loaded into the very process the proxy protects it from.

When the dashboard starts, each program whose setting is on is started as a
child process this dashboard owns:

* from a fixed argument list (never a shell) through
  :class:`maverick.owned_process.OwnedProcess`, which contains the whole
  process tree;
* one instance per kind;
* its output drained into a small bounded log (secrets masked) so a stopped
  program can say why;
* stopped politely, then forcibly, when the dashboard shuts down (registered
  with the dashboard's shutdown supervisor).

The settings take effect at the next start of the dashboard, which is why
their rows on Settings > All settings are marked "after a restart".
"""
from __future__ import annotations

import atexit
import collections
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

KINDS: dict[str, dict[str, Any]] = {
    "profiling": {"label": "Performance profiling", "module": "maverick.profiling_daemon",
                  "args": ("--pid", "{pid}")},
    "ebpf_monitor": {"label": "Kernel activity monitor", "module": "maverick.ebpf_monitor",
                     "args": ("run", "--pid", "{pid}")},
    "model_proxy": {"label": "Model proxy", "module": "maverick.model_proxy", "args": ()},
}
LOG_LINES = 100
LOG_LINE_CHARS = 400
STOP_GRACE_SECONDS = 5.0
_TRUE = {"1", "true", "yes", "on"}

_LOCK = threading.Lock()


@dataclass
class Program:
    kind: str
    process: Any = None
    started_at: float | None = None
    stopped_at: float | None = None
    exit_code: int | None = None
    last_error: str = ""
    drain: threading.Thread | None = None
    log: collections.deque = field(default_factory=lambda: collections.deque(maxlen=LOG_LINES))


_PROGRAMS: dict[str, Program] = {kind: Program(kind) for kind in KINDS}


def _setting(env: str, section: tuple[str, ...], key: str) -> bool:
    raw = os.environ.get(env, "").strip().lower() if env else ""
    if raw:
        return raw in _TRUE
    try:
        from maverick.config import load_config

        node: Any = load_config() or {}
        for part in section:
            node = node.get(part) if isinstance(node, dict) else None
        return isinstance(node, dict) and node.get(key) is True
    except Exception:  # an unreadable configuration never starts a program
        return False


def wanted(kind: str) -> bool:
    """Whether ``kind``'s setting asks for it to run (the environment wins)."""
    if kind == "profiling":
        return _setting("MAVERICK_PROFILING", ("perf", "profiling"), "enabled")
    if kind == "ebpf_monitor":
        return _setting("", ("ebpf_monitor",), "enable")
    if kind == "model_proxy":
        return _setting("", ("model_proxy",), "enable")
    raise KeyError(kind)


def command(kind: str) -> list[str]:
    """The fixed argument list for ``kind``: this interpreter running its module."""
    spec = KINDS[kind]
    args = [str(a).format(pid=os.getpid()) for a in spec["args"]]
    return [sys.executable, "-m", spec["module"], *args]


def _scrub(text: str) -> str:
    try:
        from maverick.safety.secret_detector import redact

        text, _ = redact(text)
    except Exception:  # pragma: no cover -- masking is best effort
        pass
    return text[:LOG_LINE_CHARS]


def _drain(program: Program, process: Any) -> None:
    """Move the child's output into the bounded log until both pipes close."""
    import queue as _queue

    open_streams = {0: process.stdout, 1: process.stderr}
    pending = {0: "", 1: ""}
    while open_streams:
        for slot, stream in list(open_streams.items()):
            try:
                chunk = stream.get(timeout=0.1)
            except _queue.Empty:
                continue
            if chunk is None:
                del open_streams[slot]
                if pending[slot].strip():
                    program.log.append(_scrub(pending[slot]))
                continue
            *lines, pending[slot] = (pending[slot] + chunk.decode("utf-8", "replace")).split("\n")
            if len(pending[slot]) > LOG_LINE_CHARS:  # a very long line without an end
                lines.append(pending[slot])
                pending[slot] = ""
            program.log.extend(_scrub(line) for line in lines if line.strip())
    code = process.proc.wait()
    with _LOCK:
        if program.process is process:
            program.exit_code = code
            program.stopped_at = time.time()
            if code and not program.last_error:
                program.last_error = (program.log[-1] if program.log
                                      else f"stopped with exit code {code}")


def _running(program: Program) -> bool:
    return program.process is not None and program.process.proc.poll() is None


def start(kind: str) -> dict[str, Any]:
    """Start ``kind`` unless it is already running; returns its status."""
    from maverick.owned_process import OwnedProcess

    program = _PROGRAMS[kind]
    with _LOCK:
        if _running(program):
            return _status(program)
        if getattr(sys, "frozen", False):
            program.last_error = ("this installation cannot start it: it has no separate "
                                  "Python to run the program with")
            return _status(program)
        program.log.clear()
        program.exit_code = None
        program.last_error = ""
        try:
            process = OwnedProcess(command(kind), env=dict(os.environ))
        except OSError as exc:
            program.last_error = f"could not start: {type(exc).__name__}"
            return _status(program)
        program.process = process
        program.started_at = time.time()
        program.stopped_at = None
        program.drain = threading.Thread(target=_drain, args=(program, process),
                                         name=f"background-{kind}", daemon=True)
        program.drain.start()
        return _status(program)


def _signal_stop(program: Program) -> None:
    process = program.process
    if process is None or process.proc.poll() is not None:
        return
    try:
        if os.name == "nt":  # pragma: no cover -- Windows has no polite group signal
            process.stop_tree()
        else:
            os.killpg(process.proc.pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass


def stop(kind: str, *, timeout: float = STOP_GRACE_SECONDS) -> dict[str, Any]:
    """Stop ``kind`` politely, then forcibly after ``timeout`` seconds."""
    program = _PROGRAMS[kind]
    _signal_stop(program)
    process = program.process
    if process is not None:
        try:
            process.proc.wait(timeout=max(0.0, timeout))
        except Exception:
            pass
        try:
            process.close()
        except Exception as exc:  # pragma: no cover -- cleanup reports, never raises
            program.last_error = f"did not stop cleanly: {type(exc).__name__}"
        if program.drain is not None:
            program.drain.join(timeout=2.0)
    return _status(program)


def start_enabled() -> list[str]:
    """Start every program whose setting is on (dashboard start-up)."""
    started = []
    for kind in KINDS:
        if wanted(kind):
            start(kind)
            started.append(kind)
    return started


def stop_all(*, timeout: float = STOP_GRACE_SECONDS, wait: bool = True) -> None:
    """Shutdown supervisor hook: signal every program, then (``wait``) reap them."""
    for program in _PROGRAMS.values():
        _signal_stop(program)
    if not wait:
        return
    deadline = time.monotonic() + max(0.0, timeout)
    for kind, program in _PROGRAMS.items():
        if program.process is not None:
            stop(kind, timeout=max(0.0, deadline - time.monotonic()))


@atexit.register
def _stop_at_exit() -> None:
    stop_all(timeout=1.0)


def alive() -> bool:
    return any(_running(p) for p in _PROGRAMS.values())


def _status(program: Program) -> dict[str, Any]:
    return {
        "kind": program.kind,
        "label": KINDS[program.kind]["label"],
        "wanted": wanted(program.kind),
        "running": _running(program),
        "started_at": program.started_at,
        "stopped_at": program.stopped_at,
        "exit_code": program.exit_code,
        "last_error": program.last_error,
        "log": list(program.log)[-20:],
    }


def status() -> list[dict[str, Any]]:
    """Every program's state, for the dashboard."""
    return [_status(p) for p in _PROGRAMS.values()]


__all__ = ["KINDS", "alive", "command", "start", "start_enabled", "status", "stop",
           "stop_all", "wanted"]
