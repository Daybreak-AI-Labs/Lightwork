"""Proof of Oversight: measure whether the human in the loop is really checking.

"Human in the loop" usually means someone clicked Approve on work they could not
review. This module makes that measurable, the way airport screening measures
screeners: an operator seeds realistic *canary* approvals -- items with a known
flaw -- into the real approvals queue. A denied canary is a catch; an approved
one is a miss. Nothing executes on a canary decision: no tool waits on its id.

From catch rates it derives **earned authority** (the Wilson lower bound of a
person's catch rate for a decision class, not their title), records
**capability drills** (can the team still do the work without agents?), finds
the **cruxes** of a decision (the claims whose falsity would flip it), and
issues a **signed oversight record** binding who approved what, on which cruxes,
with what measured reliability.

Seeding is an explicit trust decision: off unless ``[immune] canaries = true``.
The canary ledger is private and server-side; the approval itself carries no
marker, so it cannot be told apart in the queue.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..paths import data_dir
from ..wilson_score import wilson_lower_bound
from . import _ledger


def ledger_path() -> Path:
    return data_dir("immune", "oversight.jsonl")


def canaries_enabled() -> bool:
    from ..config import get_immune
    return get_immune()["canaries"]


def seed_canary(world, *, action: str, detail: str, decision_class: str, flaw: str,
                risk: str = "medium", requested_by: str | None = None) -> int:
    """Park a known-flawed approval in the real queue and remember it privately."""
    if not canaries_enabled():
        raise PermissionError("oversight canaries are off; set [immune] canaries = true")
    approval_id = world.create_approval(action, risk=risk, detail=detail,
                                        requested_by=requested_by)
    _ledger.append(ledger_path(), {"type": "canary", "approval_id": approval_id,
                                   "class": decision_class, "flaw": flaw, "at": time.time()})
    return approval_id


def canary_ids() -> set[int]:
    """Approval ids that are canaries -- learners must exclude these."""
    return {r["approval_id"] for r in _ledger.read(ledger_path()) if r["type"] == "canary"}


def record_drill(person: str, decision_class: str, passed: bool) -> None:
    """Outcome of a no-agent capability drill (human skill atrophy check)."""
    _ledger.append(ledger_path(), {"type": "drill", "person": person,
                                   "class": decision_class, "passed": bool(passed),
                                   "at": time.time()})


@dataclass
class Stats:
    caught: int = 0
    missed: int = 0

    @property
    def n(self) -> int:
        return self.caught + self.missed

    @property
    def assured(self) -> float:
        """Conservative catch rate: Wilson lower bound, 0 with no evidence."""
        return wilson_lower_bound(self.caught, self.n)


def score(world, kind: str = "canary") -> dict[tuple[str, str], Stats]:
    """(person, decision class) -> catches vs misses for canaries or drills.

    Pending canaries are not scored; a canary decided by nobody identifiable is
    attributed to ``"unknown"`` so misses can never silently disappear.
    """
    out: dict[tuple[str, str], Stats] = {}
    for rec in _ledger.read(ledger_path()):
        if rec["type"] != kind:
            continue
        if kind == "drill":
            ok, person = rec["passed"], rec["person"]
        else:
            appr = world.get_approval(rec["approval_id"])
            if appr is None or appr.status not in ("approved", "denied"):
                continue
            ok, person = appr.status == "denied", appr.decided_by or "unknown"
        stats = out.setdefault((person, rec["class"]), Stats())
        if ok:
            stats.caught += 1
        else:
            stats.missed += 1
    return out


def authority(stats: Stats, *, min_n: int = 10, threshold: float = 0.8) -> bool:
    """Earned authority: enough evidence AND a conservative catch rate above bar."""
    return stats.n >= min_n and stats.assured >= threshold


def find_cruxes(claims: Iterable[str], decide: Callable[[frozenset[str]], bool]) -> list[str]:
    """Claims whose falsity alone flips the decision.

    ``decide(false_claims)`` returns the decision assuming exactly those claims
    are false. A reviewer who checks the cruxes checks everything that can, on
    its own, change the outcome -- usually a handful, not the whole document.
    """
    baseline = decide(frozenset())
    return [c for c in claims if decide(frozenset({c})) != baseline]


def oversight_record(*, approver: str, subject: str, decision: str,
                     cruxes: list[str], stats: Stats | None) -> dict:
    """Signed evidence that a named human decided on stated cruxes, with the
    reliability measured for them at that moment."""
    from ..audit.signing import sign_detached_digest
    body = {"schema": "lightwork.oversight-record.v1", "approver": approver,
            "subject": subject, "decision": decision, "cruxes": sorted(cruxes),
            "measured": None if stats is None else
            {"caught": stats.caught, "missed": stats.missed, "assured": round(stats.assured, 4)},
            "at": time.time()}
    digest = hashlib.sha256(_ledger.canonical(body)).hexdigest()
    return {"body": body, "signature": sign_detached_digest(digest)}


def verify_record(record: dict, pubkey_hex: str) -> bool:
    """Offline check: the body is unaltered and signed by ``pubkey_hex``."""
    from ..audit.signing import verify_detached_digest
    digest = hashlib.sha256(_ledger.canonical(record["body"])).hexdigest()
    sig = record["signature"]
    return sig["digest"] == digest and verify_detached_digest(pubkey_hex, digest, sig["signature"])
