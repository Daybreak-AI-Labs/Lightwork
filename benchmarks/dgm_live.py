#!/usr/bin/env python3
"""LIVE runner for the governed DGM (Darwin-Godel) solver self-improvement loop.

``benchmarks/dgm_uplift.py`` is the library; ``proof/dgm_uplift_proof.py`` drives
its whole chain on a SYNTHETIC fixture (no LLM, no network) to prove the six
guarantees. This driver is the real-world sibling: it runs the SAME governed
chain against REAL staged SWE-bench Verified instances with the REAL LLM solver
(``benchmarks/solvers/baseline_v0``) and the REAL proposer
(``dgm_uplift.llm_solver_proposer``).

The loop, end to end:

  1. Load instances from a manifest (``swebench_governed._load_manifest`` -- the
     format ``fetch_swe_bench_verified.py`` emits).
  2. Optionally pre-filter to instances THIS host can actually grade for free
     (``swebench_governed._env_pregate``); report how many were dropped.
  3. Deterministically split held-in / held-out (``dgm_uplift.split_instances``).
  4. Evaluate the baseline solver on the HELD-IN slice only, to build the
     proposer's feedback (the held-out slice stays unseen -- that is the whole
     point of the uplift metric).
  5. Author a candidate solver patch: ``llm_solver_proposer`` (paid), or a
     stub patch (``--stub``, free) so the entire driver runs end-to-end at $0.
  6. ``dgm_uplift.govern_solver_change`` -- the full governed chain (editable
     surface, held-out uplift with overfit refusal, per-emitted-patch anti-cheat,
     capability non-escalation, Ed25519-signed + reversible promotion).
  7. Print an honest scoreboard: held-in / held-out resolved-rates for both arms,
     the verdict (PROMOTED / REFUSED / OVERFIT), the ledger path, and spend.

Every stage fails closed; one bad instance never crashes the run.

    # Free smoke (stub proposer + a synthetic offline solver + tiny corpus):
    python benchmarks/dgm_live.py --manifest tiny.jsonl --keys ./keys \
        --solver ./synth_solver --held-out-frac 0.7 --stub

    # Live paid round (real LLM solver + real proposer), gradable-filtered:
    python benchmarks/dgm_live.py --manifest instances.jsonl --keys ./keys \
        --ledger ./dgm_ledger.json --gradable-only --held-out-frac 0.5
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ROOT = pathlib.Path(__file__).resolve().parent.parent
# Same sys.path wiring dgm_uplift / the proof use, so ``import dgm_uplift`` and
# ``import swebench_governed`` resolve the sibling benchmark modules and their
# ``maverick`` imports find maverick-core.
sys.path.insert(0, str(_ROOT / "packages" / "maverick-core"))
sys.path.insert(0, str(_ROOT / "benchmarks"))

log = logging.getLogger(__name__)

# Mirrors the baseline_v0 solver's per-call ``max_dollars`` cap. Used only for
# the pre-flight worst-case spend CEILING (``--abort-at-dollars``); the actual
# spend is read back from the solver's per-call spend log.
_SOLVER_CALL_CAP = 0.10
_PROPOSER_EST = 0.05
_SPEND_LOG_ENV = "MAVERICK_DGM_SPEND_LOG"


@dataclass
class DriverOutcome:
    """Everything the scoreboard needs -- returned so tests can assert on it."""

    verdict: str = "NO-RUN"
    reason: str = ""
    n_instances: int = 0
    n_dropped: int = 0
    n_held_in: int = 0
    n_held_out: int = 0
    boundary_ok: bool = False
    overfit: bool = False
    promoted: bool = False
    approver_id: str | None = None
    baseline_held_in: float = 0.0
    candidate_held_in: float = 0.0
    baseline_held_out: float = 0.0
    candidate_held_out: float = 0.0
    samples: int = 0
    ledger_path: str = ""
    spend_dollars: float = 0.0


def _load_libs():
    """Import the sibling benchmark libraries (kept lazy to avoid E402)."""
    import dgm_uplift
    import swebench_governed
    return dgm_uplift, swebench_governed


def _filter_gradable(swg, instances: list, workroot: Path, *, timeout: float) -> tuple[list, int]:
    """Keep only instances this host can grade for free (``_env_pregate``).

    Returns (kept, dropped). Fail-closed: an instance that errors in the pregate
    is dropped, not run.

    The pregate runs under the instance's era-correct venv interpreter
    (``MAVERICK_TEST_PYTHON``), exactly like ``swebench_governed.main`` and
    ``dgm_uplift.score_instance`` do. Without this the filter grades every
    instance with HOST python -- on a venv'd pod (all instances on their own
    3.8/3.9 era) that drops the ENTIRE corpus and the run dies NO-RUN, or worse,
    keeps a wrong-era subset the later venv-aware scoring disagrees with."""
    import os

    kept: list = []
    dropped = 0
    for i, inst in enumerate(instances):
        vpy = swg._venv_python(inst)
        prev_tp = os.environ.get("MAVERICK_TEST_PYTHON")
        if vpy:
            os.environ["MAVERICK_TEST_PYTHON"] = vpy
        try:
            why = swg._env_pregate(inst, workroot / f"pre{i}", timeout=timeout)
        except Exception as e:  # noqa: BLE001 -- pregate must never crash the run
            why = f"pregate error: {e}"
        finally:
            if vpy:
                if prev_tp is None:
                    os.environ.pop("MAVERICK_TEST_PYTHON", None)
                else:
                    os.environ["MAVERICK_TEST_PYTHON"] = prev_tp
        if why:
            dropped += 1
            print(f"  [DROP ]  {inst.instance_id:40}  {why}")
        else:
            kept.append(inst)
    return kept, dropped


def _baseline_feedback(dgm, solver_dir: Path, held_in: list, workroot: Path,
                       *, timeout: float) -> str:
    """Run the baseline solver on the HELD-IN slice and describe what it failed.

    Only held-in instances are ever shown to the proposer, so the held-out slice
    the gate promotes on stays genuinely unseen."""
    resolved = dgm.run_solver(solver_dir, held_in, workroot, timeout=timeout)
    failed = [i.instance_id for i in held_in if not resolved.get(i.instance_id)]
    if not failed:
        return "The baseline solver already resolves every seen instance."
    lines = "\n".join(f"- {iid}" for iid in failed)
    return ("Seen (held-in) instances the current solver FAILED to resolve "
            "(its FAIL_TO_PASS tests still fail):\n" + lines +
            "\n\nGeneralise the solver so it resolves more of these -- never "
            "special-case an instance id.")


def _stub_patch(solver_dir: Path, stub_patch: str | None) -> str:
    """The known-good improvement patch for --stub mode (no LLM).

    Resolution: ``--stub-patch PATH`` if given, else ``<solver>/stub_improve.patch``.
    Lets a smoke run drive the ENTIRE chain for $0 with a hand-written diff a real
    proposer would have emitted (exactly what the proof does)."""
    candidates: list[Path] = []
    if stub_patch:
        candidates.append(Path(stub_patch))
    candidates.append(Path(solver_dir) / "stub_improve.patch")
    for c in candidates:
        if c.is_file():
            return c.read_text(encoding="utf-8")
    raise FileNotFoundError(
        "--stub needs a known-good patch: pass --stub-patch PATH or place "
        f"stub_improve.patch in {solver_dir}")


def _spend_from_log(path: Path) -> float:
    """Sum the solver's per-call actual $ costs (best-effort; 0.0 if absent)."""
    total = 0.0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                total += float(line)
    except (OSError, ValueError):
        pass
    return total


