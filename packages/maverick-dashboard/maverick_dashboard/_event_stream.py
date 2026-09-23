"""Shared durable replay rules; wire formats remain transport-specific."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

TERMINAL_RUN_STATUSES = frozenset({"done", "completed", "failed", "cancelled", "blocked", "error"})


def replay_cursor(since: object = 0, last_event_id: object = None) -> int:
    """Honor reconnect progress without inventing IDs outside SQLite's range."""
    cursor = 0
    for value in (since, last_event_id):
        try:
            cursor = max(cursor, min(int(value), 2**63 - 1))
        except (TypeError, ValueError, OverflowError):
            pass
    return cursor


def event_page(world, goal_id: int, since: int, limit: int):
    """Read one bounded page with an observed indication of remaining replay."""
    limit = max(1, min(limit, 500))
    events = world.goal_events(goal_id, since_id=since, limit=limit + 1)
    return events[:limit], len(events) > limit


@dataclass
class StreamCadence:
    """Monotonic deadlines; polling backoff never stretches heartbeat cadence."""

    started: float
    last_frame: float
    poll: float = 0.5
    max_poll: float = 5.0
    heartbeat: float = 30.0
    lifetime: float = 300.0
    interval: float = 0.5

    def expired(self, now):
        return now >= self.started + self.lifetime

    def heartbeat_due(self, now):
        return now >= self.last_frame + self.heartbeat

    def sent(self, now):
        self.last_frame = now

    def backoff(self, activity):
        self.interval = self.poll if activity else min(self.max_poll, self.interval * 1.5)

    def delay(self, now):
        return max(0.0, min(self.interval, self.last_frame + self.heartbeat - now,
                            self.started + self.lifetime - now))


class StreamLease:
    """Release once from the generator and from response cancellation paths."""

    def __init__(self, semaphore):
        self.semaphore = semaphore
        self.released = False

    def release(self):
        if not self.released:
            self.released = True
            self.semaphore.release()


class LeasedStreamingResponse(StreamingResponse):
    def __init__(self, content, *, lease, **kwargs):
        super().__init__(content, **kwargs)
        self.lease = lease

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.lease.release()


def stream_state(world, goal_id, *, include_result=False):
    """Backends project status; compatibility embedders may implement get_goal."""
    method = getattr(world, "goal_stream_state", None)
    if method is None:
        return world.get_goal(goal_id)
    return method(goal_id, include_result=include_result)


def _event_dict(event):
    return {"id": event.id, "agent": event.agent, "kind": event.kind,
            "content": event.content, "ts": event.ts}


def sse_event(event) -> str:
    kind = str(event.kind or "message").replace("\n", " ").replace("\r", " ")
    return f"id: {event.id}\nevent: {kind}\ndata: {json.dumps(_event_dict(event))}\n\n"


async def goal_stream(request, world, goal_id, *, since, lease, visible,
                      legacy=False, limit=0, max_batch=200, poll=0.5,
                      max_poll=5.0, heartbeat=30.0, lifetime=300.0,
                      clock=None, sleep=asyncio.sleep):
    """One bounded pull/replay machine; transport adapters retain their frames.

    No prefetch queue: backpressure suspends the generator before another read.
    Durable IDs, not an in-process broadcaster, remain the reconnect authority.
    """
    clock = clock or asyncio.get_running_loop().time
    now = clock()
    cadence = StreamCadence(now, now, poll, max_poll, heartbeat, lifetime, poll)
    last, sent = since, 0
    try:
        yield "retry: 3000\n\n" if legacy else ": connected\n\n"
        cadence.sent(clock())
        while True:
            if await request.is_disconnected():
                return
            if cadence.expired(clock()):
                yield 'event: timeout\ndata: {"detail": "stream lifetime exceeded"}\n\n'
                return
            current = await run_in_threadpool(stream_state, world, goal_id)
            if current is None or not visible(current):
                yield 'event: error\ndata: {"detail": "goal vanished"}\n\n'
                return
            events = await run_in_threadpool(world.goal_events, goal_id, since_id=last, limit=max_batch)
            if legacy and (events or current.status in TERMINAL_RUN_STATUSES):
                current = await run_in_threadpool(stream_state, world, goal_id, include_result=True)
                if current is None or not visible(current):
                    yield 'event: error\ndata: {"detail": "goal vanished"}\n\n'
                    return
            if events:
                if legacy:
                    last = events[-1].id
                    payload = {"status": current.status, "result": current.result or "",
                               "next_id": last, "terminal": False,
                               "events": [_event_dict(event) for event in events]}
                    yield f"id: {last}\ndata: {json.dumps(payload)}\n\n"
                    cadence.sent(clock())
                else:
                    for event in events:
                        last = event.id
                        yield sse_event(event)
                        cadence.sent(clock())
                        sent += 1
                        if limit and sent >= limit:
                            return
            elif cadence.heartbeat_due(clock()):
                yield ": heartbeat\n\n"
                cadence.sent(clock())
            if current.status in TERMINAL_RUN_STATUSES:
                if len(events) >= max_batch:
                    continue
                if legacy:
                    payload = {"status": current.status, "result": current.result or "",
                               "next_id": last, "events": [], "terminal": True}
                    yield f"id: {last}\nevent: terminal\ndata: {json.dumps(payload)}\n\n"
                else:
                    payload = {"status": current.status, "next_id": last, "terminal": True}
                    yield f"event: end\ndata: {json.dumps(payload)}\n\n"
                return
            cadence.backoff(bool(events))
            await sleep(cadence.delay(clock()))
    except asyncio.CancelledError:
        return
    finally:
        lease.release()
