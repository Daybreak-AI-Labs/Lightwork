"""Cross-process serialization for one goal's dispatch/cancel transition."""
from __future__ import annotations

from contextlib import contextmanager


@contextmanager
def goal_lifecycle_lock(goal_id: int):
    """Hold the tenant-scoped lock protecting one goal's runnable status."""
    gid = int(goal_id)
    if gid <= 0:
        raise ValueError("goal_id must be a positive integer")
    from .file_lock import cross_process_lock
    from .paths import data_dir

    target = data_dir("goal-lifecycle", f"{gid}.state")
    with cross_process_lock(target, strict=True):
        yield


__all__ = ["goal_lifecycle_lock"]
