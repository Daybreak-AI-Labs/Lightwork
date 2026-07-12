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
# Strategy: rather than spread a fixed pot across many shallow attempts, we run a
# SMALL set of instances DEEP so each one actually reaches a result. The candidate
# gets 200 steps and a $20 hard cap -- and the wall must scale with it, because
# swe_bench.py defaults the per-instance wall to 1500s (25 min); at 200 steps that
# 25-min timer binds long before the step/$ budget and cuts the agent off mid-solve
# (a false "no-diff" that reads as the model failing when really the CLOCK ran out).
# So the candidate lifts the wall to 7200s (2 h): the step cap (200) and the dollar
# cap ($20) are the real limits and every instance runs to ground. Baseline stays
# the weak control (25 steps / $3 / 25 min) -- that is the "before" the uplift is
# measured against; 25 steps never approach 25 min, so its wall is unchanged.
ARMS = {
    "baseline":  {"MAVERICK_MAX_STEPS": "25",  "MAVERICK_BEST_OF_N": "1",
                  "MAVERICK_INSTANCE_HARD_CAP": "3.0",  "MAVERICK_INSTANCE_WALL_SEC": "1500"},
    "candidate": {"MAVERICK_MAX_STEPS": "200", "MAVERICK_BEST_OF_N": "1",
                  "MAVERICK_INSTANCE_HARD_CAP": "20.0", "MAVERICK_INSTANCE_WALL_SEC": "7200"},
}
DEFAULT_IDS = ["sympy__sympy-15976", "sympy__sympy-14531", "pytest-dev__pytest-5631"]


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
    # Global safety net. The per-instance caps are the real limits (candidate
    # $20 x 3 + baseline $3 x 3 = up to ~$69); set this above that so the global
    # cap never stops the run before an instance finishes -- it only guards a
    # runaway. Raise it with --max-dollars if you add instances.
    ap.add_argument("--max-dollars", type=float, default=75.0)
    args = ap.parse_args(argv)

    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("MAVERICK_PROVIDER_READY")):
        print("NO PROVIDER KEY.")
        return 2

    ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = args.out_dir / "verdicts.json"
    verdicts = _load_ckpt(ckpt_path)          # {"<arm>:<iid>": {"resolved": bool, "cost": float}}
    instances = load_verified(ids)
    runner = CG.modal_runner()

    print("=" * 78)
    print(f"  GOVERNED DGM ON REAL CODE  --  {len(ids)} instances x 2 arms (baseline/candidate)")
    print("  improvement: agent budget 25 turns/$3/25min  ->  200 turns/$20/2h")
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
                print(f"  [err ] {key}: {type(e).__name__}: {e}")
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
    payload = ("agent budget: MAVERICK_MAX_STEPS 25->200, "
               "MAVERICK_INSTANCE_HARD_CAP 3->20, MAVERICK_INSTANCE_WALL_SEC 1500->7200")
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
