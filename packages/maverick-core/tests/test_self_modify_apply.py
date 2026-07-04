"""Real apply + rollback for a promoted change (Gap 3).

Pins that the reversibility handle is a real workspace snapshot: an applied
change can be mechanically reverted, a failed apply auto-reverts, and applying
without a rollback point is refused (fail closed).
"""
from __future__ import annotations

from pathlib import Path

from maverick import self_modify_apply as ap


def _tree(root: Path) -> Path:
    t = root / "tree"
    (t / "pkg").mkdir(parents=True)
    (t / "pkg" / "a.py").write_text("VALUE = 1\n", encoding="utf-8")
    return t


def _patch() -> str:
    return ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
            "@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 99\n")


def _store(root: Path) -> Path:
    s = root / "snaps"
    s.mkdir()
    return s


class TestRollbackPoint:
    def test_creates_a_snapshot_id(self, tmp_path):
        snap = ap.create_rollback_point(_tree(tmp_path), store=_store(tmp_path))
        assert snap and snap.startswith("snap-")


class TestApplyChange:
    def test_applies_and_returns_real_rollback(self, tmp_path):
        tree, store = _tree(tmp_path), _store(tmp_path)
        res = ap.apply_change(_patch(), tree=tree, store=store)
        assert res.ok is True
        assert (tree / "pkg" / "a.py").read_text() == "VALUE = 99\n"
        assert res.rollback is not None
        # the handle mechanically restores the pre-change tree
        assert res.rollback.revert() is True
        assert (tree / "pkg" / "a.py").read_text() == "VALUE = 1\n"

    def test_reuses_a_preexisting_snapshot_handle(self, tmp_path):
        tree, store = _tree(tmp_path), _store(tmp_path)
        snap = ap.create_rollback_point(tree, store=store)   # taken for the gate
        res = ap.apply_change(_patch(), tree=tree, snapshot_id=snap, store=store)
        assert res.ok and res.snapshot_id == snap

    def test_failed_apply_auto_reverts(self, tmp_path):
        tree, store = _tree(tmp_path), _store(tmp_path)
        bad = ("diff --git a/pkg/a.py b/pkg/a.py\n--- a/pkg/a.py\n+++ b/pkg/a.py\n"
               "@@ -1 +1 @@\n-DOES_NOT_MATCH\n+x\n")
        res = ap.apply_change(bad, tree=tree, store=store)
        assert res.ok is False
        assert (tree / "pkg" / "a.py").read_text() == "VALUE = 1\n"   # untouched

    def test_apply_refused_without_a_rollback_point(self, tmp_path, monkeypatch):
        tree, store = _tree(tmp_path), _store(tmp_path)
        monkeypatch.setattr(ap, "create_rollback_point", lambda *a, **k: None)
        res = ap.apply_change(_patch(), tree=tree, store=store)
        assert res.ok is False and "no rollback point" in res.reason
        assert (tree / "pkg" / "a.py").read_text() == "VALUE = 1\n"


class TestRevert:
    def test_revert_restores_snapshot(self, tmp_path):
        tree, store = _tree(tmp_path), _store(tmp_path)
        snap = ap.create_rollback_point(tree, store=store)
        (tree / "pkg" / "a.py").write_text("MUTATED\n", encoding="utf-8")
        assert ap.revert_change(snap, tree=tree, store=store) is True
        assert (tree / "pkg" / "a.py").read_text() == "VALUE = 1\n"

    def test_revert_bad_id_is_false_not_raise(self, tmp_path):
        assert ap.revert_change("snap-9999", tree=_tree(tmp_path),
                                store=_store(tmp_path)) is False
