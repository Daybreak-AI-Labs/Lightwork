# Running SWE-bench Pro end-to-end

This is the operator's guide for `benchmarks/swe_bench.py`. The harness
is a **producer**: it generates patches. Scoring is delegated to the
official SWE-bench Pro evaluator, so the harness never grades its own output.

All costs below are estimates for planning a budget, not measurements.

## Prerequisites

```bash
# Disk: ~150 GB free for the SWE-bench Pro Docker images
df -h .

# Docker 24+
docker --version

# Python deps (in the same venv as Lightwork)
pip install datasets

# The official SWE-bench Pro evaluator (step 3), in its own virtual environment
git clone https://github.com/scaleapi/SWE-bench_Pro-os ~/SWE-bench_Pro-os
python3 -m venv ~/SWE-bench_Pro-os/.venv
~/SWE-bench_Pro-os/.venv/bin/pip install -r ~/SWE-bench_Pro-os/requirements.txt
```

API keys in `~/.maverick/.env` (loaded automatically at startup):

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...        # optional: cross-family verifier
```

## Step 1: download the dataset

SWE-bench Pro is published on Hugging Face as `ScaleAI/SWE-bench_Pro`, with
three configs: `default` (V2, 642 tasks), `hard` (51 tasks) and `v1` (the
original 731 tasks). This runbook uses `v1`, because the official evaluator
that scores a file of precomputed patches (`swe_bench_pro_eval.py`, step 3)
belongs to the v1 pipeline. V2 is scored with Harbor under its own locked
protocol, which runs the agent inside Harbor.

Export the split for the evaluator and convert it into Lightwork's manifest
format:

```bash
mkdir -p ~/.maverick/swebench-pro
python -c "
from datasets import load_dataset
import ast, json, pathlib
ds = load_dataset('ScaleAI/SWE-bench_Pro', 'v1', split='test')
home = pathlib.Path.home() / '.maverick' / 'swebench-pro'
ds.to_json(str(home / 'swe_bench_pro_v1.jsonl'))  # evaluator input, step 3
def tests(value):
    # fail_to_pass / pass_to_pass are list literals stored as strings.
    return list(ast.literal_eval(value)) if value else []
out = home / 'manifest.jsonl'
with open(out, 'w') as f:
    for ex in ds:
        f.write(json.dumps({
            'instance_id': ex['instance_id'],
            'brief': ex['problem_statement'],
            'gold_patch': ex['patch'],
            'fail_to_pass': tests(ex['fail_to_pass']),
            'pass_to_pass': tests(ex['pass_to_pass']),
            'language': ex['repo_language'],
            'base_commit': ex['base_commit'],
            'requirements': ex['requirements'],
            'interface': ex['interface'],
        }) + chr(10))
print(f'wrote {len(ds)} instances to {out}')
"
```

Sanity check: `wc -l ~/.maverick/swebench-pro/manifest.jsonl` should equal
the number of rows in the split (731 for `v1`).

`fail_to_pass` enables the test-driven verifier, `requirements` and
`interface` are added to the brief, `base_commit` pins the checkout, and
`language` is a runner hint for monorepos.

## Step 2: smoke 10 instances first

Do **not** run the full test split until the smoke run is clean; a full
run on a broken pipeline can cost thousands of dollars.

```bash
# Slice the manifest to the first 10 lines. ~/.maverick/ works on
# Linux, macOS and Windows alike.
head -10 ~/.maverick/swebench-pro/manifest.jsonl > ~/.maverick/swebench-pro/smoke.jsonl

# Lightwork plus the single-shot baseline, with a $5 per-instance cap on the
# Lightwork pipeline and a $100 ceiling for the whole run.
python benchmarks/swe_bench.py \
    --instances ~/.maverick/swebench-pro/smoke.jsonl \
    --pipelines maverick,sonnet_single \
    --instance-hard-cap 5.0 \
    --abort-at-total-dollars 100 \
    --out ~/.maverick/swebench-pro/smoke_predictions.csv
