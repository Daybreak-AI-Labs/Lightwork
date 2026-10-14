"""Concurrency regression tests for the screenshot custody chain."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from maverick.screenshot_seal import seal, verify_ledger


def test_parallel_seals_form_one_valid_chain(tmp_path):
    captures = tmp_path / "captures"
    captures.mkdir()
    paths = []
    for index in range(16):
        path = captures / f"frame-{index}.png"
        path.write_bytes(b"frame:" + str(index).encode())
        paths.append(path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        entries = list(pool.map(lambda path: seal(path, key="test-key"), paths))

    assert len(entries) == len(paths)
    report = verify_ledger(captures, key="test-key")
    assert report["ok"] is True
    assert report["entries"] == len(paths)
    assert report["broken_at"] is None
