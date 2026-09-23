"""Durable channel intake, execution claims and individually receipted replies.

Provider adapters own authentication, routing and definitive send-error
classification. Unknown execution/send effects never become automatic retries.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from itertools import islice
from pathlib import Path

from .base import IncomingMessage, Reply, as_reply

MAX_RECORDS = 10_000
MAX_TEXT_CHARS = 1_000_000
MAX_CHUNKS = 512
MAX_ATTACHMENTS = 10
RETENTION_SECONDS = 30 * 24 * 3600
_ACTIVE = contextvars.ContextVar("channel_delivery", default=None)


class DeliveryNotSent(RuntimeError):
    """Provider definitively rejected this attempt before accepting a message."""


class DeliveryUnknown(RuntimeError):
    """An execution or send may have happened; unsafe to repeat automatically."""


class _DeliveryOwnedElsewhere(RuntimeError):
    """A concurrent worker owns this send; the observer must not mutate it."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _assert_work_allowed():
    from maverick.workspace_recovery import assert_bootstrap_start_allowed

    assert_bootstrap_start_allowed()


def _bounded(value, limit, label):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"Invalid bounded channel {label}")
    return value


def _blob_path(record_id, sha):
    from maverick.file_lock import _windows_extended_path
    from maverick.paths import data_dir

    if not re.fullmatch(r"chd-[a-f0-9]{56}", record_id) or not re.fullmatch(r"[a-f0-9]{64}", sha):
        raise ValueError("Invalid channel attachment binding")
    path = data_dir("channels", "intake-blobs", record_id, sha)
    # Publication already uses Win32 extended paths. Inspection and reopened
    # reads must name the same file when a private workspace exceeds MAX_PATH.
    return Path(_windows_extended_path(path)) if os.name == "nt" else path


def _record_id(channel, account, external_id):
    from maverick.paths import current_tenant_id_strict

    _bounded(external_id, 4096, "message identity")
    _bounded(account, 4096, "configured account identity")
    _bounded(channel, 128, "adapter name")
    return "chd-" + _digest([current_tenant_id_strict(), channel, account, external_id])[:56]


def _cleanup_paths(record_id, digests):
    if (not isinstance(digests, list) or len(digests) > MAX_ATTACHMENTS
            or any(not isinstance(digest, str) for digest in digests)):
        raise ValueError("Invalid channel attachment cleanup manifest")
    return [_blob_path(record_id, digest) for digest in digests]


def _legacy_cleanup_digests(record_id):
    """Recover only bounded blob names owned by an already compacted receipt."""
    directory = _blob_path(record_id, "0" * 64).parent
    try:
        with os.scandir(directory) as entries:
            retained = list(islice(entries, MAX_ATTACHMENTS + 1))
    except FileNotFoundError:
        return []
    digests = [entry.name for entry in retained]
    _cleanup_paths(record_id, digests)
    if any(not entry.is_file(follow_symlinks=False) for entry in retained):
        raise ValueError("Invalid channel attachment cleanup entry")
    return digests


def _payload(msg, record_id):
    from maverick.attachments import MAX_GOAL_BYTES, validate_attachment

    if not isinstance(msg.text, str) or len(msg.text) > MAX_TEXT_CHARS or len(msg.attachments) > MAX_ATTACHMENTS:
        raise ValueError("Channel message exceeds its durable intake bound")
    files, publications, total = [], [], 0
    for item in msg.attachments:
        data = item.get("data")
        if not isinstance(data, bytes):
            raise ValueError("Channel attachment requires admitted bytes")
        filename, mime = item.get("filename"), item.get("mime")
        sha = validate_attachment(filename, mime, data, existing_total=total)
        total += len(data)
        if total > MAX_GOAL_BYTES:
            raise ValueError("Channel attachments exceed aggregate quota")
        files.append({"filename": filename, "mime": mime, "sha256": sha, "size_bytes": len(data)})
        publications.append((_blob_path(record_id, sha), data, sha))
    payload = {"user_id": _bounded(msg.user_id, 4096, "recipient"), "sender_id": msg.sender_id,
               "channel": msg.channel, "message_id": msg.message_id, "text": msg.text, "attachments": files}
    return payload, publications