```

`benchmarks/run_swebench_pro_smoke.sh` wraps step 2 for a 5-instance
smoke and writes the patch files that step 3 scores.

The harness streams progress lines per (instance, pipeline) and appends
one CSV row each. After the smoke completes, inspect the patches:

```bash
python -c "
import csv, pathlib
p = pathlib.Path.home() / '.maverick' / 'swebench-pro' / 'smoke_predictions.csv'
for row in csv.DictReader(open(p, encoding='utf-8')):
    print(row['instance_id'], row['pipeline'], row['outcome'],
          row['predicted_patch'][:60].replace(chr(10), ' '))
"
```

If `outcome` is mostly `success` and `predicted_patch` starts with
`--- a/...`, scale up.

## Step 3: score the smoke

The original SWE-bench evaluator (`swebench.harness.run_evaluation`) does
not support the SWE-bench Pro repositories. Score with the official
evaluator, `swe_bench_pro_eval.py` in
[scaleapi/SWE-bench_Pro-os](https://github.com/scaleapi/SWE-bench_Pro-os),
cloned in the prerequisites. It reads a JSON list of
`{"instance_id", "patch", "prefix"}` objects, so first write one patch file
per pipeline next to the predictions CSV:

```bash
# csv.DictWriter quotes newlines, so `predicted_patch` reads back with real
# line breaks; do not post-process `\n` sequences.
python - ~/.maverick/swebench-pro/smoke_predictions.csv <<'PY'
import collections, csv, json, pathlib, sys
csv_path = pathlib.Path(sys.argv[1])
by_pipeline = collections.defaultdict(list)
with open(csv_path, newline='', encoding='utf-8') as f:
    for row in csv.DictReader(f):
        by_pipeline[row['pipeline']].append({
            'instance_id': row['instance_id'],
            'patch': row['predicted_patch'],
            'prefix': row['pipeline'],
        })
for pipeline, patches in by_pipeline.items():
    out = csv_path.with_name(f'{csv_path.stem}_{pipeline}_patches.json')
    out.write_text(json.dumps(patches), encoding='utf-8')
    print(f'wrote {out}')
PY
```

Then run the evaluator from its clone; it reads `run_scripts/` and
`dockerfiles/` relative to the working directory. `--use_local_docker` runs
the prebuilt instance images from Docker Hub on this machine; without it the
evaluator runs on Modal.

```bash
cd ~/SWE-bench_Pro-os
PREDICTIONS=~/.maverick/swebench-pro/smoke_predictions  # the CSV path without .csv
EVAL_DIR=~/.maverick/swebench-pro/eval-smoke
for pipeline in maverick sonnet_single; do
    .venv/bin/python swe_bench_pro_eval.py \
        --raw_sample_path ~/.maverick/swebench-pro/swe_bench_pro_v1.jsonl \
        --patch_path "${PREDICTIONS}_${pipeline}_patches.json" \
        --output_dir "${EVAL_DIR}/${pipeline}" \
        --scripts_dir run_scripts \
        --dockerhub_username jefzda \
        --use_local_docker \
        --num_workers 4
done
```

For each pipeline the evaluator writes `eval_results.json` to its output
directory, mapping each instance id to `true` when every `fail_to_pass` and
`pass_to_pass` test passes. The score is the share of `true` values.

## Step 4: real run

Run the pre-flight checks below first. Then:

```bash
# Lightwork + 3 baselines over the full test split. See the cost estimates
# below before choosing pipelines. The per-instance cap (MAVERICK_INSTANCE_HARD_CAP,
# default $3, or --instance-hard-cap) stops a runaway instance.
python benchmarks/swe_bench.py \
    --instances ~/.maverick/swebench-pro/manifest.jsonl \
    --pipelines maverick,sonnet_single,sonnet_tools,sonnet_self_consistency_n8 \
    --out benchmarks/RESULTS_SWE_$(date +%Y%m%d).csv
```

Run it in `tmux` or with `nohup`; expect 12-48 hours depending on
provider capacity and how many retries the verifier triggers.

Score it with step 3: pass the new CSV to the converter, then run the
evaluator loop with `PREDICTIONS` set to the CSV path without `.csv`,
`EVAL_DIR=~/.maverick/swebench-pro/eval` and all four pipelines.

## Step 5: aggregate into RESULTS.md

After both producer and scorer finish, render the per-pipeline table:

```bash
# Run from the Lightwork repository root.
python -c "
import csv, json, pathlib
from collections import defaultdict
csv_candidates = sorted(pathlib.Path('benchmarks').glob('RESULTS_SWE_*.csv'))
if not csv_candidates:
    raise SystemExit('No RESULTS_SWE_*.csv found in benchmarks/')
