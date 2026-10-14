"""Regression coverage for the DGM solver execution isolation boundary."""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "maverick-core"))
sys.path.insert(0, str(_ROOT / "benchmarks"))

import dgm_uplift  # noqa: E402


class _SensitiveInstance:
    """Representative corpus row with fields a solver must never receive."""

    instance_id = "acme__widget-42"
    brief = "Fix the public widget parsing bug."
    language = "python"
    repo_path = Path("/private/checkouts/acme-widget")
    gold_patch = "TOP-SECRET-GOLD-PATCH"
    test_patch = "TOP-SECRET-GRADER-PATCH"
    fail_to_pass = ["tests/private/test_widget.py::test_secret"]
    pass_to_pass = ["tests/private/test_widget.py::test_regression"]


def _write_malicious_solver(tmp_path: Path) -> tuple[Path, Path]:
    """A solver whose import has a visible host-side effect."""
    solver = tmp_path / "solver"
    solver.mkdir()
    marker = tmp_path / "host-executed"
    (solver / "solver.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('host execution')\n"
        "def solve(instance):\n"
        "    return 'should never run on the host'\n",
        encoding="utf-8",
    )
    return solver, marker


def test_load_solver_refuses_to_import_candidate_code_on_host(tmp_path):
    solver, marker = _write_malicious_solver(tmp_path)

    with pytest.raises(dgm_uplift.SolverIsolationError, match="host loading"):
        dgm_uplift.load_solver(solver)

    assert not marker.exists()


def test_run_solver_requires_an_explicit_isolated_runner_before_host_execution(tmp_path):
    solver, marker = _write_malicious_solver(tmp_path)

    with pytest.raises(dgm_uplift.SolverIsolationError, match="isolated solver_runner"):
        dgm_uplift.run_solver(solver, [_SensitiveInstance()], tmp_path / "work")

    assert not marker.exists()


def test_injected_runner_gets_only_redacted_task_and_can_be_scored(tmp_path):
    solver, marker = _write_malicious_solver(tmp_path)
    captured = {}

    def isolated_runner(solver_dir, task, *, timeout):
        captured["solver_dir"] = solver_dir
        captured["task"] = task
        captured["timeout"] = timeout
        return "safe candidate patch"

    def scorer(instance, patch, workroot, *, timeout):
        captured["scored_instance"] = instance
        captured["patch"] = patch
        captured["workroot"] = workroot
        captured["score_timeout"] = timeout
        return True

    result = dgm_uplift.run_solver(
        solver,
        [_SensitiveInstance()],
        tmp_path / "work",
        timeout=17.5,
        solver_runner=isolated_runner,
        score_fn=scorer,
    )

    task = captured["task"]
    assert isinstance(task, dgm_uplift.SolverTask)
    assert asdict(task) == {
        "instance_id": "acme__widget-42",
        "brief": "Fix the public widget parsing bug.",
        "language": "python",
    }
    for secret in ("repo_path", "gold_patch", "test_patch", "fail_to_pass", "pass_to_pass"):
        assert not hasattr(task, secret)
    assert captured["solver_dir"] == solver
    assert captured["timeout"] == captured["score_timeout"] == 17.5
    assert captured["patch"] == "safe candidate patch"
    assert result == {"acme__widget-42": True}
    # The injectable runner returned a patch without importing the candidate;
    # this proves run_solver itself has no residual host import sink.
    assert not marker.exists()


def test_runner_error_degrades_to_an_empty_patch(tmp_path):
    solver, marker = _write_malicious_solver(tmp_path)
    seen = []

    def failed_isolation_backend(solver_dir, task, *, timeout):
        raise RuntimeError("sandbox unavailable")

    def scorer(instance, patch, workroot, *, timeout):
        seen.append(patch)
        return bool(patch)

    result = dgm_uplift.run_solver(
        solver,
        [_SensitiveInstance()],
        tmp_path / "work",
        solver_runner=failed_isolation_backend,
        score_fn=scorer,
    )

    assert seen == [""]
    assert result == {"acme__widget-42": False}
    assert not marker.exists()
