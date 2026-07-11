"""The proposer's diff canonicalizer: a model diff with WRONG hunk-header
arithmetic (the 'corrupt patch at line N' that killed the first live DGM
cycle) must be repaired into a git-canonical, applying diff -- or rejected
loudly if it truly cannot apply. No network: canonicalization is pure git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packages" / "maverick-core"))
sys.path.insert(0, str(_ROOT / "benchmarks"))

import dgm_uplift  # noqa: E402

_SOLVER = (
    "def solve(instance):\n"
    "    x = 1\n"
    "    y = 2\n"
    "    return ''\n"
)


def _solver_dir(tmp_path: Path) -> Path:
    d = tmp_path / "solver"
    d.mkdir()
    (d / "solver.py").write_text(_SOLVER)
    return d


def _applies(solver_dir: Path, diff: str) -> bool:
    p = solver_dir / "p.diff"
    p.write_text(diff if diff.endswith("\n") else diff + "\n")
    r = subprocess.run(["git", "init", "-q", "."], cwd=solver_dir,
                       capture_output=True, text=True)
    subprocess.run(["git", "add", "solver.py"], cwd=solver_dir, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "b"], cwd=solver_dir, capture_output=True)
    r = subprocess.run(["git", "apply", "--check", str(p)], cwd=solver_dir,
                       capture_output=True, text=True)
    return r.returncode == 0


def test_wrong_hunk_counts_are_recounted_and_apply(tmp_path):
    d = _solver_dir(tmp_path)
    # Header says -1,3 +1,4 but the body has 3 context/removed and 4 new lines
    # in the WRONG count deliberately: a real git apply (no recount) rejects it.
    bad = (
        "diff --git a/solver.py b/solver.py\n"
        "--- a/solver.py\n"
        "+++ b/solver.py\n"
        "@@ -1,9 +1,9 @@\n"          # deliberately wrong counts
        " def solve(instance):\n"
        "     x = 1\n"
        "-    y = 2\n"
        "+    y = 3\n"
        "     return ''\n"
    )
    canon, why = dgm_uplift._canonicalize_solver_patch(d, bad)
    assert canon, f"canonicalizer failed to repair: {why}"
    assert "y = 3" in canon
    # the repaired diff applies cleanly under a strict git apply --check on a
    # fresh copy of the original solver
    (tmp_path / "v").mkdir()
    assert _applies(_solver_dir(tmp_path / "v"), canon)


def test_no_op_diff_is_rejected(tmp_path):
    d = _solver_dir(tmp_path)
    noop = (
        "diff --git a/solver.py b/solver.py\n"
        "--- a/solver.py\n"
        "+++ b/solver.py\n"
        "@@ -1,1 +1,1 @@\n"
        " def solve(instance):\n"
    )
    canon, why = dgm_uplift._canonicalize_solver_patch(d, noop)
    assert canon == ""
    assert "changed nothing" in why or "failed" in why


def test_garbage_is_rejected_with_reason(tmp_path):
    d = _solver_dir(tmp_path)
    canon, why = dgm_uplift._canonicalize_solver_patch(d, "not a diff at all")
    assert canon == ""
    assert why


def test_empty_is_rejected(tmp_path):
    d = _solver_dir(tmp_path)
    canon, why = dgm_uplift._canonicalize_solver_patch(d, "   ")
    assert canon == ""
    assert "empty" in why


def test_extract_fenced_diff_unwraps_code_block():
    fenced = "Here:\n```diff\ndiff --git a/solver.py b/solver.py\n--- a/solver.py\n+++ b/solver.py\n```\n"
    out = dgm_uplift._extract_fenced_diff(fenced)
    assert out.startswith("diff --git")


# --- full-file proposer path (the robust default) ------------------------------

def test_full_file_produces_applying_canonical_patch(tmp_path):
    d = _solver_dir(tmp_path)
    new = "def solve(instance):\n    x = 1\n    y = 3\n    return instance.gold_patch\n"
    patch, why = dgm_uplift._full_file_to_patch(d, new)
    assert patch, why
    assert "instance.gold_patch" in patch
    (tmp_path / "v").mkdir()
    assert _applies(_solver_dir(tmp_path / "v"), patch)


def test_full_file_identical_is_rejected(tmp_path):
    d = _solver_dir(tmp_path)
    patch, why = dgm_uplift._full_file_to_patch(d, _SOLVER)
    assert patch == ""
    assert "identical" in why


def test_full_file_without_solve_is_rejected(tmp_path):
    d = _solver_dir(tmp_path)
    patch, why = dgm_uplift._full_file_to_patch(d, "def not_a_solver():\n    return 1\n")
    assert patch == ""
    assert "solve" in why


def test_extract_fenced_code_picks_the_python_block(tmp_path):
    reply = "Sure:\n```python\ndef solve(instance):\n    return 'x'\n```\nDone."
    code = dgm_uplift._extract_fenced_code(reply)
    assert "def solve" in code and "Sure" not in code and "Done" not in code
