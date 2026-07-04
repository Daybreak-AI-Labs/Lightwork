"""Sandbox evaluation harness for governed code self-modification (Phase 2).

The Darwin-Gödel-Machine insight is **empirical validation instead of proof**: a
self-proposed code change earns promotion only by *measurably* beating the code
it would replace on held-out work. That measurement is what this module produces,
and it is the number :func:`maverick.self_modify.propose_code_change` gates on —
``baseline_score`` vs ``candidate_score``.

The evaluation is done on an **isolated copy** of the workspace, never in place:
materialize a throwaway copy, score the unpatched tree (baseline), apply the
candidate patch to a *second* copy and score that (candidate). The agent's own
code is therefore never mutated to measure a proposal — a failed or malicious
candidate can only ever damage a temp directory the caller discards.

Three seams keep it testable offline and honest in production, exactly the
pattern :mod:`maverick.self_harness_eval` uses:

* ``materialize(src, dest)`` — make the isolated copy. Default: a filtered
  ``copytree`` (skips ``.git``/caches). Injectable for snapshot-based copies.
* ``apply_fn(patch, workdir)`` — apply the diff. Default: ``git apply`` routed
  through ``sandbox.exec`` (kernel rule 4), ``--check`` first so a non-applying
  patch fails cleanly instead of half-writing.
* ``score_fn(workdir) -> Score`` — run the eval (a test/benchmark pass) and
  return ``(value, samples)``. This is the live seam; a real one runs the suite
  in the sandbox. The offline default is deterministic so the harness is
  runnable without a model or a toolchain.

Posture: pure mechanism. This module promotes nothing and reads no config; it
returns an :class:`EvalResult` the governed gate consumes. It **fails closed** —
any error (copy failed, patch didn't apply, scorer raised) yields a result whose
``ok`` is False and whose scores don't clear the gate.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Directories never worth copying into a throwaway eval tree (VCS + build/test
# caches). Skipping them keeps the copy cheap and avoids re-running stale caches.
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".venv", "venv", "node_modules", ".tox", "build", "dist",
    ".maverick-eval",  # our own scratch, never recurse into it
})


@dataclass(frozen=True)
class Score:
    """A scorer's output: a scalar ``value`` (higher is better) over ``samples``
    eval cases. ``samples`` feeds the governed gate's evidence floor."""

    value: float
    samples: int = 0


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    reason: str = ""


@dataclass(frozen=True)
class EvalResult:
    """Baseline-vs-candidate measurement for a proposed patch.

    ``baseline_score``/``candidate_score`` and ``samples`` are exactly what
    :func:`maverick.self_modify.propose_code_change` needs. ``applied`` records
    whether the patch applied to the isolated copy at all; when it didn't (or a
    scorer errored) ``ok`` is False and the scores are inert (equal), so the
    evidence gate sees no improvement and refuses — fail closed.
    """

    ok: bool
    baseline_score: float
    candidate_score: float
    samples: int
    applied: bool
    reason: str = ""

    @property
    def improvement(self) -> float:
        return self.candidate_score - self.baseline_score


# --- seam 1: materialize an isolated copy ------------------------------------

def _default_materialize(src: Path, dest: Path) -> None:
    """Copy ``src`` into ``dest`` skipping VCS/build/cache dirs. The default
    isolation: a candidate is evaluated against a throwaway tree, never the live
    workspace."""
    def _ignore(_dir: str, names: list[str]) -> set[str]:
        return {n for n in names if n in _SKIP_DIRS}
    shutil.copytree(src, dest, ignore=_ignore, symlinks=True,
                    ignore_dangling_symlinks=True)


# --- seam 2: apply the patch --------------------------------------------------

def git_apply(patch: str, workdir: Path, *, sandbox=None) -> ApplyResult:
    """Apply a unified diff to ``workdir`` with ``git apply``, routed through the
    sandbox (kernel rule 4: shell only via ``sandbox.exec``). Runs ``--check``
    first so a patch that would not apply cleanly is refused WITHOUT writing
    anything; only on a clean check is the real apply run. Returns an
    :class:`ApplyResult` — never raises."""
    workdir = Path(workdir)
    if not (patch or "").strip():
        return ApplyResult(False, "empty patch")
    try:
        patch_file = workdir / ".maverick-eval-patch.diff"
        patch_file.write_text(patch, encoding="utf-8")
    except OSError as e:  # pragma: no cover -- temp write failure
        return ApplyResult(False, f"could not stage patch: {e}")

    sb = sandbox
    if sb is None:
        from .sandbox.local import LocalBackend
        sb = LocalBackend(workdir=workdir)
    # -p1 strips the a//b/ prefix; --check validates without touching the tree.
    rel = patch_file.name
    try:
        chk = sb.exec(f"git apply --check -p1 {rel}")
        if not getattr(chk, "ok", False):
            return ApplyResult(False,
                               f"patch does not apply: {chk.stderr.strip()[:200]}")
        res = sb.exec(f"git apply -p1 {rel}")
        if not getattr(res, "ok", False):
            return ApplyResult(False,
                               f"git apply failed: {res.stderr.strip()[:200]}")
        return ApplyResult(True, "applied")
    except Exception as e:  # pragma: no cover -- sandbox exec failure
        return ApplyResult(False, f"apply error: {e}")
    finally:
        try:
            patch_file.unlink()
        except OSError:
            pass