def _materialize(record):
    from maverick.attachments import _verify_stored_attachment

    payload = dict(record["message"])
    files = []
    for item in payload.pop("attachments"):
        path = _blob_path(record["id"], item["sha256"])
        _verify_stored_attachment(path, item["sha256"])
        with path.open("rb") as stream:
            data = stream.read(item["size_bytes"] + 1)
        if len(data) != item["size_bytes"]:
            raise DeliveryUnknown("Admitted channel attachment changed size")
        files.append({"filename": item["filename"], "mime": item["mime"], "data": data})
    return IncomingMessage(**payload, attachments=files)


class DeliveryJournal:
    def __init__(self, store=None):
        from maverick.governed_records import GovernedRecordStore

        self.store = store or GovernedRecordStore("channel-deliveries", "chd", "channel_delivery")

    def get(self, record_id):
        return self.store.get(record_id)

    def recovery_window(self, *, channel, account, limit=16):
        """Fair bounded recovery; one window never proves an empty queue."""
        from maverick.governed_records import GovernedRecordStore
        from maverick.privacy_ops import RecordConflict

        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Invalid channel recovery window")
        cursors = GovernedRecordStore("channel-delivery-cursors", "chc", "channel_delivery_cursor")
        cursor_id = "chc-" + _digest([channel, account])[:56]
        cursor = cursors.get(cursor_id)
        if cursor is None:
            try:
                cursor = cursors.create({"id": cursor_id, "cursor": ""}, action="recovery_cursor_created",
                                        actor="system:channel", max_records=1000)
            except RecordConflict:
                cursor = cursors.get(cursor_id)
        rows, next_cursor = [], cursor["cursor"]
        for position, record_id in self.store.iter_record_ids(start_after=next_cursor, limit=limit):
            row = self.get(record_id)
            next_cursor = position
            if (row and not row.get("restore_hold") and row["channel"] == channel and row["account_sha256"] == _digest(account)
                    and (row["execution"] == "received" or row["delivery"] in {"ready", "retryable"})):
                rows.append(row)
        return rows, (cursors, cursor, next_cursor)

    def finish_recovery_window(self, checkpoint):
        from maverick.privacy_ops import RecordConflict

        cursors, cursor, next_cursor = checkpoint
        try:
            cursors.update(cursor["id"], lambda row: row.update(cursor=next_cursor),
                           expected_revision=cursor["revision"], action="recovery_window_completed", actor="system:channel")
        except RecordConflict:
            pass  # Another worker advanced; CAS execution/delivery still owns each item.

    def change(self, row, **updates):
        from maverick.privacy_ops import RecordConflict

        changed = self.store.update(row["id"], lambda record: record.update(updates),
                                    expected_revision=row["revision"], action="delivery_transition", actor="system:channel")
        if changed is None:
            raise RecordConflict("Channel receipt no longer exists")
        return changed

    def admit(self, msg, *, external_id, account, route=None):
        from maverick.attachments import _publish_attachment
        from maverick.file_lock import ensure_private_directory
        from maverick.privacy_ops import RecordConflict

        _assert_work_allowed()
        record_id = _record_id(msg.channel, account, external_id)
        payload, publications = _payload(msg, record_id)
        route = {} if route is None else route
        if len(json.dumps(route).encode()) > 65536:
            raise ValueError("Channel reply routing exceeds its bound")
        fingerprint = _digest([payload, route])
        row = self.get(record_id)
        if row is None:
            # Confirm the existing execution-claim authority is readable before
            # accepting intake. No execution claim is taken at this boundary.
            from maverick.world_model import close_world_if_owned, open_world

            world = open_world()
            try:
                world.is_processed_message(msg.channel, external_id)
            finally:
                close_world_if_owned(world)
            try:
                row = self.store.create({"id": record_id, "channel": msg.channel, "account_sha256": _digest(account),
                                         "external_id": external_id, "fingerprint": fingerprint, "message": payload,
                                         "route": route, "execution": "staging", "delivery": "not_ready", "chunks": {},
                                         "execution_id": str(uuid.uuid4())},
                                        action="intake_admitted", actor="system:channel", max_records=MAX_RECORDS)
            except RecordConflict:
                row = self.get(record_id)
        if not row or row["fingerprint"] != fingerprint:
            raise ValueError("Channel message identity already belongs to different content or routing")
        if row.get("restore_hold"):
            return row
        if row["execution"] == "staging":
            for path, data, sha in publications:
                ensure_private_directory(path.parent)
                _publish_attachment(path, data, sha)
            try:
                row = self.change(row, execution="received")
            except RecordConflict:
                row = self.get(record_id)
        return row

    def claim_execution(self, row):
        from maverick.privacy_ops import RecordConflict
        from maverick.world_model import close_world_if_owned, open_world

        _assert_work_allowed()
        if row.get("restore_hold") or row["execution"] != "received":
            return None
        # Open/check the legacy authority before claiming execution so a store
        # outage remains safely retryable. Legacy claims are never reopened.
        world = open_world()
        try:
            legacy = world.is_processed_message(row["channel"], row["external_id"])
            try:
                claimed = self.change(row, execution="execution_started_unconfirmed")
            except RecordConflict:
                return None
            if legacy or not world.mark_message_processed("delivery:" + row["channel"], row["id"]):
                self.change(claimed, execution="execution_unknown", reason="Prior execution claim has no confirmed result")
                return None
            return claimed
        finally:
            close_world_if_owned(world)

    def compact_delivered(self, *, limit=100):
        """Clear expired payloads only; retain dedup tombstones and unknowns.

        Capacity is a hard admission bound, not permission to evict unresolved
        or deduplication identities. Operators can archive the tenant authority
        under their retention policy before provisioning further capacity.
        """
        from maverick.governed_records import GovernedRecordStore
        from maverick.privacy_ops import RecordConflict

        _assert_work_allowed()
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid channel retention batch")
        cursors = GovernedRecordStore("channel-delivery-cursors", "chc", "channel_delivery_cursor")
        cursor_id = "chc-" + _digest(["maintenance", "retention", 1])[:56]
        cursor = cursors.get(cursor_id)
        if cursor is None:
            try:
                cursor = cursors.create({"id": cursor_id, "cursor": "", "purpose": "retention"},
                                        action="retention_cursor_created", actor="system:channel", max_records=1000)
            except RecordConflict:
                cursor = cursors.get(cursor_id)
        now, compacted = time.time(), 0
        next_cursor = cursor["cursor"]
        try:
            for position, record_id in self.store.iter_record_ids(start_after=cursor["cursor"], limit=limit):
                next_cursor = position
                compacted += self._compact_receipt(self.get(record_id), now)
        finally:
            # Surface cleanup failures, but do not let one locked or malformed
            # receipt starve every later record. The rotating scan retries it.
            self.finish_recovery_window((cursors, cursor, next_cursor))
        return compacted

    def _compact_receipt(self, row, now):
        from maverick.privacy_ops import RecordConflict

        if (not row or row.get("restore_hold") or row["execution"] != "completed"
                or row["delivery"] != "delivered"):
            return False
        if row.get("compacted") and row.get("cleanup_pending") == []:
            return False
        if ("cleanup_pending" not in row and not row.get("compacted")
                and row["updated_at"] > now - RETENTION_SECONDS):
            return False
        try:
            if "cleanup_pending" not in row:
                digests = (_legacy_cleanup_digests(row["id"]) if row.get("compacted") else
                           [item["sha256"] for item in row["message"].get("attachments", [])])
                _cleanup_paths(row["id"], digests)
                # CAS establishes durable cleanup intent before any deletion.
                # Retrying this state bypasses the updated_at retention cutoff.
                row = self.change(row, message={}, reply=None, chunks={}, route={},
                                  cleanup_pending=digests, compacted=False)
            for path in _cleanup_paths(row["id"], row["cleanup_pending"]):
                _assert_work_allowed()
                path.unlink(missing_ok=True)
            self.change(row, cleanup_pending=[], compacted=True)
        except RecordConflict:
            return False  # Another worker owns/completed the transition.
        return True