def _author_patch(args, dgm, solver_dir: Path, held_in: list, workroot: Path) -> str:
    """Produce the candidate solver patch (stub file, or the live LLM proposer)."""
    if args.stub:
        return _stub_patch(solver_dir, args.stub_patch)
    feedback = _baseline_feedback(dgm, solver_dir, held_in, workroot / "feedback",
                                  timeout=args.timeout)
    return dgm.llm_solver_proposer(solver_dir, feedback)


def _abort_ceiling(args, n_instances: int, n_held_in: int) -> float:
    """Worst-case $ ceiling for the run (every solver call hits its cap)."""
    n_calls = n_instances * 2 + (0 if args.stub else n_held_in)
    return n_calls * _SOLVER_CALL_CAP + (0.0 if args.stub else _PROPOSER_EST)


def _run_governed(args, dgm, solver_dir: Path, patch: str, instances: list,
                  work: Path):
    """Build a min_samples-tuned controller and drive the governed chain."""
    from maverick.self_improvement import (
        _RUNG_POLICY,
        PromotionLedger,
        SelfImprovementController,
    )
    policy = {k: dict(v) for k, v in _RUNG_POLICY.items()}
    policy["code"]["min_samples"] = args.min_samples  # --min-samples -> the gate
    ledger = PromotionLedger(path=args.ledger)
    ctrl = SelfImprovementController(ledger=ledger, rung_policy=policy)
    return dgm.govern_solver_change(
        solver_dir, patch, instances, keys_dir=args.keys, ledger=ledger,
        workroot=work / "gov", held_out_frac=args.held_out_frac,
        timeout=args.timeout, controller=ctrl)


