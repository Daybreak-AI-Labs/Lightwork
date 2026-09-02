"""Explicitly opted-in depth proof that drives REAL, paid LLM calls.

A credential may be present for unrelated tests or local development, so it is
not consent to spend money on this nondeterministic benchmark.  The live proof
runs only when ``MAVERICK_PROVIDER_READY=1`` is explicitly set and a nonblank
``ANTHROPIC_API_KEY`` is present.  When opted in, it must pass all three
flagships plus the signed-audit-chain check under a small hard cost cap.  A
subprocess keeps its environment mutation isolated.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks._common.provider_consent import paid_anthropic_benchmark_ready

_ROOT = Path(__file__).resolve().parent.parent

_SYNTHETIC_ANTHROPIC_KEY = "sk-ant-test-only"  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"ANTHROPIC_API_KEY": _SYNTHETIC_ANTHROPIC_KEY}, False),
        ({"MAVERICK_PROVIDER_READY": "1"}, False),
        ({"MAVERICK_PROVIDER_READY": "0"}, False),
        ({
            "MAVERICK_PROVIDER_READY": "0",
            "ANTHROPIC_API_KEY": _SYNTHETIC_ANTHROPIC_KEY,
        }, False),
        ({
            "MAVERICK_PROVIDER_READY": "true",
            "ANTHROPIC_API_KEY": _SYNTHETIC_ANTHROPIC_KEY,
        }, False),
        ({
            "MAVERICK_PROVIDER_READY": "1",
            "ANTHROPIC_API_KEY": "  ",
        }, False),
        ({
            "MAVERICK_PROVIDER_READY": "1",
            "ANTHROPIC_API_KEY": _SYNTHETIC_ANTHROPIC_KEY,
        }, True),
    ],
)
def test_paid_depth_requires_explicit_opt_in(environ, expected):
    assert paid_anthropic_benchmark_ready(environ) is expected


def _literal_assignment(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} is not a top-level literal assignment")


def test_self_harness_confirmation_set_meets_prompt_gate_floor():
    from maverick.self_improvement import SelfImprovementController

    script = _ROOT / "benchmarks" / "self_learning_depth.py"
    questions = _literal_assignment(script, "_SELF_HARNESS_QUESTIONS")
    development_cases = _literal_assignment(script, "_SELF_HARNESS_DEVELOPMENT_CASES")
    prompt_floor = SelfImprovementController().rung_policy["prompt"]["min_samples"]
    assert len(questions) - development_cases >= prompt_floor


@pytest.mark.skipif(
    not paid_anthropic_benchmark_ready(),
    reason=("self_learning_depth makes real paid LLM calls; set both "
            "MAVERICK_PROVIDER_READY=1 and ANTHROPIC_API_KEY to opt in explicitly"))
def test_depth_flagships_pass_with_real_llm():
    r = subprocess.run(
        [sys.executable, str(_ROOT / "benchmarks" / "self_learning_depth.py"),
         "--max-dollars", "6"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, f"depth proof failed:\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
    assert "DEPTH PROVEN" in r.stdout
