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


def test_burst_over_cap_drains_oldest_first_without_loss():
    # 40 new items since cursor "0"; the cap is 25. The oldest 25 must fire this
    # poll and the cursor must stop at the newest FIRED item so the remaining 15
    # drain on the next poll -- no item is skipped.
    items = [{"id": str(i)} for i in range(40, 0, -1)]  # 40..1, newest-first
    first = ev.diff_new_items(items, cursor="0", id_field="id")
    assert [e["id"] for e in first.events] == [str(i) for i in range(1, 26)]  # 1..25
    assert first.cursor == "25"
    # Next poll continues from the cursor: the remaining 26..40 fire, none lost.
    second = ev.diff_new_items(items, cursor=first.cursor, id_field="id")
    assert [e["id"] for e in second.events] == [str(i) for i in range(26, 41)]
    assert second.cursor == "40"


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


_RSS = """<?xml version="1.0"?><rss><channel>
  <item><guid>b</guid><title>Newer</title><link>http://x/b</link></item>
  <item><guid>a</guid><title>Older</title><link>http://x/a</link></item>
</channel></rss>"""


def test_rss_source_parses_and_dedups(monkeypatch):
    monkeypatch.setattr(ev, "_http_get_text", lambda url, headers=None, timeout=15.0: _RSS)
    r = ev.RssSource().poll({"url": "http://x/feed"}, cursor="a")   # 'a' already seen
    assert r.cursor == "b" and len(r.events) == 1
    assert r.events[0]["title"] == "Newer" and r.events[0]["id"] == "b"


def test_rss_refuses_doctype_entity(monkeypatch):
    evil = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><rss></rss>'
    monkeypatch.setattr(ev, "_http_get_text", lambda url, headers=None, timeout=15.0: evil)
    with pytest.raises(ev.EventSourceError):
        ev.RssSource().poll({"url": "http://x"}, cursor="1")


def test_registry_and_unknown_source():
    assert "http_json" in ev.available_sources()
    assert "rss" in ev.available_sources()
    with pytest.raises(ev.EventSourceError):
        ev.get_source("nope")


def test_enabled_env_flag(monkeypatch):
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "1")
    assert ev.enabled() is True
    monkeypatch.setenv("MAVERICK_EVENT_TRIGGERS", "0")
    assert ev.enabled() is False
