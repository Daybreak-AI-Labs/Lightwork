"""Shared trigger-registry authority, fencing, and cutover regressions."""
from __future__ import annotations

import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest


class _TenantPlane:
    """Atomic tenant documents matching ``SharedTriggerDocument``'s contract."""

    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}
        self.lock = threading.RLock()
        self.clock = 1_000.0
        self.fail_reads = False
        self.fail_mutations = False
        self.fail_after_commit = False
        self.actions: list[str] = []

    @staticmethod
    def _tenant() -> str:
        from maverick.paths import current_tenant_id_strict

        tenant = current_tenant_id_strict()
        assert tenant is not None
        return tenant

    def read(self) -> dict:
        from maverick_dashboard.trigger_authority import TriggerAuthorityUnavailable

        if self.fail_reads:
            raise TriggerAuthorityUnavailable("shared trigger registry read failed")
        with self.lock:
            return copy.deepcopy(self.documents.get(self._tenant(), {}))

    def mutate(self, callback, *, action: str):
        from maverick_dashboard.trigger_authority import TriggerAuthorityUnavailable

        if self.fail_mutations:
            raise TriggerAuthorityUnavailable("shared trigger registry update failed")
        with self.lock:
            tenant = self._tenant()
            document = copy.deepcopy(self.documents.get(tenant, {}))
            result = callback(document, self.clock)
            self.documents[tenant] = copy.deepcopy(document)
            self.actions.append(action)
            if self.fail_after_commit:
                self.fail_after_commit = False
                raise TriggerAuthorityUnavailable(
                    "shared trigger registry response was lost"
                )
            return result


def _snapshot(name: str = "triage") -> dict:
    return {
        "name": name,
        "title": "Triage item",
        "body": "Review this item.",
        "params": [],
        "budget_dollars": 1.0,
        "budget_wall_seconds": 60.0,
    }


@contextmanager
def _tenant(tenant: str | None):
    from maverick.paths import reset_tenant, set_tenant

    token = set_tenant(tenant)
    try:
        yield
    finally:
        reset_tenant(token)


@pytest.fixture
def shared_authority(monkeypatch, tmp_path):
    from maverick.paths import current_tenant_id_strict
    from maverick_dashboard import (
        event_triggers_store,
        trigger_authority,
        triggers_store,
    )

    webhook = _TenantPlane()
    event = _TenantPlane()
    active = {"acme", "globex"}
    selector = {"shared": True}

    def _selected() -> bool:
        return selector["shared"]

    def _require_active() -> str:
        tenant = current_tenant_id_strict()
        if tenant is None or tenant not in active:
            raise trigger_authority.TriggerAuthorityUnavailable(
                "shared trigger tenant authority is unavailable"
            )
        return tenant

    monkeypatch.setenv("MAVERICK_HOME", str(tmp_path))
    monkeypatch.setattr(
        triggers_store, "_path", lambda: tmp_path / "dashboard-triggers.toml"
    )
    monkeypatch.setattr(
        event_triggers_store,
        "_path",
        lambda: tmp_path / "dashboard-event-triggers.toml",
    )
    monkeypatch.setattr(triggers_store, "_SHARED_DOCUMENT", webhook)
    monkeypatch.setattr(event_triggers_store, "_SHARED_DOCUMENT", event)
    for module in (triggers_store, event_triggers_store):
        monkeypatch.setattr(module, "shared_selected", _selected)
        monkeypatch.setattr(module, "require_active_tenant", _require_active)
    monkeypatch.setattr(trigger_authority, "shared_selected", _selected)
    monkeypatch.setattr(trigger_authority, "require_active_tenant", _require_active)
    monkeypatch.setattr(
        triggers_store, "active_tenant_ids", lambda: tuple(sorted(active))
    )
    monkeypatch.setattr(
        trigger_authority, "active_tenant_ids", lambda: tuple(sorted(active))
    )
    monkeypatch.setattr(
        "maverick.tenant.registry.assert_tenant_active",
        lambda tenant: _require_active() if tenant == current_tenant_id_strict() else None,
    )
    return {
        "active": active,
        "event": event,
        "selector": selector,
        "webhook": webhook,
        "root": tmp_path,
    }


def test_concurrent_shared_creates_merge_without_lost_updates(shared_authority):
    from maverick_dashboard import event_triggers_store, triggers_store

    def _create(index: int) -> None:
        with _tenant("acme"):
            triggers_store.set_trigger(
                f"hook-{index}",
                "triage",
                owner="user:alice",
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_version=0,
            )
            event_triggers_store.set_trigger(
                f"feed-{index}",
                "triage",
                "http_json",
                config={"url": f"https://example.invalid/{index}"},
                owner="user:alice",
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_version=0,
            )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_create, range(48)))

    with _tenant("acme"):
        assert len(triggers_store.list_triggers()) == 48
        assert len(event_triggers_store.list_triggers()) == 48
    assert not (shared_authority["root"] / "dashboard-triggers.toml").exists()
    assert not (
        shared_authority["root"] / "dashboard-event-triggers.toml"
    ).exists()


