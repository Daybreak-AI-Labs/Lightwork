"""Single stdin reader with bounded owned tool dispatch and elicitation routing."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import wait

from maverick import killswitch

from .execution import ExecutionCancelled, ExecutionHandle, RequestAdmissionError, RequestExecutions
from .tasks import TaskError

log = logging.getLogger(__name__)

class StdioSession:
    def __init__(self, server):
        self.server = server
        self.loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._pending = {}
        self._elicitation: dict[str, tuple[threading.Event, dict]] = {}
        self._closed = False
        self._thread = threading.Thread(target=self._serve, name="mcp-stdio-dispatch", daemon=True)
        self._thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    def route(self, message: dict) -> bool:
        method = message.get("method")
        request_id = message.get("id")
        if method is None and isinstance(request_id, str):
            with self._lock:
                pending = self._elicitation.get(request_id)
            if pending is not None:
                event, result = pending
                result.update(message)
                event.set()
            return True
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return False
        if method == "notifications/cancelled":
            try:
                key = RequestExecutions._key("stdio", params.get("requestId"))
            except RequestAdmissionError:
                return True
            with self._lock:
                pending = self._pending.get(key)
            if pending is not None:
                pending[0].cancel()
            return True
        if method not in {"tools/call", "tasks/result"}:
            return False
        # Task creation only admits a detached job; it does not run the tool on
        # this reader. Keeping this short acknowledgment synchronous avoids a
        # needless ownership hop and preserves request ordering for task IDs.
        if method == "tools/call" and isinstance(params.get("task"), dict) and self.server._tasks_enabled:
            return False
        try:
            key = RequestExecutions._key("stdio", request_id)
        except RequestAdmissionError as exc:
            self.server._send_error(None, -32602, str(exc))
            return True
        with self._lock:
            if self._closed or len(self._pending) >= 64 or key in self._pending:
                if "id" in message:
                    self.server._send_error(request_id, -32602, "stdio request capacity or duplicate active id")
                return True
            handle = ExecutionHandle()
            future = asyncio.run_coroutine_threadsafe(self._dispatch(message, handle), self.loop)
            self._pending[key] = handle, future
        future.add_done_callback(lambda completed: self._done(key, completed))
        return True

    def _done(self, key, future):
        with self._lock:
            self._pending.pop(key, None)
        # Retrieve cancellation/errors so transport teardown owns all outcomes.
        if not future.cancelled():
            future.exception()

    async def _dispatch(self, message, handle):
        from .server import _ProtocolError

        request_id = message.get("id")
        params = message.get("params") or {}
        with self.server.resource_update_scope(set(self.server._subscriptions)):
            try:
                if message["method"] == "tasks/result":
                    result = await self.server._requests.run_async(
                        "stdio", request_id, lambda: self.server.handle_tasks_result_async(params),
                        handle=handle,
                    )
                else:
                    result = await self.server._requests.run(
                        "stdio", request_id, lambda: self.server.handle_tools_call(params),
                        handle=handle,
                    )
            except (TaskError, _ProtocolError) as exc:
                if "id" in message:
                    self.server._send_error(request_id, exc.code, exc.message)
            except ExecutionCancelled:
                if "id" in message:
                    self.server._send_error(request_id, -32800, "request cancelled; prior effects may be unknown")
            except RequestAdmissionError as exc:
                if "id" in message:
                    self.server._send_error(request_id, -32602, str(exc))
            except Exception as exc:
                if "id" in message:
                    self.server._send_error(request_id, -32603, f"internal error: {type(exc).__name__}")
            else:
                if "id" in message:
                    self.server._send_result(request_id, result)
                self.server._flush_resource_updates()

    def elicit(self, message: dict, timeout: float) -> dict | None:
        request_id = message["id"]
        event, reply = threading.Event(), {}
        with self._lock:
            if self._closed:
                return None
            self._elicitation[request_id] = event, reply
        try:
            self.server._send(message)
            deadline = time.monotonic() + timeout
            while not event.wait(max(0.0, min(0.05, deadline - time.monotonic()))):
                killswitch.check()
                if time.monotonic() >= deadline:
                    return None
            killswitch.check()
            if "error" in reply:
                return None
            result = reply.get("result")
            return result if isinstance(result, dict) else None
        finally:
            with self._lock:
                self._elicitation.pop(request_id, None)

    def close(self):
        with self._lock:
            self._closed = True
            pending = tuple(future for _, future in self._pending.values())
            elicitations = tuple(self._elicitation.values())
        # Let already-completed responses flush, then treat EOF as disconnect.
        if pending:
            _, remaining = wait(pending, timeout=0.1)
            for future in remaining:
                future.cancel()
        for event, _ in elicitations:
            event.set()

        async def drain():
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        drained = asyncio.run_coroutine_threadsafe(drain(), self.loop)
        drained.add_done_callback(lambda _: self.loop.call_soon_threadsafe(self.loop.stop))
        try:
            drained.result(timeout=2)
        except TimeoutError:
            # An uninterruptible synchronous authority check may still own the
            # loop. It stops only once its pending coroutine cleanup completes.
            log.warning("stdio dispatch cleanup remains in progress")
        self._thread.join(timeout=2)
