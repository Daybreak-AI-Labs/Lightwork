"""Operable driver for the governed DGM loop (Gap 7).

Phases 1-4 and the gap fixes are library code; nothing *invoked* them. This is the
glue that makes the capability operable: it builds the archive, proposer, eval,
policy, and signed-ledger track record from config, runs governed cycles, and
persists the DGM population across runs — the counterpart to
``self_improvement_runner`` for the code rung, and what the ``maverick
self-modify`` CLI drives.

Everything is constructed lazily and the whole thing is a **no-op unless
``[self_modify] enable`` is set** (and the self-improvement engine is on): a
default deployment never proposes, evaluates, or applies anything. The pieces are
injectable seams so a run is testable offline; the defaults wire the real ones:

* **proposer** — :func:`self_modify_loop.llm_proposer` on the coding-role model
  (kernel rule 2; ``model_for_role``). Absent a provider it degrades to the null
  proposer (the loop simply no-ops), never crashing.
* **evaluate** — an isolated-copy measurement against ``[self_modify] eval_tests``
  (the targeted corpus, held-out-judged; Gap 2) or the whole suite, inside an
  enforced sandbox (Gap 4).
* **policy** — the widening ladder from ``[self_modify] tiers`` (or a single tier
  from ``editable_paths``), unlocked by the SIGNED promotion ledger (Gap 6).
* **archive** — persisted 0600 under the data dir, so lineage accumulates.

Promotion at the code rung still requires a human Ed25519 signature: the runner
searches, evaluates, and archives autonomously, but never self-promotes code.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import self_modify as sm
from .self_modify_archive import CodeArchive
from .self_modify_loop import (
    CycleReport,
    Proposal,
    SurfaceTier,
    WideningPolicy,
    run_loop,
)

log = logging.getLogger(__name__)


def archive_path() -> Path:
    from .paths import data_dir
    return data_dir("self_modify_archive.json")


def load_archive() -> CodeArchive:
    return CodeArchive.load(archive_path())


def _self_modify_config() -> dict:
    try:
        from .config import load_config
        return (load_config() or {}).get("self_modify") or {}
    except Exception:  # pragma: no cover -- config never blocks
        return {}


def build_policy(cfg: dict | None = None) -> WideningPolicy:
    """The widening ladder from config. ``[self_modify] tiers`` is a list of
    ``{name, editable_paths, min_promotions}`` tables (proof-gated widening); when
    absent, a single tier from ``editable_paths`` is used (unlocked at zero, so an
    enabled deployment with an allowlist can start editing). Tier 0 stays empty so
    a fresh deployment with no track record edits nothing."""
    cfg = _self_modify_config() if cfg is None else cfg
    raw_tiers = cfg.get("tiers")
    tiers: list[SurfaceTier] = [SurfaceTier("none", (), 0)]
    if isinstance(raw_tiers, list) and raw_tiers:
        for t in raw_tiers:
            if not isinstance(t, dict):
                continue
            globs = tuple(str(g).strip() for g in (t.get("editable_paths") or [])
                          if str(g).strip())
            if not globs:
                continue
            try:
                minp = int(t.get("min_promotions", 0))
            except (TypeError, ValueError):
                minp = 0
            # Clamp to >= 0: a negative min_promotions would sort a NON-empty
            # tier ahead of the empty tier-0, so the rollback-freeze (which
            # returns the lowest tier) would freeze to a wide surface instead of
            # to nothing. Keep the empty tier the narrowest.
            minp = max(0, minp)
            tiers.append(SurfaceTier(str(t.get("name") or f"tier{minp}"), globs, minp))
    else:
        globs = tuple(str(g).strip() for g in (cfg.get("editable_paths") or [])
                      if str(g).strip())
        if globs:
            tiers.append(SurfaceTier("configured", globs, 0))
    return WideningPolicy(tiers=tuple(tiers))


def _default_proposer(*, budget=None):
    """A coding-role LLM proposer, or the null proposer if no provider is usable
    (the loop then no-ops rather than crashing). The model is not hard-coded."""
    try:
        from .llm import LLM, model_for_role
        from .self_modify_loop import llm_proposer
        return llm_proposer(LLM(model_for_role("coding")), budget=budget)
    except Exception:  # pragma: no cover -- no provider -> inert proposer
        log.info("self_modify_runner: no coding model available; proposer is inert")
        return lambda *_: None


def _default_evaluate(*, tree: Path, sandbox=None, budget=None, cfg: dict):
    """Build the ``evaluate(patch, surface)`` seam against the real ``tree``.

    Uses the targeted corpus (``[self_modify] eval_tests``, held-out-judged) when
    configured, else the whole-suite pass rate. Each call runs on a fresh
    throwaway copy under a temp workroot (cleaned up), inside the enforced
    isolation policy. Returns an object exposing ``ok`` / ``baseline_score`` /
    ``candidate_score`` / ``samples`` — what the loop reads."""
    eval_tests = tuple(str(t).strip() for t in (cfg.get("eval_tests") or [])
                       if str(t).strip())

    def _evaluate(patch: str, *_):
        work = Path(tempfile.mkdtemp(prefix="maverick-selfmod-", dir=_work_root()))
        try:
            if eval_tests:
                from .self_modify_corpus import CodeEvalCorpus, evaluate_on_corpus
                return evaluate_on_corpus(
                    patch, src=tree, workroot=work,
                    corpus=CodeEvalCorpus(test_ids=eval_tests),
                    sandbox=sandbox, budget=budget)
            from .self_modify_eval import evaluate_patch, pytest_score
            return evaluate_patch(
                patch, src=tree, workroot=work,
                score_fn=pytest_score(sandbox=sandbox, budget=budget),
                sandbox=sandbox)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    return _evaluate


def _work_root() -> str:
    from .paths import data_dir
    root = data_dir("self_modify_work")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


@dataclass
class RunSummary:
    """Outcome of a runner invocation."""

    ran: bool
    reason: str = ""
    reports: list[CycleReport] = field(default_factory=list)

    @property
    def promoted(self) -> int:
        return sum(1 for r in self.reports if r.promoted)

    @property
    def evaluated(self) -> int:
        return sum(1 for r in self.reports if r.evaluated)


def run(
    cycles: int = 1,
    *,
    tree: Path | None = None,
    sandbox=None,
    proposer: Callable[..., Proposal | None] | None = None,
    evaluate: Callable[[str, object], object] | None = None,
    controller: object | None = None,
    approve: Callable | None = None,
    budget=None,
    persist: bool = True,
) -> RunSummary:
    """Run ``cycles`` governed DGM cycles end-to-end. A no-op (``ran=False``)
    while the engine is disabled. Loads and (if ``persist``) saves the DGM
    archive; the widening track record is read from the signed promotion ledger.
    Never raises — a failure yields a summary whose reports carry the reason.

    The ``proposer``/``evaluate`` seams default to the real coding-model proposer
    and isolated-copy evaluator; inject them (e.g. in tests, or to pin a model)
    to override. Promotion at the code rung still needs a human signature via
    ``approve``; without it the loop proposes/evaluates/archives but never
    self-promotes code."""
    from . import self_improvement as si
    if not (sm.enabled() and si.enabled()):
        return RunSummary(False, reason="self-modification disabled "
                          "([self_modify]/[self_improvement] enable)")
    try:
        cfg = _self_modify_config()
        policy = build_policy(cfg)
        archive = load_archive()
        ctrl = controller or si.shared()
        ledger = getattr(ctrl, "ledger", None)
        if ledger is None:
            # Proof-gated widening would otherwise fall back to the forgeable
            # archive booleans. si.shared() always carries a ledger, so this only
            # trips on a hand-built controller with none — warn loudly.
            log.warning("self_modify_runner: controller has no promotion ledger; "
                        "widening cannot use the tamper-resistant track record")
        tree = Path(tree) if tree is not None else Path.cwd()

        prop = proposer or _default_proposer(budget=budget)
        ev = evaluate or _default_evaluate(
            tree=tree, sandbox=sandbox, budget=budget, cfg=cfg)

        reports = run_loop(
            archive=archive, proposer=prop, evaluate=ev, policy=policy,
            cycles=cycles, rung="code", controller=ctrl, ledger=ledger,
            approve=approve)
        if persist:
            archive.save(archive_path())
        return RunSummary(True, reports=reports)
    except Exception as e:  # pragma: no cover -- a privileged driver never crashes a run
        log.warning("self_modify_runner: run errored", exc_info=True)
        return RunSummary(False, reason=f"runner error: {e}")


__all__ = [
    "RunSummary", "archive_path", "load_archive", "build_policy", "run",
]
