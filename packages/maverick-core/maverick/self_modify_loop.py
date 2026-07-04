"""DGM cycle driver + proof-gated surface widening (Phase 4).

This ties Phases 1-3 into the governed Darwin-Gödel-Machine loop: **propose →
evaluate → gate → archive**, run under the reference monitor, with the editable
surface WIDENING only as the system earns trust.

One cycle (:func:`run_cycle`):

1. **Sample a parent** to branch from (the DGM archive; :mod:`self_modify_archive`).
2. **Propose** a patch off that parent (an injected live-model seam; the offline
   default proposes nothing, so the loop is inert without a real proposer).
3. **Boundary review** (:func:`self_modify.review_patch`) — a control-plane edit
   is refused structurally, before anything is evaluated.
4. **Evaluate** on isolated copies (:func:`self_modify_eval.evaluate_patch`) —
   the empirical baseline-vs-candidate measurement.
5. **Gate** (:func:`self_modify.propose_code_change`) — the governed ladder:
   beats baseline, capability-bounded, human-signed at the code rung, reversible,
   calibrated; every promotion signed into the audit chain.
6. **Archive** the candidate whether or not it promoted — DGM keeps the *worse*
   ancestors too, because branching from a diverse population is what escapes the
   plateau.

**Proof-gated surface widening.** The surface the loop may edit is not fixed; it
starts empty and widens one tier at a time, and a tier unlocks only after the
system has accumulated *proof* at the current tier — a run of promoted,
non-rolled-back, capability-bounded changes. A single rollback (a regression that
had to be undone) blocks further widening: trust is earned by a track record and
forfeited by a failure. This is the deployable posture for regulated
environments — the agent proves it can edit a narrow surface safely before it is
allowed a wider one, and it can never widen its *own* surface (the widening
policy lives here, outside the editable surface, and the boundary refuses any
patch that touches ``self_modify``/``config``).

Posture (kernel rule 1): OFF by default and a no-op while off. The loop runs only
when BOTH :func:`self_modify.enabled` and :func:`self_improvement.enabled` are
true; otherwise every entry point returns an inert report. It never raises — a
cycle that errors returns a non-promoting report (fail closed).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from . import self_modify as sm
from .self_modify_archive import CodeArchive, CodeCandidate

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Proposal:
    """A proposed patch off a parent. ``patch`` is a unified diff; ``summary`` is
    the human-readable change description recorded on the candidate."""

    patch: str
    summary: str = ""


# The proposer seam: generate a candidate patch to branch from ``parent`` (None
# on the first cycle), constrained to ``surface``. A real one asks the model to
# propose an edit within the allowlisted paths; the offline default proposes
# nothing so the loop is a safe no-op until a proposer is injected.
Proposer = Callable[["CodeCandidate | None", sm.EditableSurface], "Proposal | None"]


def _null_proposer(parent, surface) -> Proposal | None:
    """Offline default: propose nothing (the loop is inert without a real one)."""
    return None


# --- the live-model proposer (Gap 1) -----------------------------------------

_PROPOSER_SYSTEM = (
    "You are a careful software engineer proposing ONE small, self-contained "
    "improvement to a codebase, as a unified git diff.\n"
    "HARD CONSTRAINTS — a violation makes your output useless:\n"
    "1. You may ONLY modify files matching these allowlisted globs:\n{globs}\n"
    "2. NEVER touch control-plane files (the promotion gate, verifier, "
    "calibration, capability, approval, audit, sandbox, config, or the "
    "self-modify engine). Editing them is refused structurally.\n"
    "3. Do NOT add process spawns, network calls, dynamic code execution "
    "(eval/exec), or new capability/tool grants — such a change is rejected as "
    "an escalation.\n"
    "4. The change must be an improvement measurable by the test suite and must "
    "keep all existing tests passing.\n"
    "Output ONLY the unified diff (starting with 'diff --git'). No prose, no "
    "markdown fences."
)


def _extract_diff(text: str) -> str:
    """Pull a unified diff out of a model reply: strip ``` fences and keep from
    the first ``diff --git`` (or ``--- ``) line onward. Returns ``""`` if the
    reply contains no diff, so a non-diff answer becomes a clean no-proposal."""
    t = (text or "").strip()
    if "```" in t:
        # keep the contents of the first fenced block if present
        parts = t.split("```")
        for seg in parts[1:]:
            body = seg.split("\n", 1)[1] if "\n" in seg else seg
            if "diff --git" in body or body.lstrip().startswith("--- "):
                t = body
                break
    for marker in ("diff --git", "--- "):
        idx = t.find(marker)
        if idx != -1:
            return t[idx:].strip() + "\n"
    return ""


