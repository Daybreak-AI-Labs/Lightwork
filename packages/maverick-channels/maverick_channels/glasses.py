"""Glasses / wearable channel — Even Realities G2 "bring your own agent" bridge.

Smart glasses do on-device speech-to-text and render short text on a HUD, but
impose a hard request deadline (~30 s on the G2). This adapter implements the
**ack-then-run** pattern that makes a long-horizon agent usable on such a device:

  - a **quick** utterance (a fact, the weather, short chat) is answered
    synchronously within the deadline and shown on the HUD;
  - a **long** task (write code, research, deploy) is **acked immediately**
    ("Got it — working on it; I'll send the result to <channel>"), run in the
    background, and the full result is delivered to a **secondary channel**
    (e.g. Telegram) when done.

The classifier and the deadline split are the whole point and are pure +
unit-tested. The transport is a thin shim over the existing inbound
``POST /webhook/start`` (self-hostable as a small edge service or a Worker), and
long-task results ride the existing outbound delivery — no new server surface.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from .base import Channel, IncomingMessage, is_allowed, normalize_allowlist
from .delivery import DeliveryNotSent, DeliveryUnknown, _record_id
from .transport_worker import TransportWorker

log = logging.getLogger(__name__)

_DEFAULT_DEADLINE_S = 22.0   # under the G2's ~30s timeout, leaving headroom
_HUD_MAX_CHARS = 240          # the HUD shows short text; keep acks/answers tight

# An utterance is a *long* task if it asks for work that can't finish in the
# deadline. Conservative: only clear "do work" verbs trip it; everything else is
# answered synchronously.
_LONG_TASK_RE = re.compile(
    r"\b(write|build|create|generate|implement|code|program|"
    r"research|investigate|analy[sz]e|deploy|ship|release|"
    r"refactor|migrate|scrape|crawl|compile|train)\b",
    re.IGNORECASE,
)


def classify_utterance(text: str) -> str:
    """``"long"`` if the utterance asks for background work, else ``"quick"``."""
    return "long" if _LONG_TASK_RE.search(text or "") else "quick"


def ack_message(secondary: str | None) -> str:
    """The immediate HUD ack for a long task."""
    where = f" I'll send the result to {secondary}." if secondary else \
        " Configure a secondary channel to receive the saved result."
    return f"Got it — working on it.{where}"[:_HUD_MAX_CHARS]


def hud_trim(text: str) -> str:
    """Fit a reply to the HUD's short-text constraint."""
    text = (text or "").strip()
    return text if len(text) <= _HUD_MAX_CHARS else text[: _HUD_MAX_CHARS - 1] + "…"


