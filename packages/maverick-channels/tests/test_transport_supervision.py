"""Owned local transport streams, actual journal, no external provider calls."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from maverick_channels import transport_worker
from maverick_channels.base import Channel, IncomingMessage
from maverick_channels.delivery import DeliveryJournal, DeliveryNotSent
from maverick_channels.irc import IRCChannel, format_privmsg
from maverick_channels.signal import SignalChannel
from maverick_channels.transport_worker import TransportWorker


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(wait(), timeout=5)


class Adapter(Channel):
    name = "supervision-fixture"
    async def start(self):
        pass
    async def stop(self):
        pass
    async def send(self, user_id, text):
        pass


def test_bounded_queue_overflow_is_durable_serial_and_in_intake_order(monkeypatch):
    monkeypatch.setattr(transport_worker, "MAX_PENDING", 2)
    async def run():
        release, started, order = asyncio.Event(), asyncio.Event(), []
        active, peak = 0, 0
        async def handler(message):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            order.append(message.text)
            started.set()
            await release.wait()
            active -= 1
            return "answer"
        async def send(*args):
            return "confirmed-fixture"
        worker = TransportWorker(Adapter(handler), account="account", authorize=lambda msg: True, send=send).start()
        for index in range(8):
            await worker.submit(IncomingMessage(user_id="person", sender_id="person", text=str(index),
                                channel=Adapter.name, message_id=str(index)), external_id=str(index))
            if index == 0:
                await started.wait()
            assert len(worker.pending) <= 2
        assert worker.overflow_count and DeliveryJournal().store.count() == 8
        release.set()
        await until(lambda: len(order) == 8)
        await worker.close()
        assert worker.task.done() and peak == 1 and order == [str(index) for index in range(8)]
    asyncio.run(run())


class Writer:
    def __init__(self, callback=None):
        self.writes, self.callback, self.closed = [], callback, False
    def write(self, data):
        self.writes.append(data)
        if self.callback:
            self.callback(data)
    async def drain(self):
        pass
    def close(self):
        self.closed = True
    async def wait_closed(self):
        pass


class Process:
    pid = 314
    def __init__(self):
        self.returncode = None
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = Writer(self.rpc)
        self.terminated = self.killed = False
    def rpc(self, line):
        data = json.loads(line)
        result = {"id": data["id"], "jsonrpc": "2.0"}
        if data.get("params", {}).get("message") == "rpc-error":
            result["error"] = {"code": -32602, "message": "private provider error"}
        else:
            result["result"] = {"timestamp": 777}
        self.stdout.feed_data((json.dumps(result) + "\n").encode())
    def terminate(self):
        self.terminated = True
        self.returncode = 0
        self.stdout.feed_eof()
        self.stderr.feed_eof()
    def kill(self):
        self.killed = True
        self.terminate()
    async def wait(self):
        await until(lambda: self.returncode is not None)
        return self.returncode


def receive(index=1):
    return (json.dumps({"method": "receive", "params": {"envelope": {"source": "+owner",
            "dataMessage": {"message": str(index), "timestamp": index}}}}) + "\n").encode()


def test_signal_blocked_agent_does_not_block_rpc_stderr_or_owned_shutdown(monkeypatch):
    monkeypatch.setattr(transport_worker, "SHUTDOWN_TIMEOUT", 0.05)
    async def run():
        started = asyncio.Event()
        async def handler(message):
            started.set()
            await asyncio.Event().wait()
        proc = Process()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
        channel = SignalChannel(handler, "+self", signal_cli_path="local-fixture", allowed_user_ids={"+owner"})
        running = asyncio.create_task(channel.start())
        await until(lambda: channel._worker is not None)
        proc.stdout.feed_data(receive())
        await asyncio.wait_for(started.wait(), timeout=5)
        proc.stderr.feed_data(b"fixture-sensitive-stderr\n" * 10000)
        assert await asyncio.wait_for(channel._send_rpc("send", {"message": "probe"}), timeout=1) == {"timestamp": 777}
        with pytest.raises(DeliveryNotSent, match="rejected"):
            await channel._send_rpc("send", {"message": "rpc-error"})
        await until(lambda: channel._stderr_bytes == 250000)
        await channel.stop()
        await asyncio.wait_for(running, timeout=1)
        assert proc.terminated and channel._reader_task.done() and channel._stderr_task.done() and channel._worker.task.done()
        assert not channel._pending_rpc
        row = DeliveryJournal().store.list(limit=1)[0]
        # Cancelled mid-handler at shutdown: recorded as unknown (never re-run)
        # with a reply for the sender, not left "started" with no outcome.
        assert row["execution"] == "execution_unknown" and row["delivery"] == "ready"
    asyncio.run(run())


def test_signal_duplicate_provider_event_executes_once_and_waits_for_send_ack(monkeypatch):
    async def run():
        proc, handler = Process(), AsyncMock(return_value="answer")
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
        channel = SignalChannel(handler, "+self", signal_cli_path="local-fixture", allowed_user_ids={"+owner"})
        running = asyncio.create_task(channel.start())
        await until(lambda: channel._worker is not None)
        proc.stdout.feed_data(receive() + receive())
        await until(lambda: bool(proc.stdin.writes))
        await until(lambda: DeliveryJournal().store.list(limit=1)[0]["delivery"] == "delivered")
        await channel.stop()
        await running
        handler.assert_awaited_once()
        assert len(proc.stdin.writes) == 1
        row = DeliveryJournal().store.list(limit=1)[0]
        assert row["chunks"]["signal-0"]["provider_id"] == "777"
    asyncio.run(run())


def test_irc_ping_remains_responsive_during_agent_and_stop_owns_reader(monkeypatch):
    monkeypatch.setattr(transport_worker, "SHUTDOWN_TIMEOUT", 0.05)
    async def run():
        reader, writer, started = asyncio.StreamReader(), Writer(), asyncio.Event()
        monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))
        async def handler(message):
            started.set()
            await asyncio.Event().wait()
        channel = IRCChannel(handler, "fixture.invalid", nick="bot", channels=["#room"], allowed_user_ids={"owner"})
        running = asyncio.create_task(channel.start())
        await until(lambda: channel._reader_task is not None)
        reader.feed_data(b"@account=owner;msgid=event1 :nick!u@h PRIVMSG #room :question\r\n")
        await asyncio.wait_for(started.wait(), timeout=5)
        reader.feed_data(b"PING :still-responsive\r\n")
        await until(lambda: b"PONG :still-responsive\r\n" in writer.writes)
        await channel.stop()
        await running
        assert writer.closed and channel._worker.task.done() and channel._reader_task.done()
    asyncio.run(run())


@pytest.mark.parametrize("current_account", ["owner", "different-person"])
def test_irc_saved_direct_reply_rechecks_whois_and_never_claims_remote_delivery(monkeypatch, current_account):
    async def run():
        reader = asyncio.StreamReader()
        def reply(data):
            if data.startswith(b"WHOIS nick"):
                reader.feed_data(f":server 330 bot nick {current_account} :account\r\n:server 318 bot nick :end\r\n".encode())
        writer = Writer(reply)
        monkeypatch.setattr(asyncio, "open_connection", AsyncMock(return_value=(reader, writer)))
        channel = IRCChannel(AsyncMock(return_value="answer"), "fixture.invalid", nick="bot", allowed_user_ids={"owner"})
        running = asyncio.create_task(channel.start())
        await until(lambda: channel._reader_task is not None)
        reader.feed_data(b"@account=owner;msgid=event1 :nick!u@h PRIVMSG bot :question\r\n")
        expected = "delivery_unknown" if current_account == "owner" else "retryable"
        await until(lambda: bool(DeliveryJournal().store.list(limit=1))
                     and DeliveryJournal().store.list(limit=1)[0]["delivery"] == expected)
        await channel.stop()
        await running
        assert any(line.startswith(b"PRIVMSG nick") for line in writer.writes) == (current_account == "owner")
        channel.handler.assert_awaited_once()
    asyncio.run(run())


def test_irc_unicode_chunks_obey_wire_byte_limit_without_losing_text():
    text = "🌅" * 350
    lines = format_privmsg("#room", text)
    assert all(len((line + "\r\n").encode()) <= 512 for line in lines)
    assert "".join(line.split(" :", 1)[1] for line in lines) == text


def test_signal_missing_rpc_ack_is_unknown_and_child_kill_is_awaited(monkeypatch):
    from maverick_channels import signal

    monkeypatch.setattr(signal, "RPC_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(signal, "STOP_TIMEOUT_SECONDS", 0.03)
    async def run():
        proc = Process()
        proc.stdin.callback = None  # The daemon accepts bytes but never replies.
        def ignore_terminate():
            proc.terminated = True
        def kill():
            proc.killed = True
            proc.returncode = -1
            proc.stdout.feed_eof()
            proc.stderr.feed_eof()
        proc.terminate, proc.kill = ignore_terminate, kill
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=proc))
        channel = SignalChannel(AsyncMock(return_value="answer"), "+self", signal_cli_path="fixture",
                                 allowed_user_ids={"+owner"})
        running = asyncio.create_task(channel.start())
        await until(lambda: channel._worker is not None)
        proc.stdout.feed_data(receive())
        await until(lambda: bool(DeliveryJournal().store.list(limit=1))
                     and DeliveryJournal().store.list(limit=1)[0]["delivery"] == "delivery_unknown")
        proc.stdout.feed_data(receive())
        await channel.stop()
        await running
        assert proc.killed and proc.returncode == -1 and len(proc.stdin.writes) == 1
        channel.handler.assert_awaited_once()
    asyncio.run(run())


def test_worker_restart_recovers_received_records_in_order_without_reopening_unknown():
    journal = DeliveryJournal()
    for index in range(4):
        row = journal.admit(IncomingMessage(user_id="person", sender_id="person", text=str(index),
                             channel=Adapter.name, message_id=str(index)), external_id=str(index), account="account")
        if index == 0:
            journal.claim_execution(row)  # Crash after execution claim, before saved result.
    async def run():
        order = []
        async def handler(message):
            order.append(message.text)
            return "answer"
        worker = TransportWorker(Adapter(handler), account="account", authorize=lambda msg: True,
                                 send=AsyncMock(return_value="confirmed")).start()
        await until(lambda: len(order) == 3)
        await worker.close()
        assert order == ["1", "2", "3"]
    asyncio.run(run())


def test_definite_delivery_retry_preserves_conversation_order(monkeypatch):
    monkeypatch.setattr(transport_worker, "RETRY_DELAY", 0.08)
    async def run():
        order, sends = [], []
        async def handler(message):
            order.append(message.text)
            return message.text
        async def send(message, reply, route):
            sends.append(reply.text)
            if len(sends) == 1:
                raise DeliveryNotSent("definite fixture rejection")
        worker = TransportWorker(Adapter(handler), account="account", authorize=lambda msg: True, send=send).start()
        for index in range(2):
            await worker.submit(IncomingMessage(user_id="person", sender_id="person", text=str(index),
                                channel=Adapter.name, message_id=str(index)), external_id=str(index))
        await until(lambda: len(sends) == 3)
        await worker.close()
        assert order == ["0", "1"] and sends == ["0", "0", "1"]
    asyncio.run(run())


def test_shutdown_joins_cooperative_read_only_scan(monkeypatch):
    import threading

    monkeypatch.setattr(transport_worker, "SHUTDOWN_TIMEOUT", 0.03)
    entered = threading.Event()
    async def run():
        worker = TransportWorker(Adapter(AsyncMock()), account="account", authorize=lambda msg: True, send=AsyncMock())
        def scan():
            entered.set()
            worker._stop_scan.wait(timeout=1)
            return [], False
        monkeypatch.setattr(worker, "_oldest_window", scan)
        worker.start()
        await until(entered.is_set)
        await worker.close()
        assert worker.task.done() and worker._scan_task.done()
    asyncio.run(run())