def llm_proposer(llm, *, budget=None, model: str | None = None,
                 max_tokens: int = 1500) -> Proposer:
    """Build a :data:`Proposer` that asks a model to generate a candidate patch
    constrained to the editable surface. The model is NOT hard-coded (kernel rule
    2) — the driver wires the coding role. The reply is reduced to a unified diff
    (:func:`_extract_diff`); a non-diff / empty / errored reply yields ``None`` (a
    clean no-proposal, so the loop no-ops). Fail-open: any provider error returns
    ``None`` and never crashes the cycle. The boundary + gate downstream are what
    make a *bad* proposal safe — this only has to produce a candidate."""
    def _propose(parent: CodeCandidate | None,
                 surface: sm.EditableSurface) -> Proposal | None:
        globs = "\n".join(f"  - {g}" for g in surface.editable_globs) or "  (none)"
        system = _PROPOSER_SYSTEM.format(globs=globs)
        parts = ["Propose one small improvement within the allowlisted files."]
        if parent is not None and parent.patch:
            parts.append("\nFor reference, a prior accepted change in this "
                         f"lineage (summary: {parent.summary!r}):\n{parent.patch}")
            parts.append("\nPropose a DIFFERENT, complementary improvement — do "
                         "not repeat the above.")
        try:
            resp = llm.complete(system, [{"role": "user", "content": "\n".join(parts)}],
                                budget=budget, max_tokens=max_tokens, model=model)
        except Exception as e:  # pragma: no cover -- provider error => no proposal
            log.warning("self_modify_loop: proposer llm failed: %s", e)
            return None
        diff = _extract_diff(getattr(resp, "text", "") or "")
        if not diff:
            return None
        summary = (parent.summary + " (variant)") if parent else "code self-modification"
        return Proposal(patch=diff, summary=summary)
    return _propose


# --- proof-gated surface widening --------------------------------------------

@dataclass(frozen=True)
class SurfaceTier:
    """One rung of the widening ladder: a named surface plus how much proof it
    takes to REACH it. ``min_promotions`` is the cumulative count of promoted,
    non-rolled-back changes required before this tier's (wider) surface unlocks."""

    name: str
    editable_globs: tuple[str, ...]
    min_promotions: int = 0

    def surface(self) -> sm.EditableSurface:
        return sm.EditableSurface(editable_globs=self.editable_globs)


@dataclass
class WideningPolicy:
    """An ordered ladder of surfaces the loop is allowed to edit, unlocked by
    proof. Tier 0 should be the narrowest (often empty). A tier unlocks only when
    the accumulated track record meets its ``min_promotions`` AND no promoted
    change has been rolled back — a regression forfeits widening entirely."""

    tiers: tuple[SurfaceTier, ...] = ()

    def __post_init__(self) -> None:
        # Keep tiers in ascending proof order so `earned` can walk them.
        object.__setattr__(self, "tiers",
                           tuple(sorted(self.tiers, key=lambda t: t.min_promotions)))

    def earned(self, *, promotions: int, had_rollback: bool) -> SurfaceTier:
        """The widest tier the track record has earned. With any rollback on
        record, widening is frozen at tier 0 (the narrowest) regardless of the
        promotion count — trust, once broken by a regression, is not auto-restored."""
        if not self.tiers:
            return SurfaceTier("empty", (), 0)
        if had_rollback:
            return self.tiers[0]
        earned = self.tiers[0]
        for tier in self.tiers:
            if promotions >= tier.min_promotions:
                earned = tier
            else:
                break
        return earned