class GlassesChannel(Channel):
    name = "glasses"

    def __init__(
        self,
        handler: Callable[[IncomingMessage], Awaitable[str]],
        *,
        deadline_s: float = _DEFAULT_DEADLINE_S,
        secondary_channel: str | None = None,
        deliver: Callable[[str, str], Awaitable[str | None]] | None = None,
        spawn: Callable[[Awaitable], object] | None = None,
        allowed_user_ids=None,
    ):
        super().__init__(handler)
        self.deadline_s = float(deadline_s)
        if not 0 < self.deadline_s <= 25:
            raise ValueError("Glasses response deadline must be between zero and 25 seconds")
        self.secondary_channel = secondary_channel
        # Where a finished long-task result is delivered (the secondary channel).
        self._deliver = deliver
        # Strong refs to in-flight delivery tasks: asyncio only holds a *weak*
        # ref to a bare create_task() result, so an unreferenced background task
        # may be garbage-collected mid-flight and the user would silently never
        # get the long-task result (the whole point of this channel). Keep them.
        self._background_tasks: set = set()
        self._spawn = spawn
        self._worker = None
        self._observers = {}
        self.allowed_user_ids = normalize_allowlist(allowed_user_ids, "GLASSES_ALLOWED_USER_IDS")
        if not self.allowed_user_ids:
            raise ValueError("Set GLASSES_ALLOWED_USER_IDS to restrict who can drive the agent")

    def _account(self):
        return "glasses:" + (self.secondary_channel or "hud")

    def _ensure_worker(self):
        if self._worker is None or (self._worker.task is not None and self._worker.task.done()):
            account = self._account()
            self._worker = TransportWorker(self, account=account, send=self._send_saved,
                authorize=lambda message: account == self._account() and is_allowed(message.user_id, self.allowed_user_ids))
        return self._worker

    def _start_worker(self, worker):
        if worker.task is None:
            worker.start(task_factory=self._spawn)
            self._background_tasks.add(worker.task)
            worker.task.add_done_callback(self._background_tasks.discard)

    def _render_result(self, user_id, text, account):
        if account != self._account() or not is_allowed(user_id, self.allowed_user_ids):
            raise PermissionError("Glasses response authority changed")
        return hud_trim(text)

    async def _send_saved(self, message, reply, route):
        now = asyncio.get_running_loop().time()
        waiting = [future for future, deadline in self._observers.get(message.message_id, {}).items()
                   if not future.done() and now <= deadline]
        if waiting:
            for future in waiting:
                future.set_result(reply.text)
            # The caller can render this projection; there is no HUD receipt.
            raise DeliveryUnknown("Glasses HUD response acceptance is unconfirmed")
        if not reply.text:
            return None
        if self._deliver is None:
            raise DeliveryNotSent("Glasses secondary delivery is not configured")
        result = await self._deliver(message.user_id, reply.text)
        if result is None:
            raise DeliveryUnknown("Glasses secondary callback supplied no acceptance receipt")
        return str(result)

    async def handle_utterance(self, user_id: str, text: str, *, utterance_id: str | None = None) -> str:
        """Persist before acknowledging; stable bridge IDs deduplicate retries.

        A bridge that omits utterance_id gets a fresh local identity per call
        and cannot claim external retry deduplication. Text is never an id.
        """
        if not is_allowed(user_id, self.allowed_user_ids):
            return "Sorry, you're not authorized to drive this agent."
        started = asyncio.get_running_loop().time()
        identifier = uuid.uuid4().hex if utterance_id is None else utterance_id
        if not isinstance(identifier, str) or not identifier or len(identifier) > 1024:
            raise ValueError("Invalid glasses utterance identity")
        identity = f"{len(user_id)}:{user_id}:{identifier}"
        message = IncomingMessage(user_id=user_id, sender_id=user_id, text=text, channel=self.name, message_id=identity)
        worker = self._ensure_worker()
        existing = await asyncio.to_thread(worker.journal.get, _record_id(self.name, worker.account, identity))
        route = existing["route"] if existing else {"kind": classify_utterance(text), "secondary": self.secondary_channel}
        future = None
        if route["kind"] == "quick" and sum(len(futures) for futures in self._observers.values()) < 32:
            future = asyncio.get_running_loop().create_future()
            self._observers.setdefault(identity, {})[future] = started + self.deadline_s
        try:
            receipt = await worker.submit(message, external_id=identity, route=route)
            self._start_worker(worker)
            if receipt.get("reply"):
                return self._render_result(user_id, receipt["reply"]["text"], worker.account)
            if future is not None:
                remaining = max(0, self.deadline_s - (asyncio.get_running_loop().time() - started))
                try:
                    text = await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
                    return self._render_result(user_id, text, worker.account)
                except asyncio.TimeoutError:
                    if future.done() and not future.cancelled():
                        return self._render_result(user_id, future.result(), worker.account)
            return ack_message(self.secondary_channel)
        finally:
            if future is not None:
                waiting = self._observers.get(identity, {})
                waiting.pop(future, None)
                if not waiting:
                    self._observers.pop(identity, None)
                if not future.done():
                    future.cancel()

    async def get_result(self, user_id: str, utterance_id: str):
        """Read a saved bridge result under current caller/account authority."""
        if not is_allowed(user_id, self.allowed_user_ids):
            raise PermissionError("Glasses caller is no longer authorized")
        if not isinstance(utterance_id, str) or not utterance_id or len(utterance_id) > 1024:
            raise ValueError("Invalid glasses utterance identity")
        worker = self._ensure_worker()
        if worker.account != self._account():
            raise PermissionError("Glasses configured account changed")
        identity = _record_id(self.name, worker.account, f"{len(user_id)}:{user_id}:{utterance_id}")
        row = await asyncio.to_thread(worker.journal.get, identity)
        if worker.account != self._account() or not is_allowed(user_id, self.allowed_user_ids):
            raise PermissionError("Glasses response authority changed")
        if row is None:
            return None
        return {"execution": row["execution"], "delivery": row["delivery"],
                "text": (row.get("reply") or {}).get("text")}

    async def start(self) -> None:
        worker = self._ensure_worker()
        self._start_worker(worker)
        await asyncio.shield(worker.task)

    async def send(self, user_id: str, text: str) -> None:
        if self._deliver is None:
            raise DeliveryNotSent("Glasses secondary delivery is not configured")
        return await self._deliver(user_id, hud_trim(text))

    async def stop(self) -> None:
        for waiting in self._observers.values():
            for future in waiting:
                if not future.done():
                    future.cancel()
        self._observers.clear()
        if self._worker is not None:
            await self._worker.close()


__all__ = ["GlassesChannel", "classify_utterance", "ack_message", "hud_trim"]
