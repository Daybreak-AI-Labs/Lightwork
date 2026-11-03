"""The self-learning aggregate proof checks every in-process
capability (governed gate, dreaming, hindsight, snapshot/rollback, flows
self-evolution, fleet memory, operating record, evaluator co-evolution) and the
signed-audit-chain integrity check, while reporting the unsigned evaluator refusal. Run as a subprocess so the proof's env/
MAVERICK_HOME mutation never leaks into the rest of the suite. In-process only
(--no-scripts); the heavier proof/*.py scoreboards have their own tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_self_learning_proof_reports_unsigned_evaluator_refusal(tmp_path):
    env = os.environ.copy()
    env.pop("MAVERICK_APPROVER_KEYS", None)
    env.pop("MAVERICK_APPROVER_KEYS_DIR", None)
    env.pop("LIGHTWORK_APPROVER_KEYS", None)
    env["MAVERICK_CONFIG"] = str(tmp_path / "config.toml")
    r = subprocess.run(
        [sys.executable, str(_ROOT / "benchmarks" / "self_learning_proof.py"), "--no-scripts"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180, env=env)
    assert r.returncode == 1, f"unexpected proof result:\n{r.stdout}\n{r.stderr}"
    failures = [line for line in r.stdout.splitlines() if "[FAIL]" in line]
    assert len(failures) == 1
    assert "evaluator co-evolution" in failures[0]
    assert "requires a valid approver signature" in failures[0]
    for capability in (
        "governed promotion gate", "dreaming (insight distillation)",
        "hindsight (regression detection)", "snapshot + rollback",
        "flows self-evolution", "fleet memory (governed)",
        "operating record (signed capsule)", "signed audit chain (all of the above)",
    ):
        assert any("[PASS]" in line and capability in line
                   for line in r.stdout.splitlines()), capability
    assert "DEVIATIONS ABOVE" in r.stdout
    assert "ALL SELF-LEARNING CAPABILITIES PROVEN" not in r.stdout