def _archive_track_record(archive: CodeArchive) -> tuple[int, bool]:
    """``(promotions, had_rollback)`` from the archive booleans. Convenient but
    NOT authoritative — the archive is a mutable sidecar an attacker with the
    file could forge to unlock a wider surface. Prefer :func:`_ledger_track_record`."""
    promotions = sum(1 for c in archive.candidates if c.promoted and not c.rolled_back)
    had_rollback = any(c.promoted and c.rolled_back for c in archive.candidates)
    return promotions, had_rollback


def _ledger_track_record(ledger, *, rung: str = "code") -> tuple[int, bool]:
    """``(promotions, had_rollback)`` from the signed promotion ledger — the
    AUTHORITATIVE source for proof-gated widening (Gap 6).

    The :class:`~maverick.self_improvement.PromotionLedger` is written only by the
    controller after every gate passed and the promotion was signed into the
    audit chain, and it is persisted 0600 — so unlike the DGM archive's
    self-attested booleans it cannot be forged by tampering with a sidecar to
    unlock a wider editable surface. Counts promotions at ``rung`` (the code rung)
    that were not rolled back; a single rolled-back code promotion forfeits
    widening."""
    try:
        records = list(ledger.all())
    except Exception:  # pragma: no cover -- unreadable ledger -> no trust earned
        log.warning("self_modify_loop: could not read promotion ledger", exc_info=True)
        return 0, True  # fail closed: freeze widening if the record can't be read
    at_rung = [r for r in records if getattr(r, "rung", None) == rung]
    promotions = sum(1 for r in at_rung if not getattr(r, "rolled_back", False))
    had_rollback = any(getattr(r, "rolled_back", False) for r in at_rung)
    return promotions, had_rollback


def earned_surface(
    archive: CodeArchive, policy: WideningPolicy, *,
    ledger=None, rung: str = "code",
) -> tuple[SurfaceTier, sm.EditableSurface]:
    """The tier + surface the track record currently entitles the loop to edit.

    When a signed promotion ``ledger`` is supplied the track record is derived
    from it (the tamper-resistant, authoritative source — Gap 6); otherwise it
    falls back to the archive's booleans (convenient for tests / offline, but not
    a security boundary). Pure — reads the record, decides nothing else."""
    if ledger is not None:
        promotions, had_rollback = _ledger_track_record(ledger, rung=rung)
    else:
        promotions, had_rollback = _archive_track_record(archive)
    tier = policy.earned(promotions=promotions, had_rollback=had_rollback)
    return tier, tier.surface()


# --- one cycle ---------------------------------------------------------------

@dataclass
class CycleReport:
    """The outcome of one DGM cycle."""

    proposed: bool = False
    review_ok: bool = False
    evaluated: bool = False
    promoted: bool = False
    candidate_id: str | None = None
    tier: str = ""
    baseline_score: float = 0.0
    candidate_score: float = 0.0
    samples: int = 0
    reason: str = ""


