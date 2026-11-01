#!/usr/bin/env bash
# One-shot smoke runner for SWE-bench Pro. Runs the producer on 5
# instances and writes the patch files the official evaluator scores, so
# you know the wiring works before spending real money on the full test
# split.
#
# Costs: ~$10-30 depending on which pipelines you enable.
# Time: ~15-30 min for the producer; scoring (runbook step 3) is separate.
#
# Usage:
#   ./benchmarks/run_swebench_pro_smoke.sh             # default 5 inst, maverick + sonnet_single
#   ./benchmarks/run_swebench_pro_smoke.sh 10          # 10 instances
#   ./benchmarks/run_swebench_pro_smoke.sh 5 all       # all four pipelines
#
# Reads the manifest from ~/.maverick/swebench-pro/manifest.jsonl;
# create it with the script in RUNBOOK_SWE_BENCH_PRO.md step 1.

set -euo pipefail

INSTANCES="${1:-5}"
PIPELINES_ARG="${2:-default}"
SEED="${SMOKE_SEED:-20260526}"   # fixed seed so smoke is reproducible
STRATIFY="${SMOKE_STRATIFY:-1}"  # 1 = stratify by language (Python/JS/Go/...)

MANIFEST="${HOME}/.maverick/swebench-pro/manifest.jsonl"
WORKDIR="$(mktemp -d -t maverick-swe-XXXXXX)"
SMOKE_MANIFEST="${WORKDIR}/smoke.jsonl"
PREDS_CSV="${WORKDIR}/predictions.csv"

if [[ ! -f "${MANIFEST}" ]]; then
    echo "ERROR: manifest not found at ${MANIFEST}" >&2
    echo "Run step 1 of benchmarks/RUNBOOK_SWE_BENCH_PRO.md first." >&2
    exit 2
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "ERROR: ANTHROPIC_API_KEY not set." >&2
    exit 2
fi

if [[ "${PIPELINES_ARG}" == "all" ]]; then
    PIPELINES="maverick,sonnet_single,sonnet_tools,sonnet_self_consistency_n8"
else
    PIPELINES="maverick,sonnet_single"
fi

# Cascaded routing makes Lightwork affordable; force it on for smoke
# so the cost numbers are representative of a real Pareto run.
export MAVERICK_CASCADE_ROUTING=1

echo "[smoke] manifest:   ${MANIFEST}"
echo "[smoke] instances:  ${INSTANCES}"
echo "[smoke] pipelines:  ${PIPELINES}"
echo "[smoke] workdir:    ${WORKDIR}"
echo

# Random sample with fixed seed, stratified by language (instance_id prefix
# encodes the repo, and the repo encodes the language). `head -n` would
# be non-representative because the manifest is repo-sorted.
python - <<PY
import json, random, collections, pathlib, re
random.seed(${SEED})
rows = [json.loads(l) for l in open("${MANIFEST}")]
def lang(r):
    iid = r["instance_id"].lower()
    # crude but adequate: classify by repo name patterns in SWE-bench Pro
    if re.search(r"(node|js|ts|react|vue|next|express)", iid): return "js"
    if re.search(r"(\\bgo\\b|golang)", iid): return "go"
    if re.search(r"(rust|cargo)", iid): return "rust"
    if re.search(r"(java|kotlin|gradle|maven)", iid): return "java"
    return "py"
buckets = collections.defaultdict(list)
for r in rows: buckets[lang(r)].append(r)
for b in buckets.values(): random.shuffle(b)
# proportional allocation, but guarantee >=1 per non-empty bucket
n = ${INSTANCES}
total = sum(len(v) for v in buckets.values())
alloc = {k: max(1, round(n * len(v)/total)) for k,v in buckets.items() if v}
# trim to exactly n
while sum(alloc.values()) > n:
    k = max(alloc, key=lambda x: alloc[x]); alloc[k] -= 1
while sum(alloc.values()) < n:
    k = min(alloc, key=lambda x: alloc[x]); alloc[k] += 1
picked = []
for k, m in alloc.items(): picked.extend(buckets[k][:m])
with open("${SMOKE_MANIFEST}", "w") as f:
    for r in picked: f.write(json.dumps(r) + chr(10))
print(f"[smoke] sampled {len(picked)} instances, langs={ {k:alloc[k] for k in alloc} }, seed=${SEED}")
PY

# ---- producer ----
echo "[smoke] running producer..."
python "$(dirname "$0")/swe_bench.py" \
    --instances "${SMOKE_MANIFEST}" \
    --pipelines "${PIPELINES}" \
    --out "${PREDS_CSV}"

echo
echo "[smoke] predictions written to ${PREDS_CSV}"
echo "[smoke] inspect with:  csvtool col 1,2,9,11 ${PREDS_CSV} | head"
echo

# ---- producer-only sanity ----
# Count rows whose predicted_patch looks like a unified diff. Must use a real
# CSV parser keyed by FIELD NAME: `awk -F,` split on the wrong column ($9 is
# verifier_confidence, not the patch) and also mis-split the quoted patch field,
# which csv.DictWriter wraps because it embeds commas and newlines -- so the old
# check counted 0 every time and falsely FAILED even when every row had a patch.
read -r PATCH_COUNT TOTAL_ROWS < <(python3 - "${PREDS_CSV}" <<'PY'
import csv, sys
patches = total = 0
with open(sys.argv[1], newline="") as f:
    for row in csv.DictReader(f):
        total += 1
        if (row.get("predicted_patch") or "").startswith("---"):
            patches += 1
print(patches, total)
PY
)
echo "[smoke] ${PATCH_COUNT}/${TOTAL_ROWS} rows have a unified-diff-shaped patch"

if [[ "${PATCH_COUNT}" -eq 0 ]]; then
    echo
    echo "[smoke] FAIL: no rows have a valid patch. Common causes:"
    echo "  - the agent emitted prose instead of a diff (add a worker-prompt hint)"
    echo "  - the model hit max_dollars before producing FINAL (raise the cap)"
    echo "  - the brief format confused the agent (inspect SMOKE_MANIFEST)"
    echo
    echo "Look at the worst row:"
    echo "  csvtool col 1,2,11,9 ${PREDS_CSV} | head -20"
    exit 1
fi

# ---- patch files for the official evaluator ----
# The original `swebench` evaluator does not support the SWE-bench Pro
# repositories. Write one patch file per pipeline in the format of the
# official evaluator (swe_bench_pro_eval.py in
# https://github.com/scaleapi/SWE-bench_Pro-os) and print how to score them.
python3 - "${PREDS_CSV}" <<'PY'
import collections, csv, json, pathlib, sys
csv_path = pathlib.Path(sys.argv[1])
by_pipeline = collections.defaultdict(list)
with open(csv_path, newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        by_pipeline[row["pipeline"]].append({
            "instance_id": row["instance_id"],
            "patch": row["predicted_patch"],
            "prefix": row["pipeline"],
        })
for pipeline, patches in by_pipeline.items():
    out = csv_path.with_name(f"{csv_path.stem}_{pipeline}_patches.json")
    out.write_text(json.dumps(patches), encoding="utf-8")
    print(f"[smoke] wrote {out}")
PY

echo
echo "[smoke] DONE. Score the patch files with step 3 of RUNBOOK_SWE_BENCH_PRO.md,"
echo "         using PREDICTIONS=${PREDS_CSV%.csv}. A Pareto table on these"
echo "         instances is meaningless statistically but proves the wiring."
echo "         Then schedule the real run on the full test split (step 4)."