csv_path = csv_candidates[-1]
eval_root = pathlib.Path.home() / '.maverick' / 'swebench-pro' / 'eval'
scores = {}
for p in eval_root.glob('*/eval_results.json'):
    pipeline = p.parent.name
    for iid, resolved in json.loads(p.read_text()).items():
        scores[(iid, pipeline)] = bool(resolved)
agg = defaultdict(lambda: {'n': 0, 'pass': 0, 'cost': 0.0, 'wall': 0.0})
with open(csv_path) as f:
    for row in csv.DictReader(f):
        key = row['pipeline']
        agg[key]['n'] += 1
        if scores.get((row['instance_id'], key)):
            agg[key]['pass'] += 1
        agg[key]['cost'] += float(row['cost_dollars'])
        agg[key]['wall'] += float(row['wall_seconds'])
print('| pipeline | resolved | total | rate | total \$ | \$/task |')
print('|---|---|---|---|---|---|')
for p, a in sorted(agg.items()):
    rate = a['pass'] / a['n'] if a['n'] else 0
    print(f'| {p} | {a[\"pass\"]} | {a[\"n\"]} | {rate:.1%} | \${a[\"cost\"]:.0f} | \${a[\"cost\"]/max(a[\"n\"],1):.2f} |')
"
```

Paste that table into `benchmarks/RESULTS.md` under a
`## SWE-bench Pro (run YYYY-MM-DD, tag vX.Y.Z)` heading.

## Recommended configuration

Set these before the real run. Each one is read by the harness or the
agent runtime:

```bash
# Prompting and editing
export MAVERICK_CODING_MODE=1        # diff-only coder prompt; enables `git apply --check` self-validation
export MAVERICK_BENCHMARK_OPAQUE=1   # with coding mode: shell blocks git-history reads (`git log -p`, `git show` ...), github.com fetches and package installs
export MAVERICK_USE_SKILLS=0         # do not inject learned skills into benchmark runs
export MAVERICK_MAX_STEPS=25         # agent step limit per instance

# Candidate selection
export MAVERICK_BEST_OF_N=3          # generate N candidates and pick the best
export MAVERICK_BON_LADDER="claude-sonnet-4-6:0.3,claude-sonnet-4-6:0.7,claude-opus-4-7:0.4"
                                     # per-candidate model:temperature pairs (default: orchestrator model at 0.3/0.7/0.95)

# Model routing
export MAVERICK_CASCADE_ROUTING=1    # cheap-first cascade: draft on a cheaper model, escalate on low confidence
export MAVERICK_MODEL_OVERRIDE_ORCHESTRATOR=claude-opus-4-7
                                     # coder/verifier fall through to the configured role models

# Caching
export MAVERICK_ANTHROPIC_CACHE_TTL=5m   # SWE-bench has no cross-instance prompt reuse
export MAVERICK_CACHE_MESSAGES=1         # cache message history (on by default)

# Per-instance limits
export MAVERICK_INSTANCE_HARD_CAP=3.0    # dollars (same as --instance-hard-cap)
export MAVERICK_INSTANCE_WALL_SEC=1500   # seconds
export MAVERICK_LONG_CMD_TIMEOUT=600     # shell-tool timeout for long commands such as pytest

# Telemetry
export MAVERICK_TRACE_DIR=~/.maverick/swebench-pro-traces
```

The model IDs above are examples; `python benchmarks/preflight.py`
rejects a ladder or override that names an unknown model.
`fail_to_pass` / `pass_to_pass` are loaded per instance from the manifest.

## Cost estimates

Per-task cost depends on routing and caching. Rough estimates at May 2026
list prices:

