"""Isolated real dashboard for the work-value journey; outbound calls blocked."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server.CONFIG += '\n[value]\nhourly_rate=60.0\n[audit]\nsign=true\n'
    with tempfile.TemporaryDirectory(prefix="lightwork-intent-value-") as temporary:
        directory = Path(temporary)
        server.environment(directory)
        server.block_outbound()
        app = server.build_app(directory)
        from maverick import audit
        from maverick.paths import tenant_scope
        from maverick_dashboard import api

        with tenant_scope(tenant="browser-workspace"):
            world = api._world()
            for index in range(4):
                gid = world.create_goal("Draft an RFP", owner="user:browser-reviewer")
                if index < 3:
                    eid = world.start_episode(gid)
                    world.end_episode(eid, "Synthetic fixture", "success", cost_dollars=2)
                    world.set_goal_status(gid, "done")
                    audit.audit_event("goal_end", goal_id=gid, agent="orchestrator", status="succeeded")

        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="critical")


if __name__ == "__main__":
    main()