def _verdict(res) -> tuple[str, str]:
    if res.promoted:
        return "PROMOTED", res.reason
    if res.overfit:
        return "OVERFIT", res.reason
    if not res.boundary_ok:
        return "REFUSED (boundary)", res.reason
    return "REFUSED", res.reason


def run(args, *, workdir: Path | None = None) -> DriverOutcome:
    """Execute the whole driver flow. Returns a DriverOutcome (never raises for a
    per-instance problem; a hard config error like a bad manifest still raises)."""
    import os

    dgm, swg = _load_libs()
    solver_dir = Path(args.solver)
    out = DriverOutcome(ledger_path=str(args.ledger))

    with _tmp(workdir) as td:
        work = Path(td)
        instances = swg._load_manifest(args.manifest)
        if args.gradable_only:
            instances, out.n_dropped = _filter_gradable(
                swg, instances, work / "pregate", timeout=args.timeout)
        if args.limit:
            instances = instances[: args.limit]
        out.n_instances = len(instances)
        if len(instances) < 2:
            out.verdict, out.reason = "NO-RUN", (
                f"need >= 2 gradable instances, have {len(instances)}")
            return out

        held_in, held_out = dgm.split_instances(instances, held_out_frac=args.held_out_frac)
        out.n_held_in, out.n_held_out = len(held_in), len(held_out)

        # The gate refuses on min_samples at the END -- after the proposer and
        # both solver arms have already spent. If the held-out slice can't meet
        # the evidence floor, that refusal is knowable NOW for $0: don't start.
        if len(held_out) < args.min_samples:
            out.verdict, out.reason = "NO-RUN", (
                f"held-out slice ({len(held_out)}) < --min-samples "
                f"({args.min_samples}); the gate would refuse AFTER spending -- "
                "grow the gradable corpus or lower --min-samples")
            return out

        if args.abort_at_dollars:
            ceiling = _abort_ceiling(args, len(instances), len(held_in))
            if ceiling > args.abort_at_dollars:
                out.verdict, out.reason = "ABORTED", (
                    f"worst-case spend ceiling ${ceiling:.2f} exceeds "
                    f"--abort-at-dollars ${args.abort_at_dollars:.2f}; not started")
                return out

        spend_log = work / "spend.log"
        prev = os.environ.get(_SPEND_LOG_ENV)
        os.environ[_SPEND_LOG_ENV] = str(spend_log)
        try:
            patch = _author_patch(args, dgm, solver_dir, held_in, work)
            res = _run_governed(args, dgm, solver_dir, patch, instances, work)
        finally:
            if prev is None:
                os.environ.pop(_SPEND_LOG_ENV, None)
            else:
                os.environ[_SPEND_LOG_ENV] = prev
        out.spend_dollars = _spend_from_log(spend_log)

    out.boundary_ok = res.boundary_ok
    out.overfit = res.overfit
    out.promoted = res.promoted
    out.approver_id = res.approver_id
    out.baseline_held_in, out.candidate_held_in = res.baseline_held_in, res.candidate_held_in
    out.baseline_held_out, out.candidate_held_out = res.baseline_held_out, res.candidate_held_out
    out.samples = res.samples
    out.verdict, out.reason = _verdict(res)
    return out