async def admit_message(msg, *, external_id, account, authorize, route=None, journal=None):
    if not authorize():
        raise PermissionError("Channel sender authorization changed")
    journal = journal or DeliveryJournal()
    return await asyncio.to_thread(journal.admit, msg, external_id=external_id, account=account, route=route)


async def process_message(channel, msg, *, external_id, account, authorize, send, route=None,
                          receipt=None, journal=None, idempotent=False, prepare=None):
    _assert_work_allowed()
    journal = journal or DeliveryJournal()
    if receipt is not None:
        expected = _record_id(msg.channel, account, external_id)
        if receipt.get("id") != expected:
            raise PermissionError("Channel receipt does not belong to the current tenant and account")
        row = await asyncio.to_thread(journal.get, expected)
        if row is None:
            raise DeliveryUnknown("Admitted channel receipt is unavailable")
    else:
        row = await admit_message(msg, external_id=external_id, account=account,
                                  authorize=authorize, route=route, journal=journal)
    if not authorize():
        raise PermissionError("Channel sender authorization changed")
    # Materialization/downloads do not dispatch the agent. Leave the receipt
    # received if preparation fails so a later recovery can safely try again.
    # CAS publication plus the execution claim still gives one handler owner.
    if row.get("restore_hold"):
        return row  # A recovery disposition never authorizes old work to replay.
    inbound = None
    if row["execution"] == "received":
        inbound = await asyncio.to_thread(_materialize, row)
        if prepare is not None and not row.get("preparation_complete"):
            from maverick.attachments import _publish_attachment
            from maverick.file_lock import ensure_private_directory
            from maverick.privacy_ops import RecordConflict

            inbound = await prepare(inbound)
            payload, publications = await asyncio.to_thread(_payload, inbound, row["id"])
            if any(payload[key] != row["message"][key]
                    for key in ("user_id", "sender_id", "channel", "message_id", "text")):
                raise PermissionError("Prepared channel input changed its admitted identity or text")
            for path, data, sha in publications:
                await asyncio.to_thread(ensure_private_directory, path.parent)
                await asyncio.to_thread(_publish_attachment, path, data, sha)
            try:
                row = await asyncio.to_thread(journal.change, row, message=payload, preparation_complete=True)
            except RecordConflict:
                # Another preparer owns the current revision. It alone may
                # claim execution; an observer must not use stale input.
                return await asyncio.to_thread(journal.get, row["id"])
        if not authorize():
            raise PermissionError("Channel sender authorization changed")
    claimed = await asyncio.to_thread(journal.claim_execution, row)
    if claimed:
        try:
            if not authorize():
                raise PermissionError("Channel sender authorization changed")
            _assert_work_allowed()
            reply = as_reply(await channel.handler(inbound))
            if len(reply.text) > MAX_TEXT_CHARS or len(reply.attachments) > MAX_ATTACHMENTS:
                raise ValueError("Channel reply exceeds durable outbox bound")
            row = await asyncio.to_thread(journal.change, claimed, execution="completed", delivery="ready", reply=asdict(reply))
        except Exception:
            row = await asyncio.to_thread(journal.change, claimed, execution="execution_unknown", delivery="ready",
                                          reply=asdict(Reply("⚠ An internal error occurred.")),
                                          reason="Execution began without a confirmed stored result; review before retrying")
    else:
        row = await asyncio.to_thread(journal.get, row["id"])
    if not row.get("reply") or row["delivery"] == "delivered":
        return row
    if not authorize():
        raise PermissionError("Channel reply authorization changed")
    context = _DeliveryAttempt(journal, row["id"], authorize)
    token = _ACTIVE.set(context)
    try:
        reply = Reply(**row["reply"])
        # The outer operation protects adapters whose provider offers no
        # chunk API. Instrumented adapters additionally receipt each chunk.
        await context.send_chunk("reply", row["reply"], lambda: send(reply), idempotent=idempotent)
    except Exception:
        # The owning chunk transition records the outcome atomically. A
        # duplicate observer (or failed receipt write) cannot overwrite a
        # live owner's state with an inferred delivery outcome.
        await context.mark_unconfirmed()
    finally:
        _ACTIVE.reset(token)
    return await asyncio.to_thread(journal.get, row["id"])


