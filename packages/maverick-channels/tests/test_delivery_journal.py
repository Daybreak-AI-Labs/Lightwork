"""Actual durable CAS journal and local world; no provider or agent calls."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from maverick import world_model
from maverick.privacy_ops import RecordConflict
from maverick_channels import delivery
from maverick_channels.base import Channel, IncomingMessage


class Adapter(Channel):
    name = "fixture"
    async def start(self):
        pass
    async def stop(self):
        pass
    async def send(self, user_id, text):
        pass


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(world_model, "DEFAULT_DB", tmp_path / "world.db")
    monkeypatch.setenv("MAVERICK_GOVERNED_RECORD_BACKEND", "local")
    return delivery.DeliveryJournal()


def _message(text="Question"):
    return IncomingMessage(user_id="person", text=text, channel="fixture", message_id="event1")


def test_intake_failure_never_claims_execution_or_runs_handler(isolated, monkeypatch):
    async def handler(msg):
        pytest.fail("Handler ran before durable intake")
    monkeypatch.setattr(isolated.store, "create", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError):
        asyncio.run(Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                    authorize=lambda: True, send=lambda reply: None, journal=isolated))
    with world_model.WorldModel(world_model.DEFAULT_DB) as world:
        assert not world.is_processed_message("fixture", "event1")


def test_saved_reply_retries_only_definitively_unsent_chunk(isolated):
    runs, calls = [], []
    async def handler(msg):
        runs.append(msg.text)
        return "answer"
    async def send(reply):
        calls.append(reply.text)
        if len(calls) == 1:
            raise delivery.DeliveryNotSent("fixture rejection before acceptance")
        return "provider-message-id"
    channel = Adapter(handler)
    async def run():
        args = dict(external_id="event1", account="account1", authorize=lambda: True, send=send, journal=isolated)
        first = await channel.process_message(_message(), **args)
        assert first["execution"] == "completed" and first["delivery"] == "retryable"
        second = await channel.process_message(_message(), **args)
        assert second["delivery"] == "delivered"
        await channel.process_message(_message(), **args)
    asyncio.run(run())
    assert runs == ["Question"] and calls == ["answer", "answer"]


def test_ambiguous_send_redelivery_never_reexecutes_or_resends(isolated):
    runs, sends = [], []
    async def handler(msg):
        runs.append(msg.text)
        return "answer"
    async def send(reply):
        sends.append(reply.text)
        raise TimeoutError("possibly accepted upstream")
    async def run():
        for _ in range(2):
            row = await Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                        authorize=lambda: True, send=send, journal=isolated)
            assert row["delivery"] == "delivery_unknown"
    asyncio.run(run())
    assert runs == ["Question"] and sends == ["answer"]


def test_crash_after_execution_claim_is_explicit_and_never_reopened(isolated):
    row = isolated.admit(_message(), external_id="event1", account="account1")
    claimed = isolated.claim_execution(row)
    assert claimed["execution"] == "execution_started_unconfirmed"
    async def handler(msg):
        pytest.fail("Unknown execution replayed")
    actual = asyncio.run(Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                         authorize=lambda: True, send=lambda reply: None, journal=delivery.DeliveryJournal()))
    assert actual["execution"] == "execution_started_unconfirmed"


def test_concurrent_admission_and_execution_claim_has_one_owner(isolated):
    def admit(_):
        return delivery.DeliveryJournal().admit(_message(), external_id="event1", account="account1")
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(admit, range(8)))
    assert len({row["id"] for row in receipts}) == 1
    current = isolated.get(receipts[0]["id"])
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: isolated.claim_execution(current), range(8)))
    assert sum(row is not None for row in claims) == 1


def test_legacy_processed_receipt_is_not_automatically_reopened(isolated):
    with world_model.WorldModel(world_model.DEFAULT_DB) as world:
        world.mark_message_processed("fixture", "event1")
    row = isolated.admit(_message(), external_id="event1", account="account1")
    assert isolated.claim_execution(row) is None
    assert isolated.get(row["id"])["execution"] == "execution_unknown"


def test_authority_change_prevents_delayed_execution_and_delivery(isolated):
    authorized, runs = [True], []
    async def handler(msg):
        runs.append(msg.text)
        authorized[0] = False
        return "answer"
    async def send(reply):
        pytest.fail("Reply delivered after grant removed")
    channel = Adapter(handler)
    row = isolated.admit(_message(), external_id="event1", account="account1")
    with pytest.raises(PermissionError):
        asyncio.run(channel.process_message(_message(), external_id="event1", account="account1",
                    authorize=lambda: authorized[0], send=send, journal=isolated, receipt=row))
    assert runs == ["Question"]
    assert isolated.get(row["id"])["reply"]["text"] == "answer"


def test_chunk_recovery_preserves_confirmed_prefix_and_provider_ids(isolated):
    calls = []
    async def handler(msg):
        return "answer"
    async def send(reply):
        for index in range(2):
            async def call(index=index):
                calls.append(index)
                if calls == [0, 1]:
                    raise delivery.DeliveryNotSent("second chunk refused")
                return f"provider-{index}"
            await delivery.send_chunk(str(index), f"part{index}", call)
    async def run():
        args = dict(external_id="event1", account="account1", authorize=lambda: True, send=send, journal=isolated)
        row = await Adapter(handler).process_message(_message(), **args)
        assert row["delivery"] == "retryable"
        row = await Adapter(handler).process_message(_message(), **args)
        assert row["chunks"]["0"]["provider_id"] == "provider-0"
        assert row["delivery"] == "delivered"
    asyncio.run(run())
    assert calls == [0, 1, 1]


def test_account_and_payload_binding_and_capacity_do_not_evict_unknowns(isolated, monkeypatch):
    monkeypatch.setattr(delivery, "MAX_RECORDS", 2)
    first = isolated.admit(_message(), external_id="event1", account="account1")
    second = isolated.admit(_message(), external_id="event1", account="account2")
    assert first["id"] != second["id"]
    with pytest.raises(ValueError, match="different content"):
        isolated.admit(_message("replacement"), external_id="event1", account="account1")
    from maverick.governed_records import GovernedRecordCapacityError
    with pytest.raises(GovernedRecordCapacityError):
        isolated.admit(_message(), external_id="event2", account="account1")
    assert len(isolated.store.list(limit=3)) == 2
    assert isolated.compact_delivered() == 0


def test_attachment_bytes_survive_restart_and_corruption_blocks_execution(isolated):
    msg = _message()
    msg.attachments = [{"filename": "source.txt", "mime": "text/plain", "data": b"source bytes"}]
    row = isolated.admit(msg, external_id="event1", account="account1")
    replay = delivery._materialize(delivery.DeliveryJournal().get(row["id"]))
    assert replay.attachments == msg.attachments
    sha = row["message"]["attachments"][0]["sha256"]
    delivery._blob_path(row["id"], sha).write_bytes(b"changed")
    from maverick.attachments import AttachmentRejected
    with pytest.raises(AttachmentRejected):
        delivery._materialize(row)


@pytest.mark.skipif(os.name != "nt", reason="native Windows extended-path inspection")
def test_attachment_recovery_beyond_windows_max_path_preserves_digest_checks(isolated, monkeypatch):
    from maverick import paths
    from maverick.attachments import AttachmentRejected

    original = paths.data_dir

    def nested_blob_root(*parts):
        if parts[:2] == ("channels", "intake-blobs"):
            return original(*parts[:2], "deep-private-root-" * 8, *parts[2:])
        return original(*parts)

    monkeypatch.setattr(paths, "data_dir", nested_blob_root)
    message = _message()
    message.attachments = [{"filename": "source.txt", "mime": "text/plain", "data": b"retained bytes"}]
    row = isolated.admit(message, external_id="event1", account="account1")
    sha = row["message"]["attachments"][0]["sha256"]
    path = delivery._blob_path(row["id"], sha)
    assert len(str(path)) > 260
    try:
        assert delivery._materialize(delivery.DeliveryJournal().get(row["id"])).attachments == message.attachments
        path.write_bytes(b"different bytes")
        with pytest.raises(AttachmentRejected):
            delivery._materialize(row)
    finally:
        # Use the same extended path for this owned test artifact's cleanup.
        path.unlink(missing_ok=True)
        path.parent.rmdir()
        path.parent.parent.rmdir()


def test_revision_fence_rejects_stale_completion(isolated):
    row = isolated.admit(_message(), external_id="event1", account="account1")
    isolated.claim_execution(row)
    with pytest.raises(RecordConflict):
        isolated.change(row, execution="completed")


def test_send_receipt_commit_failure_retains_unknown_and_never_resends(isolated, monkeypatch):
    calls, runs = [], []
    change = isolated.change
    failed = []
    def fail_confirmation(row, **updates):
        if (not failed and updates.get("chunks", {}).get("reply", {}).get("state") == "delivered"):
            failed.append(True)
            raise OSError("receipt write lost after send")
        return change(row, **updates)
    monkeypatch.setattr(isolated, "change", fail_confirmation)
    async def handler(msg):
        runs.append(msg.text)
        return "answer"
    async def send(reply):
        calls.append(reply.text)
        return "accepted-provider-id"
    async def run():
        for _ in range(2):
            row = await Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                        authorize=lambda: True, send=send, journal=isolated)
            assert row["delivery"] == "delivery_unknown"
        assert row["chunks"]["reply"]["state"] == "sending"
    asyncio.run(run())
    assert calls == ["answer"] and runs == ["Question"]


def test_each_chunk_rechecks_authority_and_resumption_preserves_confirmed_prefix(isolated):
    allowed, calls = [True], []
    async def handler(msg):
        return "answer"
    async def send(reply):
        for index in range(2):
            async def call(index=index):
                calls.append(index)
                allowed[0] = False
            await delivery.send_chunk(str(index), str(index), call)
    row = asyncio.run(Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                     authorize=lambda: allowed[0], send=send, journal=isolated))
    assert calls == [0] and row["delivery"] == "delivery_unknown"
    assert row["chunks"]["0"]["state"] == "delivered"


def test_pre_ack_receipt_cannot_be_used_under_another_account(isolated):
    receipt = isolated.admit(_message(), external_id="event1", account="account1")
    async def handler(msg):
        pytest.fail("Cross-account receipt executed")
    with pytest.raises(PermissionError):
        asyncio.run(Adapter(handler).process_message(_message(), external_id="event1", account="account2",
                    authorize=lambda: True, send=lambda reply: None, receipt=receipt, journal=isolated))


def test_preparation_persists_attachments_before_handler_and_keeps_routing_identity(isolated):
    async def prepare(msg):
        msg.attachments = [{"filename": "download.txt", "mime": "text/plain", "data": b"downloaded"}]
        return msg
    async def handler(msg):
        assert msg.attachments[0]["data"] == b"downloaded"
        row = isolated.store.list(limit=1)[0]
        assert row["message"]["attachments"][0]["sha256"]
        return "answer"
    async def send(reply):
        return None
    row = asyncio.run(Adapter(handler).process_message(_message(), external_id="event1", account="account1",
                    authorize=lambda: True, send=send, prepare=prepare, journal=isolated))
    assert row["delivery"] == "delivered"


def test_retention_clears_only_confirmed_terminal_payloads_and_preserves_claim(isolated, monkeypatch):
    first = isolated.admit(_message(), external_id="event1", account="account1")
    first = isolated.change(first, execution="completed", delivery="delivered", reply={"text": "secret reply"})
    second = isolated.admit(_message(), external_id="event2", account="account1")
    second = isolated.change(second, execution="execution_unknown", delivery="delivered", reply={"text": "generic error"})
    class FutureClock:
        @staticmethod
        def time():
            return max(first["updated_at"], second["updated_at"]) + delivery.RETENTION_SECONDS + 1
    monkeypatch.setattr(delivery, "time", FutureClock)
    assert isolated.compact_delivered() == 1
    assert isolated.get(first["id"])["message"] == {}
    assert isolated.get(first["id"])["compacted"]
    assert isolated.get(second["id"])["message"]["text"] == "Question"
    assert isolated.admit(_message(), external_id="event1", account="account1")["compacted"]
