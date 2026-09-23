"""Real local archive/bootstrap flow; synthetic channel states, no transports."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from maverick import client, config, crypto_at_rest, file_lock
from maverick import workspace_recovery as recovery
from maverick.paths import data_dir
from maverick_channels.base import IncomingMessage
from maverick_channels.delivery import DeliveryJournal, process_message, send_chunk


@pytest.fixture(autouse=True)
def isolated_bootstrap_environment(monkeypatch):
    monkeypatch.setenv("MAVERICK_CLIENT_ID", "acme")
    monkeypatch.setenv("MAVERICK_ENTERPRISE", "0")
    for name in ("MAVERICK_BACKUP_SIGNING_KEY", "MAVERICK_TENANT", "MAVERICK_WORLD_BACKEND",
                 "MAVERICK_GOVERNED_RECORDS_BACKEND", "MAVERICK_ALLOW_MULTIPLE_CONTROL_PLANES",
                 "MAVERICK_GRPC_DISPATCH_TARGET", "MAVERICK_PG_DSN", "MAVERICK_KNOWLEDGE_DSN",
                 "MAVERICK_QUEUE_REDIS_DSN", "MAVERICK_ENCRYPTION_KEY", "MAVERICK_KMS_KEK",
                 "QDRANT_URL", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "load_config", dict)
    monkeypatch.setattr(config, "config_source_errors", lambda **_: [])
    monkeypatch.setattr(crypto_at_rest, "per_tenant_at_rest", lambda: False)
    client.reset_client_cache()
    yield
    client.reset_client_cache()


def _select_home(monkeypatch, home):
    monkeypatch.setenv("MAVERICK_HOME", str(home))
    monkeypatch.setattr(crypto_at_rest, "_KEY_PATH", home / "shared-keys" / "at_rest.key")
    client.reset_client_cache()
    root = data_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record(root, namespace, value, *, encrypted):
    directory = file_lock.ensure_private_directory(root / namespace)
    payload = json.dumps(value, sort_keys=True).encode()
    file_lock.atomic_create_bytes(directory / f"{value['id']}.json",
                                  crypto_at_rest.seal(payload) if encrypted else payload)


@pytest.mark.parametrize("encrypted", [False, True])
def test_restored_channel_authorities_remain_blocked_without_no_replay_reconciliation(
    monkeypatch, tmp_path, encrypted,
):
    source = _select_home(monkeypatch, tmp_path / "source")
    recovery_key = recovery.configure_key()["key"]
    paths = []
    for index, (execution, delivery) in enumerate((
        ("received", "not_ready"), ("completed", "ready"),
        ("completed", "retryable"), ("execution_started_unconfirmed", "not_ready"),
        ("execution_unknown", "delivery_unknown"), ("completed", "delivered"),
    )):
        identity = "chd-" + f"{index:056x}"
        _record(source, "channel-deliveries", {"id": identity,
                "execution": execution, "delivery": delivery}, encrypted=encrypted)
        paths.append(f"channel-deliveries/{identity}.json")
    for namespace, prefix in (("channel-delivery-cursors", "chc"),
                              ("channel-poll-cursors", "chp"),
                              ("imessage-intake-cursors", "imc")):
        identity = prefix + "-" + "a" * 56
        _record(source, namespace, {"id": identity, "cursor": "synthetic-provider-position"}, encrypted=encrypted)
        paths.append(f"{namespace}/{identity}.json")
    original = {path: (source / path).read_bytes() for path in paths}
    snapshot = recovery.create()
    exported = tmp_path / "channel-source.tgz"
    file_lock.atomic_create_bytes(exported, recovery.download_path(snapshot["id"]).read_bytes())

    target = _select_home(monkeypatch, tmp_path / "target")
    recovery.configure_key(imported=recovery_key)
    imported = recovery.import_archive(exported)
    recovery.prepare_bootstrap(isolated=True, actor="admin", source_offline=True)
    review = recovery.prepare_restore(imported["id"], isolated=True, actor="admin")
    restored = recovery.apply_restore(review["review"], review["target"], isolated=True, actor="admin")
    assert restored["state"] == "reconciliation_required"
    status = recovery.reconciliation_status(actor="admin")
    blocked = {item["source"]: item for item in status["items"]}
    assert set(paths) <= blocked.keys()
    for path in paths:
        item = blocked[path]
        assert item["safe_state"] == "global_start_blocked"
        assert item["state"] == "unresolved" and item["requires_external_readback"]
        assert (target / path).read_bytes() == original[path]
        with pytest.raises(recovery.RecoveryError, match="cannot be safely rewritten"):
            recovery.resolve_reconciliation(item["id"], "external_effect_absent",
                evidence_reference="synthetic authoritative readback", isolated=True, actor="admin")
    with pytest.raises(recovery.RecoveryError):
        recovery.complete_reconciliation(isolated=True, actor="admin")
    with pytest.raises(recovery.RecoveryError, match="reconciliation is unfinished"):
        recovery.assert_bootstrap_start_allowed()

    async def cannot_replay():
        handler, provider = AsyncMock(), AsyncMock()
        channel = type("SyntheticChannel", (), {"handler": handler})()
        message = IncomingMessage(user_id="owner", text="request", channel="fixture", message_id="event")
        with pytest.raises(recovery.RecoveryError, match="reconciliation is unfinished"):
            await process_message(channel, message, external_id="event", account="configured-account",
                                  authorize=lambda: True, send=provider)
        with pytest.raises(recovery.RecoveryError, match="reconciliation is unfinished"):
            await send_chunk("reply", {"text": "saved reply"}, provider)
        with pytest.raises(recovery.RecoveryError, match="reconciliation is unfinished"):
            DeliveryJournal().admit(message, external_id="new-event", account="configured-account")
        handler.assert_not_awaited()
        provider.assert_not_awaited()
    asyncio.run(cannot_replay())