async def recover_messages(channel, *, account, authorize, send, prepare=None, limit=16,
                           journal=None, idempotent=False):
    """Recover one fair window with adapter-owned routing and current policy.

    authorize(message) -> bool; send(message, saved_reply, route) -> awaitable;
    optional prepare(message, route) -> awaitable[IncomingMessage].
    """
    journal = journal or DeliveryJournal()
    rows, checkpoint = await asyncio.to_thread(journal.recovery_window, channel=channel.name, account=account, limit=limit)
    outcomes = []
    for row in rows:
        values = {key: value for key, value in row["message"].items() if key != "attachments"}
        incoming = IncomingMessage(**values)
        if not authorize(incoming):
            continue

        async def send_saved(reply, incoming=incoming, route=row["route"]):
            return await send(incoming, reply, route)

        async def prepare_saved(message, route=row["route"]):
            return await prepare(message, route)

        outcomes.append(await process_message(channel, incoming, external_id=row["external_id"], account=account,
                        authorize=lambda incoming=incoming: authorize(incoming), send=send_saved,
                        prepare=prepare_saved if prepare else None, receipt=row, journal=journal, idempotent=idempotent))
    await asyncio.to_thread(journal.finish_recovery_window, checkpoint)
    return outcomes


class _DeliveryAttempt:
    def __init__(self, journal, record_id, authorize):
        self.journal, self.record_id, self.authorize = journal, record_id, authorize
        self.outer_attempt_id = None

    async def mark_unconfirmed(self):
        from maverick.privacy_ops import RecordConflict

        if self.outer_attempt_id is None:
            return
        row = await asyncio.to_thread(self.journal.get, self.record_id)
        chunk = row["chunks"].get("reply", {})
        if chunk.get("attempt_id") != self.outer_attempt_id or chunk.get("state") != "sending":
            return
        try:
            await asyncio.to_thread(self.journal.change, row, delivery="delivery_unknown")
        except RecordConflict:
            pass  # A confirmed owner transition always wins over this fallback.

    async def send_chunk(self, key, payload, call, *, idempotent=False):
        from maverick.privacy_ops import RecordConflict

        _assert_work_allowed()
        if not self.authorize():
            raise PermissionError("Channel reply authorization changed")
        key = _bounded(key, 128, "chunk key")
        row = await asyncio.to_thread(self.journal.get, self.record_id)
        if row.get("restore_hold"):
            raise DeliveryUnknown("Restored channel delivery remains retired from automatic replay")
        chunks = dict(row["chunks"])
        old = chunks.get(key)
        digest = _digest(payload)
        if old and old["payload_sha256"] != digest:
            raise DeliveryUnknown("Saved channel chunk changed")
        if old and old["state"] == "delivered":
            return old.get("provider_id")
        if old and old["state"] == "sending":
            raise _DeliveryOwnedElsewhere("Another worker owns this channel delivery")
        if old and old["state"] == "delivery_unknown" and not idempotent:
            raise DeliveryUnknown("Prior channel send has no confirmed receipt")
        if key not in chunks and len(chunks) >= MAX_CHUNKS:
            raise DeliveryNotSent("Channel reply exceeds chunk bound")
        attempt_id = uuid.uuid4().hex
        chunks[key] = {"state": "sending", "payload_sha256": digest, "attempt_id": attempt_id,
                       "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL, self.record_id + ":" + key))}
        try:
            await asyncio.to_thread(self.journal.change, row, chunks=chunks, delivery="sending")
        except RecordConflict as exc:
            raise _DeliveryOwnedElsewhere("Another worker owns this channel delivery") from exc
        if key == "reply":
            self.outer_attempt_id = attempt_id
        try:
            _assert_work_allowed()
            if not self.authorize():
                raise DeliveryNotSent("Channel sender authorization changed before sending")
            result = await call()
        except DeliveryNotSent:
            await self._finish(key, attempt_id, "retryable")
            raise
        except BaseException:
            await self._finish(key, attempt_id, "delivery_unknown")
            raise
        await self._finish(key, attempt_id, "delivered", str(result or "")[:512])
        return result

    async def _finish(self, key, attempt_id, state, provider_id=""):
        row = await asyncio.to_thread(self.journal.get, self.record_id)
        chunks = dict(row["chunks"])
        if chunks[key]["attempt_id"] != attempt_id:
            raise DeliveryUnknown("Channel send ownership changed")
        chunks[key] = {**chunks[key], "state": state, "provider_id": provider_id}
        updates = {"chunks": chunks}
        if key == "reply":
            updates["delivery"] = state
        await asyncio.to_thread(self.journal.change, row, **updates)


async def send_chunk(key, payload, call, *, idempotent=False):
    """Receipt one provider call; standalone Channel.send retains its old API."""
    _assert_work_allowed()
    current = _ACTIVE.get()
    return await call() if current is None else await current.send_chunk(key, payload, call, idempotent=idempotent)


def chunk_idempotency_key(key):
    current = _ACTIVE.get()
    return str(uuid.uuid4()) if current is None else str(uuid.uuid5(uuid.NAMESPACE_URL, current.record_id + ":" + key))


def delivery_active():
    return _ACTIVE.get() is not None


@asynccontextmanager
async def recovery_worker(channel, **kwargs):
    """Own one periodic recovery task for a running transport."""
    async def run():
        while True:
            try:
                await channel.recover_messages(**kwargs)
            except Exception:
                logging.getLogger(__name__).warning("Channel %s recovery window requires review", channel.name)
            await asyncio.sleep(30)
    task = asyncio.create_task(run(), name="channel-recovery:" + channel.name)
    try:
        yield
    finally:
        task.cancel()
        done, _ = await asyncio.wait([task], timeout=5)
        if task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
        else:
            logging.getLogger(__name__).warning("Channel %s recovery cancellation remains unconfirmed", channel.name)
