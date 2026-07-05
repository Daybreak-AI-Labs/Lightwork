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


# --- isolation policy (Gap 4): never run a candidate's code on the host -------

class EvalSandboxError(RuntimeError):
    """Raised when the eval cannot run under the deployment's isolation policy —
    e.g. require-container/enterprise is active but no container backend was
    supplied, so applying + testing a candidate patch would execute untrusted
    code directly on the host."""


def container_required() -> bool:
    """Whether a container sandbox is mandatory for the eval (so host exec is
    refused). Uses the public, CONFIG-AWARE predicate
    ``sandbox.container_backend_required`` so ``[sandbox] require_container = true``
    in ``config.toml`` is honoured — not the bare private helper, which only sees
    ``MAVERICK_REQUIRE_CONTAINER_BACKEND`` / enterprise mode and would let the
    config knob silently no-op (fail-open). Off by default (single-tenant/dev
    keeps the warned host path)."""
    try:
        from .sandbox import container_backend_required
        return bool(container_backend_required())
    except Exception:  # pragma: no cover -- never block on a lookup error
        return False


def _host_visible(sb) -> bool:
    try:
        from .sandbox import fs_is_host_visible
        return bool(fs_is_host_visible(sb))
    except Exception:  # pragma: no cover -- unknown backend -> assume host-visible
        return True


_HOST_EVAL_WARNED = False


def resolve_eval_sandbox(sandbox, workdir: Path):
    """Return the sandbox the eval should exec in, enforcing isolation policy.

    A candidate patch is untrusted code; applying and *running its tests* is
    arbitrary execution. So under require-container/enterprise policy this
    REFUSES to fall back to host exec (``LocalBackend``) and raises
    :class:`EvalSandboxError` — fail closed. An explicit container backend is
    used as-is; a host-visible backend under policy is also refused. Only when
    the policy is inactive does it default to a warned ``LocalBackend`` (dev /
    single-tenant), matching ``build_sandbox``'s posture."""
    if sandbox is not None:
        if container_required() and _host_visible(sandbox):
            raise EvalSandboxError(
                "refusing to evaluate a candidate patch on a host-visible sandbox "
                "under require-container/enterprise policy: running its tests would "
                "execute untrusted code on the host. Pass a container backend "
                "(docker/gvisor/podman/firecracker).")
        return sandbox
    if container_required():
        raise EvalSandboxError(
            "refusing host-exec self-modify evaluation: require-container/enterprise "
            "policy is active but no container sandbox was supplied. Applying and "
            "testing a candidate patch on the host is arbitrary code execution. "
            "Inject a container backend into evaluate_patch(sandbox=...).")
    global _HOST_EVAL_WARNED
    if not _HOST_EVAL_WARNED:
        _HOST_EVAL_WARNED = True
        log.warning(
            "self_modify_eval: no container sandbox supplied; a candidate patch's "
            "tests will run on the HOST with no isolation. Acceptable only for a "
            "trusted single-tenant/dev box. Set [sandbox] require_container=true "
            "(or inject a container backend) to enforce isolation.")
    from .sandbox.local import LocalBackend
    return LocalBackend(workdir=Path(workdir))


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

def run_git_apply(patch: str, workdir: Path, sb, *,
                  patch_name: str = ".maverick-apply.diff") -> tuple[bool, str]:
    """Stage ``patch`` and apply it to ``workdir`` via an ALREADY-RESOLVED backend
    ``sb`` (kernel rule 4: shell only via ``sandbox.exec``): ``git apply --check``
    first so a non-applying patch writes nothing, then the real ``git apply -p1``.
    Returns ``(ok, message)``; never raises. This is the one shared apply
    mechanic — both the isolated eval (:func:`git_apply`) and the live-tree
    deploy (:func:`maverick.self_modify_apply.apply_change`) call it, so the
    staging/``-p1``/cleanup can't drift between them; the only difference is which
    backend the caller resolves."""
    workdir = Path(workdir)
    if not (patch or "").strip():
        return False, "empty patch"
    patch_file = workdir / patch_name
    try:
        patch_file.write_text(patch, encoding="utf-8")
    except OSError as e:  # pragma: no cover -- temp write failure
        return False, f"could not stage patch: {e}"
    try:
        chk = sb.exec(f"git apply --check -p1 {patch_file.name}")
        if not getattr(chk, "ok", False):
            return False, f"patch does not apply: {chk.stderr.strip()[:200]}"
        res = sb.exec(f"git apply -p1 {patch_file.name}")
        if not getattr(res, "ok", False):
            return False, f"git apply failed: {res.stderr.strip()[:200]}"
        return True, "applied"
    except Exception as e:  # pragma: no cover -- sandbox exec failure
        return False, f"apply error: {e}"
    finally:
        try:
            patch_file.unlink()
        except OSError:
            pass


