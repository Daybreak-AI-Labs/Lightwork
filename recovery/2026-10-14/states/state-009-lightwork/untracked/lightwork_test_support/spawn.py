"""Spawn-process helpers for tests collected from multiple packages."""
from __future__ import annotations

import sys
import time
from pathlib import Path
from queue import Empty


def start_spawn_processes(processes, *, test_file: str) -> None:
    """Start workers with the core test package importable in each child.

    Pytest's importlib collection can load a module as ``tests.test_x`` without
    adding ``packages/maverick-core`` to ``sys.path``. ``multiprocessing``
    serializes that module name and the parent's path for ``spawn`` children,
    so the child otherwise fails while unpickling the target, before the target
    can report an error. Keep the path change scoped to process preparation.
    """
    package_root = str(Path(test_file).resolve().parents[1])
    sys.path.insert(0, package_root)
    try:
        for process in processes:
            process.start()
    finally:
        if sys.path and sys.path[0] == package_root:
            del sys.path[0]
        else:  # pragma: no cover - defensive against unrelated path mutation
            sys.path.remove(package_root)


def collect_process_messages(
    channel,
    processes,
    *,
    phase: str,
    timeout: float = 30.0,
) -> list[tuple]:
    """Collect one message per worker or fail with phase and exit diagnostics."""
    deadline = time.monotonic() + timeout
    observed: list[tuple] = []
    try:
        for _ in processes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise Empty
            observed.append(channel.get(timeout=remaining))
    except Empty as exc:
        exitcodes = [process.exitcode for process in processes]
        raise AssertionError(
            f"{phase}: received {len(observed)}/{len(processes)} worker messages; "
            f"exitcodes={exitcodes}; observed={observed}"
        ) from exc
    return observed


def finish_spawn_processes(processes, *, timeout: float = 10.0) -> None:
    """Join workers within one deadline, then terminate any stragglers."""
    started = [process for process in processes if process.pid is not None]
    deadline = time.monotonic() + timeout
    for process in started:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
    stragglers = [process for process in started if process.is_alive()]
    for process in stragglers:
        process.terminate()
    deadline = time.monotonic() + timeout
    for process in stragglers:
        process.join(timeout=max(0.0, deadline - time.monotonic()))