def test_shared_scope_owner_cas_and_deprovisioning_fail_closed(shared_authority):
    from maverick_dashboard import event_triggers_store, triggers_store

    with _tenant("acme"):
        created = triggers_store.set_trigger(
            "deploy",
            "triage",
            owner="user:alice",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        with pytest.raises(triggers_store.TriggerAuthorityConflict):
            triggers_store.set_trigger(
                "deploy",
                "triage",
                owner="user:bob",
                tenant="acme",
                template_snapshot=_snapshot(),
                expected_owner="user:bob",
                expected_version=created["version"],
            )
        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.get_trigger("deploy", tenant="globex")

    with _tenant("globex"):
        assert triggers_store.get_trigger("deploy") is None
        assert event_triggers_store.list_triggers() == []

    shared_authority["active"].remove("acme")
    with _tenant("acme"):
        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.list_triggers()


def test_shared_created_and_cursor_times_use_backend_clock(
    shared_authority, monkeypatch,
):
    from maverick_dashboard import event_triggers_store, triggers_store

    monkeypatch.setattr(triggers_store, "time", type("Clock", (), {"time": lambda: 9e9}))
    monkeypatch.setattr(
        event_triggers_store, "time", type("Clock", (), {"time": lambda: 8e9})
    )
    with _tenant("acme"):
        hook = triggers_store.set_trigger(
            "clocked",
            "triage",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        feed = event_triggers_store.set_trigger(
            "clocked-feed",
            "triage",
            "http_json",
            cursor="seed",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        assert hook["created"] == 1_000.0
        assert feed["created"] == 1_000.0
        assert feed["cursor_updated_at"] == 1_000.0

        shared_authority["webhook"].clock = 2_000.0
        updated = triggers_store.set_trigger(
            "clocked",
            "triage",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=hook["version"],
        )
        assert updated["created"] == 1_000.0


def test_shared_cursor_is_fenced_and_response_loss_retry_is_idempotent(
    shared_authority,
):
    from maverick_dashboard import event_triggers_store

    with _tenant("acme"):
        trigger = event_triggers_store.set_trigger(
            "feed",
            "triage",
            "http_json",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        shared_authority["event"].clock = 1_250.0
        shared_authority["event"].fail_after_commit = True
        with pytest.raises(event_triggers_store.TriggerAuthorityUnavailable):
            event_triggers_store.set_cursor(
                "feed",
                "next",
                tenant="acme",
                expected_cursor=trigger["cursor"],
                expected_cursor_epoch=trigger["cursor_epoch"],
                expected_version=trigger["version"],
            )
        assert event_triggers_store.set_cursor(
            "feed",
            "next",
            tenant="acme",
            expected_cursor=trigger["cursor"],
            expected_cursor_epoch=trigger["cursor_epoch"],
            expected_version=trigger["version"],
        )
        current = event_triggers_store.get_trigger("feed", tenant="acme")
        assert current["cursor"] == "next"
        assert current["cursor_epoch"] == 1
        assert current["cursor_updated_at"] == 1_250.0
        assert current["version"] == 2
        assert not event_triggers_store.set_cursor(
            "feed",
            "stale",
            tenant="acme",
            expected_cursor="",
            expected_cursor_epoch=0,
            expected_version=1,
        )

        reconfigured = event_triggers_store.set_trigger(
            "feed",
            "triage",
            "http_json",
            config={"url": "https://new.example.invalid"},
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=current["version"],
        )
        assert reconfigured["version"] == 3
        assert not event_triggers_store.set_cursor(
            "feed",
            "newer",
            tenant="acme",
            expected_cursor=current["cursor"],
            expected_cursor_epoch=current["cursor_epoch"],
            expected_version=current["version"],
        )


def test_anonymous_webhook_resolution_rejects_absent_and_ambiguous_authority(
    shared_authority,
):
    from maverick_dashboard import triggers_store

    for tenant in ("acme", "globex"):
        with _tenant(tenant):
            triggers_store.set_trigger(
                "incoming",
                "triage",
                tenant=tenant,
                template_snapshot=_snapshot(),
                expected_version=0,
            )

    with _tenant(None):
        with pytest.raises(triggers_store.TriggerAuthorityConflict):
            triggers_store.resolve_trigger("incoming")
        assert triggers_store.resolve_trigger("absent") is None

    with _tenant("globex"):
        assert triggers_store.delete_trigger("incoming")
    with _tenant(None):
        assert triggers_store.resolve_trigger("incoming")["tenant"] == "acme"


def test_corrupt_schema_outage_and_unsafe_binding_never_fall_back(
    shared_authority,
):
    from maverick_dashboard import event_triggers_store, triggers_store

    with _tenant("acme"):
        shared_authority["event"].documents["acme"] = {
            "registry_schema": 1,
            "triggers": {"forged": {"secret": "raw-tenant-value"}},  # pragma: allowlist secret
        }
        with pytest.raises(event_triggers_store.TriggerAuthorityUnavailable):
            event_triggers_store.list_triggers()

        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.set_trigger(
                "unbound-flow",
                "",
                flow="deploy",
                tenant="acme",
                expected_version=0,
            )
        shared_authority["webhook"].fail_reads = True
        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.list_triggers()

    # A selector failure is authority failure, not permission to read a decoy
    # local registry.
    shared_authority["selector"]["shared"] = False
    with _tenant("acme"):
        triggers_store.set_trigger("local-decoy", "missing")
    shared_authority["selector"]["shared"] = True
    with _tenant("acme"):
        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.list_triggers()


def test_legacy_files_block_cutover_until_retryable_import_and_archive(
    shared_authority,
):
    from maverick_dashboard import event_triggers_store, triggers_store

    history = shared_authority["root"] / "trigger-events.ndjson"
    history.write_text('{"kind":"fired","evidence":"keep"}\n', encoding="utf-8")
    shared_authority["selector"]["shared"] = False
    with _tenant("acme"):
        triggers_store.set_trigger(
            "legacy-hook",
            "triage",
            tenant="acme",
            template_snapshot=_snapshot(),
        )
        event_triggers_store.set_trigger(
            "legacy-feed",
            "triage",
            "http_json",
            tenant="acme",
            template_snapshot=_snapshot(),
        )

    shared_authority["selector"]["shared"] = True
    with _tenant("acme"):
        with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
            triggers_store.list_triggers()
        with pytest.raises(event_triggers_store.TriggerAuthorityUnavailable):
            event_triggers_store.list_triggers()

    shared_authority["webhook"].fail_after_commit = True
    with pytest.raises(triggers_store.TriggerAuthorityUnavailable):
        triggers_store.import_legacy_to_shared()
    assert (shared_authority["root"] / "dashboard-triggers.toml").exists()

    hook_result = triggers_store.import_legacy_to_shared()
    event_result = event_triggers_store.import_legacy_to_shared()
    assert hook_result["rows"] == 1
    assert event_result["rows"] == 1
    assert hook_result["archive"]
    assert event_result["archive"]
    assert history.read_text(encoding="utf-8") == (
        '{"kind":"fired","evidence":"keep"}\n'
    )
    with _tenant("acme"):
        assert triggers_store.get_trigger("legacy-hook") is not None
        assert event_triggers_store.get_trigger("legacy-feed") is not None


def test_shared_scheduler_visits_all_active_scopes_and_restores_ambient(
    shared_authority, monkeypatch,
):
    from types import SimpleNamespace

    from maverick.paths import current_tenant_id
    from maverick_dashboard import automation_queue
    from maverick_dashboard.trigger_authority import TriggerAuthorityUnavailable

    visited: list[str] = []

    def _poll_one() -> None:
        tenant = current_tenant_id() or ""
        visited.append(tenant)
        if tenant == "acme":
            raise RuntimeError("one tenant is unavailable")

    monkeypatch.setattr(
        automation_queue, "_poll_due_triggers_current_tenant", _poll_one
    )
    monkeypatch.setattr(
        automation_queue, "queue", lambda: SimpleNamespace(is_shared=True)
    )
    with _tenant("ambient-poison"):
        with pytest.raises(TriggerAuthorityUnavailable):
            automation_queue._poll_due_triggers()
        assert current_tenant_id() == "ambient-poison"
    assert visited == ["acme", "globex"]


def test_shared_deprovisioned_owner_is_not_polled(
    shared_authority, monkeypatch,
):
    from maverick_dashboard import event_poll, event_triggers_store

    with _tenant("acme"):
        event_triggers_store.set_trigger(
            "owned-feed",
            "triage",
            "http_json",
            owner="user:departed",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        trigger = event_triggers_store.get_trigger("owned-feed", tenant="acme")

    polled: list[bool] = []
    monkeypatch.setattr(
        "maverick_dashboard.scim_groups.active_for_principal", lambda _owner: False
    )
    monkeypatch.setattr(
        "maverick.automation_events.poll_source",
        lambda *_args: polled.append(True),
    )
    result = event_poll.poll_and_fire(
        object(), [trigger], fire=lambda _goal: None
    )[0]
    assert result["poll_error"] is True
    assert result["notes"] == ["trigger authorization unavailable"]
    assert polled == []


def test_shared_audit_action_names_do_not_embed_tenant_owner_or_config(
    shared_authority,
):
    from maverick_dashboard import event_triggers_store, triggers_store

    with _tenant("acme"):
        triggers_store.set_trigger(
            "secret-hook",
            "triage",
            params={"password": "do-not-audit"},  # pragma: allowlist secret
            owner="user:alice",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )
        event_triggers_store.set_trigger(
            "secret-feed",
            "triage",
            "http_json",
            config={"authorization": "do-not-audit"},
            owner="user:alice",
            tenant="acme",
            template_snapshot=_snapshot(),
            expected_version=0,
        )

    actions = shared_authority["webhook"].actions + shared_authority["event"].actions
    assert actions
    assert all(
        secret not in action
        for action in actions
        for secret in ("acme", "alice", "secret", "do-not-audit")
    )
