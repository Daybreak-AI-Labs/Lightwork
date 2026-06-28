"""SSE migration for goal events (council perf-seat fix).

Replaces the 2s/5s client-side polling chain with a Server-Sent Events
stream. The endpoint:

- Closes immediately on a terminal status with a final 'terminal' event.
- Emits SSE heartbeat comment lines while idle so proxies don't time
  out the connection.
- Returns 404 for an unknown goal.
"""
from __future__ import annotations

import json
import threading
import time

from fastapi.testclient import TestClient


def _client():
    from lightwork_dashboard.app import app
    return TestClient(app)


def test_sse_404_for_unknown_goal(monkeypatch, tmp_path):
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    from lightwork_dashboard import app as dash_app
    dash_app._world_cache.clear()
    client = _client()
    resp = client.get("/api/goal/9999/events/stream")
    assert resp.status_code == 404


def test_sse_terminal_status_closes_stream(monkeypatch, tmp_path):
    """A goal already 'done' before the stream opens yields one terminal event and ends."""
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal("done-goal", "x")
    wm.append_event(gid, "agent", "plan", "first")
    wm.set_goal_status(gid, "done", result="ok")
    from lightwork_dashboard import app as dash_app
    dash_app._world_cache.clear()

    client = _client()
    # TestClient streams over the wire; collect the body once the
    # generator hits the terminal event and closes.
    with client.stream("GET", f"/api/goal/{gid}/events/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if "event: terminal" in body:
                break

    assert "event: terminal" in body
    # The data payload after the terminal marker is JSON with status=done.
    term_section = body.split("event: terminal", 1)[1]
    data_line = next(
        line for line in term_section.splitlines() if line.startswith("data:")
    )
    payload = json.loads(data_line[len("data: "):])
    assert payload["status"] == "done"
    assert payload["terminal"] is True


def test_sse_streams_new_events_then_closes(monkeypatch, tmp_path):
    """Open the stream against an active goal; a background thread appends an
    event mid-stream and then sets the goal done. The stream must surface the
    new event AND the terminal close."""
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal("active-goal", "x")
    wm.set_goal_status(gid, "active")
    from lightwork_dashboard import app as dash_app
    dash_app._world_cache.clear()

    def driver():
        time.sleep(0.6)
        wm.append_event(gid, "agent", "plan", "midstream-event")
        time.sleep(0.6)
        wm.set_goal_status(gid, "done", result="ok")

    t = threading.Thread(target=driver, daemon=True)
    t.start()

    client = _client()
    body = ""
    with client.stream("GET", f"/api/goal/{gid}/events/stream") as resp:
        assert resp.status_code == 200
        deadline = time.time() + 5.0
        for chunk in resp.iter_text():
            body += chunk
            if "event: terminal" in body or time.time() > deadline:
                break

    assert "midstream-event" in body
    assert "event: terminal" in body
    t.join(timeout=1.0)


def test_legacy_polling_endpoint_still_works(monkeypatch, tmp_path):
    """SSE migration must NOT break the polling fallback."""
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal("g", "x")
    from lightwork_dashboard import app as dash_app
    dash_app._world_cache.clear()
    client = _client()
    resp = client.get(f"/api/goal/{gid}/events")
    assert resp.status_code == 200
    assert "events" in resp.json()


def test_chat_goal_template_uses_eventsource(monkeypatch, tmp_path):
    """Inline contract: the template wires `new EventSource(...)` against
    the new endpoint. Catches accidental regressions to polling-only."""
    from lightwork import world_model
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.delenv("MAVERICK_DASHBOARD_TOKEN", raising=False)
    wm = world_model.WorldModel(tmp_path / "world.db")
    gid = wm.create_goal("g", "x")
    from lightwork_dashboard import app as dash_app
    dash_app._world_cache.clear()
    client = _client()
    resp = client.get(f"/chat/goal/{gid}")
    assert resp.status_code == 200
    assert "new EventSource(" in resp.text
    assert "/events/stream?since=" in resp.text
    # The polling fallback for old browsers / blocked SSE is still there.
    assert "openPollingFallback" in resp.text
