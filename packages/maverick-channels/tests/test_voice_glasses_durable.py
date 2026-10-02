"""Local ASGI/callback acceptance with real durable channel authority."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from maverick_channels import transport_worker
from maverick_channels.delivery import DeliveryJournal, DeliveryNotSent
from maverick_channels.glasses import GlassesChannel
from maverick_channels.voice import VoiceChannel


async def until(predicate):
    async def wait():
        while not await predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout=5)


def voice_event(**fields):
    return {"message": {"type": "transcript", "role": "user", "transcriptType": "final",
            "transcript": "question", "id": "event1", "call": {"id": "call1", "customer": {"number": "+12025550111"}},
            **fields}}


def test_voice_parallel_callbacks_return_saved_result_with_one_execution():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(message):
            entered.set()
            await release.wait()
            return "saved response"
        handler_mock = AsyncMock(side_effect=handler)
        channel = VoiceChannel(handler_mock, api_key="fixture", webhook_token="fixture", allowed_callers={"+12025550111"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=channel._app), base_url="http://fixture",
                                     headers={"Authorization": "Bearer fixture"}) as client:
            first = asyncio.create_task(client.post("/webhook/voice", json=voice_event()))
            await asyncio.wait_for(entered.wait(), timeout=5)
            duplicates = await asyncio.gather(*(client.post("/webhook/voice", json=voice_event()) for _ in range(3)))
            assert all(response.status_code == 202 for response in duplicates)
            release.set()
            assert (await first).json() == {"response": "saved response"}
            assert (await client.post("/webhook/voice", json=voice_event())).json() == {"response": "saved response"}
            handler_mock.assert_awaited_once()
            channel.allowed_callers = {"+12025550112"}
            response = await client.post("/webhook/voice", json=voice_event())
            assert "saved response" not in response.text
        row = DeliveryJournal().store.list(limit=1)[0]
        assert row["execution"] == "completed" and row["delivery"] == "delivery_unknown"
    asyncio.run(run())


def test_voice_partial_missing_identity_and_changed_replay_do_not_execute():
    async def run():
        handler = AsyncMock(return_value="answer")
        channel = VoiceChannel(handler, api_key="fixture", webhook_token="fixture", allowed_callers={"+12025550111"})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=channel._app), base_url="http://fixture",
                                     headers={"Authorization": "Bearer fixture"}) as client:
            assert (await client.post("/webhook/voice", json=voice_event(transcriptType="partial"))).json() == {}
            assert (await client.post("/webhook/voice", json=voice_event(id=None))).status_code == 400
            handler.assert_not_awaited()
            assert (await client.post("/webhook/voice", json=voice_event())).status_code == 200
            assert (await client.post("/webhook/voice", json=voice_event(transcript="changed"))).status_code == 400
            handler.assert_awaited_once()
    asyncio.run(run())


def glasses(handler=None, deliver=None, **kwargs):
    return GlassesChannel(handler or AsyncMock(return_value="answer"), allowed_user_ids={"owner"},
                          secondary_channel="fixture-secondary", deliver=deliver, **kwargs)


def test_glasses_concurrent_quick_bridge_retry_shares_saved_response():
    async def run():
        channel = glasses()
        results = await asyncio.gather(*(channel.handle_utterance("owner", "question", utterance_id="same") for _ in range(4)))
        assert results == ["answer"] * 4
        assert await channel.handle_utterance("owner", "question", utterance_id="same") == "answer"
        with pytest.raises(ValueError, match="different content"):
            await channel.handle_utterance("owner", "changed", utterance_id="same")
        channel.handler.assert_awaited_once()
        await channel.stop()
        assert not channel._background_tasks
    asyncio.run(run())


def test_glasses_ack_is_durable_and_restart_recovers_before_execution(monkeypatch):
    async def run():
        original = glasses()
        monkeypatch.setattr(original, "_start_worker", lambda worker: None)  # Crash boundary after admission.
        ack = await original.handle_utterance("owner", "write report", utterance_id="work1")
        assert "working on it" in ack and DeliveryJournal().store.count() == 1
        original.handler.assert_not_awaited()
        send = AsyncMock(return_value="fixture-accepted")
        restarted = glasses(deliver=send)
        running = asyncio.create_task(restarted.start())
        async def delivered():
            result = await restarted.get_result("owner", "work1")
            return result["delivery"] == "delivered"
        await until(delivered)
        assert send.await_count == restarted.handler.await_count == 1
        await restarted.stop()
        await running
        assert not restarted._background_tasks
    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["rejected", "unknown", "opaque"])
def test_glasses_saved_result_retries_delivery_only_when_definitive(monkeypatch, outcome):
    monkeypatch.setattr(transport_worker, "RETRY_DELAY", 0.03)
    async def run():
        calls = []
        async def deliver(user_id, text):
            calls.append(text)
            if len(calls) == 1:
                if outcome == "rejected":
                    raise DeliveryNotSent("fixture definitive rejection")
                if outcome == "opaque":
                    return None  # Callback completion alone cannot confirm delivery.
                raise OSError("fixture connection lost after possible acceptance")
            return "fixture-accepted"
        channel = glasses(deliver=deliver)
        await channel.handle_utterance("owner", "write report", utterance_id="work")
        async def terminal():
            result = await channel.get_result("owner", "work")
            return result["delivery"] == ("delivered" if outcome == "rejected" else "delivery_unknown")
        await until(terminal)
        await channel.stop()
        assert len(calls) == (2 if outcome == "rejected" else 1)
        channel.handler.assert_awaited_once()
    asyncio.run(run())


def test_glasses_identity_is_per_utterance_user_and_current_account():
    async def run():
        channel = glasses()
        channel.allowed_user_ids.add("other")
        # Without a bridge id these are two intentional requests, not a text hash.
        assert await channel.handle_utterance("owner", "same question") == "answer"
        assert await channel.handle_utterance("owner", "same question") == "answer"
        assert channel.handler.await_count == 2
        assert await channel.handle_utterance("owner", "same question", utterance_id="stable") == "answer"
        assert await channel.get_result("other", "stable") is None
        with pytest.raises(ValueError, match="identity"):
            await channel.get_result("owner", "a" * 1025)
        channel.secondary_channel = "different-configured-channel"
        with pytest.raises(PermissionError, match="account changed"):
            await channel.get_result("owner", "stable")
        await channel.stop()
    asyncio.run(run())


def test_glasses_revoked_queued_caller_never_executes_or_reads_saved_result():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(message):
            entered.set()
            await release.wait()
            return "private result"
        channel = glasses(AsyncMock(side_effect=handler), AsyncMock(return_value="accepted"))
        await channel.handle_utterance("owner", "write first", utterance_id="one")
        await entered.wait()
        await channel.handle_utterance("owner", "write second", utterance_id="two")
        channel.allowed_user_ids.clear()
        release.set()
        await channel.stop()
        channel.handler.assert_awaited_once()
        channel._deliver.assert_not_awaited()
        with pytest.raises(PermissionError):
            await channel.get_result("owner", "one")
    asyncio.run(run())
