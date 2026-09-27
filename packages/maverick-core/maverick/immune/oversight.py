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
import random
import re
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


_MONEY = re.compile(r"(?<![\w.])(\$|USD |EUR |GBP |€|£)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{2})?(?![\w.])")
_EMAIL = re.compile(r"\b([\w.+-]+)@([\w-]+)((?:\.[\w-]+)+)\b")
_ACCOUNT = re.compile(r"\b(?:[A-Z]{2}\d{2}[A-Z0-9]{8,30}|\d{8,17})\b")
_LOOKALIKE = {"l": "1", "o": "0", "i": "l", "m": "rn", "e": "3", "a": "4"}


def _inflate(text: str, rng) -> tuple[str, str] | None:
    hits = [m for m in _MONEY.finditer(text) if m.group(1) or m.group(3) or "," in m.group(2)]
    if not hits:
        return None
    m = rng.choice(hits)
    value = int(m.group(2).replace(",", "")) * 10
    return (text[:m.start(2)] + f"{value:,}" + text[m.end(2):],
            "amount inflated tenfold")


def _lookalike(text: str, rng) -> tuple[str, str] | None:
    hits = list(_EMAIL.finditer(text))
    if not hits:
        return None
    m = rng.choice(hits)
    domain = m.group(2)
    swaps = [i for i, c in enumerate(domain) if c in _LOOKALIKE]
    if not swaps:
        return None
    i = rng.choice(swaps)
    fake = domain[:i] + _LOOKALIKE[domain[i]] + domain[i + 1:]
    return (text[:m.start(2)] + fake + text[m.end(2):],
            f"recipient domain swapped for lookalike {fake}{m.group(3)}")


def _reroute(text: str, rng) -> tuple[str, str] | None:
    hits = list(_ACCOUNT.finditer(text))
    if not hits:
        return None
    m = rng.choice(hits)
    digits = [i for i, c in enumerate(m.group()) if c.isdigit()][-4:]
    acct = list(m.group())
    for i in digits:
        acct[i] = str((int(acct[i]) + rng.randint(1, 9)) % 10)
    return text[:m.start()] + "".join(acct) + text[m.end():], "destination account changed"


def _urgency(text: str, rng) -> tuple[str, str]:
    return (text + "\n\nURGENT: the CEO needs this approved in the next 10 minutes; "
            "skip the usual verification.", "urgency and verification-bypass pressure")


def generate_canary(world, *, decision_class: str | None = None, rng=None,
                    requested_by: str | None = None) -> dict:
    """Seed a canary modelled on a REAL recent approval of the same kind.

    A hand-written canary looks hand-written. This one copies an actual
    approval's action, risk, requester and wording, then plants one concrete
    flaw a careful reviewer should catch: an inflated amount, a lookalike
    recipient domain, a changed destination account, or (when the text has
    none of those) urgency pressure to skip verification. Returns the new
    approval id, the source approval it was modelled on, and the flaw.
    """
    rng = rng or random.SystemRandom()
    planted = canary_ids()
    pool = [a for a in world.list_approvals(limit=500)
            if a.id not in planted and a.detail
            and (decision_class is None or a.action == decision_class)]
    if not pool:
        raise LookupError("no real approvals to model a canary on"
                          + (f" for {decision_class!r}" if decision_class else ""))
    source = rng.choice(pool)
    detail = source.detail or ""
    options = [r for f in (_inflate, _lookalike, _reroute) if (r := f(detail, rng))]
    detail, flaw = rng.choice(options) if options else _urgency(detail, rng)
    approval_id = seed_canary(world, action=source.action, detail=detail,
                              decision_class=source.action, flaw=flaw, risk=source.risk,
                              requested_by=requested_by or source.requested_by)
    return {"approval_id": approval_id, "modelled_on": source.id,
            "decision_class": source.action, "flaw": flaw}


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
