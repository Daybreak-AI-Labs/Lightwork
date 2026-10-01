"""Hosted-form submissions stay bounded and receive unique cursors."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))


def test_concurrent_submissions_have_unique_monotonic_sequences(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import form_store

    with ThreadPoolExecutor(max_workers=12) as pool:
        sequences = list(pool.map(lambda n: form_store.append("lead", {"n": n}), range(32)))
    assert sorted(sequences) == list(range(1, 33))
    events, cursor = form_store.since("lead", "0")
    assert cursor == "32"
    assert [event["_seq"] for event in events] == list(range(1, 33))


def test_full_form_refuses_a_new_submission_without_losing_old_ones(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import form_store

    monkeypatch.setattr(form_store, "_MAX_LINES", 2)
    assert form_store.append("lead", {"n": "1"}) == 1
    assert form_store.append("lead", {"n": "2"}) == 2
    with pytest.raises(form_store.FormStoreFull):
        form_store.append("lead", {"n": "3"})
    events, cursor = form_store.since("lead", "0")
    assert cursor == "2"
    assert [event["n"] for event in events] == ["1", "2"]


def test_legacy_punctuation_token_uses_its_existing_file(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    from maverick import form_store

    old_path = tmp_path / "forms" / "a-b.ndjson"
    old_path.parent.mkdir()
    old_path.write_text('{"seq":1,"ts":0.0,"fields":{"n":"old"}}\n', encoding="utf-8")
    assert form_store.append("a.b", {"n": "new"}) == 2
    events, _ = form_store.since("a.b", "0")
    assert [event["n"] for event in events] == ["old", "new"]
