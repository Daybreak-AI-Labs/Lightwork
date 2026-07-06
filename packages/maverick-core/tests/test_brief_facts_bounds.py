"""Bounds on the orchestrator brief's facts block.

Regression target: _brief_facts_block concatenated EVERY persisted fact with
no count cap and no per-value length cap — and the world_model feature gate
fails soft to ON — so a tenant accumulating facts (kv_memory, dashboard
set_fact, MCP) silently inflated the standing brief of every future goal.
The QA block right next to it has had _QA_MAX_* caps all along.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from maverick.orchestrator import _brief_facts_block
from maverick.world_model import WorldModel


def _world() -> WorldModel:
    return WorldModel(Path(tempfile.mkdtemp()) / "w.db")


def test_small_fact_set_unchanged():
    w = _world()
    w.upsert_fact("region", "eu-west-1")
    block = _brief_facts_block(w, goal_id=1, shield=None)
    assert "region: eu-west-1" in block


def test_fact_count_capped_newest_kept(monkeypatch):
    monkeypatch.setenv("MAVERICK_BRIEF_FACTS_MAX", "5")
    w = _world()
    for i in range(12):
        w.upsert_fact(f"k{i:02d}", f"v{i}")
    block = _brief_facts_block(w, goal_id=1, shield=None)
    rendered = [ln for ln in block.splitlines() if ln.startswith("  k")]
    assert len(rendered) == 5
    assert "k11: v11" in block          # newest kept
    assert "k00: v0" not in block       # oldest dropped
    assert "7 more fact(s) omitted" in block


def test_long_value_truncated(monkeypatch):
    monkeypatch.setenv("MAVERICK_BRIEF_FACT_VALUE_CHARS", "100")
    w = _world()
    w.upsert_fact("dump", "x" * 5_000)
    block = _brief_facts_block(w, goal_id=1, shield=None)
    assert len(block) < 400
    assert "truncated" in block


def test_defaults_are_generous_enough_for_normal_use():
    w = _world()
    for i in range(10):
        w.upsert_fact(f"pref{i}", "a short preference value")
    block = _brief_facts_block(w, goal_id=1, shield=None)
    assert "omitted" not in block and "truncated" not in block
