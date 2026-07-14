"""Offline structural tests for the agent-based DGM solver (agent_v0).

No network / no agent: verify the solver contract the DGM relies on -- imports,
defines solve(instance) -> str, never raises, is keyless-safe (-> ""), exposes
the improvable budget knobs, and clamps them. The live resolving behavior is
validated separately (paid); here we pin that a bad instance or missing key can
never crash the uplift evaluation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SOLVER = _ROOT / "benchmarks" / "solvers" / "agent_v0" / "solver.py"


def _load():
    sys.path.insert(0, str(_ROOT / "benchmarks"))
    spec = importlib.util.spec_from_file_location("agent_v0_solver", _SOLVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_defines_solve_and_budget_knobs():
    m = _load()
    assert callable(m.solve)
    assert isinstance(m.MAX_STEPS, int) and m.MAX_STEPS >= 1
    assert isinstance(m.BEST_OF_N, int) and m.BEST_OF_N >= 1
    assert isinstance(m.RETRY_ON_EMPTY, bool)


def test_keyless_solve_returns_empty_string(monkeypatch):
    m = _load()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MAVERICK_PROVIDER_READY", raising=False)
    assert m.solve(object()) == ""          # never raises, returns ""


def test_solve_never_raises_on_a_broken_instance(monkeypatch):
    # Even with a "key present", a nonsense instance must yield "" not a crash
    # (llm_proposer will fail on the bogus object; solve swallows it).
    m = _load()
    monkeypatch.setenv("MAVERICK_PROVIDER_READY", "1")
    assert m.solve(object()) == ""


def test_clamp_bounds_and_tolerates_garbage():
    m = _load()
    assert m._clamp(999, 1, 200) == 200
    assert m._clamp(0, 1, 200) == 1
    assert m._clamp("nope", 1, 200) == 1


def test_solve_restores_env_knobs(monkeypatch):
    m = _load()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MAVERICK_PROVIDER_READY", raising=False)
    monkeypatch.setenv("MAVERICK_MAX_STEPS", "sentinel")
    m.solve(object())                        # keyless: returns early, no leak
    import os
    assert os.environ.get("MAVERICK_MAX_STEPS") == "sentinel"
