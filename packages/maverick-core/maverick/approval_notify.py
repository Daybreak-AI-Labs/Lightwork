"""Tell chat channels that a new approval is waiting.

``approval_created`` is called by the world model right after an approval row
is written. It never blocks or fails the caller: the work happens on a
background thread that carries the caller's tenant context, and every error is
logged. Today the only listener is the Microsoft Teams channel
(``[channels.teams] approvals_enabled``); other channels can be added here.
"""
from __future__ import annotations

import contextvars
import logging
import threading

log = logging.getLogger(__name__)


def teams_approvals_enabled(cfg: dict | None = None) -> bool:
    if cfg is None:
        try:
            from .config import load_config

            cfg = ((load_config() or {}).get("channels") or {}).get("teams") or {}
        except Exception:  # noqa: BLE001 -- config never blocks an approval
            return False
    return bool(isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("approvals_enabled"))


def approval_created(approval_id: int, *, provenance: str | None = None) -> None:
    """Post the approval to every linked Teams approver, in the background."""
    if provenance == "settings_quorum":
        return  # protected-settings changes are decided in the dashboard only
    if not teams_approvals_enabled():
        return
    context = contextvars.copy_context()

    def run() -> None:
        try:
            from maverick_channels.teams import post_approval_to_linked_users

            context.run(post_approval_to_linked_users, int(approval_id))
        except Exception:  # noqa: BLE001 -- a failed post never blocks the queue
            log.warning("teams: could not post approval #%s", approval_id, exc_info=True)

    threading.Thread(target=run, name=f"teams-approval-{approval_id}", daemon=True).start()


__all__ = ["approval_created", "teams_approvals_enabled"]
