"""Slack channel via Socket Mode (no public webhook needed).

Set up:
  1. Create a Slack app at https://api.slack.com/apps
  2. Enable Socket Mode; copy the App-Level Token to ${SLACK_APP_TOKEN}
  3. Install to your workspace; copy the Bot Token to ${SLACK_BOT_TOKEN}
  4. Subscribe to `message.im` events; add `chat:write`, `im:history` scopes
     (+ `files:read` if users will share files with the bot — shared files
     become goal attachments)

Requires::

    python -m pip install -e './packages/maverick-channels[slack]'
"""
from __future__ import annotations

import asyncio
import logging
import os

from .base import Channel, IncomingMessage, is_allowed, normalize_allowlist
from .delivery import DeliveryNotSent, DeliveryUnknown, delivery_active, send_chunk

log = logging.getLogger(__name__)

try:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.socket_mode.response import SocketModeResponse
    from slack_sdk.web.async_client import AsyncWebClient
    _HAVE_SLACK = True
except ImportError:
    _HAVE_SLACK = False
    SocketModeClient = AsyncWebClient = SocketModeResponse = None  # type: ignore


class SlackChannel(Channel):
    name = "slack"

    def __init__(
        self,
        handler,
        app_token: str | None = None,
        bot_token: str | None = None,
        allowed_user_ids=None,
        thread_replies: bool | None = None,
    ):
        super().__init__(handler)
        if not _HAVE_SLACK:
            raise ImportError(
                "slack_sdk not installed. From the reviewed checkout run: "
                "python -m pip install -e './packages/maverick-channels[slack]'"
            )
        self.app_token = app_token or os.environ.get("SLACK_APP_TOKEN")
        self.bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN")
        if not self.app_token or not self.bot_token:
            raise ValueError("SLACK_APP_TOKEN and SLACK_BOT_TOKEN must be set")
        # Without an allowlist, ANY workspace user who can message the bot
        # drives the agent (and burns the operator's budget). Require one,
        # matching discord/telegram. Default-deny via base.is_allowed.
        self.allowed_user_ids = normalize_allowlist(
            allowed_user_ids, "SLACK_ALLOWED_USER_IDS",
        )
        if not self.allowed_user_ids:
            raise ValueError("Set SLACK_ALLOWED_USER_IDS to restrict access")
        # Opt-in reply threading: answers post under the asking message's
        # thread (thread_ts) instead of interleaving into the channel.
        # [channels.slack] thread_replies / SLACK_THREAD_REPLIES.
        if thread_replies is None:
            thread_replies = os.environ.get(
                "SLACK_THREAD_REPLIES", "").strip().lower() in {"1", "true", "yes", "on"}
        self.thread_replies = bool(thread_replies)
        self._web = AsyncWebClient(token=self.bot_token)
        self._sm = SocketModeClient(app_token=self.app_token, web_client=self._web)
        self._sm.socket_mode_request_listeners.append(self._on_request)
        self._stop_event = asyncio.Event()
        self._account_id = ""
        self._team_id = ""

    def _authorized(self, msg, account):
        return bool(account and account == self._account_id
                    and is_allowed(msg.principal_id, self.allowed_user_ids))

    async def _prepare(self, msg, route):
        msg.attachments = await self._download_files({"files": route.get("files", [])})
        return msg

    async def _send_saved(self, msg, reply, route):
        if reply.text:
            return await self.send_threaded(msg.user_id, reply.text,
                                            reply_to=route.get("reply_to"))

    async def _download_files(self, event: dict) -> list[dict]:
        """Download a message's shared files into IncomingMessage.attachments.

        Slack file objects carry ``url_private_download`` which requires the
        bot token as a bearer header (and the ``files:read`` scope). Each
        file becomes ``{"filename", "mime", "data"}``; the server stores them
        under the same size/mime/magic-byte rules as a dashboard upload.
        Best-effort: a missing scope or a failed download just means no
        attachment, never a dropped message.
        """
        files = event.get("files") or []
        if not files:
            return []
        try:
            import aiohttp  # slack_sdk's async client already requires it
        except ImportError:  # pragma: no cover
            return []
        out: list[dict] = []
        headers = {"Authorization": f"Bearer {self.bot_token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            for f in files[:10]:
                url = f.get("url_private_download") or f.get("url_private")
                if not url:
                    continue
                size = f.get("size")
                if size and int(size) > 100 * 1024 * 1024:
                    continue  # attachments.store would reject it anyway
                try:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        data = await resp.content.read(100 * 1024 * 1024 + 1)
                except Exception:  # noqa: BLE001 -- never drop the message
                    log.warning("slack: file download failed", exc_info=True)
                    continue
                if not data or len(data) > 100 * 1024 * 1024:
                    continue
                out.append({
                    "filename": f.get("name") or "file.bin",
                    "mime": f.get("mimetype") or "application/octet-stream",
                    "data": data,
                })
        return out

    async def _on_request(self, client, req):
        event = req.payload.get("event", {}) if req.type == "events_api" else {}
        sender = event.get("user", "")
        accepted = (event.get("type") == "message" and "bot_id" not in event
                    and is_allowed(sender, self.allowed_user_ids))
        if accepted:
            # auth_test binds this transport to its immutable workspace/bot.
            # Persist only the envelope before ACK; file downloads and agent
            # work happen afterward. A failed/slow save permits redelivery.
            if (not self._account_id or req.payload.get("team_id") != self._team_id
                    or not event.get("channel") or not event.get("ts")):
                raise PermissionError("Slack event has no verified account/message identity")
            files = [{key: value for key, value in item.items() if key in {
                "id", "name", "mimetype", "size", "url_private", "url_private_download",
            }} for item in (event.get("files") or [])[:10]]
            msg = IncomingMessage(
                user_id=event["channel"], text=event.get("text") or
                ("Process the attached file(s)." if files else ""), channel=self.name,
                sender_id=sender, message_id=event["ts"],
            )
            account = self._account_id
            route = {"reply_to": event["ts"] if self.thread_replies else None, "files": files}
            external_id = event["channel"] + ":" + event["ts"]
            receipt = await self.admit_message(msg, external_id=external_id, account=account,
                authorize=lambda: self._authorized(msg, account), route=route)
        await client.send_socket_mode_response(
            SocketModeResponse(envelope_id=req.envelope_id)
        )
        if accepted:
            return await self.process_message(msg, external_id=external_id, account=account,
                authorize=lambda: self._authorized(msg, account), route=route, receipt=receipt,
                prepare=lambda incoming: self._prepare(incoming, route),
                send=lambda reply: self._send_saved(msg, reply, route))

    async def start(self) -> None:
        identity = await self._web.auth_test()
        if not identity.get("team_id") or not identity.get("user_id"):
            raise PermissionError("Slack account identity unavailable")
        self._team_id = identity["team_id"]
        self._account_id = self._team_id + ":" + identity["user_id"]
        account = self._account_id
        await self._sm.connect()
        log.info("Slack channel connected")
        async with self.recovery_worker(account=account,
                authorize=lambda msg: self._authorized(msg, account),
                send=self._send_saved, prepare=self._prepare):
            await self._stop_event.wait()

    async def send(self, user_id: str, text: str) -> None:
        return await self.send_threaded(user_id, text)

    async def send_threaded(
        self, user_id: str, text: str, *, reply_to: str | None = None,
    ) -> None:
        from .formatting import split_for_discord, to_slack_mrkdwn
        rejected = {"not_authed", "invalid_auth", "account_inactive", "token_revoked",
                    "missing_scope", "channel_not_found", "not_in_channel", "is_archived",
                    "msg_too_long", "no_text", "restricted_action", "ratelimited"}
        receipt = None
        # Format before splitting so escaping cannot expand a chunk past its
        # bound. Slack otherwise silently truncates a long successful post.
        for index, chunk in enumerate(split_for_discord(to_slack_mrkdwn(text), limit=4000)):
            kwargs = {"channel": user_id, "text": chunk, "mrkdwn": True}
            if reply_to:
                kwargs["thread_ts"] = reply_to
            async def post(kwargs=kwargs):
                try:
                    result = await self._web.chat_postMessage(**kwargs)
                except Exception as exc:
                    response = getattr(exc, "response", None)
                    if response is not None and response.get("error") in rejected:
                        raise DeliveryNotSent("Slack rejected the reply") from exc
                    raise
                if result.get("ok") is False:
                    if result.get("error") in rejected:
                        raise DeliveryNotSent("Slack rejected the reply")
                    raise DeliveryUnknown("Slack reply acceptance is uncertain")
                provider_id = result.get("ts")
                if delivery_active() and not isinstance(provider_id, str):
                    raise DeliveryUnknown("Slack reply acknowledgement has no message id")
                return provider_id
            receipt = await send_chunk("text-" + str(index), kwargs, post)
        return receipt

    async def stop(self) -> None:
        self._stop_event.set()
        await self._sm.disconnect()
