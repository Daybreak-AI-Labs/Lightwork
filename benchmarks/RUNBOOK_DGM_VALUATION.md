# Runbook: governed SWE-bench and governed DGM runs

Operator's guide to the two paid runs of the governed Darwin-Gödel machinery,
in order. The machinery itself is exercised offline by the proofs in
[`proof/RESULTS.md`](../proof/RESULTS.md) (sections 5-7); these runs produce
externally comparable numbers.

| Run | Needs | Estimated cost | Time |
|-----|-------|----------------|------|
| 1. SWE-bench Verified under governance | API key (+ Docker only for full-500 grading) | ~$65–120 (50-slice) / ~$650–1,210 (full 500) | hours / a day |
| 2. Governed DGM uplift | API key + run-1 harness | ~$2–5k across cycles | days |

Publish whatever number comes out, together with the signed ledger.

---

## Run 1 — SWE-bench Verified under governance

### 0. One-time setup (any Linux/macOS machine, ~15 min)

```bash
git clone https://github.com/Daybreak-AI-Labs/Lightwork && cd Lightwork
bash .devcontainer/post-create.sh          # editable-installs all packages
pip install datasets                        # HF fetcher dependency
export ANTHROPIC_API_KEY=sk-ant-...         # scoped key, workspace spend cap set

# Operator signing keypair (the human-approval key; keep .priv OFFLINE)
mkdir -p ~/dgm-keys && python3 - <<'PY'
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization
from pathlib import Path
k = ed25519.Ed25519PrivateKey.generate()
d = Path.home() / "dgm-keys"
(d / "operator.priv.hex").write_text(k.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()).hex())
(d / "operator.pub").write_bytes(k.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))
print("keys written to", d)
PY
```

### 1. Validate the pipeline without a key (no cost)

```bash
python proof/swebench_governed_proof.py     # every check must pass
python proof/dgm_uplift_proof.py            # every check must pass
```

### 2. Stage instances (manifest + per-instance repo checkouts)

```bash
# 50-instance smoke slice, repos cloned at base_commit
python benchmarks/fetch_swe_bench_verified.py --limit 50 --stage \
    --out-manifest ./swebench_verified/instances.jsonl \
    --repos-dir ./swebench_verified/repos
```

### 3. Dry-run the harness end to end (no cost)

```bash
MAVERICK_BENCH_DRY_RUN=1 python benchmarks/swebench_governed.py \
    --manifest ./swebench_verified/instances.jsonl \
    --proposer llm --keys ~/dgm-keys --limit 3
```

External instances run model-generated shell commands and their own tests, so
configure an isolated `[sandbox] backend` (for example `docker`) before running
them. `--allow-host-exec` is only for a trusted local smoke corpus.

### 4. The paid run

```bash
# 50-instance slice first (~$65–120): sanity-check the resolved rate + ledger
MAVERICK_INSTANCE_HARD_CAP=3.0 python benchmarks/swebench_governed.py \
    --manifest ./swebench_verified/instances.jsonl \
    --proposer llm --keys ~/dgm-keys \
    --ledger ./swebench_ledger.json --limit 50

# then the full 500 (~$650–1,210) with an isolated sandbox backend; do not
# pass --allow-host-exec for untrusted external instances. Docker + ~80 GB
# disk recommended so grading runs in the reproducible per-instance
# environments (see RUNBOOK_SWE_BENCH_VERIFIED.md and RUNBOOK_MODAL_GRADING.md).
```

**Deliverables:** the scoreboard line
(`resolved under governance: N/500 (X%)`), `swebench_ledger.json` (the signed
promotion ledger — one Ed25519-signed entry per resolved instance), and the
per-instance JSONL. Verify any ledger entry offline against
`~/dgm-keys/operator.pub`.

### Grading without Docker

Patch *generation* never needs Docker. *Grading* runs each instance's tests via
`sandbox.exec` in an isolated copy, which gives a valid result only if the
instance's dependencies are importable there. For a first number with just a
key, grade a curated pure-Python slice (install each instance repo's
dependencies into a venv first); move to Docker for the full 500 so the number
is comparable with published SWE-bench results.

Three grading behaviours the harness handles:

- **`test_patch` is applied to BOTH arms** before scoring (SWE-bench semantics:
  the graded tests usually don't exist at `base_commit`). The manifest carries
  it; the anti-cheat still refuses any *candidate* patch touching tests.
- **Malformed upstream test ids** (the dataset comma-split parametrized ids like
  `test_x[foo,`) are dropped LOUDLY at load — one bad id otherwise aborts the
  whole pytest chunk and zeroes the instance.
- **`ungradable-here` (NOENV) is reported separately from unresolved**: if the
  baseline can't run the instance's own passing tests (deps/pytest-version
  mismatch — e.g. flask 2.3's conftest needs an older pytest), the environment
  can't grade it. Never counted as resolved; never blamed on the agent.

---

## Run 2 — governed uplift (the agent improves its own solver)

The harness is `benchmarks/dgm_uplift.py`; the offline proof
(`proof/dgm_uplift_proof.py`) exercises every gate on fixture instances. The
real run swaps in Verified instances and the LLM patch-author.

One cycle:

```python
# sketch of the loop a cycle-runner script drives (see dgm_uplift.py API):
from dgm_uplift import (govern_solver_change, apply_and_archive,
                        llm_solver_proposer, run_solver, split_instances)

# 1. evaluate current solver -> feedback (which instances fail)
# 2. patch = llm_solver_proposer(solver_dir, feedback)     # needs API key
# 3. r = govern_solver_change(solver_dir, patch, instances,
#                             keys_dir=..., ledger=ledger, workroot=...)
# 4. if r.promoted: apply_and_archive(solver_dir, patch, archive, version=...)
#    else: record the refusal (boundary / OVERFIT / no-uplift) and iterate
```

Practical notes for the real run:

- **Corpus:** use a 60–100 instance slice; the deterministic split holds ~1/3
  out. NEVER show held-out failures to the proposer — feed it held-in feedback
  only, or the held-out split stops being unseen.
- **Solver surface:** start the solver as a thin wrapper around the coding-mode
  agent (its strategy prompt/heuristics in `solver.py`), so patches evolve the
  *strategy*, not the platform. The boundary globs pin exactly which files are
  editable; the control plane is refused regardless.
- **Expect refusals.** Most proposed patches will be refused (no uplift /
  overfit). That is the system working; the refusal log is evidence too.
- **Budget:** each cycle ≈ one slice evaluation × 2 arms × instance cost.
  Batch proposals: evaluate the baseline once per generation, not per patch.

**Deliverable:** the uplift curve (`v0: X% → v1: X+Δ% → ...` held-out), the
signed ledger with one entry per promoted solver version, the archive of every
prior version (rollback demonstrated), and the refusal log.
