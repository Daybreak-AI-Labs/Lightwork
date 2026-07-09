#!/bin/bash
# One-shot pod bootstrap for the SWE-bench-under-governance slice run.
# Run from anywhere:  bash ~/Lightwork/benchmarks/pod_setup.sh
# Assumes: repo already cloned to ~/Lightwork, ANTHROPIC_API_KEY exported
# (or saved in ~/.bashrc). Installs everything, stages the Verified slice,
# self-tests for free, and starts the capped paid run only if the test passes.

if [ -z "$ANTHROPIC_API_KEY" ]; then
    line=$(grep -m1 'export ANTHROPIC_API_KEY=' ~/.bashrc 2>/dev/null)
    [ -n "$line" ] && eval "$line"
fi
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "!!! ANTHROPIC_API_KEY is not set. Run the export line first, then rerun me."
    exit 1
fi
echo "key OK (${ANTHROPIC_API_KEY:0:13}..., length ${#ANTHROPIC_API_KEY})"

echo "[1/5] installing Lightwork packages (few minutes)..."
cd ~/Lightwork || { echo "!!! ~/Lightwork missing - clone the repo first"; exit 1; }
bash .devcontainer/post-create.sh > /tmp/postcreate.log 2>&1 \
    && echo "    install OK" \
    || { echo "!!! INSTALL FAILED - last lines:"; tail -5 /tmp/postcreate.log; exit 1; }

echo "[2/5] signing keys..."
python3 - <<'PY'
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from pathlib import Path
k = Path.home() / "dgm-keys"; k.mkdir(exist_ok=True)
priv = ed25519.Ed25519PrivateKey.generate()
(k / "operator.priv.hex").write_text(priv.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
    serialization.NoEncryption()).hex())
(k / "operator.pub").write_bytes(priv.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw))
print("    keys OK ->", k)
PY

echo "[3/5] staging SWE-bench Verified instances (few minutes)..."
pip install -q datasets >/dev/null 2>&1
mkdir -p ~/swebench_stage && cd ~/swebench_stage
python3 ~/Lightwork/benchmarks/fetch_swe_bench_verified.py \
    --repos psf/requests,pylint-dev/pylint,pallets/flask \
    --out-manifest slice_manifest.jsonl --stage --repos-dir ./repos \
    > /tmp/fetch.log 2>&1 \
    && echo "    staged $(wc -l < slice_manifest.jsonl) instances" \
    || { echo "!!! FETCH FAILED - last lines:"; tail -5 /tmp/fetch.log; exit 1; }

echo "[4/5] instance test dependencies..."
cd ~/swebench_stage/repos
for fam in pylint-dev__pylint psf__requests pallets__flask; do
    d=$(ls -d "${fam}"-* 2>/dev/null | head -1)
    [ -n "$d" ] && pip install -q -e "./$d" >/dev/null 2>&1 && echo "    $fam deps OK"
done

echo "[5/5] FREE SELF-TEST..."
cd ~/swebench_stage
python3 ~/Lightwork/proof/swebench_governed_proof.py 2>&1 | tail -2
grep 8898 slice_manifest.jsonl > one_instance.jsonl 2>/dev/null \
    || head -1 slice_manifest.jsonl > one_instance.jsonl
python3 ~/Lightwork/benchmarks/swebench_governed.py \
    --manifest one_instance.jsonl --proposer oracle \
    --keys ~/dgm-keys --ledger /tmp/preflight_ledger.json --timeout 300 \
    > /tmp/oracle.log 2>&1
tail -4 /tmp/oracle.log

if grep -q "resolved under governance: 1/1" /tmp/oracle.log; then
    echo ""
    echo "================ SELF-TEST PASSED - STARTING PAID RUN ================"
    cd ~/swebench_stage
    MAVERICK_INSTANCE_HARD_CAP=3.0 MAVERICK_SUPPRESS_SANDBOX_WARNING=1 \
    nohup python3 ~/Lightwork/benchmarks/swebench_governed.py \
        --manifest slice_manifest.jsonl --proposer llm \
        --keys ~/dgm-keys --ledger slice_ledger.json --timeout 700 \
        --abort-at-dollars 40 --max-consecutive-failures 4 \
        > slice.log 2>&1 &
    echo "PAID RUN STARTED. Caps: \$3/task, \$40 total, stops after 4 fails in a row."
    echo "Watch it:   tail -f ~/swebench_stage/slice.log"
    echo "(Ctrl-C stops the watching only, never the run.)"
else
    echo ""
    echo "!!! SELF-TEST DID NOT PASS - paid run NOT started."
    echo "!!! Copy everything on this screen and send it to Claude."
fi
