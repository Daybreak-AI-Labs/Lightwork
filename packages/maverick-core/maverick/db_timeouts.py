"""Bound libpq connection attempts without replacing operator connection options."""
from __future__ import annotations

import os


def postgres_connect_timeout(requested: object = None) -> int:
    """Return a finite per-host timeout, including for reconnects and pool workers.

    libpq's omitted/nonpositive timeout means an indefinite wait. A shorter
    DSN or PGCONNECT_TIMEOUT value is retained; the operator's Lightwork cap
    defaults to ten seconds and can be configured from two to 300 seconds.
    The configured minimum is two seconds; this limit applies to each host.
    """
    try:
        cap = int(os.environ.get("MAVERICK_PG_CONNECT_TIMEOUT_SECONDS", "10"))
    except ValueError:
        cap = 10
    if not 2 <= cap <= 300:
        cap = 10
    if requested is None:
        requested = os.environ.get("PGCONNECT_TIMEOUT", "")
    try:
        seconds = int(requested)
    except (TypeError, ValueError, OverflowError):
        return cap
    return min(cap, max(2, seconds)) if seconds > 0 else cap