| pipeline                       | model | $/task | total / 731 tasks |
|--------------------------------|-------|--------|-------------------|
| sonnet_single (no tools)       | Sonnet 4.6  | $0.05  | ~$37          |
| sonnet_tools                   | Sonnet 4.6  | $0.40  | ~$290         |
| sonnet_self_consistency_n8     | Sonnet 4.6  | $0.40  | ~$290         |
| maverick (cascade ON)          | Haiku→Sonnet→Opus | $0.80 | ~$585 |
| maverick (cascade OFF, Opus)   | Opus 4.7    | $4.50  | ~$3,300       |

## Pre-flight: environment checker

Run this before any paid run. It catches the most common failure modes
(bad API key, mistyped model overrides, low disk, blocked egress, stale
`config.toml`) in a few seconds for a fraction of a cent:

```bash
python benchmarks/preflight.py
```

For offline boxes or to skip the API ping:
```bash
python benchmarks/preflight.py --skip-network --skip-api
```

For a Verified-only smoke (smaller disk requirement):
```bash
python benchmarks/preflight.py --min-disk-gb 50
```

Exits 0 on PASS, 2 on FAIL. Fix every FAIL before launching a paid run.
Warnings are advisory but worth reading.

## Pre-flight: invariant tests

Before the real run, check that the patch-application and opaque-mode
invariants hold on the machine that will execute the sweep:

```bash
python -m pytest benchmarks/test_swe_bench_adversarial_preflight.py -v
```

Seven tests cover CRLF handling, new-file SEARCH/REPLACE blocks, atomic
rollback, the test-file edit block, the github.com curl block and the
`pip install -e` block. If any fails, **abort**: the real run would lose
score silently.

## Pre-flight: shadow benchmark on Verified

A 50-instance SWE-bench Verified slice is a low-cost way to validate
adoption rates before a Pro run (see
[`RUNBOOK_SWE_BENCH_VERIFIED.md`](RUNBOOK_SWE_BENCH_VERIFIED.md) for the
full Verified path):

```bash
# Pull a 50-instance Verified slice
python -c "
from datasets import load_dataset
import json, pathlib, random
ds = load_dataset('princeton-nlp/SWE-bench_Verified', split='test')
random.seed(20260526)
sample = random.sample(list(ds), 50)
out = pathlib.Path.home() / '.maverick' / 'swebench-verified-smoke.jsonl'
with open(out, 'w') as f:
    for ex in sample:
        f.write(json.dumps({
            'instance_id': ex['instance_id'],
            'brief': ex['problem_statement'],
            'gold_patch': ex['patch'],
            'fail_to_pass': json.loads(ex.get('FAIL_TO_PASS', '[]')),
            'pass_to_pass': json.loads(ex.get('PASS_TO_PASS', '[]')),
            'base_commit': ex.get('base_commit', ''),
            'language': 'python',
        }) + chr(10))
print(f'wrote {out}')
"

# Run with adoption tripwire — abort if SR usage drops below 50%
python benchmarks/swe_bench.py \
    --instances ~/.maverick/swebench-verified-smoke.jsonl \
    --pipelines maverick \
    --instance-hard-cap 3.0 \
    --adoption-tripwire 0.5 \
    --out ~/.maverick/swebench-pro/verified_smoke.csv
```

Check:
- `SEARCH/REPLACE adoption: NN%` reported at the end of the run
- At least 25/50 (50%) instances have a non-empty `predicted_patch`
- Total spend ≤ $80

If adoption < 50% OR resolution < 25/50, **do not scale to Pro**.
Investigate prompt drift or model availability first.

## Troubleshooting

- **`docker pull` rate-limited**: log into Docker Hub or use a mirror.
- **`gh: not found`** during PR fallback: not needed for SWE-bench.
- **Lightwork produces no patch**: check that the agent's `FINAL` is a
  unified diff. If it is prose, confirm `MAVERICK_CODING_MODE=1` is set.
- **Evaluator is slow on the first pass**: it pulls a large prebuilt
  image per instance the first time. Run with `--num_workers 1` for the
  first pass, then raise it.

## What the harness provides vs. what you bring

- Provided: the harness, three baseline pipelines (`sonnet_single`,
  `sonnet_tools`, `sonnet_self_consistency_n8`), the cost tracker, the
  contamination guard (reported in the CSV's `contamination` column), and
  the aggregation snippet in step 5.
- You bring: the dataset, the Docker host, the API budget, the time.
