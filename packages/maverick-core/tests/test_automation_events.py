"""Event-trigger poll engine: cursor-based dedup, payload flattening, and the
built-in http_json source (with an injected fetch -- no network)."""
from __future__ import annotations

import pytest
from maverick import automation_events as ev


def test_first_poll_is_baseline_only():
    # Newest-first feed; empty cursor -> record newest id, fire nothing.
    items = [{"id": "3"}, {"id": "2"}, {"id": "1"}]
    r = ev.diff_new_items(items, cursor="", id_field="id")
    assert r.events == [] and r.cursor == "3"


def test_only_items_after_cursor_fire_oldest_first():
    items = [{"id": "5"}, {"id": "4"}, {"id": "3"}, {"id": "2"}, {"id": "1"}]
    r = ev.diff_new_items(items, cursor="3", id_field="id")
    assert [e["id"] for e in r.events] == ["4", "5"]   # oldest-first
    assert r.cursor == "5"


def test_no_new_items_keeps_cursor():
    items = [{"id": "3"}, {"id": "2"}, {"id": "1"}]
    r = ev.diff_new_items(items, cursor="3", id_field="id")
    assert r.events == [] and r.cursor == "3"


def test_nested_fields_flatten_for_params():
    items = [{"id": "2", "issue": {"title": "boom", "labels": ["a", "b"]}}, {"id": "1"}]
    r = ev.diff_new_items(items, cursor="1", id_field="id")
    ev0 = r.events[0]
    assert ev0["issue.title"] == "boom"
    assert ev0["issue.labels"] == "a, b"


def test_dotted_id_field():
    items = [{"meta": {"id": "b"}}, {"meta": {"id": "a"}}]
    r = ev.diff_new_items(items, cursor="a", id_field="meta.id")
    assert r.cursor == "b" and len(r.events) == 1


def test_event_burst_is_bounded():
    items = [{"id": str(i)} for i in range(100, 0, -1)]  # 100 newest-first
    r = ev.diff_new_items(items, cursor="1", id_field="id")
    assert len(r.events) <= ev._MAX_EVENTS


def test_http_json_source_with_injected_fetch(monkeypatch):
    monkeypatch.setattr(
        ev, "_http_get_json",
        lambda url, headers=None, timeout=15.0: {"data": [{"id": "9", "who": "acme"}]})
    r = ev.HttpJsonSource().poll(
        {"url": "http://x/api", "items_path": "data", "id_field": "id"}, cursor="8")
    assert r.events[0]["who"] == "acme" and r.cursor == "9"


def test_http_json_requires_url():
    with pytest.raises(ev.EventSourceError):
        ev.HttpJsonSource().poll({}, cursor="")


def test_http_json_rejects_non_array(monkeypatch):
    monkeypatch.setattr(ev, "_http_get_json", lambda url, **k: {"data": {"not": "a list"}})
    with pytest.raises(ev.EventSourceError):
        ev.HttpJsonSource().poll({"url": "http://x", "items_path": "data"}, cursor="")


def test_registry_and_unknown_source():
    assert "http_json" in ev.available_sources()
    with pytest.raises(ev.EventSourceError):
        ev.get_source("nope")


def test_enabled_env_flag(monkeypatch):
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    assert ev.enabled() is True
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "0")
    assert ev.enabled() is False
