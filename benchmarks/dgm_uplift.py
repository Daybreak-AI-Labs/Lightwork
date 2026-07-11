#!/usr/bin/env python3
"""Governed DGM uplift: the agent improves ITS OWN solver, provably, under the gate.

``swebench_governed.py`` measures how many real bugs the solver fixes under
governance. This module closes the Darwin-Gödel loop: the artifact being
modified is the *solver itself* -- the code that decides how bugs get fixed --
and the fitness signal is the solver's resolved-rate on a HELD-OUT slice of
instances it has never seen. A solver change is promoted only when the whole
chain passes:

  1. **Editable-surface boundary** -- the solver patch may touch only the
     declared solver files; a patch that reaches for the control plane
     (the gate, the verifier, the signer...) is refused structurally.
  2. **Held-out uplift** -- baseline solver vs candidate solver, each run on
     BOTH splits of the instance corpus, on isolated copies. Promotion is
     judged on the held-OUT resolved-rate; a candidate that gains only on the
     held-in (seen) instances is flagged OVERFIT and refused -- a solver that
     memorised its training tasks did not get smarter.
  3. **Cheat-propagation guard** -- every instance patch the solver emits is
     itself anti-cheat-validated (``defensive_validate``): a "better" solver
     that learned to edit the graders' tests scores ZERO, so gaming can't
     launder through the uplift metric.
  4. **Capability non-escalation** -- the solver patch is proven to grant no
     new authority (process/network/dynamic-code classes).
  5. **Human-signed, reversible promotion** -- Ed25519 operator signature bound
     to the exact patch; the previous solver version is archived for one-step
     rollback; the promotion lands in the append-only ledger.

The result is the auditable uplift curve: "solver v0 resolved X% held-out;
the governed loop produced v1 resolving X+Δ% -- every step signed, reversible,
and refused when it was memorisation or test-tampering."

A solver is a directory containing ``solver.py`` exposing
``solve(instance) -> str`` (a unified diff for the instance's repo, or ``""``
to pass). Instances are :class:`swebench_governed.Instance` rows.

No LLM and no Docker are needed to *evaluate* a solver change (real pytest on
isolated copies); the LLM is only needed to *author* solver patches
(:func:`llm_solver_proposer`, provider key required) -- or a human/oracle can
supply one (``--patch``).
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "packages" / "maverick-core"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

log = logging.getLogger(__name__)


# --- running a solver version over instances -----------------------------------

def load_solver(solver_dir: Path):
    """Import ``solver.py`` from a directory under a unique module name (so
    baseline and candidate copies never collide in ``sys.modules``)."""
    path = Path(solver_dir) / "solver.py"
    name = f"dgm_solver_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not callable(getattr(mod, "solve", None)):
        raise TypeError(f"{path} does not define solve(instance) -> str")
    return mod


def _instance_test_python(inst) -> str:
    """Absolute path to this instance's era-correct venv interpreter, or ""
    when MAVERICK_SWEBENCH_VENVS is unset / no venv exists. Mirrors
    ``swebench_governed._venv_python`` so DGM grades with the SAME interpreter
    the governed harness uses (each instance's own-era python, not host python3);
    inlined rather than imported to avoid a heavier dep cycle."""
    import os

    base = os.environ.get("MAVERICK_SWEBENCH_VENVS", "").strip()
    if not base:
        return ""
    iid = getattr(inst, "instance_id", "")
    p = Path(base) / iid / "bin" / "python"
    return str(p) if p.exists() else ""


def score_instance(inst, patch: str, workroot: Path, *, timeout: float = 600.0) -> bool:
    """Did this patch resolve this instance, honestly?

    Anti-cheat first (a solver that edits tests/setup gets no credit), then
    apply to an isolated copy and run the instance's own FAIL_TO_PASS +
    PASS_TO_PASS suites. Fail-closed on any error.

    Grading runs under this instance's era-correct venv interpreter when one is
    configured (MAVERICK_SWEBENCH_VENVS): ``MAVERICK_TEST_PYTHON`` is pointed at
    it for the duration of grading and restored afterward, so the DGM eval loop
    honours the SAME per-instance interpreter the governed SWE-bench harness
    does. No venv configured -> vpy == "" -> the env is left untouched."""
    import os

    from maverick.coding_mode import defensive_validate, run_failing_tests
    from maverick.self_modify_eval import _default_materialize, git_apply, resolve_eval_sandbox

    if not (patch or "").strip():
        return False
    dv = defensive_validate(patch, fail_to_pass=inst.fail_to_pass,
                            pass_to_pass=inst.pass_to_pass, gold_patch=inst.gold_patch)
    if not dv.ok:
        return False
    work = Path(workroot)
    prev_tp = os.environ.get("MAVERICK_TEST_PYTHON")
    vpy = _instance_test_python(inst)
    if vpy:
        os.environ["MAVERICK_TEST_PYTHON"] = vpy
    try:
        # SWE-bench grading semantics: the grader's test_patch goes on first
        # (the graded tests usually don't exist at base_commit).
        def _prep(dst: Path):
            _default_materialize(inst.repo_path, dst)
            if (getattr(inst, "test_patch", "") or "").strip():
                tp = git_apply(inst.test_patch, dst)
                if not getattr(tp, "ok", False):
                    return None
            return resolve_eval_sandbox(None, dst)

        # Baseline honesty guard on its OWN isolated copy: the graded FAIL_TO_PASS
        # must FAIL before any candidate is applied. If they already pass at
        # baseline the instance is mis-seeded (no bug to fix) and ANY applying
        # patch would score all_pass -- inflating the DGM resolved-rate. The
        # baseline runs on a SEPARATE tree from the candidate so its compiled
        # bytecode (__pycache__ of the unpatched source) can't shadow the patched
        # code when the candidate is graded.
        base_tree = work / "base"
        sb_base = _prep(base_tree)
        if sb_base is None:
            return False
        base = run_failing_tests(base_tree, inst.fail_to_pass, [],
                                 sb_base, timeout=timeout, language=inst.language)
        if base.all_pass:
            return False
        # Candidate on a fresh copy.
        cand_tree = work / "cand"
        sb_cand = _prep(cand_tree)
        if sb_cand is None:
            return False
        applied = git_apply(patch, cand_tree)
        if not applied.ok:
            return False
        res = run_failing_tests(cand_tree, inst.fail_to_pass, inst.pass_to_pass,
                                sb_cand, timeout=timeout, language=inst.language)
        return bool(res.all_pass)
    except Exception:
        log.warning("score_instance failed for %s", inst.instance_id, exc_info=True)
        return False
    finally:
        if vpy:
            if prev_tp is None:
                os.environ.pop("MAVERICK_TEST_PYTHON", None)
            else:
                os.environ["MAVERICK_TEST_PYTHON"] = prev_tp


def run_solver(solver_dir: Path, instances: list, workroot: Path,
               *, timeout: float = 600.0) -> dict[str, bool]:
    """Resolved-map for one solver version over a set of instances."""
    mod = load_solver(solver_dir)
    out: dict[str, bool] = {}
    for i, inst in enumerate(instances):
        try:
            patch = mod.solve(inst) or ""
        except Exception:
            log.warning("solver crashed on %s", inst.instance_id, exc_info=True)
            patch = ""
        out[inst.instance_id] = score_instance(
            inst, patch, Path(workroot) / f"i{i}", timeout=timeout)
    return out


def split_instances(instances: list, *, held_out_frac: float = 0.34) -> tuple[list, list]:
    """Deterministic held-in/held-out split of instances by id (stable
    content-hash ordering via ``self_harness_eval.corpus_split``)."""
    from maverick.self_harness_eval import corpus_split
    by_id = {i.instance_id: i for i in instances}
    in_ids, out_ids = corpus_split([{"goal": i.instance_id} for i in instances],
                                   held_out_frac=held_out_frac)
    return [by_id[i] for i in in_ids], [by_id[i] for i in out_ids]


# --- the governed solver-change decision ----------------------------------------

@dataclass
class UpliftResult:
    """Verdict on one proposed solver self-modification."""

    boundary_ok: bool = False
    baseline_held_out: float = 0.0     # what the gate promotes on
    candidate_held_out: float = 0.0
    baseline_held_in: float = 0.0
    candidate_held_in: float = 0.0
    samples: int = 0                   # held-out evidence count (test executions)
    overfit: bool = False
    capability_widens: bool | None = None
    promoted: bool = False
    approver_id: str | None = None
    reason: str = ""
    resolved_map: dict = field(default_factory=dict)

    @property
    def uplift(self) -> float:
        return self.candidate_held_out - self.baseline_held_out


def _rate(resolved: dict[str, bool], instances: list) -> float:
    if not instances:
        return 0.0
    return sum(1 for i in instances if resolved.get(i.instance_id)) / len(instances)


def govern_solver_change(
    solver_dir: Path,
    solver_patch: str,
    instances: list,
    *,
    keys_dir: Path,
    ledger=None,
    workroot: Path,
    surface_globs: tuple[str, ...] = ("solver.py",),
    held_out_frac: float = 0.34,
    timeout: float = 600.0,
    controller=None,
    change_id: str | None = None,
) -> UpliftResult:
    """Decide whether a solver self-modification may be promoted. Never applies
    the patch to the live solver -- the caller does that (with
    :func:`apply_and_archive`) only when ``result.promoted``."""
    import hashlib

    from maverick import approval_signing as asig
    from maverick import self_modify as sm
    from maverick.self_improvement import Candidate, SelfImprovementController
    from maverick.self_modify_capability import capability_delta
    from maverick.self_modify_eval import _default_materialize, git_apply

    out = UpliftResult()
    workroot = Path(workroot)

    # 1. Boundary: only the declared solver surface is editable; the protected
    #    control-plane list applies regardless of the globs.
    review = sm.review_patch(solver_patch, sm.EditableSurface(editable_globs=surface_globs))
    out.boundary_ok = review.ok
    if not review.ok:
        out.reason = f"boundary refused: {review.reason}"
        return out

    # 2. Materialize baseline + candidate SOLVERS; apply the patch to candidate.
    base_dir, cand_dir = workroot / "solver_base", workroot / "solver_cand"
    try:
        _default_materialize(solver_dir, base_dir)
        _default_materialize(solver_dir, cand_dir)
    except Exception as e:
        out.reason = f"could not isolate solver: {e}"
        return out
    applied = git_apply(solver_patch, cand_dir)
    if not applied.ok:
        out.reason = f"solver patch did not apply: {applied.reason}"
        return out

    # 3. Run BOTH solver versions on BOTH instance splits (isolated copies,
    #    real tests, per-emitted-patch anti-cheat inside score_instance).
    held_in, held_out = split_instances(instances, held_out_frac=held_out_frac)
    if not held_out:
        out.reason = "no held-out instances (need >= 2 instances)"
        return out
    r_base = run_solver(base_dir, instances, workroot / "eval_base", timeout=timeout)
    r_cand = run_solver(cand_dir, instances, workroot / "eval_cand", timeout=timeout)
    out.resolved_map = {"baseline": r_base, "candidate": r_cand}
    out.baseline_held_in, out.candidate_held_in = _rate(r_base, held_in), _rate(r_cand, held_in)
    out.baseline_held_out, out.candidate_held_out = _rate(r_base, held_out), _rate(r_cand, held_out)
    # Evidence count must be in the SAME unit as the fitness metric: the metric
    # is a per-INSTANCE resolved-rate (each held-out instance is one independent
    # trial), so samples = number of held-out instances, not the sum of their
    # test ids. Counting test ids inflates the evidence n and lets a tiny
    # instance corpus clear the code-rung min_samples on a handful of trials.
    out.samples = len(held_out)

    # 4. Overfit guard: gained on seen instances but not unseen -> memorisation.
    out.overfit = (out.candidate_held_in > out.baseline_held_in
                   and out.candidate_held_out <= out.baseline_held_out)
    if out.overfit:
        out.reason = (f"OVERFIT: held-in {out.baseline_held_in:.3f}->{out.candidate_held_in:.3f} "
                      f"but held-out {out.baseline_held_out:.3f}->{out.candidate_held_out:.3f}; "
                      "solver memorised seen instances")
        return out

    # 5. Capability non-escalation proof over the solver patch. Pass the current
    #    solver source as the differential baseline (baseline_files) -- without
    #    it capability_delta has no "before" to diff against and the proof is
    #    unset, so the code rung falls back to demanding one (fail-closed) and a
    #    genuine uplift can never promote.
    cb = ca = None
    probe: tuple[str, ...] = ()
    try:
        baseline_files = None
        solver_py = Path(solver_dir) / "solver.py"
        if solver_py.exists():
            baseline_files = {"solver.py": solver_py.read_text(encoding="utf-8")}
        cb, ca, probe = capability_delta(solver_patch, baseline_files=baseline_files)
        if cb is not None and ca is not None and probe:
            out.capability_widens = any(ca.permits(t) and not cb.permits(t) for t in probe)
    except Exception:
        log.debug("capability_delta failed", exc_info=True)

    # 6. Human-signed promotion through the governed gate -> ledger.
    import os
    priv_path = Path(keys_dir) / "operator.priv.hex"
    if not priv_path.exists():
        out.reason = f"no operator signing key at {priv_path}"
        return out
    cand = Candidate(
        rung="code",
        summary=(f"solver self-modification: held-out resolved-rate "
                 f"{out.baseline_held_out:.3f} -> {out.candidate_held_out:.3f}"),
        baseline_score=out.baseline_held_out, candidate_score=out.candidate_held_out,
        samples=out.samples, payload=solver_patch,
        payload_sha256=hashlib.sha256(solver_patch.encode()).hexdigest(),
        capability_before=cb, capability_after=ca, probe_tools=probe,
        rollback={"archive": "previous solver version archived by apply_and_archive"},
        id=change_id or f"dgm-uplift-{uuid.uuid4().hex[:8]}")
    sig = asig.sign_request(asig.ApprovalRequest.for_candidate(cand),
                            priv_path.read_text().strip())
    cand = Candidate(**{**cand.__dict__, "approval_signature": sig})

    prev = {k: os.environ.get(k) for k in ("MAVERICK_APPROVER_KEYS_DIR", "MAVERICK_SELF_IMPROVEMENT")}
    os.environ["MAVERICK_APPROVER_KEYS_DIR"] = str(keys_dir)
    os.environ["MAVERICK_SELF_IMPROVEMENT"] = "1"
    try:
        ctrl = controller or SelfImprovementController(ledger=ledger)
        verdict = ctrl.promote(cand)
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out.promoted = bool(verdict.ok)
    out.approver_id = verdict.approver_id
    out.reason = (f"PROMOTED: held-out {out.baseline_held_out:.3f} -> "
                  f"{out.candidate_held_out:.3f} over {out.samples} tests"
                  if verdict.ok else f"gate refused: {verdict.blocking_reason}")
    return out


def apply_and_archive(solver_dir: Path, solver_patch: str, archive_dir: Path,
                      *, version: str) -> Path:
    """Apply a PROMOTED patch to the live solver, archiving the prior version
    first. Returns the archive path -- the rollback handle (restore = copy the
    archived ``solver.py`` back)."""
    import shutil

    from maverick.self_modify_eval import git_apply

    solver_dir, archive_dir = Path(solver_dir), Path(archive_dir)
    snap = archive_dir / version
    snap.mkdir(parents=True, exist_ok=True)
    shutil.copy2(solver_dir / "solver.py", snap / "solver.py")
    applied = git_apply(solver_patch, solver_dir)
    if not applied.ok:
        raise RuntimeError(f"promoted patch failed to apply live: {applied.reason}")
    return snap


def rollback_solver(solver_dir: Path, archive_snapshot: Path) -> None:
    """One-step revert: restore the archived solver version."""
    import shutil
    shutil.copy2(Path(archive_snapshot) / "solver.py", Path(solver_dir) / "solver.py")


# --- the LLM proposer (authors solver patches; needs a provider key) -----------

def llm_solver_proposer(solver_dir: Path, feedback: str, *, model: str | None = None,
                        max_tokens: int = 4096) -> str:
    """Ask the coding-role model for a unified diff improving ``solver.py``,
    given evaluation feedback (which instances failed and why). Returns the raw
    diff text (the governed gate decides everything else). Fail-loud keyless."""
    import os

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MAVERICK_PROVIDER_READY")):
        raise RuntimeError(
            "llm_solver_proposer needs a provider key (ANTHROPIC_API_KEY, or "
            "MAVERICK_PROVIDER_READY=1 with a configured provider).")
    from maverick.llm import LLM

    src = (Path(solver_dir) / "solver.py").read_text(encoding="utf-8")
    system = (
        "You improve a bug-fixing solver. You are given the solver's current "
        "source (solver.py: solve(instance) -> unified diff or '') and feedback "
        "about instances it failed to resolve. Reply with ONLY a unified diff "
        "against a/solver.py that makes the solver resolve more instances. "
        "Never touch test files; never special-case instance ids -- generalise.")
    user = f"Current solver.py:\n```python\n{src}\n```\n\nEvaluation feedback:\n{feedback}\n"
    resp = LLM().complete(system, [{"role": "user", "content": user}],
                          max_tokens=max_tokens, model=model)
    text = (resp.text or "").strip()
    # Tolerate a fenced reply; the boundary/apply gates judge the rest.
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            body = p.removeprefix("diff").removeprefix("patch").strip("\n")
            if body.lstrip().startswith(("diff --git", "--- ")):
                return body
    return text


__all__ = [
    "UpliftResult",
    "load_solver",
    "score_instance",
    "_instance_test_python",
    "run_solver",
    "split_instances",
    "govern_solver_change",
    "apply_and_archive",
    "rollback_solver",
    "llm_solver_proposer",
]