# --- seam 3: score (deterministic offline default) ---------------------------

def _default_score(workdir: Path) -> Score:
    """Deterministic offline scorer default — NOT a real evaluator. It counts
    Python files under the tree as a stand-in "score" so the whole harness is
    runnable and testable without a toolchain or a model. Inject a real
    ``score_fn`` (a sandboxed test/benchmark pass) for production."""
    n = sum(1 for _ in Path(workdir).rglob("*.py"))
    return Score(value=float(n), samples=n)


def pytest_score(
    *, sandbox=None, command: str = "python3 -m pytest -q",
    timeout: float = 600.0,
) -> Callable[[Path], Score]:
    """Build a ``score_fn`` that runs a test command in the sandbox and scores by
    pass rate. The score is ``passed / (passed + failed)`` parsed from pytest's
    summary line; ``samples`` is the number of tests seen. A run that errors or
    yields no parseable summary scores ``0.0`` over ``0`` samples, so the
    evidence gate refuses it (fail closed). The command is NOT hard-coded to a
    package — the caller points it at the surface under evaluation."""
    def _score(workdir: Path) -> Score:
        sb = sandbox
        if sb is None:
            from .sandbox.local import LocalBackend
            sb = LocalBackend(workdir=Path(workdir))
        try:
            res = sb.exec(command, timeout=timeout)
        except Exception as e:  # pragma: no cover -- exec failure is a 0 score
            log.warning("self_modify_eval: test command failed: %s", e)
            return Score(0.0, 0)
        passed, failed = _parse_pytest_counts(
            (getattr(res, "stdout", "") or "") + (getattr(res, "stderr", "") or ""))
        total = passed + failed
        if total == 0:
            return Score(0.0, 0)
        return Score(value=passed / total, samples=total)
    return _score


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    """Extract ``(passed, failed)`` from pytest terminal output. Tolerant: a
    missing count is 0. Errors count as failures (a collection error is not a
    pass)."""
    import re
    passed = failed = 0
    for kind, rx in (("passed", r"(\d+) passed"), ("failed", r"(\d+) failed"),
                     ("error", r"(\d+) error")):
        m = re.search(rx, output)
        if m:
            if kind == "passed":
                passed = int(m.group(1))
            else:
                failed += int(m.group(1))
    return passed, failed


# --- the harness --------------------------------------------------------------

def evaluate_patch(
    patch: str,
    *,
    src: Path,
    workroot: Path,
    score_fn: Callable[[Path], Score] | None = None,
    apply_fn: Callable[[str, Path], ApplyResult] | None = None,
    materialize: Callable[[Path, Path], None] | None = None,
    sandbox=None,
) -> EvalResult:
    """Measure a patch on isolated copies of ``src``: score the unpatched tree
    (baseline), apply the patch to a second copy and score that (candidate).

    ``workroot`` is a caller-owned scratch directory the two copies are made
    under (and which the caller cleans up). Never mutates ``src``. Never raises —
    any failure (copy, apply, scorer) returns a non-``ok`` result whose scores do
    not clear the gate. The default seams make this runnable offline; inject
    ``score_fn=pytest_score(...)`` and a ``sandbox`` for a real evaluation."""
    src = Path(src)
    workroot = Path(workroot)
    score = score_fn or _default_score
    materialize = materialize or _default_materialize
    apply = apply_fn or (lambda p, wd: git_apply(p, wd, sandbox=sandbox))

    base_dir = workroot / "baseline"
    cand_dir = workroot / "candidate"
    try:
        materialize(src, base_dir)
        materialize(src, cand_dir)
    except Exception as e:
        log.warning("self_modify_eval: materialize failed: %s", e, exc_info=True)
        return EvalResult(False, 0.0, 0.0, 0, applied=False,
                          reason=f"could not isolate workspace: {e}")

    applied = apply(patch, cand_dir)
    if not applied.ok:
        return EvalResult(False, 0.0, 0.0, 0, applied=False, reason=applied.reason)

    try:
        base = score(base_dir)
        cand = score(cand_dir)
    except Exception as e:  # pragma: no cover -- a scorer must not crash the pass
        log.warning("self_modify_eval: scorer raised: %s", e, exc_info=True)
        return EvalResult(False, 0.0, 0.0, 0, applied=True,
                          reason=f"scorer error: {e}")

    # Evidence count is the smaller of the two arms' sample counts — the honest
    # basis for the comparison (you can only compare on cases both arms saw).
    samples = min(base.samples, cand.samples)
    return EvalResult(
        ok=True, baseline_score=base.value, candidate_score=cand.value,
        samples=samples, applied=True,
        reason=f"baseline={base.value:.4f} candidate={cand.value:.4f} "
               f"over {samples} samples",
    )


__all__ = [
    "Score", "ApplyResult", "EvalResult",
    "git_apply", "pytest_score", "evaluate_patch",
]
