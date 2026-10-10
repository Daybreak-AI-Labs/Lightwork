"""Owned local transport streams, actual journal, no external provider calls."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from maverick_channels import transport_worker
from maverick_channels.base import Channel, IncomingMessage
from maverick_channels.delivery import DeliveryJournal, DeliveryNotSent
from maverick_channels.irc import IRCChannel, format_privmsg
from maverick_channels.signal import SignalChannel
from maverick_channels.transport_worker import TransportWorker


async def until(predicate, *, on_timeout=None):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.005)
    try:
        await asyncio.wait_for(wait(), timeout=5)
    except asyncio.TimeoutError:
        if on_timeout is not None:
            try:
                on_timeout()
            except Exception:
                pass  # Diagnostics must not replace the original timeout.
        raise


def transport_timeout_snapshot(worker, handler_started_count):
    """Report owned queue state and stack locations without message contents."""
    def frame_location(frame):
        return {"file": Path(frame.f_code.co_filename).name,
                "function": frame.f_code.co_name, "line": frame.f_lineno}

    def task_location(task):
        if task is None:
            return None
        locations, seen = [], set()
        awaiting = task.get_coro()
        while awaiting is not None and id(awaiting) not in seen:
            seen.add(id(awaiting))
            frame = getattr(awaiting, "cr_frame", None) or getattr(awaiting, "gi_frame", None)
            if frame is not None:
                locations.append(frame_location(frame))
            awaiting = getattr(awaiting, "cr_await", None) or getattr(awaiting, "gi_yieldfrom", None)
        return {"done": task.done(), "cancelled": task.cancelled(), "stack": locations}

    threads = []
    for current in sys._current_frames().values():
        locations = []
        while current is not None:
            locations.append(frame_location(current))
            current = current.f_back
        threads.append(locations)
    print("TRANSPORT_TIMEOUT " + json.dumps({
        "handler_started_count": handler_started_count,
        "pending_count": len(worker.pending), "overflow": worker._overflow,
        "wake": worker._wake.is_set(), "generation": worker._generation,
        "closing": worker._closing,
        "worker": task_location(worker.task), "scan": task_location(worker._scan_task),
        "threads": threads,
    }, sort_keys=True))


class Adapter(Channel):
    name = "supervision-fixture"
    async def start(self):
        pass
    async def stop(self):
        pass
    async def send(self, user_id, text):
        pass


def test_recovery_scan_keeps_oldest_order_across_short_store_pages():
    class ShortPages:
        ids = ("a", "b", "c")

        def iter_record_ids(self, *, start_after="", limit=100):
            pivot = next((i for i, key in enumerate(self.ids) if key > start_after), len(self.ids))
            for key in (self.ids[pivot:] + self.ids[:pivot])[:2]:
                yield key, key

    class Journal:
        store = ShortPages()

        def get(self, record_id):
            return {"id": record_id, "channel": Adapter.name,
                    "account_sha256": transport_worker._digest("account"),
                    "execution": "received", "delivery": "not_ready",
                    "created_at": {"a": 3, "b": 1, "c": 2}[record_id]}

    worker = TransportWorker(Adapter(AsyncMock()), account="account",
                             authorize=lambda msg: True, send=AsyncMock(), journal=Journal())
    assert worker._oldest_window() == (["b", "c", "a"], False)


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
        await until(lambda: len(order) == 8,
                    on_timeout=lambda: transport_timeout_snapshot(worker, len(order)))
        await worker.close()
        assert worker.task.done() and peak == 1 and order == [str(index) for index in range(8)]
    asyncio.run(run())


@pytest.mark.parametrize("diagnostic_failure", [False, True])
def test_transport_timeout_diagnostic_preserves_failure_without_message_content(monkeypatch, capsys, diagnostic_failure):
    failure = asyncio.TimeoutError("fixture deadline")

    async def timed_out(awaitable, *, timeout):
        assert timeout == 5
        awaitable.close()
        raise failure

    monkeypatch.setattr(asyncio, "wait_for", timed_out)

    async def run():
        private_text = "private-fixture-message-must-not-appear"
        worker = TransportWorker(Adapter(AsyncMock()), account=private_text,
                                 authorize=lambda msg: True, send=AsyncMock())
        worker.pending.append(private_text)
        worker.task = asyncio.current_task()

        def diagnose():
            if diagnostic_failure:
                raise RuntimeError(private_text)
            transport_timeout_snapshot(worker, 0)

        with pytest.raises(asyncio.TimeoutError) as raised:
            await until(lambda: False, on_timeout=diagnose)
        assert raised.value is failure
        output = capsys.readouterr().out
        assert private_text not in output
        if not diagnostic_failure:
            diagnostic = json.loads(output.removeprefix("TRANSPORT_TIMEOUT "))
            assert diagnostic["handler_started_count"] == 0 and diagnostic["pending_count"] == 1
            assert diagnostic["worker"]["stack"] and diagnostic["threads"]
        assert worker.pending[0] == private_text and not worker._closing

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


def test_a_reply_the_source_check_holds_back_does_not_block_the_next_message(monkeypatch):
    # A reply whose task or sources the person can no longer use is refused
    # before sending and stays retryable (the check may pass again). It used to
    # block the conversation too, so none of the person's later messages ran.
    from maverick_channels import delivery

    monkeypatch.setattr(transport_worker, "RETRY_DELAY", 0.05)
    def source_check(row):
        if row["message"]["text"] == "0":
            raise PermissionError("The saved reply's task ownership changed")
    monkeypatch.setattr(delivery, "_require_reply_source", source_check)
    async def run():
        order, sends = [], []
        async def handler(message):
            order.append(message.text)
            return message.text
        async def send(message, reply, route):
            sends.append(reply.text)
        worker = TransportWorker(Adapter(handler), account="account", authorize=lambda msg: True, send=send).start()
        for index in range(2):
            await worker.submit(IncomingMessage(user_id="person", sender_id="person", text=str(index),
                                channel=Adapter.name, message_id=str(index)), external_id=str(index))
        await until(lambda: sends == ["1"])
        await worker.close()
        assert order == ["0", "1"]
    asyncio.run(run())
    held = [row for row in DeliveryJournal().store.list(limit=5) if row["message"]["text"] == "0"]
    assert held[0]["delivery"] == "retryable" and held[0]["chunks"]["reply"]["withheld"] is True


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


def test_the_oldest_window_scan_stops_at_the_wrap_when_the_first_record_is_gone(monkeypatch):
    # Same wrap rule as the capacity count: one pass, no duplicates, even when
    # compaction deletes the first record while the scan is running.
    from types import SimpleNamespace

    from maverick_channels.delivery import _digest

    ids = ["a", "b", "c", "d", "e"]
    rows = {i: {"id": i, "channel": Adapter.name, "account_sha256": _digest("account"),
                "execution": "received", "delivery": "not_ready", "created_at": n} for n, i in enumerate(ids)}
    class Store:
        calls = 0
        def iter_record_ids(self, *, start_after="", limit=100):
            Store.calls += 1
            if Store.calls == 2:
                ids.pop(0)
            later = [i for i in ids if i > start_after]
            ordered = later + [i for i in ids if i <= start_after]
            return [(i, i) for i in ordered[:min(limit, 2)]]
    worker = TransportWorker(Adapter(AsyncMock()), account="account", authorize=lambda msg: True, send=AsyncMock())
    worker.journal = SimpleNamespace(store=Store(), get=lambda record_id: rows.get(record_id))
    selected, more = worker._oldest_window()
    assert selected == ["a", "b", "c", "d", "e"] and more is False
