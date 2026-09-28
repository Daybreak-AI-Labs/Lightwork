"""Several approvers for changes to protected settings ([safety] quorum_*).

When an operator sets ``[safety] quorum_required`` (2 or more; the
environment's ``MAVERICK_QUORUM_REQUIRED`` wins), a save on Settings > All
settings that touches a protected setting (``[safety] quorum_protected_keys``,
by default every safety, governance and add-on setting and the spending limit
per task) is not written. It becomes a request in the dashboard's Approvals,
through the same approvals path agents use: N distinct approvers, never the
person who asked. Saving exactly the same change again once it is approved
applies it; the approval is then used up. A change whose values differ from
what was approved, a denied request or one older than
``[safety] quorum_ttl_days`` starts a new request.

What approvers see, and what the approval is bound to, are the settings'
names with their old and new values as the activity record shows them
(a secret only as "set" or "not set"). Unset, nothing here runs.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)

ACTION = "settings_change"
PROVENANCE = "settings_quorum"
_DETAIL_LIMIT = 1_500


def _shown(value: object) -> str:
    text = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return text if len(text) <= 120 else text[:117] + "..."


def _rows(changes: list[dict], shown) -> list[tuple[str, str, str, str]]:
    return sorted(
        (c["setting"].id, c["setting"].label,
         _shown(shown(c["setting"], c["old"])), _shown(shown(c["setting"], c["new"])))
        for c in changes
    )


def fingerprint(changes: list[dict], shown) -> str:
    """The id a request is stored under: the exact change set as shown."""
    body = json.dumps([(sid, old, new) for sid, _label, old, new in _rows(changes, shown)],
                      ensure_ascii=False)
    return "settings:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _detail(changes: list[dict], shown) -> str:
    lines = [f"{label}: {old} -> {new}" for _sid, label, old, new in _rows(changes, shown)]
    text = "Change settings. " + "; ".join(lines)
    return text if len(text) <= _DETAIL_LIMIT else text[: _DETAIL_LIMIT - 3] + "..."


def _held(approval_id: int, required: int, votes: int, fresh: bool) -> dict[str, Any]:
    need = "approval" if required == 1 else "approvals"
    if fresh:
        message = (f"Not saved yet: this change needs {required} {need} from other "
                   "administrators. It was sent to Approvals; save it again once it is "
                   "approved.")
    else:
        message = (f"Not saved yet: waiting for approval ({votes} of {required} so far). "
                   "Save it again once it is approved.")
    return {"approval_id": approval_id, "required": required, "approvals": votes,
            "message": message}


def _votes(world, approval_id: int) -> int:
    try:
        state = world.approval_state(approval_id) or {}
        return int(state.get("approved_count") or 0)
    except Exception:  # noqa: BLE001 -- the count is only shown
        return 0


def review(changes: list[dict], actor: str, shown) -> tuple[dict | None, str | None]:
    """Decide one save.

    Returns ``(hold, None)`` when the save must wait (``hold`` is what the
    page shows), ``(None, change_id)`` when an approved request covers it
    (call :func:`settle` after writing), and ``(None, None)`` when no
    quorum applies.
    """
    from maverick import quorum

    policy = quorum.configured_policy()
    if policy is None or not any(quorum.is_protected(c["setting"].id, policy)
                                 for c in changes):
        return None, None
    from ._shared import _world

    world = _world()
    store = quorum.ProposalStore()
    change_id = fingerprint(changes, shown)
    proposal = store.get(change_id)
    if proposal is not None:
        approval_id = int((proposal.new or {}).get("approval", 0))
        approval = world.get_approval(approval_id) if approval_id else None
        expired = quorum.status(change_id, store=store, policy=policy) == quorum.EXPIRED
        if approval is not None and not expired and approval.status == "approved":
            return None, change_id
        if approval is not None and not expired and approval.status == "pending":
            return _held(approval_id, approval.approvals_required,
                         _votes(world, approval_id), fresh=False), None
        store.delete([change_id])  # denied, gone or too old: ask again
    approval_id = world.create_approval(
        ACTION, risk="high", scope="settings", detail=_detail(changes, shown),
        provenance=PROVENANCE, approvals_required=policy.required, requested_by=actor,
    )
    try:
        quorum.propose(change_id, ",".join(sorted(c["setting"].id for c in changes)),
                       None, {"approval": approval_id}, actor, policy=policy, store=store)
    except quorum.QuorumError:
        # The same change was requested at the same moment: keep that request.
        log.info("settings quorum: request %s already open", change_id)
    return _held(approval_id, policy.required, 0, fresh=True), None


def settle(change_id: str | None) -> None:
    """Use up an approved request once its change is written."""
    if not change_id:
        return
    from maverick import quorum

    quorum.ProposalStore().delete([change_id])


__all__ = ["ACTION", "PROVENANCE", "fingerprint", "review", "settle"]
