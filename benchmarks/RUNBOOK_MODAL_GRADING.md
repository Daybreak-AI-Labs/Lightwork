# Runbook — governed SWE-bench grading in official per-instance containers

## Why per-instance containers

The limiting factor for host-based grading is the **environment**: one host
cannot reproduce 500 different Python eras and pinned dependencies, so instances
grade `NOENV`/`EMPTY` and agent spend goes to instances that can never grade.

The SWE-bench project publishes a **pre-built image per instance**
(`swebench/sweb.eval.x86_64.<id>`) that already contains the repo at
`base_commit` with the correct environment. `benchmarks/swebench_container_grade.py`
grades **inside that image**, reusing the official spec, eval script, and grader,
so the era-mismatch bug class **cannot recur** — the environment travels with the
instance. Lightwork's governance (anti-cheat boundary, mis-seed guard, signed promotion)
runs around it, unchanged.

Backends: **Docker** (a VM with a Docker daemon) for untrusted patches, or
**Modal** only when you explicitly acknowledge provider-default networking in an
isolated trusted environment. Same grading code path.

## Step 0 — one-time setup (Modal)

```bash
pip install modal
modal token new          # opens a browser once; links your Modal account
```

(A single-instance validation uses a few cents of Modal compute. No Anthropic
key is used — this is grading only, no agent.)

## Step 1 — validate the environment on ONE instance ($0 Anthropic, ~cents Modal)

Prove that the official image and grading work end to end **before** spending
on an agent. Grade the instance's **gold patch** — it must
resolve. Then grade the **baseline** (no patch) — it must NOT resolve.

```bash
cd ~/Lightwork
# gold patch must RESOLVE (proves image + grading are correct):
python benchmarks/swebench_container_grade.py --instance astropy__astropy-12907 --backend docker --gold
# baseline must NOT resolve (proves the tests really fail without a fix):
python benchmarks/swebench_container_grade.py --instance astropy__astropy-12907 --backend docker
```

Expected (gold run):

```
instance: astropy__astropy-12907
image:    swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
grading:  GOLD patch (must resolve) via docker
====================================================================
  candidate applied: True
  FAIL_TO_PASS: 2 pass / 0 fail
  PASS_TO_PASS: 13 pass / 0 fail
  RESOLVED (official): True
====================================================================
VALIDATION: PASS -- official image + grading work end to end
```

If the gold run prints `VALIDATION: PASS`, the official image and grading work
and each instance grades in its own container. If it fails, **stop and
investigate the output** before spending on any run.

### Modal for trusted isolated validation only

The Docker backend runs untrusted candidate code with `--network none` and
additional hardening. The Modal API used here does not expose an equivalent
per-sandbox no-egress switch, so Modal fails closed by default. If you are
grading only trusted patches in an isolated Modal environment with no reachable
secrets or internal services, opt in explicitly:

```bash
python benchmarks/swebench_container_grade.py --instance astropy__astropy-12907 --backend modal --gold --allow-network
```

The official images are large (~1–2 GB each); Docker pulls on first use, and
Modal caches them remotely.

## Step 2 — plug into the governed run (the DGM / SWE-bench score)

`swebench_container_grade.governed_container_grade(instance, patch, run_in_image)`
returns the official `resolved` verdict after the host-side anti-cheat boundary
and a baseline mis-seed guard. Layer promotion on top by reusing
`swebench_governed._promote(...)` (same signed, reversible ledger) — the module
holds no keys and writes no ledger itself, so the governance stays in one place.

This replaces the era-venv host grading in the governed scorer with
container grading; the governance chain (boundary → baseline-fails → resolved →
signed promotion) is identical. The corpus no longer needs a hand-built winnable
set — **every** Verified instance is gradable because every instance carries its
own environment.

## Cost shape (estimates)

- **Per-instance grade:** seconds-to-minutes of container CPU. Modal bills per
  second; a full-500 grade sweep is compute-cheap (no GPUs — this is not a
  GPU workload).
- **The expensive part remains the agent** (Anthropic), and it is spent only on
  instances that are known to grade, which avoids `NOENV`/`EMPTY` results.
- Validate on one instance first; only then scale.

## Why per-instance environments matter

With correct per-instance environments you can grade a **random** sample of the
500 (or all of them) instead of a hand-picked winnable subset. Correct
environments plus the existing governance chain give a signed governed score
over an unselected sample.
