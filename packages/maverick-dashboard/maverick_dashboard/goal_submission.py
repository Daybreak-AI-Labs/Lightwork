"""Same-key admission and read-only reconciliation for optional HTTP clients."""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException
from maverick.attachment_submissions import SubmissionConflict, SubmissionStore

LEGACY_CHANNEL = "idempotency:api:goals"


def api_token(key):
    if not isinstance(key, str) or not 1 <= len(key) <= 255 or any(ord(c) < 32 for c in key):
        raise HTTPException(status_code=400, detail="Invalid Idempotency-Key (maximum 255 characters)")
    return "api-goal:" + hashlib.sha256(key.encode()).hexdigest()


def reserve_api(world, *, key, owner, payload, title, description, domain, caps):
    token = api_token(key)
    # Historical processed-message rows did not bind full request payloads.
    # They remain readable for reconciliation, but cannot authorize new work.
    legacy = world.lookup_processed_message(LEGACY_CHANNEL, f"{owner}:{key}")
    if legacy is not None:
        raise HTTPException(status_code=409, detail=(
            "A legacy goal receipt exists for this key. Read /api/v1/goal-submissions/receipt "
            "with the same Idempotency-Key to reconcile it; no new work was dispatched."
        ))
    fingerprint = hashlib.sha256(json.dumps({
        "schema": "api-goal-request.v1", "payload": payload.model_dump(mode="json"),
        "title": title, "description": description, "domain": domain, "caps": caps,
    }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    try:
        return SubmissionStore(world).create(token=token, fingerprint=fingerprint, owner=owner,
            title=title[:200], description=description, domain=domain or "", manifest=[])
    except SubmissionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def lookup_receipt(world, *, key, owner, kind):
    if kind not in {"api", "chat"}:
        raise HTTPException(status_code=400, detail="Unknown submission kind")
    try:
        receipt = SubmissionStore(world).lookup(token=api_token(key) if kind == "api" else key, owner=owner)
    except SubmissionConflict as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if receipt is None and kind == "api":
        goal_id = world.lookup_processed_message(LEGACY_CHANNEL, f"{owner}:{key}")
        if goal_id is not None:
            receipt = {"goal_id": goal_id, "state": "legacy_unverified"}
    if receipt is None:
        raise HTTPException(status_code=404, detail=(
            "No matching receipt is visible. The original request may still be in flight; do not submit a new key."
        ))
    goal = world.get_goal(receipt["goal_id"])
    if goal is None:
        raise HTTPException(status_code=409, detail="Submission receipt exists but its goal is unavailable; reconcile before retrying")
    # A token receipt is not a share link and never grants administrator-wide
    # lookup of another user's request identity.
    if (goal.owner or "") != owner:
        raise HTTPException(status_code=404, detail="No matching submission receipt")
    return receipt, goal


def claim_api_dispatch(world, receipt, intent):
    store = SubmissionStore(world)
    store.record_dispatch_intent(receipt["key"], intent)
    store.update(receipt["key"], state="ready", message="Request accepted; execution admission is pending.")
    if not store.claim_dispatch(receipt["key"]):
        raise HTTPException(status_code=409, detail="Request retained; execution admission is unconfirmed. Reconcile before retrying.")
