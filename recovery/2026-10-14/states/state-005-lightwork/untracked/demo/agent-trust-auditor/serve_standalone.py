"""Run the Agent Trust Auditor without importing Lightwork."""
from __future__ import annotations

import os

os.environ["AGENT_TRUST_AUDITOR_STANDALONE"] = "1"

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="127.0.0.1",
        port=int(os.environ.get("AGENT_TRUST_AUDITOR_PORT", "8894")),
    )

