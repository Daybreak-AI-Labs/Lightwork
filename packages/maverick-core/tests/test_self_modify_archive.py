"""DGM lineage archive: bounded, diversity-aware, lineage-tracking, persistent.

The archive is the DGM's escape from the plateau -- it keeps a diverse population
(including worse ancestors) to branch from. These pin: id/digest stability,
score-ranked update-in-place, diversity eviction that keeps the best, lineage
walking, rolled-back dead ends excluded from best/sample, and round-trip persist.
"""
from __future__ import annotations

import random

from maverick.file_lock import private_path_is_restricted
from maverick.self_modify_archive import CodeArchive, CodeCandidate


def _cand(summary: str, patch: str, score: float, **kw) -> CodeCandidate:
    return CodeCandidate(summary=summary, patch=patch, score=score, **kw)


class TestCodeCandidate:
    def test_id_and_digest_are_derived(self):
        c = _cand("s", "+x", 1.0)
        assert len(c.id) == 12
        assert len(c.patch_sha256) == 64

    def test_same_content_same_id(self):
        assert _cand("s", "+x", 1.0).id == _cand("s", "+x", 2.0).id

    def test_different_patch_different_id(self):
        assert _cand("s", "+x", 1.0).id != _cand("s", "+y", 1.0).id

    def test_oversized_patch_body_is_dropped_digest_kept(self):
        big = "+" + ("a" * (300 * 1024))
        c = _cand("s", big, 1.0)
        assert c.patch == ""            # body dropped
        assert len(c.patch_sha256) == 64  # digest still pins it

    def test_improvement(self):
        assert _cand("s", "+x", 0.9, baseline_score=0.4).improvement == 0.5


class TestArchiveCore:
    def test_add_and_get(self):
        a = CodeArchive()
        c = a.add(_cand("s", "+x", 1.0))
        assert a.get(c.id) is c

    def test_update_in_place_on_better_score(self):
        a = CodeArchive()
        a.add(_cand("s", "+x", 1.0))
        a.add(_cand("s", "+x", 2.0))          # same id, higher score
        assert len(a.candidates) == 1
        assert a.best().score == 2.0

    def test_lower_score_does_not_downgrade(self):
        a = CodeArchive()
        a.add(_cand("s", "+x", 2.0))
        a.add(_cand("s", "+x", 1.0))
        assert a.best().score == 2.0

    def test_best_excludes_rolled_back(self):
        a = CodeArchive()
        a.add(_cand("hi", "+x", 5.0, promoted=True, rolled_back=True))
        a.add(_cand("lo", "+y", 1.0))
        assert a.best().summary == "lo"      # the rolled-back 5.0 is a dead end

    def test_best_none_when_all_rolled_back(self):
        a = CodeArchive()
        a.add(_cand("s", "+x", 5.0, rolled_back=True))
        assert a.best() is None

    def test_mark_promoted_and_rolled_back(self):
        a = CodeArchive()
        c = a.add(_cand("s", "+x", 1.0))
        a.mark_promoted(c.id)
        assert a.get(c.id).promoted is True
        a.mark_rolled_back(c.id)
        assert a.get(c.id).rolled_back is True


class TestLineage:
    def test_ancestry_chain(self):
        a = CodeArchive()
        root = a.add(_cand("root", "+r", 1.0))
        child = a.add(_cand("child", "+c", 2.0, parent_id=root.id))
        grand = a.add(_cand("grand", "+g", 3.0, parent_id=child.id))
        chain = [c.summary for c in a.lineage(grand.id)]
        assert chain == ["grand", "child", "root"]

    def test_broken_parent_link_terminates(self):
        a = CodeArchive()
        c = a.add(_cand("only", "+c", 1.0, parent_id="deadbeef0000"))
        assert [x.summary for x in a.lineage(c.id)] == ["only"]

    def test_cyclic_parent_is_safe(self):
        a = CodeArchive()
        c = a.add(_cand("self", "+c", 1.0))
        c.parent_id = c.id                   # pathological self-cycle
        assert a.lineage(c.id) == [c]


class TestDiversityEviction:
    def test_eviction_keeps_capacity_and_the_best(self):
        a = CodeArchive(capacity=3)
        for i in range(10):
            a.add(_cand(f"s{i}", f"+line_{i} token_{i}", float(i)))
        assert len(a.candidates) == 3
        assert a.best().score == 9.0         # the top scorer survives

    def test_sample_excludes_rolled_back(self):
        a = CodeArchive()
        a.add(_cand("dead", "+x", 100.0, rolled_back=True))
        a.add(_cand("live", "+y", 1.0))
        rng = random.Random(0)
        picks = {a.sample(rng).summary for _ in range(20)}
        assert picks == {"live"}

    def test_sample_none_when_empty(self):
        assert CodeArchive().sample() is None

    def test_patch_distance_identical_is_zero(self):
        c = _cand("s", "+a b c", 1.0)
        assert CodeArchive.patch_distance(c, c) == 0.0

    def test_patch_distance_disjoint_is_one(self):
        d = CodeArchive.patch_distance(
            _cand("s", "+a b c", 1.0), _cand("t", "+x y z", 1.0))
        assert d == 1.0


class TestPersistence:
    def test_round_trip(self, tmp_path):
        a = CodeArchive(capacity=7)
        root = a.add(_cand("root", "+r", 1.0, promoted=True))
        a.add(_cand("child", "+c token", 2.0, parent_id=root.id, samples=8,
                    capability_widens=True, reasons=("network client",)))
        path = tmp_path / "arch.json"
        a.save(path)
        b = CodeArchive.load(path)
        assert b.capacity == 7
        assert len(b.candidates) == 2
        loaded = b.get(root.id)
        assert loaded.promoted is True
        child = next(c for c in b.candidates if c.summary == "child")
        assert child.parent_id == root.id
        assert child.capability_widens is True
        assert child.reasons == ("network client",)

    def test_load_missing_is_fresh(self, tmp_path):
        assert CodeArchive.load(tmp_path / "nope.json").candidates == []

    def test_load_corrupt_is_fresh(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        assert CodeArchive.load(p).candidates == []

    def test_saved_file_is_owner_only(self, tmp_path):
        p = tmp_path / "arch.json"
        CodeArchive().save(p)
        assert private_path_is_restricted(p, 0o600)
