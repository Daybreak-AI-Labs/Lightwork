"""Copilot-enabled synthetic server for specialist-draft task journeys."""
from __future__ import annotations

import server

server.CONFIG = server.CONFIG.replace(
    "[features]",
    "[features]\ncopilot_operations = true",
).replace(
    'operation_families = ["learning", "commitments"]',
    'operation_families = ["learning", "commitments", "models"]',
)


if __name__ == "__main__":
    server.main()
