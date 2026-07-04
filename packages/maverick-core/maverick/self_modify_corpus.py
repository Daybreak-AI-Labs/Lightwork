"""Targeted code-eval corpus + held-in/held-out split (Gap 2).

``self_modify_eval.pytest_score`` runs the WHOLE suite and scores by pass rate.
For a code change that fixes one thing and breaks nothing, that reads as ~flat
(9,000 passing → 9,000 passing) → the evidence gate sees ≈0 improvement and
refuses. To make "beats baseline" *discriminating* for code, the eval has to be
targeted (the tests that exercise the changed capability) and it has to guard
against overfitting the same way :mod:`maverick.self_harness_eval` does for
prompt lines: split the corpus into a held-in set and an UNSEEN held-out set, and
judge promotion on the held-out generalization, not the in-sample score.

A :class:`CodeEvalCorpus` is just the pytest node-ids (or files) that pin the
targeted capability. :func:`evaluate_on_corpus` scores baseline vs candidate on
both splits on isolated copies (never the live tree, via the Phase-2 harness),
and reports:

* the **held-out** baseline/candidate scores — what the governed gate promotes
  on (generalization, the honest signal);
* an **overfit** flag — candidate beat baseline on held-in but NOT held-out,
  which a caller treats as a refusal (the change learned the seen tests, not the
  capability).

Deterministic throughout (the split reuses ``self_harness_eval.corpus_split``'s
stable content-hash ordering), so a re-run validates against the same unseen
tests. Isolation policy is inherited from ``self_modify_eval`` — under
require-container/enterprise it fails closed rather than running a candidate's
tests on the host.
"""
from __future__ import annotations

