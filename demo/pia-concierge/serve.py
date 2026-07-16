"""Single-process launcher: real Lightwork dashboard + the external-world harness.

Both servers run in ONE process and event loop, matching the platform's own
deployment shape (`maverick dashboard` hosts the runner in-process) — and,
critically, giving the Ed25519 audit chain its required single writer: the
dashboard's approval-decision audit rows and the concierge's agent-step rows
chain through the same signer, so `maverick audit verify` stays green.

  :8765  maverick_dashboard.app:app  (goals · approvals · assessments · audit)
  :8890  app:app                     (ServiceNow · inbox · questionnaire · OneTrust)
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Run the REPO's platform code, not whatever happens to be pip-installed:
# a stale (non-editable) site-packages install otherwise serves yesterday's
# dashboard templates after a `git pull` — the demo silently demos old code.
# Prepending the checkout's package dirs makes `git pull` + `python serve.py`
# always show what you just pulled, on every OS, with no pip step.
_REPO = Path(__file__).resolve().parents[2]
for _pkg in sorted((_REPO / "packages").glob("maverick-*")):
    # A package root qualifies if it holds an importable top-level package
    # (maverick-core's is `maverick`, the rest are underscored names).
    if any(p.joinpath("__init__.py").is_file()
           for p in _pkg.iterdir() if p.is_dir()):
        sys.path.insert(0, str(_pkg))


async def main() -> None:
    import uvicorn
    from app import app as world_app
    from maverick_dashboard.app import app as dash_app

    dash_port = int(os.environ.get("DASH_PORT", "8765"))
    world_port = int(os.environ.get("WORLD_PORT", "8890"))

    dash = uvicorn.Server(uvicorn.Config(
        dash_app, host="127.0.0.1", port=dash_port, log_level="warning"))
    world = uvicorn.Server(uvicorn.Config(
        world_app, host="127.0.0.1", port=world_port, log_level="warning"))
    # Server.serve() (unlike run()) installs no signal handlers, so two servers
    # coexist on one loop; Ctrl-C propagates as KeyboardInterrupt to both.
    print(f"Lightwork dashboard  → http://127.0.0.1:{dash_port}")
    print(f"External world       → http://127.0.0.1:{world_port}")
    await asyncio.gather(dash.serve(), world.serve())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
