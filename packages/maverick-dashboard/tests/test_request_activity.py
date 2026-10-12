"""What counts as the dashboard serving requests right now."""
from __future__ import annotations

from fastapi import FastAPI, WebSocket
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from maverick_dashboard import request_activity
from starlette.middleware.gzip import GZipMiddleware


def _app(seen):
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1)  # inside, as in the dashboard
    app.add_middleware(request_activity.RequestActivityMiddleware)

    @app.get("/work")
    def work():
        seen.append(("work", request_activity.in_flight()))
        return {"ok": True}

    @app.get("/stream")
    def stream():
        def events():
            seen.append(("stream", request_activity.in_flight()))
            yield "data: one\n\n"
            seen.append(("stream", request_activity.in_flight()))
            yield "data: two\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.websocket("/socket")
    async def socket(websocket: WebSocket):
        await websocket.accept()
        seen.append(("socket", request_activity.in_flight()))
        await websocket.close()

    return app


def test_ordinary_requests_count_while_event_streams_and_sockets_do_not(monkeypatch):
    # A page's open event stream or socket kept the dashboard "busy" forever,
    # so the schema build and garbage rounds that wait for quiet never ran.
    # One request is already in flight elsewhere, so a request finished twice
    # would show as a missing count.
    monkeypatch.setattr(request_activity, "_in_flight", 1)
    monkeypatch.setattr(request_activity, "_last_start", 0.0)
    seen = []
    client = TestClient(_app(seen))
    assert client.get("/work").json() == {"ok": True}
    # A browser's EventSource asks for the stream, so it is never counted, even
    # while GZip holds the response start until the first event.
    events = client.get("/stream", headers={"Accept": "text/event-stream"})
    assert events.text == "data: one\n\ndata: two\n\n"
    # Without the header the stream counts until its response starts.
    assert client.get("/stream").text == "data: one\n\ndata: two\n\n"
    with client.websocket_connect("/socket"):
        pass
    assert seen == [("work", 2), ("stream", 1), ("stream", 1), ("stream", 2), ("stream", 1), ("socket", 1)]
    assert request_activity.in_flight() == 1