def git_apply(patch: str, workdir: Path, *, sandbox=None) -> ApplyResult:
    """Apply a unified diff to an isolated ``workdir`` under the eval isolation
    policy: resolve a sandbox (refusing host exec under require-container), then
    run the shared :func:`run_git_apply`. Never raises."""
    workdir = Path(workdir)
    if not (patch or "").strip():
        return ApplyResult(False, "empty patch")
    try:
        sb = resolve_eval_sandbox(sandbox, workdir)
    except EvalSandboxError as e:
        return ApplyResult(False, str(e))
    ok, msg = run_git_apply(patch, workdir, sb, patch_name=".maverick-eval-patch.diff")
    return ApplyResult(ok, msg)


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
    timeout: float = 600.0, budget=None,
) -> Callable[[Path], Score]:
    """Build a ``score_fn`` that runs a test command in the sandbox and scores by
    pass rate. The score is ``passed / (passed + failed)`` parsed from pytest's
    summary line; ``samples`` is the number of tests seen. A run that errors or
    yields no parseable summary scores ``0.0`` over ``0`` samples, so the
    evidence gate refuses it (fail closed). The command is NOT hard-coded to a
    package — the caller points it at the surface under evaluation.

    Isolation (Gap 4): when no ``sandbox`` is given the backend is resolved
    through :func:`resolve_eval_sandbox`, which REFUSES host exec under
    require-container/enterprise policy — a candidate's tests never run on the
    host there. ``timeout`` bounds the run; an optional ``budget`` is checked
    before the run so an exhausted budget stops the eval (fail closed)."""
    def _score(workdir: Path) -> Score:
        if budget is not None:
            try:
                budget.check()
            except Exception as e:
                log.warning("self_modify_eval: budget stop before test run: %s", e)
                return Score(0.0, 0)
        try:
            sb = resolve_eval_sandbox(sandbox, Path(workdir))
        except EvalSandboxError as e:
            log.warning("self_modify_eval: %s", e)
            return Score(0.0, 0)
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
    ``score_fn=pytest_score(...)`` and a ``sandbox`` for a real evaluation.

    Isolation (Gap 4): the harness resolves the sandbox up front and REFUSES to
    run under require-container/enterprise policy without a container backend —
    returning a non-``ok`` result rather than executing a candidate's code on the
    host. This is unconditional (there is no opt-out): a custom ``apply_fn``
    still hits the early check, though a custom ``score_fn`` is the caller's own
    responsibility to isolate."""
    src = Path(src)
    workroot = Path(workroot)
    score = score_fn or _default_score
    materialize = materialize or _default_materialize
    apply = apply_fn or (lambda p, wd: git_apply(p, wd, sandbox=sandbox))

    # Fail closed BEFORE copying/applying if policy forbids host exec here.
    try:
        resolve_eval_sandbox(sandbox, workroot)
    except EvalSandboxError as e:
        return EvalResult(False, 0.0, 0.0, 0, applied=False, reason=str(e))

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
    "Score", "ApplyResult", "EvalResult", "EvalSandboxError",
    "container_required", "resolve_eval_sandbox",
    "run_git_apply", "git_apply", "pytest_score", "evaluate_patch",
]
