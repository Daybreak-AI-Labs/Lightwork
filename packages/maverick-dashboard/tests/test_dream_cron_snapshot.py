"""The scheduled dream cycle takes the same rollback snapshot as `maverick dream`."""
from __future__ import annotations

import pytest
from maverick import dreaming
from maverick_dashboard import automation_queue as aq


@pytest.fixture
def calls(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(aq, "_run_for_active_tenants", lambda _op, fn: fn() or True)
    monkeypatch.setattr(aq, "_world", lambda: object())
    monkeypatch.setattr(dreaming, "enabled", lambda: True)
    monkeypatch.setattr(dreaming, "snapshot_learning_state",
                        lambda keep_last: seen.append(f"snapshot:{keep_last}"))
    monkeypatch.setattr(dreaming, "dream_cycle", lambda _w: seen.append("cycle"))
    return seen


def test_snapshot_precedes_the_cycle(calls, monkeypatch):
    monkeypatch.setattr(dreaming, "settings", lambda: {"snapshot_keep_last": 3})
    aq._handle_dream()
    assert calls == ["snapshot:3", "cycle"]


def test_snapshots_off_is_honoured(calls, monkeypatch):
    monkeypatch.setattr(dreaming, "settings", lambda: {"snapshots": False})
    aq._handle_dream()
    assert calls == ["cycle"]


def test_failed_snapshot_blocks_the_cycle(calls, monkeypatch):
    monkeypatch.setattr(dreaming, "settings", dict)

    def boom(keep_last):
        raise OSError("disk full")

    monkeypatch.setattr(dreaming, "snapshot_learning_state", boom)
    with pytest.raises(OSError):
        aq._handle_dream()
    assert calls == []
