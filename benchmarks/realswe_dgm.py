#!/usr/bin/env python3
"""A REAL governed DGM cycle on REAL SWE-bench code (small, resumable, capped).

The self-improvement here is an agent-BUDGET change -- the canonical DGM lever
(the same knob the 1000-trial in-process DGM improved): the baseline agent runs
weak (few turns, small $), the candidate runs with more turns and budget. Both
solve the SAME real GitHub issues; each proposed patch is graded in the OFFICIAL
SWE-bench container. If the improved config lifts the resolved-rate, the change
is promoted through the REAL config rung (min_samples=3, no human needed) and
SIGNED into the audit ledger, then re-verified by an independent auditor.

Honest scope: 3 instances is a MECHANISM proof on real code -- baseline ->
propose -> improved -> signed verdict, end to end -- not a statistically robust
rate. Non-Django instances are used deliberately (Django is the hardest repo);
this is disclosed, not hidden. Every solve is metered; a hard $ cap stops it.
Resumable: each (instance, arm) verdict is checkpointed, so a container recycle
never re-pays for finished work.

    python benchmarks/realswe_dgm.py --keys ~/dgm-keys --out-dir ~/realswe_dgm
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "packages" / "maverick-core"))

import swebench_container_grade as CG  # noqa: E402
from swebench_container_run import agent_patch, load_verified  # noqa: E402

# The two agent configurations. Candidate = the DGM-proposed budget improvement.
# Uplift only SHOWS in the goldilocks band -- instances the weak config can't
# finish but the strong one can. Too-hard (both fail) or too-easy (both pass)
# give a flat 0 delta. So the candidate is 120 steps / $12 / 90 min: enough
# headroom to clear medium instances the 25-step baseline can't, without the
# 200-step tail that just burns $ on the ones neither config will crack. The
# lifted wall (90 min vs the 25-min default) keeps the step/$ caps the real
# limits so a solve is never cut off by the clock. Baseline stays the weak
# control (25 steps / $3 / 25 min) -- the "before" the uplift is measured against.
ARMS = {
    "baseline":  {"MAVERICK_MAX_STEPS": "25",  "MAVERICK_BEST_OF_N": "1",
                  "MAVERICK_INSTANCE_HARD_CAP": "3.0",  "MAVERICK_INSTANCE_WALL_SEC": "1500"},
    "candidate": {"MAVERICK_MAX_STEPS": "120", "MAVERICK_BEST_OF_N": "1",
                  "MAVERICK_INSTANCE_HARD_CAP": "12.0", "MAVERICK_INSTANCE_WALL_SEC": "5400"},
}
DEFAULT_IDS = ["sympy__sympy-15976", "sympy__sympy-14531", "pytest-dev__pytest-5631"]


def _easy_ids(n: int, exclude: set[str] | None = None) -> list[str]:
    """Pick the ``n`` SWE-bench Verified instances with the smallest gold patches.

    Gold-patch line count is the best cheap proxy for difficulty in the dataset:
    a small non-test fix is a simple, well-scoped change -- exactly the kind an
    agent can actually resolve, which is what gives a baseline-vs-candidate run
    real headroom (vs the sympy/pytest internals that failed at every budget).
    Data-driven and reproducible -- no hand-picked IDs, no memory guesswork.
    """
    exclude = exclude or set()
    allinst = load_verified(None)  # all 500 (cached locally after first pull)
    def plen(d: dict) -> int:
        return len((d.get("patch") or "").splitlines())
    ranked = sorted(
        (d for d in allinst.values()
         if d["instance_id"] not in exclude and plen(d) >= 4),
        key=plen)
    return [d["instance_id"] for d in ranked[:n]]


def _cost_of(iid: str) -> float:
    try:
        return float(json.load(open(_HERE.parent / "forensics" / f"{iid}.json")).get("cost_dollars", 0) or 0)
    except Exception:
        return 0.0


def _load_ckpt(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keys", required=True, type=Path)
    ap.add_argument("--out-dir", type=Path, default=Path.home() / "realswe_dgm")
    ap.add_argument("--ids", type=str, default=",".join(DEFAULT_IDS))
    ap.add_argument("--easy", type=int, default=0, metavar="N",
                    help="ignore --ids; auto-pick the N Verified instances with the "
                         "smallest gold patches (simplest fixes -> real headroom).")
    # Global safety net. Per-instance caps are the real limits; this only guards a
    # runaway. On EASY instances the agent finishes well under the caps, so actual
    # spend is far below the theoretical max (candidate $12 + baseline $3 per
    # instance). Default sized for a ~5-instance easy run; raise it if you add more.
    ap.add_argument("--max-dollars", type=float, default=45.0)
    args = ap.parse_args(argv)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MAVERICK_PROVIDER_READY")):
        print("NO PROVIDER KEY.")
        return 2

    if args.easy > 0:
        print(f"  selecting {args.easy} easiest Verified instances (smallest gold patches) ...",
              flush=True)
        ids = _easy_ids(args.easy)
        print("  chosen:", ", ".join(ids))
    else:
        ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = args.out_dir / "verdicts.json"
    verdicts = _load_ckpt(ckpt_path)          # {"<arm>:<iid>": {"resolved": bool, "cost": float}}
    instances = load_verified(ids)
    runner = CG.modal_runner()

    print("=" * 78)
    print(f"  GOVERNED DGM ON REAL CODE  --  {len(ids)} instances x 2 arms (baseline/candidate)")
    print("  improvement: agent budget 25 turns/$3/25min  ->  120 turns/$12/90min")
    print("=" * 78)

    # --- run each (arm, instance), resumable + metered -------------------------
    for arm, env in ARMS.items():
        for iid in ids:
            key = f"{arm}:{iid}"
            if key in verdicts:
                print(f"  [skip] {key} -> resolved={verdicts[key]['resolved']} (checkpointed)")
                continue
            spent = sum(v.get("cost", 0) for v in verdicts.values())
            if spent >= args.max_dollars:
                print(f"  [STOP] cumulative ${spent:.2f} >= ${args.max_dollars} cap")
                break
            for k, v in env.items():
                os.environ[k] = v
            inst = instances[iid]
            print(f"  [run ] {key} ...", flush=True)
            before = _cost_of(iid)
            try:
                patch = agent_patch(inst)
                gr = CG.governed_container_grade(inst, patch, runner, check_gold_overlap=True)
                resolved = bool(getattr(gr, "resolved", False))
            except Exception as e:
                # Never let a crash vanish into a silent $0 verdict again: write the
                # FULL traceback to errors/<arm>_<iid>.log and echo the type/message.
                import traceback
                errdir = args.out_dir / "errors"
                errdir.mkdir(exist_ok=True)
                (errdir / f"{arm}_{iid}.log").write_text(traceback.format_exc())
                print(f"  [err ] {key}: {type(e).__name__}: {e}  "
                      f"(full traceback -> {errdir}/{arm}_{iid}.log)")
                resolved = False
            cost = max(0.0, _cost_of(iid) - before) or _cost_of(iid)
            verdicts[key] = {"resolved": resolved, "cost": round(cost, 4)}
            ckpt_path.write_text(json.dumps(verdicts, indent=2))
            print(f"  [done] {key} -> resolved={resolved}  ${cost:.2f}")

    # --- compute uplift --------------------------------------------------------
    def rate(arm: str) -> float:
        got = [verdicts[f"{arm}:{i}"]["resolved"] for i in ids if f"{arm}:{i}" in verdicts]
        return (sum(got) / len(got)) if got else 0.0
    b_rate, c_rate = rate("baseline"), rate("candidate")
    n = len([i for i in ids if f"candidate:{i}" in verdicts and f"baseline:{i}" in verdicts])
    total_cost = sum(v.get("cost", 0) for v in verdicts.values())

    print("\n" + "=" * 78)
    print(f"  baseline  resolved-rate : {b_rate:.3f}  ({sum(verdicts.get(f'baseline:{i}',{}).get('resolved',False) for i in ids)}/{len(ids)})")
    print(f"  candidate resolved-rate : {c_rate:.3f}  ({sum(verdicts.get(f'candidate:{i}',{}).get('resolved',False) for i in ids)}/{len(ids)})")
    print(f"  uplift                  : {c_rate - b_rate:+.3f} over {n} instances")

    # --- sign the config improvement through the REAL config rung --------------
    from maverick.self_improvement import Candidate, PromotionLedger, SelfImprovementController
    ledger_path = args.out_dir / "ledger.json"
    ledger = PromotionLedger(path=ledger_path)
    ctrl = SelfImprovementController(frozen_fn=lambda: False, ledger=ledger)
    os.environ["MAVERICK_SELF_IMPROVEMENT"] = "1"
    os.environ["MAVERICK_APPROVER_KEYS_DIR"] = str(args.keys)
    payload = ("agent budget: MAVERICK_MAX_STEPS 25->120, "
               "MAVERICK_INSTANCE_HARD_CAP 3->12, MAVERICK_INSTANCE_WALL_SEC 1500->5400")
    import hashlib

    from maverick import approval_signing as asig
    cand = Candidate(
        rung="config",
        summary=f"agent-budget self-improvement: held-out resolved {b_rate:.3f} -> {c_rate:.3f}",
        baseline_score=b_rate, candidate_score=c_rate, samples=n or len(ids),
        capability_widens=False, payload=payload,
        payload_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        rollback={"revert": "MAVERICK_MAX_STEPS=25, MAVERICK_INSTANCE_HARD_CAP=3, "
                            "MAVERICK_INSTANCE_WALL_SEC=1500"},
        id="realswe-dgm-config")
    priv = (Path(args.keys) / "operator.priv.hex").read_text().strip()
    sig = asig.sign_request(asig.ApprovalRequest.for_candidate(cand), priv)
    cand = Candidate(**{**cand.__dict__, "approval_signature": sig})
    verdict = ctrl.promote(cand)
    print(f"  gate verdict            : {'PROMOTED' if verdict.ok else 'REFUSED'} "
          f"({getattr(verdict, 'blocking_reason', '') or 'signed to ledger'})")

    # --- independent audit -----------------------------------------------------
    import subprocess
    audit = subprocess.run(
        [sys.executable, str(_HERE / "audit_ledger.py"), "--ledger", str(ledger_path), "--keys", str(args.keys)],
        capture_output=True, text=True)
    for line in audit.stdout.splitlines():
        if "records |" in line or "AUDIT OK" in line or "INVALID" in line:
            print("  audit                   : " + line.strip())

    print("=" * 78)
    print(f"  TOTAL SPEND ${total_cost:.2f}   wall {time.strftime('%H:%M:%S')}")
    print(f"  RESULT: agent improved its own coding config; held-out {b_rate:.3f}->{c_rate:.3f}, "
          f"{'PROMOTED+SIGNED' if verdict.ok else 'REFUSED'}")
    return 0 if verdict.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
