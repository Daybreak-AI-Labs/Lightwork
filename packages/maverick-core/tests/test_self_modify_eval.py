"""Sandbox eval harness: isolated-copy baseline-vs-candidate scoring.

The load-bearing safety property is that a proposed patch is measured on a
THROWAWAY copy, never the live tree. These pin: isolation (src untouched),
git-apply through the sandbox with a --check gate, fail-closed on a
non-applying patch / scorer error, the pytest pass-rate scorer, and that the
produced scores are exactly what the governed gate consumes.
"""
from __future__ import annotations

from pathlib import Path

from maverick import self_modify_eval as ev
from maverick.self_modify_eval import EvalResult, Score, evaluate_patch


def _seed_repo(root: Path) -> Path:
    src = root / "src"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    (src / "pkg" / "b.py").write_text("x = 2\n", encoding="utf-8")
    return src


def _edit_patch() -> str:
    return ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
            "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 99\n")


class TestParsePytest:
    def test_passed_and_failed(self):
        assert ev._parse_pytest_counts("... 12 passed, 3 failed in 1s") == (12, 3)

    def test_errors_count_as_failures(self):
        assert ev._parse_pytest_counts("5 passed, 2 errors") == (5, 2)

    def test_no_summary_is_zero(self):
        assert ev._parse_pytest_counts("nothing here") == (0, 0)


class TestScorers:
    def test_default_score_counts_python_files(self, tmp_path):
        src = _seed_repo(tmp_path)
        s = ev._default_score(src)
        assert isinstance(s, Score) and s.value == 2.0 and s.samples == 2

    def test_pytest_score_pass_rate(self, tmp_path):
        # Fake sandbox returns a canned pytest summary.
        class FakeSB:
            def exec(self, cmd, timeout=None):
                class R:
                    stdout = "8 passed, 2 failed in 0.1s"
                    stderr = ""
                    exit_code = 0
                return R()
        score = ev.pytest_score(sandbox=FakeSB())(tmp_path)
        assert score.samples == 10
        assert abs(score.value - 0.8) < 1e-9

    def test_pytest_score_no_summary_is_zero(self, tmp_path):
        class FakeSB:
            def exec(self, cmd, timeout=None):
                class R:
                    stdout = ""
                    stderr = ""
                    exit_code = 1
                return R()
        score = ev.pytest_score(sandbox=FakeSB())(tmp_path)
        assert score == Score(0.0, 0)


class TestGitApply:
    def test_applies_a_clean_patch(self, tmp_path):
        src = _seed_repo(tmp_path)
        res = ev.git_apply(_edit_patch(), src)   # default LocalBackend
        assert res.ok is True
        assert (src / "pkg" / "a.py").read_text() == "VALUE = 99\n"

    def test_rejects_a_non_applying_patch(self, tmp_path):
        src = _seed_repo(tmp_path)
        bad = ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
               "@@ -1 +1 @@\n-DOES_NOT_MATCH\n+new\n")
        res = ev.git_apply(bad, src)
        assert res.ok is False
        assert (src / "pkg" / "a.py").read_text() == "VALUE = 1\n"   # untouched

    def test_empty_patch_refused(self, tmp_path):
        assert ev.git_apply("", tmp_path).ok is False


class TestEvaluatePatch:
    def test_isolation_source_is_untouched(self, tmp_path):
        src = _seed_repo(tmp_path)
        evaluate_patch(_edit_patch(), src=src, workroot=tmp_path / "work")
        assert (src / "pkg" / "a.py").read_text() == "VALUE = 1\n"   # never mutated

    def test_scores_from_injected_scorer(self, tmp_path):
        src = _seed_repo(tmp_path)

        def score(workdir):
            # candidate copy has VALUE=99 after the patch; baseline has VALUE=1
            text = (Path(workdir) / "pkg" / "a.py").read_text()
            return Score(1.0 if "99" in text else 0.0, samples=4)

        res = evaluate_patch(_edit_patch(), src=src, workroot=tmp_path / "w",
                             score_fn=score)
        assert isinstance(res, EvalResult)
        assert res.ok is True and res.applied is True
        assert res.baseline_score == 0.0 and res.candidate_score == 1.0
        assert res.samples == 4
        assert res.improvement == 1.0

    def test_non_applying_patch_fails_closed(self, tmp_path):
        src = _seed_repo(tmp_path)
        bad = ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
               "@@ -1 +1 @@\n-NOPE\n+new\n")
        res = evaluate_patch(bad, src=src, workroot=tmp_path / "w")
        assert res.ok is False and res.applied is False

    def test_scorer_error_fails_closed(self, tmp_path):
        src = _seed_repo(tmp_path)

        def boom(workdir):
            raise RuntimeError("scorer blew up")

        res = evaluate_patch(_edit_patch(), src=src, workroot=tmp_path / "w",
                             score_fn=boom)
        assert res.ok is False
        assert "scorer error" in res.reason

    def test_samples_is_the_min_of_the_two_arms(self, tmp_path):
        src = _seed_repo(tmp_path)
        calls = {"n": 0}

        def score(workdir):
            calls["n"] += 1
            return Score(0.5, samples=10 if calls["n"] == 1 else 6)

        res = evaluate_patch(_edit_patch(), src=src, workroot=tmp_path / "w",
                             score_fn=score)
        assert res.samples == 6
