# SWE-bench Verified under governance — evidence record

**Date:** 2026-07-10 · **Operator:** Day AI Labs · **Agent:** Lightwork coding agent (llm proposer) · **Harness:** `benchmarks/swebench_governed.py`

## The claim this evidence supports — and its limits

**Supported:** Lightwork's coding agent resolves real SWE-bench Verified issues
end-to-end **under a governed self-modification chain** — anti-cheat boundary,
isolated held-out test grading, capability non-escalation proof, and an
Ed25519-signed, reversible promotion ledger — on autonomous cloud
infrastructure. Reproduced across **two independent runs** and **two codebase
families** (pylint, flask).

**Not claimed:** a SWE-bench leaderboard percentage. Only 3 of 19 staged
instances were gradable in this single-host environment (the rest need
per-instance Docker environments — see "Path to a headline number"). Every
ungradable instance is reported as such, never counted as resolved, and — via
the free environment pre-gate — cost $0 of agent spend.

## Results

| Run | Resolved (all) | Resolved of gradable | Agent spend | Ledger |
|-----|----------------|----------------------|-------------|--------|
| 1 (2026-07-09) | 1/19 (5.3%) | 1/4 (25.0%) | ~$7.45 | branch `evidence-run1` |
| 2 (2026-07-10, env fixes) | 2/19 (10.5%) | **2/3 (66.7%)** | ~$3.34 | branch `evidence-run2` |

Run 2 final scoreboard (verbatim, full log committed as
`evidence/swebench-run2/slice2_log.txt`):

```
resolved under governance: 2/19 (10.5% of all)   |  boundary-refused: 0  |  unresolved: 1   |  ungradable-here: 16
resolved of GRADABLE (excl. ungradable): 2/3 (66.7%)
agent spend (attributed): ~$3.34   |   signed ledger: slice2_ledger.json
```

## The two resolved instances

**`pylint-dev__pylint-8898`** — *bad-names-rgxs mangles regular expressions
with commas.* The agent added a brace-depth-aware CSV splitter so commas inside
regex quantifiers (`{1,3}`) are not treated as list separators. Baseline
0.944 → candidate 1.000 on the instance's own FAIL_TO_PASS + PASS_TO_PASS
tests. **Resolved in both runs independently** (repeatability).

**`pallets__flask-5014`** — *require a non-empty name for Blueprints.* Patch to
`src/flask/blueprints.py`. Baseline 0.983 → candidate 1.000. Agent cost for
this resolve: **$0.54**.

## The governance chain every pass traversed

1. **Anti-cheat boundary** — `defensive_validate` refuses patches touching
   tests or grading-sensitive config (conftest/pytest.ini/setup*/pyproject/…),
   plus gold-patch overlap detection (armed for the llm proposer).
2. **Held-out fitness on isolated copies** — candidate applied to a throwaway
   copy; the grader's `test_patch` applied to BOTH arms (upstream SWE-bench
   semantics); the baseline MUST fail the graded tests or the instance is
   marked ungradable, never resolved.
3. **Capability non-escalation proof** — the patch must not widen the agent's
   tool capability envelope.
4. **Ed25519 operator-signed promotion** recorded to an append-only ledger with
   one-step rollback.

## Why this number cannot be quietly inflated

- Ungradable-here is a separate scoreboard bucket, never counted as resolved;
  the free pre-gate skips such instances **before** any agent spend.
- A proposer that produces no patch is `EMPTY` (unresolved), distinct from
  boundary refusals (`CHEAT`).
- Malformed upstream dataset test ids are dropped loudly at load; an instance
  left with no gradable failing test is refused a resolve verdict.
- Both figures (of-all and of-gradable) print on every run, with the note to
  report both.
- Offline proof suites pin the gates: `proof/swebench_governed_proof.py`
  (4/4: genuine fix promotes, test-edit cheat refused, regression caught,
  ledger integrity) and `proof/dgm_uplift_proof.py` (6/6: uplift promotes,
  memoriser refused, tamperer zeroed, rollback byte-identical).

## Artifacts

- Branch `evidence-run1`: run-1 signed ledger + per-instance forensics.
- Branch `evidence-run2`: run-2 signed ledger + forensics + full run log.
- Forensics per instance: agent cost, tokens, outcome, and the exact patch.
- Operator keypair generated on the run host; promotions are refused without a
  valid signature (pinned by the proof suite).

## Cost of the entire proof

≈ **$10.79** total agent spend across both runs, plus ~$4 of cloud CPU time
(32 vCPU pod, $0.96/hr).

## Path to a headline percentage (roadmap, not claimed)

The gradable pool is bounded by the single-host environment, not the agent:
per-instance Docker environments (upstream SWE-bench's own approach) make all
500 Verified instances gradable. Next milestones: (1) per-instance Docker
grading, (2) modern families with large Verified counts (sphinx, django),
(3) n ≥ 100 gradable for a quotable percentage under identical governance.

## One-line deck framing (honest)

> Lightwork's agent resolves real SWE-bench Verified issues across multiple
> codebases end-to-end under governance — every fix graded on held-out tests in
> isolation, checked for capability escalation, and promoted only with a
> cryptographically signed, reversible ledger record. Reproduced across two
> independent runs, with ungradable instances honestly excluded and both
> figures reported.
