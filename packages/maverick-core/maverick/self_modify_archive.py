"""DGM lineage archive for governed code self-modification (Phase 3).

The Darwin-Gödel-Machine insight (arXiv:2505.22954) that beats naive hill-climbing
is an **archive of diverse ancestors** — including *worse* ones — so the search
branches from across a population and escapes the local optimum a single
best-only lineage stalls in (the "plateau problem"). ``maverick_evolve.archive``
already does this for **config** candidates (dicts, safe to keep and sample);
this is the same idea for the **code** rung, and it is deliberately separate
because code carries a heavier posture.

What is stored is a **lineage record**, never applied state: each entry is a
proposed patch's metadata plus the patch text (bounded), the scores it earned in
sandbox evaluation, its parent, its generation, and whether it was promoted or
rolled back. The archive is inert data — it is never executed, and *sampling a
parent to branch from does not promote anything*: a re-derived child still runs
the full editable-surface boundary + governed promotion ladder before it can
take effect. The archive only shapes *which* parent the loop mutates next.

Posture: this module is a passive datastore with no side effects beyond the file
it is told to persist to. It does not read config, does not gate, and is a no-op
unless the Phase-4 loop (which IS off by default) constructs and feeds it. The
store lives under the maverick data dir (0600) like the promotion ledger.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# A patch kept in the archive is capped so a runaway diff can't bloat the store;
# beyond this the text is dropped (the metadata + digest are still kept, so the
# lineage is intact and the candidate is simply not re-derivable from the archive).
_MAX_PATCH_BYTES = 256 * 1024


def _patch_sha256(patch: str) -> str:
    return hashlib.sha256((patch or "").encode("utf-8")).hexdigest()


def _short_id(patch: str, summary: str) -> str:
    blob = f"{summary}\0{_patch_sha256(patch)}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _added_line_tokens(patch: str) -> frozenset[str]:
    """The set of tokens on ADDED lines — the signature used for lineage
    distance. Coarse on purpose: two patches that add mostly the same tokens are
    "near", so the diversity keep favours structurally distinct lineages."""
    toks: set[str] = set()
    for line in (patch or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            for t in line[1:].split():
                if t:
                    toks.add(t)
    return frozenset(toks)


@dataclass
class CodeCandidate:
    """One node in the code-modification lineage.

    ``patch`` may be dropped (set to ``""``) if it exceeded the size cap or was
    never kept; ``patch_sha256`` always pins what the node represents. ``score``
    is the candidate's sandbox score (higher is better); ``baseline_score`` is
    what it was measured against. ``parent_id`` / ``generation`` record where in
    the search the node came from; ``promoted`` / ``rolled_back`` mirror the
    governed ladder's decision so the loop can prefer promoted lineages without
    discarding the informative "worse" ones.
    """

    summary: str
    patch: str = ""
    patch_sha256: str = ""
    score: float = 0.0
    baseline_score: float = 0.0
    samples: int = 0
    parent_id: str | None = None
    generation: int = 0
    promoted: bool = False
    rolled_back: bool = False
    capability_widens: bool | None = None
    reasons: tuple[str, ...] = ()
    created_at: float = 0.0
    id: str = ""

    def __post_init__(self) -> None:
        if not self.patch_sha256:
            self.patch_sha256 = _patch_sha256(self.patch)
        if not self.id:
            self.id = _short_id(self.patch, self.summary)
        if self.patch and len(self.patch.encode("utf-8")) > _MAX_PATCH_BYTES:
            # Keep the lineage node, drop the oversized body (digest still pins it).
            log.info("code archive: dropping oversized patch body for %s", self.id)
            self.patch = ""

    @property
    def improvement(self) -> float:
        return self.score - self.baseline_score

    def to_dict(self) -> dict:
        return {
            "id": self.id, "summary": self.summary, "patch": self.patch,
            "patch_sha256": self.patch_sha256, "score": self.score,
            "baseline_score": self.baseline_score, "samples": self.samples,
            "parent_id": self.parent_id, "generation": self.generation,
            "promoted": self.promoted, "rolled_back": self.rolled_back,
            "capability_widens": self.capability_widens,
            "reasons": list(self.reasons), "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CodeCandidate:
        return cls(
            summary=str(d.get("summary", "")), patch=str(d.get("patch", "")),
            patch_sha256=str(d.get("patch_sha256", "")),
            score=float(d.get("score", 0.0)),
            baseline_score=float(d.get("baseline_score", 0.0)),
            samples=int(d.get("samples", 0)),
            parent_id=(d.get("parent_id") or None),
            generation=int(d.get("generation", 0)),
            promoted=bool(d.get("promoted", False)),
            rolled_back=bool(d.get("rolled_back", False)),
            capability_widens=d.get("capability_widens"),
            reasons=tuple(d.get("reasons") or ()),
            created_at=float(d.get("created_at", 0.0)),
            id=str(d.get("id", "")),
        )


@dataclass
class CodeArchive:
    """Bounded, score-ranked, diversity-aware archive of code candidates.

    Eviction (when over ``capacity``) keeps a diverse high-performing subset —
    the best plus the most patch-distant others — so the population keeps a
    spread of lineages instead of collapsing onto the single best (which is what
    re-introduces the plateau). ``sample`` picks a parent to branch from weighted
    by score, so exploration favours strong lineages without abandoning the tail.
    """

    capacity: int = 100
    candidates: list[CodeCandidate] = field(default_factory=list)

    def add(self, cand: CodeCandidate) -> CodeCandidate:
        """Insert, or update in place if the same id arrives with a better score."""
        for existing in self.candidates:
            if existing.id == cand.id:
                if cand.score > existing.score:
                    existing.score = cand.score
                    existing.samples = max(existing.samples, cand.samples)
                existing.promoted = existing.promoted or cand.promoted
                existing.rolled_back = existing.rolled_back or cand.rolled_back
                return existing
        self.candidates.append(cand)
        self._evict_if_needed()
        return cand

    def get(self, cand_id: str) -> CodeCandidate | None:
        return next((c for c in self.candidates if c.id == cand_id), None)

    def best(self) -> CodeCandidate | None:
        """The highest-scoring candidate that was not rolled back (a rolled-back
        line is a dead end — never the one to branch from as 'best')."""
        live = [c for c in self.candidates if not c.rolled_back]
        return max(live, key=lambda c: c.score) if live else None

    def mark_promoted(self, cand_id: str, *, promoted: bool = True) -> None:
        c = self.get(cand_id)
        if c is not None:
            c.promoted = promoted

    def mark_rolled_back(self, cand_id: str) -> None:
        c = self.get(cand_id)
        if c is not None:
            c.rolled_back = True

    def lineage(self, cand_id: str) -> list[CodeCandidate]:
        """The ancestry chain from the given candidate back to its root
        (self first, root last). Robust to a broken/cyclic parent link."""
        chain: list[CodeCandidate] = []
        seen: set[str] = set()
        cur = self.get(cand_id)
        while cur is not None and cur.id not in seen:
            seen.add(cur.id)
            chain.append(cur)
            cur = self.get(cur.parent_id) if cur.parent_id else None
        return chain

    def sample(self, rng: random.Random | None = None) -> CodeCandidate | None:
        """Sample a parent to branch from, weighted by score (promoted lineages
        get a small bonus). Rolled-back dead ends are excluded."""
        pool = [c for c in self.candidates if not c.rolled_back]
        if not pool:
            return None
        rng = rng or random
        lo = min(c.score for c in pool)
        weights = [(c.score - lo) + (0.1 if c.promoted else 0.0) + 1e-3 for c in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    def diverse(self, k: int) -> list[CodeCandidate]:
        """Best plus the most patch-distant others (greedy quality-diversity)."""
        if not self.candidates or k <= 0:
            return []
        chosen = [max(self.candidates, key=lambda c: c.score)]
        pool = [c for c in self.candidates if c.id != chosen[0].id]
        while pool and len(chosen) < k:
            nxt = max(
                pool,
                key=lambda c: (
                    min(self.patch_distance(c, s) for s in chosen),
                    c.score,
                ),
            )
            chosen.append(nxt)
            pool.remove(nxt)
        return chosen

    def _evict_if_needed(self) -> None:
        if len(self.candidates) <= self.capacity:
            return
        keep_ids = {c.id for c in self.diverse(self.capacity)}
        self.candidates = [c for c in self.candidates if c.id in keep_ids]

    @staticmethod
    def patch_distance(a: CodeCandidate, b: CodeCandidate) -> float:
        """Jaccard distance between the two patches' added-line token sets
        (0.0 identical adds, 1.0 fully disjoint). Identical patch digests are
        distance 0 regardless of token overlap."""
        if a.patch_sha256 and a.patch_sha256 == b.patch_sha256:
            return 0.0
        ta, tb = _added_line_tokens(a.patch), _added_line_tokens(b.patch)
        union = ta | tb
        if not union:
            return 0.0 if a.patch_sha256 == b.patch_sha256 else 1.0
        return 1.0 - (len(ta & tb) / len(union))

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict:
        return {"capacity": self.capacity,
                "candidates": [c.to_dict() for c in self.candidates]}

    @classmethod
    def from_dict(cls, data: dict) -> CodeArchive:
        arch = cls(capacity=int(data.get("capacity", 100)))
        for c in data.get("candidates", []):
            if isinstance(c, dict) and c.get("summary") is not None:
                try:
                    arch.candidates.append(CodeCandidate.from_dict(c))
                except (TypeError, ValueError):
                    continue
        return arch

    def save(self, path: str | Path) -> None:
        """Atomically persist (0600). Best-effort: a persistence failure must not
        crash a learning pass."""
        try:
            from .file_lock import atomic_write_text  # writes 0600 atomically
            atomic_write_text(path, json.dumps(self.to_dict(), indent=2, sort_keys=True))
        except Exception:  # pragma: no cover -- persistence is best-effort
            log.debug("code archive save failed", exc_info=True)

    @classmethod
    def load(cls, path: str | Path) -> CodeArchive:
        """Load a persisted archive, or a fresh one if absent/corrupt."""
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return cls()


__all__ = ["CodeCandidate", "CodeArchive"]