def run_cycle(
    *,
    archive: CodeArchive,
    proposer: Proposer,
    evaluate: Callable[[str, sm.EditableSurface], object],
    policy: WideningPolicy | None = None,
    rung: str = "code",
    approve: Callable[[CodeCandidate, str], tuple[str | None, str | None]] | None = None,
    controller: object | None = None,
    ledger=None,
    generation: int = 0,
) -> CycleReport:
    """Run one governed DGM cycle. Returns a :class:`CycleReport`; never raises.

    ``evaluate(patch, surface) -> EvalResult`` is the injected Phase-2 harness
    (isolated-copy baseline-vs-candidate scoring). ``approve`` is the human
    approval seam for the code rung — given the candidate and the payload digest
    it returns ``(approval_signature, payload_sha256)``; ``None`` means no human
    signed, so a code-rung change will be refused by the gate (the correct
    autonomous-loop behaviour: propose + evaluate + archive freely, but never
    self-promote code). ``policy`` gates which surface may be edited this cycle;
    when a signed ``ledger`` is given the widening track record is read from it
    (the tamper-resistant source — Gap 6) instead of the archive.

    A no-op (empty report) while the engine is disabled — checked here so no
    proposer is ever invoked on a default deployment."""
    report = CycleReport()
    if not (sm.enabled() and _si_enabled()):
        report.reason = "self-modification disabled"
        return report
    try:
        policy = policy or WideningPolicy()
        tier, surface = earned_surface(archive, policy, ledger=ledger, rung=rung)
        report.tier = tier.name
        if not surface.editable_globs:
            report.reason = f"tier {tier.name!r} grants no editable surface yet"
            return report

        parent = archive.sample()
        proposal = proposer(parent, surface)
        if proposal is None or not (proposal.patch or "").strip():
            report.reason = "proposer produced no patch"
            return report
        report.proposed = True

        review = sm.review_patch(proposal.patch, surface)
        report.review_ok = review.ok
        if not review.ok:
            report.reason = review.reason
            return report

        result = evaluate(proposal.patch, surface)
        report.evaluated = bool(getattr(result, "ok", False))
        report.baseline_score = float(getattr(result, "baseline_score", 0.0))
        report.candidate_score = float(getattr(result, "candidate_score", 0.0))
        report.samples = int(getattr(result, "samples", 0))
        if not report.evaluated:
            report.reason = getattr(result, "reason", "") or "evaluation failed"
            return report

        widens, reasons = sm.capability_diff(proposal.patch)
        cand = CodeCandidate(
            summary=proposal.summary or "code self-modification",
            patch=proposal.patch,
            score=report.candidate_score, baseline_score=report.baseline_score,
            samples=report.samples,
            parent_id=(parent.id if parent else None), generation=generation,
            capability_widens=(True if widens else None), reasons=tuple(reasons),
        )
        report.candidate_id = cand.id

        signature = payload_sha256 = None
        if approve is not None:
            signature, payload_sha256 = approve(cand, cand.patch_sha256)

        verdict = sm.propose_code_change(
            proposal.patch, summary=cand.summary,
            baseline_score=cand.baseline_score, candidate_score=cand.score,
            samples=cand.samples, rollback=cand.id, surface=surface,
            capability_widens=cand.capability_widens,
            approval_signature=signature, payload_sha256=payload_sha256,
            controller=controller, rung=rung,
        )
        cand.promoted = bool(getattr(verdict, "ok", False))
        report.promoted = cand.promoted
        report.reason = "" if cand.promoted else (getattr(verdict, "reason", "") or
                                                  "not promoted")
        archive.add(cand)
        return report
    except Exception as e:  # pragma: no cover -- a privileged loop must never crash a run
        log.warning("self_modify_loop: cycle errored; refusing", exc_info=True)
        report.reason = f"cycle error: {e}"
        return report


def run_loop(
    *,
    archive: CodeArchive,
    proposer: Proposer,
    evaluate: Callable[[str, sm.EditableSurface], object],
    policy: WideningPolicy | None = None,
    cycles: int = 1,
    **cycle_kwargs,
) -> list[CycleReport]:
    """Run up to ``cycles`` governed DGM cycles, threading the generation counter
    and accumulating into ``archive``. Stops early with an inert report if the
    engine is disabled. Each cycle is independent and fail-closed."""
    reports: list[CycleReport] = []
    if not (sm.enabled() and _si_enabled()):
        return [CycleReport(reason="self-modification disabled")]
    for gen in range(max(0, int(cycles))):
        reports.append(run_cycle(
            archive=archive, proposer=proposer, evaluate=evaluate,
            policy=policy, generation=gen, **cycle_kwargs))
    return reports


def _si_enabled() -> bool:
    try:
        from . import self_improvement
        return bool(self_improvement.enabled())
    except Exception:  # pragma: no cover -- can't confirm -> treat as off
        return False


__all__ = [
    "Proposal", "Proposer", "llm_proposer", "SurfaceTier", "WideningPolicy",
    "CycleReport", "earned_surface", "run_cycle", "run_loop",
]