def _scoreboard(out: DriverOutcome) -> None:
    print("=" * 78)
    print("  MAVERICK -- GOVERNED DGM LIVE   (solver improves itself, under the gate)")
    print("=" * 78)
    if out.n_dropped:
        print(f"  gradable-only pre-filter: dropped {out.n_dropped} ungradable instance(s)")
    print(f"  corpus: {out.n_instances} instances  "
          f"(held-in {out.n_held_in} / held-out {out.n_held_out})")
    print(f"  held-in  resolved-rate:  baseline {out.baseline_held_in:.3f}"
          f"  ->  candidate {out.candidate_held_in:.3f}")
    print(f"  held-out resolved-rate:  baseline {out.baseline_held_out:.3f}"
          f"  ->  candidate {out.candidate_held_out:.3f}"
          f"   (the gate promotes on THIS, over {out.samples} unseen instances)")
    print("-" * 78)
    print(f"  VERDICT: {out.verdict}")
    print(f"  {out.reason}")
    if out.approver_id:
        print(f"  signed by: {out.approver_id}")
    print(f"  ledger: {out.ledger_path}")
    print(f"  solver LLM spend (actual, best-effort): ~${out.spend_dollars:.4f}")
    print("=" * 78)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, type=Path,
                    help="JSONL manifest (fetch_swe_bench_verified format)")
    ap.add_argument("--keys", required=True, type=Path,
                    help="operator key dir (operator.priv.hex + operator.pub)")
    ap.add_argument("--ledger", type=Path, default=Path("dgm_uplift_ledger.json"))
    ap.add_argument("--solver", type=Path,
                    default=_ROOT / "benchmarks" / "solvers" / "baseline_v0",
                    help="solver dir under improvement (contains solver.py)")
    ap.add_argument("--held-out-frac", type=float, default=0.5,
                    help="fraction of instances held out (the uplift is judged here)")
    ap.add_argument("--min-samples", type=int, default=10,
                    help="held-out evidence floor passed to the code rung "
                         "(gate default is 10)")
    ap.add_argument("--gradable-only", action="store_true",
                    help="pre-filter to instances this host can grade for free "
                         "(_env_pregate); prints how many were dropped")
    ap.add_argument("--limit", type=int, default=0, help="cap the corpus size (0 = all)")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="per-arm test-run timeout in seconds")
    ap.add_argument("--abort-at-dollars", type=float, default=0.0,
                    help="refuse to start if the worst-case spend ceiling exceeds "
                         "this (best-effort; 0 = no cap)")
    ap.add_argument("--stub", action="store_true",
                    help="NO-LLM mode: author the candidate from a known-good patch "
                         "file instead of the paid proposer (free end-to-end run)")
    ap.add_argument("--stub-patch", type=Path, default=None,
                    help="explicit patch file for --stub (else <solver>/stub_improve.patch)")
    return ap.parse_args(argv)


def _tmp(workdir: Path | None):
    """A tempdir context: caller-supplied (kept) or auto (cleaned up)."""
    import contextlib
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
        return contextlib.nullcontext(str(workdir))
    return tempfile.TemporaryDirectory(prefix="dgm-live-")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    args = _parse_args(argv)
    out = run(args)
    _scoreboard(out)
    return 0 if out.verdict in ("PROMOTED", "REFUSED", "OVERFIT", "REFUSED (boundary)") else 1


if __name__ == "__main__":
    sys.exit(main())
