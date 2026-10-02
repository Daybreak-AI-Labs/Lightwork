"""Voice channel (Vapi / Retell / Bland).

Tenth channel adapter. A voice provider exposes a REST + webhook surface
that matches Lightwork's existing IncomingMessage / handler contract —
when the user speaks, the provider transcribes (Deepgram / Whisper /
ElevenLabs) and POSTs the transcript to our webhook; we run the swarm;
the reply goes back via the provider's TTS.

Config in ~/.maverick/config.toml::

    [channels.voice]
    enabled       = true
    provider      = "vapi"            # vapi | retell | bland
    api_key       = "${VAPI_API_KEY}" # RETELL_API_KEY / BLAND_API_KEY for those
    phone_number  = "+14155551234"
    port          = 8770
    assistant_id  = "<provider assistant/agent id>"
    webhook_token = "${VAPI_WEBHOOK_TOKEN}"
    allowed_callers = ["+12025550111"]

Only listed caller numbers may start work. Caller ID is a coarse restriction,
not proof of the individual behind a call; sensitive actions still need review.

The TTS / STT happens on the provider's side; Lightwork is just the
"reasoning brain" the provider calls when the user pauses. This keeps
the adapter thin.

Provider support:
  - Inbound webhook parsing is Vapi-shaped today (the swarm-facing
    transcript handler). Retell/Bland inbound is not yet wired.
  - Outbound ``send()`` (proactive calls) is provider-dispatched and
    supports vapi, retell, and bland. The Vapi contract is live-tested;
    the Retell and Bland contracts are wired from each vendor's public
    API docs and should be smoke-tested with real credentials before
    you depend on them.

Requires::

    python -m pip install -e './packages/maverick-channels[voice]'
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re

from .base import (
    Channel,
    IncomingMessage,
    add_webhook_body_limit,
    is_allowed,
    normalize_allowlist,
)

log = logging.getLogger(__name__)

# Which env var holds the API key for each provider. Used both to resolve
# the key at construction and by the installer wizard to prompt for the
# right secret.
PROVIDER_KEY_ENV = {
    "vapi": "VAPI_API_KEY",
    "retell": "RETELL_API_KEY",
    "bland": "BLAND_API_KEY",
}


_E164 = re.compile(r"\+[1-9][0-9]{5,14}\Z")


def configured_callers(values=None) -> set[str]:
    """Resolve and validate the effective list of callers allowed to start work.

    An explicit empty list overrides the environment. A missing list remains
    empty so the webhook and listener can refuse inbound work safely.
    """
    if values is not None and not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("voice channel: caller list must contain phone numbers")
    callers = normalize_allowlist(values, "VOICE_ALLOWED_CALLERS")
    if any(not _E164.fullmatch(number) for number in callers):
        raise ValueError("voice channel: caller list needs phone numbers in +countrycode format")
    return callers


try:
    import httpx
    from fastapi import FastAPI, Header, HTTPException, Request, Response
    _HAVE_DEPS = True
except ImportError:
    _HAVE_DEPS = False
    FastAPI = Header = HTTPException = Request = Response = httpx = None  # type: ignore


class VoiceChannel(Channel):
    """Vapi-backed voice channel.

    Lifecycle:
      - start(): bring up a FastAPI server on `port` exposing
        `/webhook/voice` for Vapi's events.
      - on each `function-call` / `transcript` event, dispatch to
        Lightwork's handler and return a `result` Vapi will TTS.
      - send(): for proactive notifications (e.g., goal completed
        while user was on another call) we POST to Vapi's outbound
        call endpoint with the assistant_id.
    """

    name = "voice"

    def __init__(
        self,
        handler,
        api_key: str | None = None,
        phone_number: str | None = None,
        port: int = 8770,
        assistant_id: str | None = None,
        provider: str = "vapi",
        webhook_token: str | None = None,
        allowed_callers=None,
    ):
        super().__init__(handler)
        if not _HAVE_DEPS:
            raise ImportError(
                "fastapi/httpx not installed. Run: "
                "From the reviewed checkout run: python -m pip install -e "
                "'./packages/maverick-channels[voice]'"
            )
        self.provider = provider
        # Each provider keeps its key in its own env var; fall back to the
        # Vapi var for an unknown provider so the error message is sensible.
        key_env = PROVIDER_KEY_ENV.get(provider, "VAPI_API_KEY")
        self.api_key = api_key or os.environ.get(key_env)
        if not self.api_key:
            raise ValueError(f"voice channel: {key_env} not set")
        self.phone_number = phone_number
        self.port = port
        self.assistant_id = assistant_id
        self.webhook_token = webhook_token or os.environ.get("VAPI_WEBHOOK_TOKEN")
        # The bearer authenticates the webhook service, not the phone caller.
        # A caller list is required before any transcript can start work.
        self.allowed_callers = configured_callers(allowed_callers)

        self._app = FastAPI()
        add_webhook_body_limit(self._app)
        self._app.post("/webhook/voice")(self._handle_webhook)
        self._uvicorn_server = None

    def _check_webhook_auth(self, authorization: str | None) -> bool:
        """Require bearer auth for inbound webhook events."""
        expected = self.webhook_token
        if not expected:
            return False
        if not authorization or not authorization.startswith("Bearer "):
            return False
        given = authorization[len("Bearer "):].strip()
        return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))

    async def _handle_webhook(
        self,
        request: Request,
        authorization: str | None = Header(None),
    ):
        if not self._check_webhook_auth(authorization):
            raise HTTPException(status_code=401, detail="invalid webhook bearer")
        if not self.allowed_callers:
            raise HTTPException(status_code=403, detail="voice caller list is not configured")

        # Vapi sends webhook events with a `type` field: "function-call",
        # "end-of-call-report", "transcript", "speech-update", etc.
        # We only care about transcripts that resolve into agent turns.
        try:
            payload = await request.json()
        except Exception:
            # Malformed / non-JSON body: 400, don't 500 (which Vapi
            # retries, amplifying load).
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="expected a JSON object")
        # Coerce nested fields to dicts: a body like {"message": "x"} would make
        # the chained .get() raise AttributeError -> unhandled 500 (and Vapi
        # retries non-2xx, amplifying load).
        msg_data = payload.get("message")
        if not isinstance(msg_data, dict):
            msg_data = {}
        ev_type = msg_data.get("type", "")

        if ev_type == "transcript":
            transcript = msg_data.get("transcript", "")
            role = msg_data.get("role", "user")
            if role != "user" or not transcript or msg_data.get("transcriptType") == "partial":
                return Response(content="{}", media_type="application/json")
            call = msg_data.get("call") or payload.get("call")
            call = call if isinstance(call, dict) else {}
            customer = call.get("customer")
            customer = customer if isinstance(customer, dict) else {}
            user_id = customer.get("number")
            if not isinstance(user_id, str) or not _E164.fullmatch(user_id):
                raise HTTPException(status_code=403, detail="canonical caller number required")
            if not is_allowed(user_id, self.allowed_callers):
                log.warning("unauthorized voice caller")
                return {"response": "Sorry, this number isn't authorized to use this assistant."}
            from .delivery import DeliveryUnknown

            call_id = str(call.get("id") or "")
            event_id = str(msg_data.get("id") or msg_data.get("timestamp") or "")
            if not call_id or not event_id:
                raise HTTPException(status_code=400, detail="stable call and transcript event identity required")
            identity = f"{len(call_id)}:{call_id}:{event_id}"
            account = self._delivery_account()
            msg = IncomingMessage(user_id=user_id, sender_id=user_id, text=transcript,
                                  channel=self.name, message_id=identity)
            def authorize():
                return (account == self._delivery_account() and self._check_webhook_auth(authorization)
                        and is_allowed(user_id, self.allowed_callers))
            async def prepare_response(reply):
                # Returning a response object cannot confirm provider receipt
                # or spoken delivery. Repeated callbacks read the saved result.
                raise DeliveryUnknown("Voice HTTP response acceptance is unconfirmed")
            try:
                row = await self.process_message(msg, external_id=identity, account=account,
                    authorize=authorize, send=prepare_response, route={"response_transport": "http"})
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail="voice caller authority changed") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid or conflicting voice input") from exc
            except Exception as exc:
                raise HTTPException(status_code=503, detail="voice intake or receipt unavailable") from exc
            if row.get("reply"):
                return {"response": row["reply"]["text"]}
            from fastapi.responses import JSONResponse

            pending = row["execution"] in {"received", "execution_started_unconfirmed"}
            return JSONResponse({"response": "Your request is still processing." if pending else
                                 "This request needs review before it can be repeated.",
                                 "status": row["execution"]}, status_code=202 if pending else 409)

        # Other events (end-of-call, status updates) are informational.
        # Acknowledge with empty body so Vapi doesn't retry.
        return Response(content="{}", media_type="application/json")

    def _delivery_account(self):
        return ":".join((self.provider, self.assistant_id or "", self.phone_number or "",
                         hashlib.sha256(self.api_key.encode()).hexdigest()))

    async def start(self) -> None:
        if not self.allowed_callers:
            raise ValueError("voice channel: set allowed_callers or VOICE_ALLOWED_CALLERS before receiving calls")
        import uvicorn
        if not self.webhook_token:
            # Inbound webhook auth rejects every request without a token, so the
            # listener would accept nothing. Fail loudly rather than run inert.
            raise ValueError(
                "voice channel: webhook token not set "
                "(pass webhook_token or set VAPI_WEBHOOK_TOKEN) to receive calls"
            )
        log.info("Voice channel listening on :%d (provider=%s)",
                 self.port, self.provider)
        config = uvicorn.Config(
            self._app, host="127.0.0.1", port=self.port,
            log_level="info",
        )
        self._uvicorn_server = uvicorn.Server(config)
        await self._uvicorn_server.serve()

    def _outbound_request(self, user_id: str, text: str) -> dict:
        """Build the provider-specific outbound-call HTTP request.

        Returns a dict with ``url`` / ``headers`` / ``json``. Contracts
        come from the existing provider integrations. Local transport fixtures
        verify request construction; live provider acceptance needs separate
        qualification. Raises ValueError for an unknown provider so ``send()``
        can report definitive nonacceptance.
        """
        if self.provider == "vapi":
            return {
                "url": "https://api.vapi.ai/call",
                "headers": {"Authorization": f"Bearer {self.api_key}"},
                "json": {
                    "assistantId": self.assistant_id,
                    "customer": {"number": user_id},
                    "phoneNumber": {"number": self.phone_number},
                    "assistantOverrides": {"firstMessage": text},
                },
            }
        if self.provider == "retell":
            # https://docs.retellai.com/api-references/create-phone-call
            return {
                "url": "https://api.retellai.com/v2/create-phone-call",
                "headers": {"Authorization": f"Bearer {self.api_key}"},
                "json": {
                    "from_number": self.phone_number,
                    "to_number": user_id,
                    "override_agent_id": self.assistant_id,
                    "retell_llm_dynamic_variables": {"first_message": text},
                },
            }
        if self.provider == "bland":
            # https://docs.bland.ai/api-v1/post/calls
            return {
                "url": "https://api.bland.ai/v1/calls",
                "headers": {"authorization": self.api_key or ""},
                "json": {
                    "phone_number": user_id,
                    "from": self.phone_number,
                    "task": text,
                    "pathway_id": self.assistant_id,
                },
            }
        raise ValueError(f"unsupported voice provider {self.provider!r}")

    async def send(self, user_id: str, text: str) -> str:
        """Outbound: place a call to `user_id` (a phone number) via the
        configured provider and have the assistant deliver `text`."""
        from .delivery import DeliveryNotSent, DeliveryUnknown, send_chunk

        if not self.assistant_id:
            raise DeliveryNotSent("Voice outbound assistant is not configured")
        try:
            req = self._outbound_request(user_id, text)
        except ValueError as exc:
            raise DeliveryNotSent("Voice outbound provider is unsupported") from exc
        async def create():
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(req["url"], headers=req["headers"], json=req["json"])
            if 400 <= response.status_code < 500 and response.status_code != 408:
                raise DeliveryNotSent("Voice provider rejected the outbound call")
            if response.status_code >= 300:
                raise DeliveryUnknown("Voice outbound acceptance is unconfirmed")
            result = response.json()
            identity = result.get("id") or result.get("call_id")
            if not identity:
                raise DeliveryUnknown("Voice provider returned no call identity")
            return str(identity)
        return await send_chunk("voice-call-0", {"provider": self.provider, "to": user_id,
                                "text": text, "assistant": self.assistant_id}, create)

    async def stop(self) -> None:
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
