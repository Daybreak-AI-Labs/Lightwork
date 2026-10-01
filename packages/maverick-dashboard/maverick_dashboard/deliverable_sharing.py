"""Fresh result previews and version-bound public release metadata.

New ``r1_`` links never resolve without their governed metadata. Publishing a
native token before saving metadata is therefore fail-closed even if storage
fails or the process exits between those writes. Legacy links retain their
existing current-release checks. No token is stored in the metadata record.
"""
from __future__ import annotations

import hashlib
import json

from maverick.governed_records import GovernedRecordStore

MAX_LIFETIME = 7 * 24 * 3600
LIFETIMES = (3600, 24 * 3600, MAX_LIFETIME)


def _store():
    return GovernedRecordStore("deliverable-share-releases", "shr", "deliverable_share_release")


def _id(goal_id, link_id):
    return f"shr-{int(goal_id)}-{int(link_id)}"


def snapshot(world, goal):
    """The public view's exact saved content, including selected latest artifacts."""
    artifacts = world.latest_artifacts(goal.id)
    content = {"goal_id": goal.id, "owner": goal.owner or "", "title": goal.title,
               "result": goal.result or "", "updated_at": goal.updated_at,
               "domain": goal.domain, "status": goal.status,
               "artifacts": [{key: a.get(key) for key in ("id", "kind", "title", "content", "version", "created_at")}
                             for a in artifacts]}
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {**content, "sha256": hashlib.sha256(encoded).hexdigest()}


def remember(world, goal, *, link_id, source, label, lifetime, actor):
    record = {"id": _id(goal.id, link_id), "schema": 1, "goal_id": goal.id,
              "link_id": link_id, "source_sha256": source["sha256"],
              "label": label, "lifetime_seconds": lifetime, "owner": actor,
              "release_version": goal.updated_at}
    _store().create(record, actor=actor or "local-operator", action="confirmed_public_release")


def release_matches(world, goal, token):
    """Token authority cannot silently expand to a later unreviewed version."""
    if not token.startswith("r1_"):
        return True
    digest = hashlib.sha256(token.encode()).hexdigest()
    rows = world.query("SELECT id FROM share_links WHERE goal_id=? AND token_sha256=?", (goal.id, digest))
    if len(rows) != 1:
        return False
    record = _store().get(_id(goal.id, int(rows[0][0])))
    return bool(record and record.get("schema") == 1 and record.get("goal_id") == goal.id
                and record.get("source_sha256") == snapshot(world, goal)["sha256"])


def listing(world, goal):
    result = []
    current = snapshot(world, goal)["sha256"]
    for row in world.share_links_for_goal(goal.id):
        record = _store().get(_id(goal.id, row["id"]))
        stale = bool(record and record.get("source_sha256") != current)
        result.append({**row, "label": record.get("label") if record else f"Read-only link #{row['id']}",
                       "stale": stale, "active": row["active"] and not stale,
                       "state": "revoked" if row["revoked"] else "expired" if row["expired"] else "source changed" if stale else "active"})
    return result
