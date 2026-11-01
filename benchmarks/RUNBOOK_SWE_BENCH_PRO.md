# Running SWE-bench Pro end-to-end

This is the operator's guide for `benchmarks/swe_bench.py`. The harness
is a **producer**: it generates patches. Scoring is delegated to the
upstream `swebench` evaluator, so the harness never grades its own output.

All costs below are estimates for planning a budget, not measurements.

## Prerequisites

```bash
# Disk: ~150 GB free for the Docker images SWE-bench Pro builds
df -h .

# Docker 24+
docker --version

# Python deps (in the same venv as Lightwork)
pip install datasets "swebench>=2.0"
```

API keys in `~/.maverick/.env` (loaded automatically at startup):

```bash
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-proj-...        # optional: cross-family verifier
```

## Step 1: download the dataset

SWE-bench Pro is published on Hugging Face as `Scale/swe-bench-pro`.
Convert it into Lightwork's manifest format:

```bash
mkdir -p ~/.maverick/swebench-pro
python -c "
from datasets import load_dataset
import json, pathlib
ds = load_dataset('Scale/swe-bench-pro', split='test')
out = pathlib.Path.home() / '.maverick' / 'swebench-pro' / 'manifest.jsonl'
with open(out, 'w') as f:
    for ex in ds:
        f.write(json.dumps({
            'instance_id': ex['instance_id'],
            'brief': ex['problem_statement'],
            'gold_patch': ex['patch'],
            'fail_to_pass': ex.get('FAIL_TO_PASS', []),
            'pass_to_pass': ex.get('PASS_TO_PASS', []),
            'language': ex.get('language', ''),
        }) + chr(10))
print(f'wrote {out}')
"
```

Sanity check: `wc -l ~/.maverick/swebench-pro/manifest.jsonl` should
report 1865.

The harness also reads the optional manifest fields `base_commit`,
`requirements` and `interface` when present; `fail_to_pass` enables the
test-driven verifier and `language` is a runner hint for monorepos.

## Step 2: smoke 10 instances first

Do **not** run the full 1865 instances until the smoke run is clean; a
full run on a broken pipeline can cost several thousand dollars.

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

`benchmarks/run_swebench_pro_smoke.sh` wraps steps 2 and 3 for a
5-instance smoke.

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

Convert the CSV into the `predictions.jsonl` format that SWE-bench
expects, then run the official evaluator in Docker:

```bash
# csv.DictWriter quotes newlines, so `predicted_patch` reads back with real
# line breaks; do not post-process `\n` sequences.
python -c "
import csv, json, pathlib
home = pathlib.Path.home() / '.maverick' / 'swebench-pro'
with open(home / 'smoke_predictions.csv') as f, \
     open(home / 'preds.jsonl', 'w') as out:
    r = csv.DictReader(f)
    for row in r:
        if row['pipeline'] != 'maverick':
            continue
        out.write(json.dumps({
            'instance_id': row['instance_id'],
            'model_patch': row['predicted_patch'],
            'model_name_or_path': row['model_id'],
        }) + chr(10))
"

# Run the official evaluator (builds Docker images per instance; first
# run is slow, subsequent runs reuse cache).
python -m swebench.harness.run_evaluation \
    --predictions_path ~/.maverick/swebench-pro/preds.jsonl \
    --dataset_name Scale/swe-bench-pro \
    --split test \
    --max_workers 4 \
    --run_id maverick-smoke
```

The evaluator writes `evaluation_results/maverick-smoke/results.json`
with per-instance `passed` booleans. The score is the share where
`resolved == true`.

## Step 4: real run

Run the pre-flight checks below first. Then:

```bash
# Lightwork + 3 baselines x 1865 instances. See the cost estimates below
# before choosing pipelines. The per-instance cap (MAVERICK_INSTANCE_HARD_CAP,
# default $3, or --instance-hard-cap) stops a runaway instance.
python benchmarks/swe_bench.py \
    --instances ~/.maverick/swebench-pro/manifest.jsonl \
    --pipelines maverick,sonnet_single,sonnet_tools,sonnet_self_consistency_n8 \
    --out benchmarks/RESULTS_SWE_$(date +%Y%m%d).csv
```

Run it in `tmux` or with `nohup`; expect 12-48 hours depending on
provider capacity and how many retries the verifier triggers.

## Step 5: aggregate into RESULTS.md

After both producer and scorer finish, render the per-pipeline table:

```bash
python -c "
import csv, json, pathlib
from collections import defaultdict
csv_candidates = sorted(pathlib.Path('benchmarks').glob('RESULTS_SWE_*.csv'))
if not csv_candidates:
    raise SystemExit('No RESULTS_SWE_*.csv found in benchmarks/')
csv_path = csv_candidates[-1]
score_paths = pathlib.Path('evaluation_results').rglob('results.json')
scores = {}
for p in score_paths:
    d = json.loads(p.read_text())
    pipeline = p.parent.name.replace('maverick-', '')
    for iid, info in d.items():
        scores[(iid, pipeline)] = info.get('resolved', False)
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
`FAIL_TO_PASS` / `PASS_TO_PASS` are loaded per instance from the manifest.

## Cost estimates

Per-task cost depends on routing and caching. Rough estimates at May 2026
list prices:

| pipeline                       | model | $/task | total / 1865 |
|--------------------------------|-------|--------|---------------|
| sonnet_single (no tools)       | Sonnet 4.6  | $0.05  | ~$95          |
| sonnet_tools                   | Sonnet 4.6  | $0.40  | ~$750         |
| sonnet_self_consistency_n8     | Sonnet 4.6  | $0.40  | ~$750         |
| maverick (cascade ON)          | Haiku→Sonnet→Opus | $0.80 | ~$1,500 |
| maverick (cascade OFF, Opus)   | Opus 4.7    | $4.50  | ~$8,400       |

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
- **Evaluator hangs on Docker build**: SWE-bench Pro images are ~5 GB
  each the first time. Run with `--max_workers 1` for the first build
  pass, then raise it.

## What the harness provides vs. what you bring

- Provided: the harness, three baseline pipelines (`sonnet_single`,
  `sonnet_tools`, `sonnet_self_consistency_n8`), the cost tracker, the
  contamination guard (reported in the CSV's `contamination` column), and
  the aggregation snippet in step 5.
- You bring: the dataset, the Docker host, the API budget, the time.
