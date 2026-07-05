"""First-class email / form / file event sources (poll model)."""
from __future__ import annotations

import time

import pytest
from maverick import automation_events as ev

# ---- file_dir ---------------------------------------------------------------

def test_file_dir_baseline_then_fires_new_file(tmp_path):
    (tmp_path / "a.txt").write_text("one")
    src = ev.get_source("file_dir")
    cfg = {"path": str(tmp_path)}
    base = src.poll(cfg, "")                       # baseline: record, fire nothing
    assert base.events == [] and base.cursor
    time.sleep(0.01)
    (tmp_path / "b.txt").write_text("two")
    out = src.poll(cfg, base.cursor)
    names = sorted(e["name"] for e in out.events)
    assert names == ["b.txt"]                      # only the new file fired
    assert out.events[0]["path"].endswith("b.txt")


def test_file_dir_glob_filters(tmp_path):
    src = ev.get_source("file_dir")
    cfg = {"path": str(tmp_path), "glob": "*.csv"}
    base = src.poll(cfg, "")
    time.sleep(0.01)
    (tmp_path / "keep.csv").write_text("x")
    (tmp_path / "skip.txt").write_text("y")
    out = src.poll(cfg, base.cursor)
    assert [e["name"] for e in out.events] == ["keep.csv"]


def test_file_dir_missing_dir_errors(tmp_path):
    with pytest.raises(ev.EventSourceError):
        ev.get_source("file_dir").poll({"path": str(tmp_path / "nope")}, "")


# ---- imap_email -------------------------------------------------------------

def test_imap_baseline_then_fires_new_mail(monkeypatch):
    calls = {"since": []}

    def fake_fetch(config, since_uid):
        calls["since"].append(since_uid)
        if since_uid == 0:
            return [], 5                            # baseline: high-water 5
        return ([{"id": "6", "uid": 6, "from": "a@b.com",
                  "subject": "hi", "date": "", "message_id": "<6>"}], 6)

    monkeypatch.setattr(ev, "_fetch_imap", fake_fetch)
    src = ev.get_source("imap_email")
    base = src.poll({"host": "h", "username": "u", "password_env": "P"}, "")
    assert base.events == [] and base.cursor == "5"
    out = src.poll({"host": "h", "username": "u", "password_env": "P"}, base.cursor)
    assert len(out.events) == 1 and out.events[0]["subject"] == "hi"
    assert out.cursor == "6"
    assert calls["since"] == [0, 5]


def test_imap_requires_config():
    with pytest.raises(ev.EventSourceError):
        # _fetch_imap validates required keys; password_env missing
        ev.get_source("imap_email").poll({"host": "h", "username": "u"}, "1")


# ---- form -------------------------------------------------------------------

def test_form_source_fires_new_submissions(tmp_path, monkeypatch):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from maverick import form_store
    src = ev.get_source("form")
    cfg = {"token": "signup"}
    form_store.append("signup", {"email": "old@x.com"})
    base = src.poll(cfg, "")                        # baseline over existing rows
    assert base.events == [] and base.cursor == "1"
    form_store.append("signup", {"email": "new@x.com", "plan": "pro"})
    out = src.poll(cfg, base.cursor)
    assert len(out.events) == 1
    assert out.events[0]["email"] == "new@x.com" and out.events[0]["plan"] == "pro"
    assert out.cursor == "2"


def test_form_source_requires_token():
    with pytest.raises(ev.EventSourceError):
        ev.get_source("form").poll({}, "")


def test_new_sources_are_registered():
    for name in ("file_dir", "imap_email", "form"):
        assert name in ev.available_sources()
