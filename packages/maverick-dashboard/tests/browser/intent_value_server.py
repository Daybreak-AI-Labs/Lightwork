"""Isolated real dashboard for the work-value journey; outbound calls blocked."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

import server


def install_execution_fixture(app):
    """Keep native publication, queue, approvals and audit; replace paid AI only."""
    from maverick import audit, runner
    from maverick_dashboard import api

    calls = []

    def synthetic_provider(gid, **kwargs):
        world = api._world()
        calls.append(gid)
        eid = world.start_episode(gid)
        result = "Synthetic financial analysis: supplied revenue exceeds expenses by 20. Human review required."
        world.end_episode(eid, result, "success", cost_dollars=1)
        world.set_goal_status(gid, "done", result=result)
        audit.audit_event("goal_end", goal_id=gid, agent="orchestrator", status="succeeded")
        kwargs["usage_observer"](1)
        return result

    runner.run_goal_in_thread = synthetic_provider

    @app.post("/__browser_fixture/intent-value-execution", include_in_schema=False)
    def seed_execution():
        world = api._world()
        for _ in range(4):
            gid = world.create_goal("Analyze financial analysis", owner="user:browser-reviewer")
            if _ < 3:
                eid = world.start_episode(gid)
                world.end_episode(eid, "Synthetic prior analysis", "success", cost_dollars=2)
                world.set_goal_status(gid, "done")
                audit.audit_event("goal_end", goal_id=gid, agent="orchestrator", status="succeeded")
        return {"synthetic": True, "provider_calls": len(calls)}

    @app.get("/__browser_fixture/intent-value-execution", include_in_schema=False)
    def execution_state():
        return {"synthetic": True, "provider_calls": len(calls)}


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
        install_execution_fixture(app)
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