import logging
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .self_modify_eval import (
    ApplyResult,
    EvalSandboxError,
    Score,
    _default_materialize,
    git_apply,
    resolve_eval_sandbox,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CodeEvalCorpus:
    """The targeted test set that pins the capability a code change touches.

    ``test_ids`` are pytest node-ids or paths (e.g.
    ``packages/x/tests/test_money.py::test_round``). ``command_prefix`` is the
    runner the ids are appended to. Keep it NARROW — the whole point is to
    measure the changed capability, not the entire suite."""

    test_ids: tuple[str, ...] = ()
    command_prefix: str = "python3 -m pytest -q"

    def split(self, *, held_out_frac: float = 0.3) -> tuple[list[str], list[str]]:
        """Deterministic ``(held_in, held_out)`` split of the test ids. Reuses the
        stable content-hash ordering from :func:`self_harness_eval.corpus_split`,
        so the same corpus always holds out the same tests."""
        from .self_harness_eval import corpus_split
        cases = [{"goal": t} for t in self.test_ids]
        return corpus_split(cases, held_out_frac=held_out_frac)


@dataclass(frozen=True)
class CorpusEvalResult:
    """Baseline-vs-candidate on both splits, plus the overfit verdict."""

    ok: bool
    baseline_score: float          # held-OUT baseline (what the gate promotes on)
    candidate_score: float         # held-OUT candidate
    samples: int                   # held-OUT test count
    held_in_baseline: float = 0.0
    held_in_candidate: float = 0.0
    overfit: bool = False
    applied: bool = False
    reason: str = ""

    @property
    def improvement(self) -> float:
        return self.candidate_score - self.baseline_score


def _subset_score(
    ids: list[str], sb, *, command_prefix: str, timeout: float, budget,
) -> Score:
    """Run the given test ids in the (already-resolved, per-copy) sandbox ``sb``
    and score by pass rate. An empty id list is a legitimately empty split →
    ``Score(0.0, 0)`` (the caller's finite guards then skip it). Fails closed
    (0/0) on a budget stop or exec error."""
    if not ids:
        return Score(0.0, 0)
    if budget is not None:
        try:
            budget.check()
        except Exception as e:
            log.warning("self_modify_corpus: budget stop: %s", e)
            return Score(0.0, 0)
    cmd = command_prefix + " " + " ".join(shlex.quote(i) for i in ids)
    try:
        res = sb.exec(cmd, timeout=timeout)
    except Exception as e:  # pragma: no cover -- exec failure is a 0 score
        log.warning("self_modify_corpus: test command failed: %s", e)
        return Score(0.0, 0)
    from .self_modify_eval import _parse_pytest_counts
    passed, failed = _parse_pytest_counts(
        (getattr(res, "stdout", "") or "") + (getattr(res, "stderr", "") or ""))
    total = passed + failed
    if total == 0:
        return Score(0.0, 0)
    return Score(value=passed / total, samples=total)


def evaluate_on_corpus(
    patch: str,
    *,
    src: Path,
    workroot: Path,
    corpus: CodeEvalCorpus,
    sandbox=None,
    sandbox_factory: Callable[[Path], object] | None = None,
    held_out_frac: float = 0.3,
    timeout: float = 600.0,
    budget=None,
    materialize: Callable[[Path, Path], None] | None = None,
    apply_fn: Callable[[str, Path], ApplyResult] | None = None,
) -> CorpusEvalResult:
    """Score a patch on isolated copies against the targeted corpus, split into
    held-in and held-out, and judge on the held-OUT generalization.

    Never mutates ``src``; never raises. The promotion scores are the held-out
    baseline/candidate. ``overfit`` is set when the candidate improved the
    held-IN set but did not improve the held-OUT set — a caller treats that as a
    refusal (the change fit the seen tests, not the capability). Fails closed
    (non-``ok``) on isolation-policy refusal, a non-applying patch, or an empty
    held-out split.

    Execution is **per copy**: baseline and candidate run in their own trees, so
    the exec backend is resolved for each. ``sandbox_factory(copy_dir)`` is the
    seam that builds a backend rooted at a copy (a real container backend per
    copy); the default resolves through :func:`resolve_eval_sandbox`, which under
    require-container/enterprise refuses host exec (fail closed)."""
    src, workroot = Path(src), Path(workroot)
    materialize = materialize or _default_materialize
    apply = apply_fn or (lambda p, wd: git_apply(p, wd, sandbox=sandbox))
    make_sb = sandbox_factory or (lambda wd: resolve_eval_sandbox(sandbox, Path(wd)))

    held_in, held_out = corpus.split(held_out_frac=held_out_frac)
    if not held_out:
        return CorpusEvalResult(
            False, 0.0, 0.0, 0, applied=False,
            reason="corpus has no held-out split (need >= 2 targeted tests)")

    base_dir, cand_dir = workroot / "baseline", workroot / "candidate"
    try:
        materialize(src, base_dir)
        materialize(src, cand_dir)
    except Exception as e:
        return CorpusEvalResult(False, 0.0, 0.0, 0, applied=False,
                                reason=f"could not isolate workspace: {e}")

    applied = apply(patch, cand_dir)
    if not applied.ok:
        return CorpusEvalResult(False, 0.0, 0.0, 0, applied=False,
                                reason=applied.reason)

    try:
        sb_base, sb_cand = make_sb(base_dir), make_sb(cand_dir)
    except EvalSandboxError as e:
        return CorpusEvalResult(False, 0.0, 0.0, 0, applied=True, reason=str(e))

    def _score(ids, sb):
        return _subset_score(ids, sb, command_prefix=corpus.command_prefix,
                             timeout=timeout, budget=budget)

    b_out = _score(held_out, sb_base)
    c_out = _score(held_out, sb_cand)
    b_in = _score(held_in, sb_base)
    c_in = _score(held_in, sb_cand)

    overfit = (c_in.value > b_in.value) and (c_out.value <= b_out.value)
    reason = (f"held-out baseline={b_out.value:.4f} candidate={c_out.value:.4f} "
              f"over {min(b_out.samples, c_out.samples)} tests"
              + ("; OVERFIT: gained held-in only" if overfit else ""))
    return CorpusEvalResult(
        ok=not overfit,
        baseline_score=b_out.value, candidate_score=c_out.value,
        samples=min(b_out.samples, c_out.samples),
        held_in_baseline=b_in.value, held_in_candidate=c_in.value,
        overfit=overfit, applied=True, reason=reason,
    )


__all__ = ["CodeEvalCorpus", "CorpusEvalResult", "evaluate_on_corpus"]
