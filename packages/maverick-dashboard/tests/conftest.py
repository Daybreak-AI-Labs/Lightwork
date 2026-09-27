"""Dashboard test isolation shared by every dashboard test module."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_cluster_halt_cache():
    """Forget the killswitch's cached cluster-wide halt after every test (it is
    served for two seconds without re-reading the backend, so a test that saw a
    shared halt would otherwise halt whichever test this worker runs next)."""
    yield
    try:
        from maverick import killswitch
        killswitch._last_shared_check_ts = 0.0
        killswitch._last_shared_halt = None
        world, killswitch._shared_world = killswitch._shared_world, None
        if world is not None and hasattr(world, "close"):
            world.close()
    except Exception:
        pass
