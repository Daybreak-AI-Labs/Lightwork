"""Governed code self-modification: the editable-surface boundary + code rung.

The load-bearing safety primitive is the boundary: a proposed patch is refused
structurally if it touches ANY control-plane path, before and independently of
the promotion gate. These pin that refusal, the fail-closed defaults, and that a
clean change still has to clear the full self_improvement ladder.
"""
from __future__ import annotations

import pytest
from maverick import self_modify as sm


def _patch(path: str) -> str:
    return (f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n")


PROTECTED = [
    "packages/maverick-core/maverick/self_improvement.py",
    "packages/maverick-core/maverick/self_modify.py",
    "packages/maverick-core/maverick/approval_signing.py",
    "packages/maverick-core/maverick/calibration.py",
    "packages/maverick-core/maverick/verifier.py",
    "packages/maverick-core/maverick/capability.py",
    "packages/maverick-core/maverick/config.py",
    "packages/maverick-core/maverick/migrate.py",
    "packages/maverick-core/maverick/audit/writer.py",
    "packages/maverick-core/maverick/sandbox/backends.py",
    "/etc/passwd",                                   # absolute -> forbidden
    "../../etc/passwd",                              # traversal -> forbidden
    "packages/maverick-core/maverick/audit/../self_improvement.py",  # traversal
]

NOT_PROTECTED = [
    "packages/maverick-core/maverick/domains/foo.toml",
    "packages/maverick-core/maverick/skills_builtin/x.md",
    "docs/readme.md",
]


class TestBoundary:
    @pytest.mark.parametrize("path", PROTECTED)
    def test_control_plane_is_protected(self, path):
        assert sm.is_protected(path) is True

    @pytest.mark.parametrize("path", NOT_PROTECTED)
    def test_ordinary_paths_are_not_protected(self, path):
        assert sm.is_protected(path) is False

    def test_classify_precedence(self):
        s = sm.EditableSurface(
            editable_globs=("packages/maverick-core/maverick/domains/*.toml",))
        assert s.classify("packages/maverick-core/maverick/self_improvement.py") == "protected"
        assert s.classify("packages/maverick-core/maverick/domains/a.toml") == "editable"
        assert s.classify("packages/maverick-core/maverick/agent.py") == "unknown"

    def test_empty_allowlist_makes_everything_unknown(self):
        # Fail-closed default: no allowlist -> nothing editable.
        s = sm.EditableSurface()
        assert s.classify("packages/maverick-core/maverick/domains/a.toml") == "unknown"

    def test_protected_wins_over_a_permissive_allowlist(self):
        # Even '**' cannot make a control-plane file editable.
        s = sm.EditableSurface(editable_globs=("**",))
        assert s.classify("packages/maverick-core/maverick/audit/writer.py") == "protected"


class TestReviewPatch:
    def _surface(self):
        return sm.EditableSurface(
            editable_globs=("packages/maverick-core/maverick/domains/*.toml",))

    def test_ok_when_all_paths_editable(self):
        r = sm.review_patch(
            _patch("packages/maverick-core/maverick/domains/a.toml"), self._surface())
        assert r.ok is True and not r.protected and not r.unknown

    def test_rejects_protected_even_alongside_editable(self):
        s = sm.EditableSurface(editable_globs=("**",))
        patch = (_patch("packages/maverick-core/maverick/domains/a.toml")
                 + _patch("packages/maverick-core/maverick/audit/writer.py"))
        r = sm.review_patch(patch, s)
        assert r.ok is False
        assert any("audit" in p for p in r.protected)
        assert "protected" in r.reason

    def test_rejects_unknown_paths(self):
        r = sm.review_patch(
            _patch("packages/maverick-core/maverick/agent.py"), self._surface())
        assert r.ok is False and r.unknown

    def test_rejects_empty_patch(self):
        assert sm.review_patch("", sm.EditableSurface(editable_globs=("**",))).ok is False

    def test_new_file_diff_excludes_dev_null(self):
        p = "packages/maverick-core/maverick/domains/a.toml"
        patch = (f"diff --git a/{p} b/{p}\nnew file mode 100644\n"
                 f"--- /dev/null\n+++ b/{p}\n@@ -0,0 +1 @@\n+x\n")
        r = sm.review_patch(patch, self._surface())
        assert r.ok is True
        assert "/dev/null" not in " ".join(r.touched)

    def test_rename_paths_are_classified(self):
        s = sm.EditableSurface(editable_globs=("**",))
        patch = ("diff --git a/packages/maverick-core/maverick/domains/a.toml "
                 "b/packages/maverick-core/maverick/audit/x.py\n"
                 "rename from packages/maverick-core/maverick/domains/a.toml\n"
                 "rename to packages/maverick-core/maverick/audit/x.py\n")
        r = sm.review_patch(patch, s)
        assert r.ok is False  # the rename target is under the protected audit/ dir


class TestGate:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("MAVERICK_SELF_MODIFY", raising=False)
        monkeypatch.setattr(sm, "_settings", lambda: {"enable": False, "editable_paths": []})
        assert sm.enabled() is False

    def test_env_enables(self, monkeypatch):
        monkeypatch.setenv("MAVERICK_SELF_MODIFY", "1")
        assert sm.enabled() is True
        monkeypatch.setenv("MAVERICK_SELF_MODIFY", "0")
        assert sm.enabled() is False


class TestProposeCodeChange:
    def _surface(self):
        return sm.EditableSurface(
            editable_globs=("packages/maverick-core/maverick/domains/*.toml",))

    def _controller(self):
        from maverick import self_improvement as si
        return si.SelfImprovementController(
            min_improvement=0.0, max_auto_rung="policy",
            frozen_fn=lambda: False, audit_fn=lambda **k: None)

    def test_disabled_is_a_noop(self, monkeypatch):
        monkeypatch.setattr(sm, "enabled", lambda: False)
        res = sm.propose_code_change(
            _patch("packages/maverick-core/maverick/domains/a.toml"),
            summary="s", baseline_score=0.5, candidate_score=0.9, samples=8,
            rollback="snap")
        assert res.ok is False
        assert "disabled" in res.reason

    def test_control_plane_edit_refused_before_the_gate(self, monkeypatch):
        # The boundary refuses a control-plane edit WITHOUT ever consulting the
        # promotion gate -- defense in depth.
        from maverick import self_improvement as si
        monkeypatch.setattr(sm, "enabled", lambda: True)
        called = []
        monkeypatch.setattr(si, "consider",
                            lambda *a, **k: called.append(1) or None)
        res = sm.propose_code_change(
            _patch("packages/maverick-core/maverick/self_improvement.py"),
            summary="s", baseline_score=0.5, candidate_score=0.9, samples=20,
            rollback="snap", surface=sm.EditableSurface(editable_globs=("**",)),
            rung="code")
        assert res.review.ok is False
        assert res.verdict is None
        assert called == []                 # the gate was never reached
        assert "protected" in res.reason

    def test_clean_change_promotes_through_the_gate(self, monkeypatch):
        # A low-rung edit to an allowlisted path that beats baseline promotes.
        from maverick import self_improvement as si
        monkeypatch.setattr(sm, "enabled", lambda: True)
        monkeypatch.setattr(si, "enabled", lambda: True)
        res = sm.propose_code_change(
            _patch("packages/maverick-core/maverick/domains/foo.toml"),
            summary="tune pack", baseline_score=0.5, candidate_score=0.9, samples=8,
            rollback="snap-1", surface=self._surface(), capability_widens=False,
            rung="prompt", controller=self._controller())
        assert res.review.ok is True
        assert res.ok is True, res.reason

    def test_code_rung_refused_without_human_approval(self, monkeypatch):
        # The surface is clean, but the code rung needs a human signature -- the
        # boundary passing does NOT bypass the promotion ladder.
        from maverick import self_improvement as si
        monkeypatch.setattr(sm, "enabled", lambda: True)
        monkeypatch.setattr(si, "enabled", lambda: True)
        res = sm.propose_code_change(
            _patch("packages/maverick-core/maverick/domains/foo.toml"),
            summary="risky", baseline_score=0.5, candidate_score=0.9, samples=20,
            rollback="snap-1", surface=self._surface(), capability_widens=False,
            rung="code", controller=self._controller())
        assert res.review.ok is True         # surface fine
        assert res.ok is False               # ...but the code rung gate refused
        assert "approval" in res.reason.lower() or "human" in res.reason.lower()

    def test_insufficient_evidence_refused(self, monkeypatch):
        from maverick import self_improvement as si
        monkeypatch.setattr(sm, "enabled", lambda: True)
        monkeypatch.setattr(si, "enabled", lambda: True)
        res = sm.propose_code_change(
            _patch("packages/maverick-core/maverick/domains/foo.toml"),
            summary="no gain", baseline_score=0.9, candidate_score=0.9, samples=8,
            rollback="snap-1", surface=self._surface(), capability_widens=False,
            rung="prompt", controller=self._controller())
        assert res.ok is False               # no improvement over baseline


class TestBypassRegression:
    """Every bypass the security review confirmed must now be refused."""

    def test_git_quoted_path_is_protected(self):
        # git C-quoted spelling of a control-plane path -> boundary un-quotes it.
        assert sm.is_protected('"a/maverick/self_improvement.py"') is True
        assert sm.is_protected('"b/packages/maverick-core/maverick/verifier.py"') is True

    def test_octal_escaped_path_is_protected(self):
        # \163 == 's': "\163elf_improvement.py" -> self_improvement.py
        assert sm.is_protected(r'"a/maverick/\163elf_improvement.py"') is True

    def test_double_slash_path_is_protected(self):
        assert sm.is_protected("maverick//self_improvement.py") is True
        assert sm.is_protected("packages/maverick-core/maverick//audit/writer.py") is True

    def test_dot_segment_path_is_protected(self):
        assert sm.is_protected("maverick/./self_improvement.py") is True

    def test_case_variant_is_protected(self):
        # macOS/Windows are case-insensitive: these resolve to the real file.
        assert sm.is_protected("packages/maverick-core/maverick/Self_Improvement.py") is True
        assert sm.is_protected("packages/maverick-core/maverick/AUDIT/writer.py") is True

    @pytest.mark.parametrize("mod", [
        "entitlements.py", "budget.py", "promotion_effect.py", "llm.py",
        "evaluator_evolution.py", "migration_governance.py", "approval_delegation.py",
    ])
    def test_newly_protected_governance_modules(self, mod):
        assert sm.is_protected(f"packages/maverick-core/maverick/{mod}") is True

    def test_uncanonicalizable_paths_fail_closed(self):
        for bad in ["/etc/passwd", "../../etc/passwd",
                    'packages/maverick-core/maverick/audit/../self_improvement.py']:
            assert sm.is_protected(bad) is True

    def test_review_refuses_quoted_real_path_behind_a_clean_decoy(self):
        # Decoy clean header + the real edit riding on quoted +++/--- lines.
        s = sm.EditableSurface(editable_globs=("**",))
        patch = ('diff --git a/x b/x\n'
                 '--- "a/maverick/verifier.py"\n'
                 '+++ "b/maverick/verifier.py"\n'
                 '@@ -1 +1 @@\n-o\n+n\n')
        r = sm.review_patch(patch, s)
        assert r.ok is False
        assert any("verifier.py" in p for p in r.protected)

    def test_review_refuses_double_slash_under_permissive_allowlist(self):
        s = sm.EditableSurface(editable_globs=("**",))
        patch = _patch("packages/maverick-core/maverick//self_improvement.py")
        r = sm.review_patch(patch, s)
        assert r.ok is False
