"""A worker must not reuse another worker's invalidated cache generation."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from maverick.cache import epoch, tool


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_TOOL_CACHE", "1")
    monkeypatch.setenv("MAVERICK_TOOL_CACHE_SNAPSHOT", "1")
    monkeypatch.setenv("MAVERICK_TOOL_CACHE_BACKEND", "local")
    tool.reset()
    yield
    tool.reset()


def test_other_process_fences_local_snapshot_and_late_read():
    read = SimpleNamespace(name="read_file", parallel_safe=True)
    before = tool.generation()
    tool.store_cached(read, {}, "old", generation=before)
    assert tool.save_snapshot() == 1
    subprocess.run([sys.executable, "-c", "from maverick.cache.epoch import local_epoch; assert local_epoch(advance=True)"],
                   env=os.environ.copy(), check=True, timeout=30)
    assert tool.get_cached(read, {}) == (False, None)
    tool.store_cached(read, {}, "late old", generation=before)
    assert tool.get_cached(read, {}) == (False, None)
    tool.reset()
    assert tool.get_cached(read, {}) == (False, None)
    tool.store_cached(read, {}, "fresh", generation=tool.generation())
    assert tool.get_cached(read, {}) == (True, "fresh")


def test_unavailable_epoch_bypasses_cached_values(monkeypatch):
    read = SimpleNamespace(name="read_file", parallel_safe=True)
    tool.store_cached(read, {}, "old")
    monkeypatch.setattr(epoch, "local_epoch", lambda **kwargs: None)
    assert tool.get_cached(read, {}) == (False, None)
    tool.store_cached(read, {}, "unverified")
    assert tool.stats()["size"] == 0
